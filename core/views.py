from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Membership
from core.permissions import IsTenantAdmin
from core.serializers import TenantSerializer
from core.tenancy import resolve_membership_for_user

_EMPTY_TASK_COUNTS = {"absences": 0, "trades": 0, "time_records": 0}


def _task_counts(membership, employee):
    """
    "Offene Tasks"-Zähler für die Header-Badges (MVP-Fahrplan Block 2.4):
    Admin/Planer sehen, was auf tenant-weite Freigabe wartet; Mitarbeitende
    sehen nur eigene, tatsächlich an sie persönlich adressierte Tasks
    (Tauschanfragen, bei denen sie die Zielperson sind) -- Absenzen/
    Zeiterfassung genehmigen sie ohnehin nicht, dort bleibt der Zähler 0.
    Import von scheduling.models hier aus demselben Grund wie in MeView.get()
    (core bleibt die "unterste" App).
    """
    from scheduling.models import Absence, ShiftTradeRequest, TimeRecord

    if membership.role in (Membership.Role.ADMIN, Membership.Role.PLANNER):
        return {
            "absences": Absence.all_objects.filter(
                tenant=membership.tenant, status=Absence.Status.PENDING
            ).count(),
            # EMPLOYEE_ACCEPTED, nicht PENDING: das ist der Stand, an dem die
            # Anfrage tatsächlich auf Admin/Planer-Freigabe wartet (siehe
            # ShiftTradeRequest-Docstring) -- ein PENDING-Request wartet in
            # aller Regel zuerst auf die Zielperson.
            "trades": ShiftTradeRequest.all_objects.filter(
                tenant=membership.tenant, status=ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED
            ).count(),
            "time_records": TimeRecord.all_objects.filter(
                tenant=membership.tenant, status=TimeRecord.Status.SUBMITTED
            ).count(),
        }
    if membership.role == Membership.Role.EMPLOYEE and employee:
        return {
            "absences": 0,
            "trades": ShiftTradeRequest.all_objects.filter(
                tenant=membership.tenant,
                target_employee=employee,
                status=ShiftTradeRequest.Status.PENDING,
            ).count(),
            "time_records": 0,
        }
    return dict(_EMPTY_TASK_COUNTS)


class MeView(APIView):
    """
    Rolle + (falls vorhanden) verknüpfte Employee des eingeloggten Users --
    Grundlage für ein rollenbewusstes Frontend (MVP-Fahrplan, Block 2.2):
    Admin/Planer sehen die volle Bearbeitungs-Oberfläche, Mitarbeitende/HR
    eine eingeschränkte Self-Service-/Reporting-Ansicht.

    Import von scheduling.models.Employee bewusst hier (nicht in core/models.py),
    damit core keine Modul-Level-Abhängigkeit zu scheduling bekommt --
    core bleibt die "unterste" App, auf die scheduling aufbaut, nicht umgekehrt.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from scheduling.models import Employee

        membership = resolve_membership_for_user(request.user)
        if not membership:
            return Response(
                {"role": None, "tenant_name": None, "employee": None, "task_counts": dict(_EMPTY_TASK_COUNTS)}
            )

        employee = Employee.all_objects.filter(tenant=membership.tenant, user=request.user).first()
        return Response(
            {
                "role": membership.role,
                "tenant_name": membership.tenant.name,
                "employee": (
                    {
                        "id": employee.id,
                        "first_name": employee.first_name,
                        "last_name": employee.last_name,
                    }
                    if employee
                    else None
                ),
                "task_counts": _task_counts(membership, employee),
            }
        )


class TenantView(APIView):
    """
    Tenant-Konfiguration (MVP-Fahrplan Block 2, Punkt 14): GET liefert die
    numerischen ArG-/Zuschlags-Grenzwerte des eigenen Tenants (Lesen wie
    überall in der App für alle vier Rollen offen), PATCH ändert sie
    (Admin-only, siehe core.permissions.IsTenantAdmin) -- bislang nur im
    Django-Admin editierbar. Single-Object-Endpoint analog zu MeView, kein
    ViewSet mit Liste: es gibt genau einen Tenant pro eingeloggtem Account.

    request.membership wird hier wie in
    scheduling.views.TenantScopedViewSet.initial() aufgelöst -- NACH der
    Authentifizierung, VOR der Permission-Prüfung, weil core.permissions
    das erwartet. Nicht in einer Middleware (siehe core/tenancy.py):
    request.user ist an der Middleware-Stelle bei Token-Logins noch nicht
    aufgelöst.
    """

    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def initial(self, request, *args, **kwargs):
        self.format_kwarg = self.get_format_suffix(**kwargs)
        neg = self.perform_content_negotiation(request)
        request.accepted_renderer, request.accepted_media_type = neg
        version, scheme = self.determine_version(request, *args, **kwargs)
        request.version, request.versioning_scheme = version, scheme

        self.perform_authentication(request)
        membership = resolve_membership_for_user(request.user)
        request.membership = membership
        request.tenant = membership.tenant if membership else None

        self.check_permissions(request)
        self.check_throttles(request)

    def get(self, request):
        if not request.tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        return Response(TenantSerializer(request.tenant).data)

    def patch(self, request):
        if not request.tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        serializer = TenantSerializer(request.tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

from django.utils import timezone
from rest_framework import viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Membership, TenantHolidayOverride
from core.permissions import IsTenantAdmin
from core.serializers import MembershipSerializer, TenantHolidayOverrideSerializer, TenantSerializer
from core.tenancy import resolve_membership_for_user

_EMPTY_TASK_COUNTS = {"absences": 0, "trades": 0, "time_records": 0}


class TenantScopedAPIMixin:
    """
    Gemeinsame initial()-Logik für core-Views, die auf request.tenant/
    request.membership angewiesen sind (core.permissions), aber -- anders
    als scheduling.views.TenantScopedViewSet -- kein scheduling importieren
    dürfen (core bleibt die "unterste" App, siehe MeView-Docstring). request.
    tenant wird bewusst hier gesetzt, NACH self.perform_authentication()
    (Token-/Session-Login), nicht in einer Middleware, wo request.user bei
    Token-Logins noch nicht aufgelöst wäre (siehe core/tenancy.py). Vorher
    dreimal dupliziert (TenantView, TenantHolidayOverrideViewSet,
    TenantHolidaysView) -- ab hier ein gemeinsamer Mixin.
    """

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


def _task_counts(membership, employee):
    """
    "Offene Tasks"-Zähler für die Header-Badges (MVP-Fahrplan Block 2.4):
    Admin/Planer sehen, was auf tenant-weite Freigabe wartet; Mitarbeitende
    sehen nur eigene, tatsächlich an sie persönlich adressierte Tasks
    (Tauschanfragen, bei denen sie die Zielperson sind) -- Absenzen/
    Zeiterfassung genehmigen sie ohnehin nicht, dort bleibt der Zähler 0.
    Import von scheduling.models hier aus demselben Grund wie in MeView.get()
    (core bleibt die "unterste" App).

    Nutzer-Feedback (2026-08): "ich muss die Stationen durchsuchen, bis ich
    die zu bestätigende Erfassung finde" -- der time_records-Zähler zählte
    bisher tenant-weit, unabhängig davon, ob ein Planer inzwischen (siehe
    Membership.scoped_nodes) auf einzelne Stationen eingeschränkt ist. Jetzt
    stationsübergreifend über GENAU die Stationen gezählt, die der Planer in
    der neuen "Zu bestätigen"-Übersicht auch tatsächlich sieht (dieselbe
    Scoping-Logik wie TimeRecordViewSet, siehe _employee_scoped_node_ids) --
    sonst würde die Badge-Zahl wieder nicht zu dem passen, was ein Klick
    darauf zeigt. absences/trades bleiben bewusst tenant-weit (kein direkter
    Stationsbezug ohne zusätzlichen Join über Employee -- ausserhalb des
    Rahmens dieser Änderung, siehe README).
    """
    from scheduling.models import Absence, ShiftTradeRequest, TimeRecord
    from scheduling.views import _employee_scoped_node_ids

    if membership.role in (Membership.Role.ADMIN, Membership.Role.PLANNER):
        time_records_qs = TimeRecord.all_objects.filter(
            tenant=membership.tenant, status=TimeRecord.Status.SUBMITTED
        )
        node_ids = _employee_scoped_node_ids(membership, None)
        if node_ids is not None:
            time_records_qs = time_records_qs.filter(assignment__node_id__in=node_ids)
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
            "time_records": time_records_qs.count(),
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


class TenantView(TenantScopedAPIMixin, APIView):
    """
    Tenant-Konfiguration (MVP-Fahrplan Block 2, Punkt 14): GET liefert die
    numerischen ArG-/Zuschlags-Grenzwerte des eigenen Tenants (Lesen wie
    überall in der App für alle vier Rollen offen), PATCH ändert sie
    (Admin-only, siehe core.permissions.IsTenantAdmin) -- bislang nur im
    Django-Admin editierbar. Single-Object-Endpoint analog zu MeView, kein
    ViewSet mit Liste: es gibt genau einen Tenant pro eingeloggtem Account.
    """

    permission_classes = [IsAuthenticated, IsTenantAdmin]

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


class TenantHolidayOverrideViewSet(TenantScopedAPIMixin, viewsets.ModelViewSet):
    """
    Manuelle Feiertags-Ausnahmen zum kantonalen Kalender (Arbeitszeitmodell,
    README Block 2.7 Punkt 7, siehe Tenant.public_holidays()). Selbe
    Berechtigungs-Struktur wie TenantView, da Teil derselben Tenant-
    Konfiguration -- Lesen für alle vier Rollen offen, Schreiben nur Admin
    (IsTenantAdmin).
    """

    serializer_class = TenantHolidayOverrideSerializer
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_queryset(self):
        # Explizit über all_objects + request.tenant statt ContextVar-Manager
        # (dieselbe "explizite Filterung ist die echte Grenze"-Philosophie
        # wie in scheduling.views.TenantScopedViewSet).
        if not self.request.tenant:
            return TenantHolidayOverride.all_objects.none()
        return TenantHolidayOverride.all_objects.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


class MembershipViewSet(TenantScopedAPIMixin, viewsets.ModelViewSet):
    """
    Nutzer-Feedback (2026-08): "Kann man [Planer] Stationen zuweisen?" --
    Admin-only Verwaltung von Membership.scoped_nodes (siehe
    scheduling.views._employee_scoped_node_ids und MembershipSerializer).
    Bewusst kein create/destroy über diesen Endpoint: es geht nur um die
    Stations-Einschränkung EINER bereits bestehenden Mitgliedschaft, nicht um
    Einladung/Rollenvergabe (bleibt wie bisher Django-Admin-only, siehe
    EmployeeSettings.jsx-Kommentar) -- ein "falscher" Endpoint dafür wäre
    mehr Verwirrung als Nutzen.
    """

    http_method_names = ["get", "head", "options", "patch"]
    serializer_class = MembershipSerializer
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_queryset(self):
        if not self.request.tenant:
            return Membership.objects.none()
        return Membership.objects.filter(tenant=self.request.tenant).select_related("user").order_by(
            "role", "user__username"
        )

    def perform_update(self, serializer):
        # ADMIN ist in _employee_scoped_node_ids() unbedingt uneingeschränkt
        # (Nutzer-Vorgabe: "Nur Admin darf immer alles sehen") -- scoped_nodes
        # auf einer Admin-Mitgliedschaft zu speichern hätte also nie einen
        # Effekt. Klarer Fehler statt eines stillen No-Ops.
        if serializer.instance.role == Membership.Role.ADMIN:
            raise ValidationError(
                {"scoped_nodes": "Admin sieht immer alle Stationen -- keine Einschränkung möglich."}
            )
        serializer.save()


class TenantHolidaysView(TenantScopedAPIMixin, APIView):
    """
    Aufgelöste Feiertagsdaten (inkl. Name) für ein Kalenderjahr
    (Arbeitszeitmodell, README Block 2.7 Punkt 7) -- Grundlage für die
    Feiertags-Markierung im Planblatt/Jahresplan (PlanGrid.jsx/YearPlan.jsx).
    Anders als /api/tenant/ (dort steht nur der Kanton-Code) liefert dieser
    Endpoint die vom Kanton + TenantHolidayOverride bereits aufgelöste Liste,
    weil die eigentliche Berechnung (holidays-Bibliothek, bewegliche Feste)
    bewusst nur im Backend passiert -- siehe
    Tenant.public_holidays_with_names(). Lesen wie bei /api/tenant/ für alle
    vier Rollen offen, kein eigener Schreibzugriff (die Konfiguration läuft
    über canton/TenantHolidayOverride).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        year_param = request.query_params.get("year")
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                raise ValidationError({"year": "Ungültiges Jahr."})
        else:
            year = timezone.localdate().year
        entries = sorted(request.tenant.public_holidays_with_names(year).items())
        return Response({"year": year, "dates": [{"date": d.isoformat(), "name": n} for d, n in entries]})

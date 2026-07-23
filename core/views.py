from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.tenancy import resolve_membership_for_user


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
            return Response({"role": None, "tenant_name": None, "employee": None})

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
            }
        )

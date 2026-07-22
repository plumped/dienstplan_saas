from core.context import set_current_tenant
from core.models import Membership


class TenantMiddleware:
    """
    Ermittelt den aktuellen Tenant aus der Mitgliedschaft des eingeloggten
    Users und macht ihn über request.tenant sowie die ContextVar
    (core.context) für die Dauer des Requests verfügbar.

    MVP-Annahme: ein User gehört zu genau einem Tenant. Für Mitarbeitende,
    die an mehreren Einrichtungen arbeiten, könnte hier später ein Header
    (z. B. X-Tenant-Slug) ausgewertet werden, um zwischen mehreren
    Memberships zu wählen.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = None
        if request.user.is_authenticated:
            membership = (
                Membership.objects.select_related("tenant")
                .filter(user=request.user)
                .first()
            )
            if membership:
                tenant = membership.tenant

        request.tenant = tenant
        set_current_tenant(tenant)
        try:
            response = self.get_response(request)
        finally:
            set_current_tenant(None)
        return response

from core.middleware import ADMIN_TENANT_SESSION_KEY
from core.models import Tenant


def active_admin_tenant(request):
    """
    Macht den aktiven Admin-Tenant (core.middleware.AdminActiveTenantMiddleware)
    in jedem Template verfügbar -- genutzt vom Tenant-Umschalter im
    Admin-Header (templates/admin/base_site.html).
    """
    tenant_id = getattr(request, "session", {}).get(ADMIN_TENANT_SESSION_KEY)
    if not tenant_id:
        return {"active_tenant_name": None}
    tenant = Tenant.objects.filter(pk=tenant_id).first()
    return {"active_tenant_name": tenant.name if tenant else None}

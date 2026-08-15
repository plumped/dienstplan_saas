"""
Hält den 'aktuellen Tenant' pro Request in einer ContextVar fest, damit
Model-Manager (siehe models.TenantScopedManager) automatisch danach filtern
können, ohne dass jede Query explizit tenant=... mitgeben muss.

Gesetzt wird sie für API-Requests in core.tenancy.apply_tenant_scoped_initial()
(nur wenn bind_scheduling_context=True, siehe scheduling.views.
TenantScopedViewSet) und für Django-Admin-Requests in
core.middleware.AdminActiveTenantMiddleware; zurückgesetzt nach jedem Request
in core.middleware.TenantContextCleanupMiddleware.
"""

from contextvars import ContextVar

_current_tenant: ContextVar = ContextVar("current_tenant", default=None)


def get_current_tenant():
    return _current_tenant.get()


def set_current_tenant(tenant) -> None:
    _current_tenant.set(tenant)

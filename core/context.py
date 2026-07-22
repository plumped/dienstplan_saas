"""
Hält den 'aktuellen Tenant' pro Request in einer ContextVar fest, damit
Model-Manager (siehe models.TenantScopedManager) automatisch danach filtern
können, ohne dass jede Query explizit tenant=... mitgeben muss.

Die ContextVar wird von core.middleware.TenantMiddleware gesetzt und nach
jedem Request wieder zurückgesetzt.
"""

from contextvars import ContextVar

_current_tenant: ContextVar = ContextVar("current_tenant", default=None)


def get_current_tenant():
    return _current_tenant.get()


def set_current_tenant(tenant) -> None:
    _current_tenant.set(tenant)

from core.context import set_current_tenant


class TenantContextCleanupMiddleware:
    """
    Setzt die Tenant-ContextVar (core.context) nach jedem Request zurück.

    Eine frühere Version dieser Middleware hat den Tenant hier auch
    GESETZT -- das wurde entfernt (siehe core/tenancy.py: request.user ist
    an dieser Stelle bei TokenAuthentication noch AnonymousUser, das Setzen
    passiert daher bewusst in TenantScopedViewSet.initial()). Ohne diese
    Middleware bliebe die ContextVar aber nach dem Request auf dem
    zuletzt aufgelösten Tenant stehen: WSGI-Worker-Threads werden über
    mehrere Requests hinweg wiederverwendet, und jeder Code, der über
    TenantScopedManager (core/models.py) ungefiltert auf ein Modell
    zugreift -- z. B. `employee.skills.all()`, eine Shell-Session oder ein
    Celery-Task auf demselben Thread -- würde sonst den ContextVar-Tenant
    des vorherigen Requests sehen. Das ist ein Cross-Tenant-Datenleck, auch
    wenn die DRF-ViewSets zusätzlich explizit über request.tenant filtern
    (die eigentliche Sicherheitsgrenze, siehe core/tenancy.py).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        finally:
            set_current_tenant(None)

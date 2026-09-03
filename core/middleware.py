from core.context import set_current_tenant

ADMIN_TENANT_SESSION_KEY = "admin_active_tenant_id"


class AdminActiveTenantMiddleware:
    """
    Aktiviert die Tenant-ContextVar-Filterung (core.models.TenantScopedManager)
    für Django-Admin-Requests (/admin/) -- ohne das zeigt der Admin ALLE
    Tenants ungefiltert gemischt an (siehe README, Architektur-Abschnitt
    "Django Admin ist bewusst kein Kundenzugriff"): weder die Changelists
    noch die FK-/M2M-Dropdowns in Formularen (z. B. Node/Skill beim
    Anlegen eines Employee) sind ohne gesetzte ContextVar gescopt.

    Anders als bei der API (siehe core/tenancy.py, wo genau deshalb KEINE
    Middleware genutzt wird) ist eine Middleware hier unproblematisch:
    Django-Admin läuft über SessionAuthentication, AuthenticationMiddleware
    hat request.user bereits aufgelöst, bevor diese Middleware läuft --
    die Einschränkung, die eine Middleware für TokenAuthentication
    unbrauchbar macht, betrifft Admin-Requests nicht.

    Der aktive Tenant wird über core.admin_views.tenant_switch gewählt und
    in der Session gespeichert (ADMIN_TENANT_SESSION_KEY). Ohne Auswahl
    bleibt die ContextVar auf None -- core.admin.TenantScopedAdminMixin
    sorgt dafür, dass tenant-gescopte ModelAdmins dann explizit NICHTS
    anzeigen (sicherer Default), statt sich auf das implizite Verhalten
    von TenantScopedManager bei fehlendem Tenant zu verlassen (das
    absichtlich "ungefiltert" bedeutet, siehe dessen Docstring -- diese
    Middleware ändert daran nichts, sie liefert nur den Kontext).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            from core.models import Tenant

            tenant_id = request.session.get(ADMIN_TENANT_SESSION_KEY)
            # Immer explizit setzen (auch auf None), nicht nur wenn ein
            # Tenant gewählt ist -- sonst bliebe ohne Auswahl die ContextVar
            # auf einem eventuell noch vorhandenen Altwert stehen (z. B. aus
            # Worker-Thread-Wiederverwendung), genau das Cross-Tenant-Risiko,
            # gegen das core.middleware.TenantContextCleanupMiddleware sonst
            # nach jedem Request schützt -- hier zusätzlich schon VOR dem
            # eigentlichen View wichtig, nicht erst danach.
            tenant = Tenant.objects.filter(pk=tenant_id).first() if tenant_id else None
            set_current_tenant(tenant)
        return self.get_response(request)


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

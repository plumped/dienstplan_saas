"""
Tenant-Auflösung für die DRF-API.

Wichtig: Das darf NICHT als normale Django-Middleware passieren (wie in
einer früheren Version dieses Projekts), weil Django-Middleware VOR der
DRF-Authentifizierung läuft. request.user ist an der Middleware-Stelle nur
dann gesetzt, wenn Session-Auth genutzt wird -- bei TokenAuthentication
(wie das Frontend sie verwendet) ist request.user zu diesem Zeitpunkt noch
AnonymousUser, weil DRF Tokens erst innerhalb von APIView.dispatch()
auswertet. Ergebnis wäre: request.tenant bliebe für Token-Logins immer
None, und alle Listen kämen leer zurück -- genau das wurde beim Testen
mit echten curl-Requests (statt Django-Test-Client-Sessions) sichtbar.

Deshalb wird die Tenant-Auflösung stattdessen in
TenantScopedViewSet.initial() aufgerufen, NACH super().initial(), also
nachdem DRF die Authentifizierung (Token oder Session) bereits durchgeführt
hat.
"""

from core.models import Membership


def resolve_membership_for_user(user):
    """
    MVP-Annahme (siehe core.middleware): ein User hat genau eine Membership.
    Wird für Tenant-Auflösung UND für die rollenbasierten Berechtigungen
    (core.permissions) genutzt -- beides hängt an derselben Membership.
    """
    if not user or not user.is_authenticated:
        return None
    return Membership.objects.select_related("tenant").filter(user=user).first()


def resolve_tenant_for_user(user):
    membership = resolve_membership_for_user(user)
    return membership.tenant if membership else None


def apply_tenant_scoped_initial(view, request, *args, resolve_employee_profile=False,
                                 enforce_billing=True, **kwargs):
    """
    Gemeinsame initial()-Logik aller drei tenant-gescopten Basisklassen
    (core.views.TenantScopedAPIMixin, core.billing_views.
    _TenantScopedNoBillingGateMixin, scheduling.views.TenantScopedViewSet).
    request.tenant/.membership werden bewusst hier gesetzt, NACH
    perform_authentication() (Token-Login) und VOR check_permissions() (die
    rollenbasierten Permission-Klassen lesen request.membership/
    .employee_profile) -- deshalb wird APIView.initial() hier Schritt für
    Schritt nachgebaut statt als Ganzes über super().initial() aufgerufen
    (siehe Moduldocstring oben für den Token-Auth-Grund, warum das nicht per
    Middleware passiert).

    set_current_tenant() (ContextVar, zweite Verteidigungslinie für
    TenantScopedManager, siehe dessen Docstring) wird für ALLE drei
    Basisklassen unbedingt aufgerufen -- Nutzer-Feedback (2026-08): vorher
    nur bei TenantScopedViewSet gesetzt, was TenantScopedAPIMixin/
    _TenantScopedNoBillingGateMixin-Endpunkte (TenantView, MembershipViewSet,
    PayrollExportView, BillingStatusView, u. a.) ohne diese zweite
    Absicherung liess, obwohl sie ausnahmslos ebenfalls tenant-gescopte
    Modelle lesen. set_current_tenant importiert nur core.context (kein
    scheduling-Import nötig, siehe core.context selbst), deshalb unproblematisch
    auch für core-Views.

    resolve_employee_profile=True (nur TenantScopedViewSet) setzt zusätzlich
    request.employee_profile -- lazy-importiert Employee, damit core
    weiterhin nichts von scheduling auf Modulebene importiert (gleiches
    Muster wie core.views._task_counts()).

    enforce_billing=False (nur _TenantScopedNoBillingGateMixin) lässt
    enforce_billing_access() aus, damit sich ein gesperrter Tenant über die
    Billing-Views noch selbst freischalten kann.
    """
    from core.context import set_current_tenant

    view.format_kwarg = view.get_format_suffix(**kwargs)
    neg = view.perform_content_negotiation(request)
    request.accepted_renderer, request.accepted_media_type = neg
    version, scheme = view.determine_version(request, *args, **kwargs)
    request.version, request.versioning_scheme = version, scheme

    view.perform_authentication(request)
    membership = resolve_membership_for_user(request.user)
    tenant = membership.tenant if membership else None
    request.tenant = tenant
    request.membership = membership
    set_current_tenant(tenant)

    if resolve_employee_profile:
        from scheduling.models import Employee

        request.employee_profile = (
            Employee.all_objects.filter(tenant=tenant, user=request.user).first() if tenant else None
        )

    view.check_permissions(request)
    view.check_throttles(request)

    if enforce_billing:
        from core.billing import enforce_billing_access

        enforce_billing_access(request)

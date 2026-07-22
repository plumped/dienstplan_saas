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


def resolve_tenant_for_user(user):
    if not user or not user.is_authenticated:
        return None
    membership = Membership.objects.select_related("tenant").filter(user=user).first()
    return membership.tenant if membership else None

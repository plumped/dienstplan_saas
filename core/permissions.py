"""
Rollenbasierte Berechtigungen auf Basis von core.models.Membership.Role.

Alle Klassen hier gehen davon aus, dass request.membership und
request.employee_profile bereits gesetzt sind -- das passiert in
scheduling.views.TenantScopedViewSet.initial() (analog zu request.tenant,
siehe core/tenancy.py für die Begründung, warum das nicht in einer
Django-Middleware passiert).

Lesezugriff (list/retrieve) ist für alle vier Rollen (Admin/Planer/
Mitarbeiter/HR) innerhalb des eigenen Tenants erlaubt -- die eigentliche
Abgrenzung ist die Tenant-Isolation, nicht die Rolle. Diese Klassen regeln
nur, wer *schreiben* darf.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from core.models import Membership

MANAGER_ROLES = {Membership.Role.ADMIN, Membership.Role.PLANNER}


class IsTenantManager(BasePermission):
    """
    Schreiben (create/update/delete) ist Admin/Planer vorbehalten. HR ist
    laut Rollendefinition "nur Reporting", Mitarbeitende bearbeiten den
    Stammdaten/Dienstplan nicht direkt -- für die kontrollierten Ausnahmen
    (eigene Absenzen, eigener Diensttausch) siehe OwnEmployeeRecordPermission
    und ShiftTradeRequestPermission.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        return bool(membership and membership.role in MANAGER_ROLES)


class OwnEmployeeRecordPermission(BasePermission):
    """
    Für Absenzen: Admin/Planer dürfen alles. Mitarbeitende dürfen lesen und
    für sich selbst (Employee.user == request.user, siehe
    request.employee_profile) Absenzen anlegen/ändern/löschen. HR bleibt
    aussen vor (nur Reporting). Die Objekt-Prüfung (has_object_permission)
    greift bei update/partial_update/destroy; die Prüfung, für WEN eine neue
    Absenz angelegt wird, passiert zusätzlich in AbsenceViewSet.perform_create
    (das Objekt existiert bei create ja noch nicht).
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if not membership:
            return False
        return membership.role in MANAGER_ROLES or membership.role == Membership.Role.EMPLOYEE

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if membership and membership.role in MANAGER_ROLES:
            return True
        employee_profile = getattr(request, "employee_profile", None)
        return bool(employee_profile and obj.employee_id == employee_profile.id)


class ShiftTradeRequestPermission(BasePermission):
    """
    Admin/Planer dürfen alles. Mitarbeitende dürfen lesen, eigene
    Tauschangebote erstellen (Prüfung in ShiftTradeRequestViewSet.perform_create,
    das Objekt existiert bei create noch nicht) und über die Custom-Actions
    reagieren: `cancel` nur als anbietende Person (requester_assignment),
    `accept`/`decline` nur als Zielperson (target_employee). Direktes
    update/partial_update/destroy ist niemandem ausser Admin/Planer erlaubt
    -- Statusänderungen laufen ausschliesslich über die Actions, damit die
    Regel-Engine in ShiftTradeRequest.accept() garantiert durchlaufen wird.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if not membership:
            return False
        return membership.role in MANAGER_ROLES or membership.role == Membership.Role.EMPLOYEE

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if membership and membership.role in MANAGER_ROLES:
            return True
        employee_profile = getattr(request, "employee_profile", None)
        if not employee_profile:
            return False
        if view.action == "cancel":
            return obj.requester_assignment.employee_id == employee_profile.id
        if view.action in ("accept", "decline"):
            return obj.target_employee_id == employee_profile.id
        return False

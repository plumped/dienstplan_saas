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
# Nutzer-Feedback (2026-08): die neue stationsübergreifende
# Zeiterfassungs-Übersicht (README, Zeiterfassung-Notiz) ist auch für HR
# relevant ("nur Reporting", aber genau dafür ist ein Überblick über offene
# Erfassungen gedacht) -- anders als MANAGER_ROLES, das nur die
# SCHREIB-Berechtigung (IsTenantManager) beschreibt.
MANAGER_AND_HR_ROLES = MANAGER_ROLES | {Membership.Role.HR}


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


class IsTenantAdmin(BasePermission):
    """
    Für die Tenant-Konfiguration (MVP-Fahrplan Block 2, Punkt 14): Schreiben
    ist Admin-only vorbehalten -- strenger als IsTenantManager (Admin+
    Planer). Begründung: diese Werte (numerische ArG-/Zuschlags-Grenzwerte)
    steuern direkt Rechtssicherheit und Lohnzuschläge, nicht das
    Tagesgeschäft der Planung (siehe README, Architektur-Abschnitt). Lesen
    bleibt wie überall in der App für alle vier Rollen offen (Transparenz)
    -- nur das Schreiben ist eingeschränkter als sonst.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        return bool(membership and membership.role == Membership.Role.ADMIN)


class _ManagerOrEmployeeCanWriteMixin:
    """
    Gemeinsame has_permission()-Basis für die drei Berechtigungsklassen, bei
    denen sowohl Admin/Planer (unbeschränkt) als auch Mitarbeitende (mit
    weiteren Einschränkungen auf Objekt-Ebene, siehe jeweiliges
    has_object_permission) grundsätzlich schreiben dürfen --
    OwnEmployeeRecordPermission, ShiftTradeRequestPermission,
    TimeRecordPermission. Vorher dreifach identisch dupliziert.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if not membership:
            return False
        return membership.role in MANAGER_ROLES or membership.role == Membership.Role.EMPLOYEE


class OwnEmployeeRecordPermission(_ManagerOrEmployeeCanWriteMixin, BasePermission):
    """
    Für Absenzen: Admin/Planer dürfen alles, inkl. der Genehmigungs-Actions
    `approve`/`reject` (Block 2.3) -- die bleiben Mitarbeitenden immer
    verwehrt, unabhängig von Eigentümerschaft, sonst könnte man seine eigene
    Absenz selbst genehmigen. Mitarbeitende dürfen lesen und für sich selbst
    (Employee.user == request.user, siehe request.employee_profile) Absenzen
    anlegen sowie ändern/löschen, SOLANGE die Absenz noch nicht entschieden
    ist (status == PENDING) -- eine bereits genehmigte/abgelehnte Absenz ist
    für die Mitarbeiter-Rolle nur noch lesbar. HR bleibt aussen vor (nur
    Reporting). Die Prüfung, für WEN eine neue Absenz angelegt wird, passiert
    zusätzlich in AbsenceViewSet.perform_create (das Objekt existiert bei
    create ja noch nicht).
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if membership and membership.role in MANAGER_ROLES:
            return True
        if view.action in ("approve", "reject"):
            return False
        employee_profile = getattr(request, "employee_profile", None)
        if not employee_profile or obj.employee_id != employee_profile.id:
            return False
        return obj.status == obj.Status.PENDING


class ShiftTradeRequestPermission(_ManagerOrEmployeeCanWriteMixin, BasePermission):
    """
    Admin/Planer dürfen alles, inkl. der Genehmigungs-Actions
    `approve`/`reject` (Block 2.3), die Mitarbeitenden immer verwehrt
    bleiben (fallen unten durch auf `return False`, da sie in keiner der
    Mitarbeiter-Bedingungen auftauchen). Mitarbeitende dürfen lesen, eigene
    Tauschangebote erstellen (Prüfung in ShiftTradeRequestViewSet.perform_create,
    das Objekt existiert bei create noch nicht) und über die übrigen
    Custom-Actions reagieren: `cancel` nur als anbietende Person
    (requester_assignment), `accept`/`decline` nur als Zielperson
    (target_employee). Direktes update/partial_update/destroy ist niemandem
    ausser Admin/Planer erlaubt -- Statusänderungen laufen ausschliesslich
    über die Actions, damit die Regel-Engine in ShiftTradeRequest.approve()
    garantiert durchlaufen wird.
    """

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


class TimeRecordPermission(_ManagerOrEmployeeCanWriteMixin, BasePermission):
    """
    Für die Ist-Arbeitszeiterfassung (Block 1.9): Admin/Planer dürfen alles,
    inkl. der Bestätigungs-Action `confirm` -- die bleibt Mitarbeitenden
    immer verwehrt, sonst könnte man seinen eigenen Ist-Eintrag selbst
    bestätigen. Mitarbeitende dürfen lesen und für die eigene Schicht
    (assignment.employee via request.employee_profile) einen Eintrag
    anlegen sowie ändern/löschen, SOLANGE er noch nicht bestätigt ist
    (status == SUBMITTED). Die Prüfung, für WESSEN Schicht ein neuer
    Eintrag angelegt wird, passiert zusätzlich in
    TimeRecordViewSet.perform_create (das Objekt existiert bei create ja
    noch nicht).
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if membership and membership.role in MANAGER_ROLES:
            return True
        if view.action == "confirm":
            return False
        employee_profile = getattr(request, "employee_profile", None)
        if not employee_profile or obj.assignment.employee_id != employee_profile.id:
            return False
        return obj.status == obj.Status.SUBMITTED


class PregnancyPermission(BasePermission):
    """
    Mutterschutz (Block 1.15): strenger als OwnEmployeeRecordPermission --
    dort dürfen alle Admin/Planer-Rollen lesen UND schreiben, hier nur Admin
    (nicht Planer, nicht HR) sowie die betroffene Mitarbeiterin selbst für
    ihre eigenen Einträge. Sensibelste Kategorie personenbezogener Daten in
    der App (Schwangerschaft), deshalb bewusst NICHT wie sonst in dieser
    Datei üblich für alle vier Rollen lesbar (siehe Modul-Docstring oben).
    Gilt für has_permission UND has_object_permission gleichermassen --
    anders als bei den übrigen Klassen hier reicht Objekt-Ebene allein nicht,
    weil auch die Liste anderer Mitarbeiterinnen für Planer/HR unsichtbar
    bleiben muss (siehe PregnancyViewSet.get_queryset für die
    Listen-Filterung -- diese Klasse regelt nur den Einzelzugriff).
    """

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        if not membership:
            return False
        if membership.role == Membership.Role.ADMIN:
            return True
        return bool(getattr(request, "employee_profile", None))

    def has_object_permission(self, request, view, obj):
        membership = getattr(request, "membership", None)
        if membership and membership.role == Membership.Role.ADMIN:
            return True
        employee_profile = getattr(request, "employee_profile", None)
        return bool(employee_profile) and obj.employee_id == employee_profile.id


class ShiftPreferencePermission(_ManagerOrEmployeeCanWriteMixin, BasePermission):
    """
    Wunschfrei/Wunschdienst (Block 2.13): Anlegen bleibt reine
    Selbstauskunft ohne Fremdbestimmung -- Admin/Planer dürfen weiterhin
    KEINE Wünsche für andere Personen anlegen/ändern/löschen, weil ein
    Wunsch per Definition höchstpersönlich ist (kein "im Auftrag von"-Fall
    wie bei Absenzen). ShiftPreferenceViewSet.perform_create erzwingt
    zusätzlich employee=request.employee_profile, unabhängig davon, was im
    Payload mitgeschickt wurde. Lesen ist wie beim übrigen Planblatt für
    jede Rolle offen (der Planer muss die Wünsche aller Mitarbeitenden
    sehen können, um sie bei der Planung zu berücksichtigen).

    Nutzer-Feedback (2026-08, Automatisierte Planung mit Auffülldienst): die
    einzige neue Ausnahme von "höchstpersönlich" sind die Genehmigungs-
    Actions `approve`/`reject` -- die bleiben Admin/Planer vorbehalten und
    Mitarbeitenden immer verwehrt (sonst könnte man seinen eigenen Wunsch
    selbst freigeben), analog zu Absence (siehe OwnEmployeeRecordPermission).
    Direktes Ändern/Löschen bleibt wie bisher der antragstellenden Person
    vorbehalten, jetzt zusätzlich nur solange status == PENDING -- ein schon
    entschiedener Wunsch ist nur noch lesbar, weil eine APPROVED-Freigabe
    von der Automatik als harte Vorgabe behandelt wird (siehe
    scheduling.planning) und nicht nachträglich unbemerkt verändert werden
    soll.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        membership = getattr(request, "membership", None)
        if view.action in ("approve", "reject"):
            return bool(membership and membership.role in MANAGER_ROLES)
        employee_profile = getattr(request, "employee_profile", None)
        if not employee_profile or obj.employee_id != employee_profile.id:
            return False
        return obj.status == obj.Status.PENDING


class IsTenantManagerOrHR(BasePermission):
    """
    Rein lesend, ausschliesslich für Admin/Planer/HR (Nutzer-Feedback
    2026-08, Zeiterfassungs-Übersicht): Mitarbeitende haben bereits ihre
    eigene, stationsgebundene Sicht (TimeRecordPanel) und brauchen die
    stationsübergreifende Triage-Tabelle nicht -- anders als
    IsTenantManager (das SAFE_METHODS für ALLE vier Rollen offen lässt)
    blendet diese Klasse Mitarbeitende auch beim Lesen aus.
    """

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        return bool(membership and membership.role in MANAGER_AND_HR_ROLES)

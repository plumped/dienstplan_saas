import calendar
import csv
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import get_random_string
from rest_framework import filters, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from treebeard.exceptions import InvalidMoveToDescendant, PathOverflow

from core.context import set_current_tenant
from core.models import Membership
from core.notifications import (
    notify_absence_decision,
    notify_new_absence_request,
    notify_new_trade_request,
    notify_trade_accepted_by_employee,
    notify_trade_declined,
    notify_trade_decision,
)
from core.permissions import (
    MANAGER_ROLES,
    IsTenantAdmin,
    IsTenantManager,
    IsTenantManagerOrHR,
    OwnEmployeeRecordPermission,
    PregnancyPermission,
    ShiftPreferencePermission,
    ShiftTradeRequestPermission,
    TimeRecordPermission,
)
from core.tenancy import resolve_membership_for_user
from core.views import TenantScopedAPIMixin

from .models import (
    Absence,
    AbsenceType,
    Employee,
    Node,
    PayrollCategoryMapping,
    Pregnancy,
    ShiftAssignment,
    ShiftPreference,
    ShiftTradeRequest,
    Skill,
    TimeRecord,
    TimeTemplate,
)
from .serializers import (
    AbsenceSerializer,
    AbsenceTypeSerializer,
    EmployeeAccessSetupSerializer,
    EmployeeBalanceSerializer,
    EmployeeSerializer,
    FairnessSummarySerializer,
    MonthlySummarySerializer,
    NightWorkSummarySerializer,
    NodeSerializer,
    PayrollCategoryMappingSerializer,
    PregnancySerializer,
    ShiftAssignmentSerializer,
    ShiftPreferenceSerializer,
    ShiftTradeRequestSerializer,
    SickPaySummarySerializer,
    SkillSerializer,
    TimeRecordSerializer,
    TimeTemplateSerializer,
    WeeklyOvertimeSerializer,
)

User = get_user_model()


class TenantScopedViewSet(viewsets.ModelViewSet):
    """
    Basis-ViewSet: filtert explizit über all_objects nach request.tenant,
    statt sich allein auf die ContextVar-Manager in core.models zu verlassen
    (doppelte Absicherung gegen Datenlecks zwischen Mandanten).

    request.tenant wird bewusst hier in initial() gesetzt, NACH
    self.perform_authentication() (das führt die DRF-Authentifizierung, also
    Token- oder Session-Login, aus) -- nicht in einer Django-Middleware, wo
    request.user bei Token-Logins noch nicht aufgelöst wäre. Siehe
    core/tenancy.py.

    Gleich hier werden auch request.membership (für core.permissions) und
    request.employee_profile (die zum eingeloggten User gehörende Employee,
    falls vorhanden -- für "nur eigene Absenz/eigener Diensttausch"-Regeln)
    aufgelöst, da beides an derselben Membership-Abfrage hängt. Wichtig:
    request.tenant/.membership müssen VOR self.check_permissions() gesetzt
    sein, weil die rollenbasierten Permission-Klassen (core.permissions)
    request.membership lesen -- deshalb hier bewusst NICHT super().initial()
    als Ganzes aufgerufen (das würde check_permissions() bereits vor der
    Zuweisung ausführen), sondern APIView.initial() Schritt für Schritt
    nachgebaut mit der Zuweisung dazwischen.
    """

    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        self.format_kwarg = self.get_format_suffix(**kwargs)
        neg = self.perform_content_negotiation(request)
        request.accepted_renderer, request.accepted_media_type = neg
        version, scheme = self.determine_version(request, *args, **kwargs)
        request.version, request.versioning_scheme = version, scheme

        self.perform_authentication(request)

        membership = resolve_membership_for_user(request.user)
        tenant = membership.tenant if membership else None
        request.tenant = tenant
        request.membership = membership
        request.employee_profile = (
            Employee.all_objects.filter(tenant=tenant, user=request.user).first() if tenant else None
        )
        set_current_tenant(tenant)

        self.check_permissions(request)
        self.check_throttles(request)

    def get_queryset(self):
        tenant = self.request.tenant
        if tenant is None:
            return self.queryset.model.all_objects.none()
        return self.queryset.model.all_objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)


def _employee_scoped_node_ids(membership, employee_profile):
    """
    Wer welche Stationen sieht, hängt von der Rolle ab. Gibt None zurück,
    wenn keine Einschränkung gilt (unveränderte Sicht auf den ganzen
    Tenant), sonst die Liste der Node-IDs, auf die die Sicht beschränkt ist.
    Von NodeViewSet, TimeTemplateViewSet, ShiftAssignmentViewSet,
    TimeRecordViewSet und MissingTimeRecordViewSet gleich ausgewertet, damit
    alle node-bezogenen Ressourcen konsistent eingeschränkt sind.

    - ADMIN: immer None -- uneingeschränkt, unabhängig von allem anderen
      (Nutzer-Vorgabe 2026-08: "Nur Admin darf immer alles sehen").
    - EMPLOYEE: beschränkt auf employee_profile.nodes (leere Liste, falls
      kein Employee-Profil existiert). README Punkt 17: employee_profile.
      nodes enthält bei Team-Anstellungen Team- statt Stations-Ids. Damit
      TimeTemplates (die bewusst stationsweit bleiben, siehe
      ShiftAssignmentViewSet-Docstring) und die Station selbst im
      NodeViewSet weiterhin sichtbar sind, wird hier zusätzlich der direkte
      Elternknoten jedes eigenen Knotens aufgenommen (eine Ebene, konsistent
      mit der "genau ein Team-Level"-Entscheidung). Für jede heutige, flache
      Station ohne Eltern-Knoten ist das ein No-Op.
    - PLANNER/HR (Nutzer-Feedback 2026-08: "Natürlich gibt es in einer
      Klinik Planer mit unterschiedlichen Zuständigkeiten! Gleiches gilt
      auch für HR."): beschränkt auf Membership.scoped_nodes samt aller
      Unterstationen (get_descendants() -- ein "Bereich" wie "Pflege" soll
      automatisch auch dessen Teams/Unterstationen einschliessen, anders als
      bei EMPLOYEE reicht hier eine Ebene nicht). Leere scoped_nodes = keine
      Einschränkung konfiguriert -> None, damit jede heute schon bestehende
      Planer-/HR-Mitgliedschaft ohne Zutun weiterhin alles sieht
      (migrationssicher) -- erst eine explizite Zuweisung (siehe
      MembershipViewSet) schränkt tatsächlich ein.
    """
    if not membership or membership.role == Membership.Role.ADMIN:
        return None
    if membership.role == Membership.Role.EMPLOYEE:
        if not employee_profile:
            return []
        own_nodes = list(employee_profile.nodes.all())
        node_ids = {n.id for n in own_nodes}
        for n in own_nodes:
            parent = n.get_parent()
            if parent is not None:
                node_ids.add(parent.id)
        return list(node_ids)
    if membership.role in (Membership.Role.PLANNER, Membership.Role.HR):
        scoped = list(membership.scoped_nodes.all())
        if not scoped:
            return None
        node_ids = set()
        for n in scoped:
            node_ids.add(n.id)
            node_ids.update(d.id for d in n.get_descendants())
        return list(node_ids)
    return None


class NodeViewSet(TenantScopedViewSet):
    """
    Achtung: Node erbt von treebeard's MP_Node, das Baumfelder (path, depth,
    numchild) selbst verwaltet. Ein normales .objects.create() (wie es
    ModelViewSet.perform_create standardmässig via serializer.save() macht)
    lässt diese Felder leer und wirft einen IntegrityError. Neue Knoten
    müssen daher über add_root()/add_child() erzeugt werden.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = Node.all_objects.all()
    serializer_class = NodeSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            qs = qs.filter(id__in=node_ids)
        return qs

    def perform_create(self, serializer):
        tenant = self.request.tenant
        parent_id = serializer.validated_data.pop("parent", None)
        name = serializer.validated_data["name"]
        cost_center = serializer.validated_data.get("cost_center", "")

        if parent_id:
            parent = Node.all_objects.filter(tenant=tenant, pk=parent_id).first()
            if parent is None:
                raise ValidationError({"parent": "Ungültiger oder fremder Knoten."})
            node = parent.add_child(name=name, tenant=tenant, cost_center=cost_center)
        else:
            node = Node.add_root(name=name, tenant=tenant, cost_center=cost_center)

        serializer.instance = node

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """
        Nutzer-Feedback (2026-08): Stationen per Drag & Drop verschieben statt
        nur anlegen/umbenennen/löschen zu können. `node_order_by = ["name"]`
        (siehe Node-Modell) sortiert Geschwisterknoten immer automatisch
        alphabetisch -- Drag & Drop reparentet daher (verschiebt UNTER einen
        anderen Knoten oder auf die oberste Ebene), sortiert aber nicht
        manuell innerhalb derselben Ebene um (treebeard erzwingt ohnehin
        `pos="sorted-child"`/`"sorted-sibling"`, sobald `node_order_by`
        gesetzt ist -- ein `pos=None` würde denselben Effekt haben).
        """
        node = self.get_object()
        tenant = request.tenant
        parent_id = request.data.get("parent")

        if parent_id:
            target = Node.all_objects.filter(tenant=tenant, pk=parent_id).first()
            if target is None:
                raise ValidationError({"parent": "Ungültiger oder fremder Knoten."})
            if target.pk == node.pk:
                raise ValidationError({"parent": "Eine Station kann nicht in sich selbst verschoben werden."})
            try:
                current_parent = node.get_parent()
                if current_parent is not None and current_parent.pk != target.pk:
                    # Bugfix (2026-08, Nutzer-Feedback: "ich kann Küche direkt in
                    # Station A ziehen, nicht aber von Station A zurück in
                    # Hauswirtschaft"): treebeards move(pos="sorted-child") wandelt
                    # das intern in "sorted-sibling" gegen target.get_last_child()
                    # um und bricht früh ab, falls die letzte Pfad-Ziffer des
                    # gezogenen Knotens zufällig mit der berechneten neuen Position
                    # übereinstimmt ("bereits an der richtigen Stelle") -- OHNE zu
                    # prüfen, ob es sich überhaupt um denselben Elternknoten
                    # handelt. Bei kleinen Bäumen (Position 1 unter dem alten
                    # Elternknoten, Position 1 unter dem neuen) ist das keine
                    # Seltenheit, sondern der Normalfall, und der Knoten bleibt
                    # dann unbemerkt an alter Stelle. Ein Zwischenstopp auf der
                    # obersten Ebene (bereits einzeln erprobt: Wurzel<->Kind
                    # funktioniert immer zuverlässig) umgeht das zuverlässig, weil
                    # beide Teilschritte dann echte, unabhängig berechnete
                    # Positionen vergleichen statt einer zufälligen Kollision.
                    anchor_root = Node.get_first_root_node()
                    if anchor_root is not None and anchor_root.pk != node.pk:
                        node.move(anchor_root, pos="sorted-sibling")
                        node.refresh_from_db()
                        # target selbst kann eine bestehende Wurzel sein (oder
                        # -- egal ob ja oder nein -- ihr Pfadsegment kann sich
                        # durch die Einfügung verschieben, da node_order_by
                        # alle Wurzeln sortiert hält). Ohne Refresh würde
                        # target.get_last_child() im zweiten Schritt mit dem
                        # veralteten Pfad suchen und fälschlich nichts finden
                        # -- treebeard setzt self.target dann intern auf None.
                        target.refresh_from_db()
                node.move(target, pos="sorted-child")
            except InvalidMoveToDescendant:
                raise ValidationError(
                    {"parent": "Eine Station kann nicht in eine ihrer eigenen Unterstationen verschoben werden."}
                )
            except PathOverflow:
                raise ValidationError({"parent": "Zu viele Stationen auf dieser Ebene -- Verschieben nicht möglich."})
            node.refresh_from_db()
            actual_parent = node.get_parent()
            if actual_parent is None or actual_parent.pk != target.pk:
                raise ValidationError({"parent": "Verschieben fehlgeschlagen -- bitte erneut versuchen."})
        else:
            # Auf die oberste Ebene verschieben (Wurzelknoten). Wurzelknoten
            # liegen -- wie schon bei add_root() oben -- in einem
            # tenant-übergreifend gemeinsamen Pfad-Namensraum (treebeard
            # partitioniert die Baumstruktur selbst nicht nach dem
            # `tenant`-Feld), deshalb reicht irgendein bestehender
            # Wurzelknoten als reine Sortier-Referenz.
            sibling = Node.get_first_root_node()
            if sibling is not None and sibling.pk != node.pk:
                try:
                    node.move(sibling, pos="sorted-sibling")
                except InvalidMoveToDescendant:
                    raise ValidationError({"parent": "Ungültige Verschiebung."})

        node.refresh_from_db()
        return Response(NodeSerializer(node).data)


class SkillViewSet(TenantScopedViewSet):
    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = Skill.all_objects.all()
    serializer_class = SkillSerializer


class EmployeeViewSet(TenantScopedViewSet):
    """
    Stammdatenpflege (Nutzer-Feedback 2026-08): bei mehreren hundert
    Mitarbeitenden skaliert "alles laden und im Frontend filtern" nicht mehr
    -- Suche/Sortierung/Filterung laufen deshalb serverseitig, mit der
    normalen DRF-Pagination (PAGE_SIZE=50) als Ergebnis. Andere Stellen der
    App (Planblatt, Absenzen, Diensttausch, Dashboard), die weiterhin den
    KOMPLETTEN Mitarbeiterbestand brauchen, rufen unverändert `GET /api/
    employees/` ohne diese Parameter auf und paginieren clientseitig durch
    (api.getEmployees()/requestAllPages) -- dieser Endpoint bleibt also für
    beide Nutzungsarten kompatibel, nur die neue Stammdaten-Tabelle
    (EmployeeSettings.jsx) nutzt `?search=`/`?ordering=`/`?node=`/
    `?is_active=` aktiv.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = Employee.all_objects.all()
    serializer_class = EmployeeSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["first_name", "last_name"]
    ordering_fields = ["last_name", "first_name", "employment_pct", "is_active", "employment_start_date"]
    ordering = ["last_name", "first_name"]

    def get_queryset(self):
        qs = super().get_queryset()
        node_id = self.request.query_params.get("node")
        if node_id:
            qs = qs.filter(nodes__id=node_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))
        return qs.distinct()

    @action(detail=True, methods=["get"], url_path="weekly-overtime")
    def weekly_overtime(self, request, pk=None):
        """
        Soll/Ist-Vergleich + Überzeit-Zuschlag für eine Kalenderwoche
        (MVP-Fahrplan Block 1.11, Art. 13 ArG). ?week=YYYY-MM-DD (ein
        beliebiges Datum innerhalb der Woche, Default heute) -- die Woche
        wird auf Montag-Sonntag normalisiert, siehe
        Employee.weekly_hours_summary. Lesen ist wie beim übrigen Planblatt
        für alle Rollen offen (IsTenantManager), nicht nur für den
        betroffenen Mitarbeiter selbst.
        """
        employee = self.get_object()
        week_param = request.query_params.get("week")
        if week_param:
            try:
                reference_date = date.fromisoformat(week_param)
            except ValueError:
                raise ValidationError({"week": "Ungültiges Datum, erwartet YYYY-MM-DD."})
        else:
            reference_date = timezone.localdate()
        summary = employee.weekly_hours_summary(reference_date)
        return Response(WeeklyOvertimeSerializer(summary).data)

    @action(detail=True, methods=["get"], url_path="night-work")
    def night_work(self, request, pk=None):
        """
        Nachtarbeit-Auswertung für ein Kalenderjahr (MVP-Fahrplan Block 1.5,
        Art. 17b/17c ArG): Anzahl Nächte, Zeitgutschrift bei regelmässiger
        Nachtarbeit, Bewilligungs-Warnhinweis und fällige arbeitsmedizinische
        Untersuchung. ?year=YYYY (Default aktuelles Jahr). Lesen wie bei
        weekly_overtime/balance für alle Rollen offen.
        """
        employee = self.get_object()
        year_param = request.query_params.get("year")
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                raise ValidationError({"year": "Ungültiges Jahr."})
        else:
            year = timezone.localdate().year
        summary = employee.night_work_summary(year)
        return Response(NightWorkSummarySerializer(summary).data)

    @action(detail=True, methods=["get"])
    def fairness(self, request, pk=None):
        """
        Fairness-Punkte für unpopuläre Schichten (MVP-Fahrplan Block 2,
        Punkt 20): gleitendes 365-Tage-Fenster ab ?as_of=YYYY-MM-DD
        (Default heute), siehe Employee.fairness_summary(). Lesen wie bei
        balance/night-work für alle Rollen offen.
        """
        employee = self.get_object()
        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            try:
                reference_date = date.fromisoformat(as_of_param)
            except ValueError:
                raise ValidationError({"as_of": "Ungültiges Datum, erwartet YYYY-MM-DD."})
        else:
            reference_date = timezone.localdate()
        summary = employee.fairness_summary(reference_date)
        return Response(FairnessSummarySerializer(summary).data)

    @action(detail=True, methods=["get"])
    def balance(self, request, pk=None):
        """
        Arbeitszeitmodell (README Block 2.7 Punkt 7): laufender Saldo +
        Jahresrestsoll (Employee.time_account_summary) + Feriensaldo für ein
        Kalenderjahr (Employee.vacation_balance). ?as_of=YYYY-MM-DD (Default
        heute) bestimmt sowohl den Stichtag für den laufenden Saldo als auch,
        falls ?year nicht gesetzt ist, das Jahr für Jahressoll/Ferienjahr.
        Lesen wie bei weekly_overtime für alle Rollen offen, nicht nur für
        den betroffenen Mitarbeiter selbst -- Admin/Planer sollen dieselbe
        Ansicht auch für andere Mitarbeitende sehen können.
        """
        employee = self.get_object()
        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            try:
                as_of_date = date.fromisoformat(as_of_param)
            except ValueError:
                raise ValidationError({"as_of": "Ungültiges Datum, erwartet YYYY-MM-DD."})
        else:
            as_of_date = timezone.localdate()

        year_param = request.query_params.get("year")
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                raise ValidationError({"year": "Ungültiges Jahr."})
        else:
            year = as_of_date.year

        time_account = employee.time_account_summary(as_of_date)
        vacation = employee.vacation_balance(year)
        data = {
            "as_of": as_of_date,
            "saldo_hours": time_account["saldo_hours"],
            "plan_saldo_hours": time_account["plan_saldo_hours"],
            "annual_target_hours": time_account["annual_target_hours"],
            "annual_remaining_hours": time_account["annual_remaining_hours"],
            "is_provisional": time_account["is_provisional"],
            "vacation_year": vacation["year"],
            "vacation_entitlement_days": vacation["entitlement_days"],
            "vacation_used_days": vacation["used_days"],
            "vacation_remaining_days": vacation["remaining_days"],
        }
        return Response(EmployeeBalanceSerializer(data).data)

    @action(detail=True, methods=["get"], url_path="sick-pay")
    def sick_pay(self, request, pk=None):
        """
        Lohnfortzahlungs-Anspruch bei Krankheit für das laufende Dienstjahr
        (MVP-Fahrplan Block 1 Punkt 16, Art. 324a OR, Employee.
        sick_pay_summary). ?as_of=YYYY-MM-DD (Default heute) bestimmt den
        Stichtag für die Dienstjahr-Ermittlung. Lesen wie bei balance/
        weekly_overtime/night_work für alle Rollen offen (bewusste
        Mitarbeiter-Selbstauskunft, README Block 2.7).
        """
        employee = self.get_object()
        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            try:
                as_of_date = date.fromisoformat(as_of_param)
            except ValueError:
                raise ValidationError({"as_of": "Ungültiges Datum, erwartet YYYY-MM-DD."})
        else:
            as_of_date = timezone.localdate()
        summary = employee.sick_pay_summary(as_of_date)
        return Response(SickPaySummarySerializer(summary).data)

    @action(detail=True, methods=["get"], url_path="monthly-summary")
    def monthly_summary(self, request, pk=None):
        """
        Monatsauswertung Soll/Ist-Stunden inkl. Überzeit- und Nacht-/
        Sonntagszuschlag (README Block 2.6, "Basis für den Lohnlauf").
        ?year=YYYY&month=1-12 (Default aktueller Monat). Anders als
        weekly_overtime/night_work/balance (bewusste Mitarbeiter-
        Selbstauskunft, README Block 2.7) bewusst NICHT für alle Rollen
        offen -- das hier ist Lohnlauf-Vorbereitung, kein Bedürfnis eines
        einzelnen Mitarbeitenden, die eigenen Zahlen einzusehen, dafür
        gelten balance()/weekly_overtime() weiterhin. Nur Admin/Planer.
        """
        if request.membership.role not in (Membership.Role.ADMIN, Membership.Role.PLANNER):
            raise PermissionDenied("Nur Admin/Planer dürfen die Monatsauswertung einsehen.")
        employee = self.get_object()
        year_param = request.query_params.get("year")
        month_param = request.query_params.get("month")
        today = timezone.localdate()
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                raise ValidationError({"year": "Ungültiges Jahr."})
        else:
            year = today.year
        if month_param:
            try:
                month = int(month_param)
            except ValueError:
                raise ValidationError({"month": "Ungültiger Monat."})
            if not 1 <= month <= 12:
                raise ValidationError({"month": "Monat muss zwischen 1 und 12 liegen."})
        else:
            month = today.month
        summary = employee.monthly_summary(year, month)
        return Response(MonthlySummarySerializer(summary).data)

    @action(detail=True, methods=["post"], url_path="settle-overtime")
    def settle_overtime(self, request, pk=None):
        """
        Bestätigt den aktuellen Gleitzeit-Korridor-Überschuss (Employee.
        _flextime_corridor_status) für einen Monat als abrechnungsrelevant
        (Nutzer-Feedback 2026-08: "bei uns gilt Gleitzeit, nur angeordnete
        Überstunden werden effektiv abgerechnet"). Body: {"year": YYYY,
        "month": 1-12} (Default aktueller Monat). Idempotent -- ein bereits
        bestätigter Monat ändert sich durch einen erneuten Aufruf nicht.
        Gleiche Berechtigung wie monthly_summary() (Admin/Planer, kein
        Selbstbedienungs-Endpoint).
        """
        if request.membership.role not in (Membership.Role.ADMIN, Membership.Role.PLANNER):
            raise PermissionDenied("Nur Admin/Planer dürfen einen Gleitzeit-Überschuss bestätigen.")
        employee = self.get_object()
        today = timezone.localdate()
        year_param = request.data.get("year")
        month_param = request.data.get("month")
        try:
            year = int(year_param) if year_param else today.year
            month = int(month_param) if month_param else today.month
        except (TypeError, ValueError):
            raise ValidationError({"year": "Ungültiges Jahr oder ungültiger Monat."})
        if not 1 <= month <= 12:
            raise ValidationError({"month": "Monat muss zwischen 1 und 12 liegen."})
        try:
            employee.confirm_overtime_settlement(year, month)
        except ValueError as exc:
            raise ValidationError(str(exc))
        return Response(MonthlySummarySerializer(employee.monthly_summary(year, month)).data)

    @action(detail=True, methods=["post"], url_path="setup-access")
    def setup_access(self, request, pk=None):
        """
        Login-Zugang für eine bestehende Employee einrichten (Nutzer-Feedback
        2026-08: "Es gibt nun Tab Mitarbeitende, Tab Mitglieder und Zugriff
        [...] Das muss doch intuitiver gelöst werden?" -- ein Ort, ein
        Formular pro Person, statt Employee (scheduling) und User/Membership
        (core) getrennt zu verwalten). Legt User + Membership in einem Zug an
        und verknüpft `employee.user`, analog zu core.serializers.
        MembershipCreateSerializer (Direktanlage statt E-Mail-Einladung,
        Temp-Passwort wird EINMALIG zurückgegeben), nur eben ausgehend von
        einer bereits bestehenden Employee statt einem freistehenden
        Membership-Datensatz.

        Admin-only wie jede Rollen-/Kontoverwaltung in dieser App (siehe
        core.views.MembershipViewSet) -- bewusst strenger als die sonstige
        IsTenantManager-Berechtigung (Admin+Planer) dieses ViewSets.
        """
        if request.membership.role != Membership.Role.ADMIN:
            raise PermissionDenied("Nur Admin darf Login-Zugänge einrichten.")
        employee = self.get_object()
        if employee.user_id:
            raise ValidationError(
                {"username": "Diese Person hat bereits einen Zugang -- Rolle stattdessen über "
                 "PATCH /api/memberships/<id>/ ändern."}
            )
        serializer = EmployeeAccessSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        temp_password = get_random_string(12)
        user = User.objects.create_user(
            username=serializer.validated_data["username"],
            password=temp_password,
            first_name=employee.first_name,
            last_name=employee.last_name,
            must_change_password=True,
        )
        membership = Membership.objects.create(
            user=user, tenant=request.tenant, role=serializer.validated_data["role"]
        )
        employee.user = user
        employee.save(update_fields=["user"])
        return Response(
            {
                "username": user.username,
                "temporary_password": temp_password,
                "role": membership.role,
                "membership_id": membership.id,
            },
            status=201,
        )


class AbsenceTypeViewSet(TenantScopedViewSet):
    """
    Nutzer-Feedback (2026-08): Absenzarten (bisher hartcodiert Ferien/
    Krankheit/Sonstiges) sind jetzt ein tenant-eigener Katalog, analog
    TimeTemplateViewSet -- Lesen für alle Rollen, Schreiben nur Admin/Planer.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = AbsenceType.all_objects.all()
    serializer_class = AbsenceTypeSerializer


class PayrollCategoryMappingViewSet(TenantScopedViewSet):
    """
    MVP-Fahrplan Block 2, Punkt 30: Verwaltung der Lohnart-Zuordnung (siehe
    PayrollCategoryMapping-Docstring). Admin-only fürs Schreiben --
    strenger als das sonst übliche IsTenantManager (Admin+Planer), analog
    core.views.TenantView: diese Codes steuern direkt die Übergabe an das
    Lohnsystem des Kunden, nicht das Tagesgeschäft der Planung. Lesen bleibt
    wie überall für alle Rollen offen (IsTenantAdmin-Docstring).
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantAdmin]
    queryset = PayrollCategoryMapping.all_objects.all()
    serializer_class = PayrollCategoryMappingSerializer


class TimeTemplateViewSet(TenantScopedViewSet):
    """
    Nutzer-Feedback (2026-08): analog EmployeeViewSet -- Suche/Sortierung/
    Stations-Filter server-seitig, damit die Schichttyp-Liste in Organisationen
    mit vielen Stationen (z. B. 15 Stationen x 10 Schichttypen) nicht mehr
    unstrukturiert alles auf einmal zeigt.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = TimeTemplate.all_objects.all()
    serializer_class = TimeTemplateSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["name", "start_time", "end_time", "category", "node__name"]
    ordering = ["node__name", "start_time"]

    def get_queryset(self):
        qs = super().get_queryset()
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            qs = qs.filter(node_id__in=node_ids)
        node_id = self.request.query_params.get("node")
        if node_id:
            qs = qs.filter(node_id=node_id)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs


class ShiftAssignmentViewSet(TenantScopedViewSet):
    """
    Unterstützt ?node=<id>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD, um genau
    das Planblatt-Grid (ein Knoten, ein Monat) effizient zu laden.

    Schreiben ist Admin/Planer vorbehalten -- Mitarbeitende sehen den ganzen
    Plan ihrer eigenen Station(en) (Transparenz, aber begrenzt auf die
    eigene(n) Station(en) -- siehe _employee_scoped_node_ids), ändern ihn
    aber nicht direkt, sondern nur über einen genehmigten Diensttausch
    (ShiftTradeRequestViewSet).
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = ShiftAssignment.all_objects.all()
    serializer_class = ShiftAssignmentSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee", "template", "node")
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            qs = qs.filter(node_id__in=node_ids)
        node = self.request.query_params.get("node")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if node:
            # README Punkt 17: eine Station mit Teams speichert Zuweisungen
            # auf den Team-Knoten, nicht auf der Station selbst (siehe
            # ShiftAssignment._check_node_has_no_children) -- ein exakter
            # Treffer auf die Stations-Id würde bei einer Station mit Teams
            # daher immer leer bleiben. Ein Knoten ohne Kinder (heutiger
            # Normalfall) verhält sich weiterhin identisch (nur sein eigenes
            # Ergebnis).
            target = Node.all_objects.filter(tenant=self.request.tenant, pk=node).first()
            if target:
                qs = qs.filter(node_id__in=[target.id] + [c.id for c in target.get_children()])
            else:
                qs = qs.none()
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs

    @action(detail=False, methods=["get"], url_path="other-team-conflicts")
    def other_team_conflicts(self, request):
        """
        Nutzer-Feedback ("massiver Bug"): ein leeres Feld im Planblatt liess
        sich trotzdem nicht beplanen ("Nina Kaufmann hat am ... bereits
        'Frühschicht' ..."), obwohl weder das Grid noch die Admin-Liste einen
        Dienst zeigten. Tatsächlich keine Datenkorruption, sondern eine
        gezielte Design-Entscheidung, die im UI bisher unsichtbar blieb: bei
        einer Mehrfachanstellung (README Punkt 17) prüft
        ShiftAssignment._check_no_overlap() JEDE Zuweisung der Person
        TENANT-WEIT über alle Teams/Stationen hinweg (zu Recht -- niemand
        kann an zwei Orten gleichzeitig arbeiten), während das Planblatt pro
        Zeile nur die Zuweisungen DES aktuell angezeigten Teams lädt
        (?node=<Station>, siehe get_queryset oben) -- ein blockierender
        Dienst in einem ANDEREN Team der Person war dadurch für den Planer
        nicht auffindbar, ausser über die (kryptische) Fehlermeldung beim
        Versuch, die Zelle zu beplanen.

        Liefert für die übergebenen Mitarbeiter/Zeitraum genau die Info, die
        eine Warnung auf der sonst leeren Zelle braucht (Dienstname + Team-
        name) -- keine neuen Daten gegenüber dem, was die Fehlermeldung beim
        Versuch, die Zelle zu beplanen, ohnehin schon preisgibt, nur
        proaktiv statt erst nach einem fehlgeschlagenen Versuch. Bewusst
        NICHT node-gescoped wie get_queryset oben (die Formulierung "auf die
        eigene(n) Station(en) beschränkt" gilt hier nicht) -- genau
        stationsübergreifend zu suchen ist der ganze Zweck dieses Endpoints
        --, daher zusätzlich (anders als die Klassen-Permission, die GET
        jedem Tenant-Mitglied erlaubt) explizit auf Admin/Planer beschränkt,
        damit normale Mitarbeitende nicht versehentlich Einblick in fremde
        Stationen bekommen, für die sie sonst keine Berechtigung hätten.
        """
        membership = getattr(request, "membership", None)
        if not (membership and membership.role in MANAGER_ROLES):
            raise PermissionDenied("Nur Admin/Planer dürfen stationsübergreifende Konflikte einsehen.")

        raw_employee_ids = request.query_params.get("employees", "")
        try:
            employee_ids = [int(x) for x in raw_employee_ids.split(",") if x]
        except ValueError:
            raise ValidationError({"employees": "Muss eine kommagetrennte Liste von IDs sein."})
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        exclude_node = request.query_params.get("exclude_node")
        if not employee_ids or not date_from or not date_to:
            return Response([])

        qs = (
            ShiftAssignment.all_objects.filter(
                tenant=request.tenant,
                employee_id__in=employee_ids,
                date__gte=date_from,
                date__lte=date_to,
            )
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template", "node")
        )
        if exclude_node:
            target = Node.all_objects.filter(tenant=request.tenant, pk=exclude_node).first()
            if target:
                excluded_ids = [target.id] + [c.id for c in target.get_children()]
                qs = qs.exclude(node_id__in=excluded_ids)

        return Response(
            [
                {
                    "employee": a.employee_id,
                    "date": a.date.isoformat(),
                    "template_name": a.template.name,
                    "start_time": a.template.start_time.strftime("%H:%M"),
                    "end_time": a.template.end_time.strftime("%H:%M"),
                    "node_name": a.node.name,
                }
                for a in qs
            ]
        )

    @action(detail=False, methods=["post"])
    def swap(self, request):
        """
        README Block 2.8: echter Swap im Drag & Drop (Ziehen auf eine
        belegte Zelle) statt der bisherigen Ablehnung -- siehe
        ShiftAssignment.swap() für die eigentliche Tausch-/Validierungslogik.
        `detail=False`, weil ein Tausch zwischen zwei gleichberechtigten
        Zuweisungen kein natürliches "Hauptobjekt" hat (anders als die
        detail=True-Actions von ShiftTradeRequestViewSet unten).
        """
        first_id = request.data.get("first")
        second_id = request.data.get("second")
        if not first_id or not second_id:
            raise ValidationError({"first": "Erforderlich.", "second": "Erforderlich."})
        try:
            # Form-/Multipart-kodierte Requests liefern Strings -- ohne
            # diese Umwandlung würde `lo.pk == first_id` in
            # ShiftAssignment.swap() (int == str) immer falsch sein und
            # "first"/"second" in der Antwort stillschweigend vertauschen.
            first_id = int(first_id)
            second_id = int(second_id)
        except (TypeError, ValueError):
            raise ValidationError({"first": "Muss eine Zahl sein.", "second": "Muss eine Zahl sein."})
        get_object_or_404(self.get_queryset(), pk=first_id)
        get_object_or_404(self.get_queryset(), pk=second_id)
        try:
            first, second = ShiftAssignment.swap(first_id, second_id)
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        return Response(
            {
                "first": self.get_serializer(first).data,
                "second": self.get_serializer(second).data,
            }
        )


class AbsenceViewSet(TenantScopedViewSet):
    """
    Unterstützt ?employee=<id>, um die Abwesenheiten eines Mitarbeiters zu laden.

    Admin/Planer dürfen Absenzen für jeden anlegen/ändern/löschen; von ihnen
    angelegte Absenzen sind sofort APPROVED (die Freigabe ist durch die
    anlegende Rolle bereits impliziert). Mitarbeitende dürfen nur für sich
    selbst (request.employee_profile) schreiben -- durchgesetzt hier in
    perform_create (das Objekt existiert bei create noch nicht, deshalb
    reicht has_object_permission allein nicht) sowie über
    OwnEmployeeRecordPermission.has_object_permission für update/destroy;
    ihre Absenzen starten als PENDING (Model-Default) und brauchen
    approve()/reject() durch Admin/Planer (Block 2.3). HR ist aussen vor
    (nur Reporting).
    """

    permission_classes = [permissions.IsAuthenticated, OwnEmployeeRecordPermission]
    queryset = Absence.all_objects.all()
    serializer_class = AbsenceSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee")
        employee = self.request.query_params.get("employee")
        if employee:
            qs = qs.filter(employee_id=employee)
        return qs

    def perform_create(self, serializer):
        is_manager = self.request.membership.role in (Membership.Role.ADMIN, Membership.Role.PLANNER)
        if not is_manager:
            target_employee = serializer.validated_data.get("employee")
            employee_profile = self.request.employee_profile
            if not employee_profile or target_employee.id != employee_profile.id:
                raise PermissionDenied("Mitarbeitende dürfen nur eigene Absenzen anlegen.")
        absence = serializer.save(
            tenant=self.request.tenant,
            status=Absence.Status.APPROVED if is_manager else Absence.Status.PENDING,
        )
        # Block 2.4: nur bei selbst-erstellten (PENDING) Absenzen -- von
        # Admin/Planer angelegte sind sofort APPROVED, niemand muss dafür
        # noch benachrichtigt werden.
        if not is_manager:
            notify_new_absence_request(absence)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        absence = self.get_object()
        if absence.status != Absence.Status.PENDING:
            raise ValidationError("Nur offene Absenzanträge können genehmigt werden.")
        absence.status = Absence.Status.APPROVED
        try:
            absence.clean()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        absence.save(update_fields=["status"])
        notify_absence_decision(absence)
        return Response(self.get_serializer(absence).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        absence = self.get_object()
        if absence.status != Absence.Status.PENDING:
            raise ValidationError("Nur offene Absenzanträge können abgelehnt werden.")
        absence.status = Absence.Status.REJECTED
        absence.save(update_fields=["status"])
        notify_absence_decision(absence)
        return Response(self.get_serializer(absence).data)


class PregnancyViewSet(TenantScopedViewSet):
    """
    Mutterschutz (Block 1.15). Anders als bei Absence/TimeRecord ist hier
    schon das LESEN eingeschränkt (siehe PregnancyPermission-Docstring):
    Admin sieht alles im Tenant, alle anderen Rollen nur ihre eigenen
    Einträge (get_queryset) -- auch Planer/HR sehen also grundsätzlich
    keine fremden Schwangerschaften, ausser sie sind selbst betroffen.
    Unterstützt ?employee=<id> nur für Admin (für andere Rollen ist die
    Liste ohnehin serverseitig auf die eigene Person begrenzt).
    """

    permission_classes = [permissions.IsAuthenticated, PregnancyPermission]
    queryset = Pregnancy.all_objects.all()
    serializer_class = PregnancySerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee")
        membership = self.request.membership
        if membership and membership.role == Membership.Role.ADMIN:
            employee = self.request.query_params.get("employee")
            if employee:
                qs = qs.filter(employee_id=employee)
            return qs
        employee_profile = self.request.employee_profile
        if not employee_profile:
            return qs.none()
        return qs.filter(employee_id=employee_profile.id)

    def perform_create(self, serializer):
        membership = self.request.membership
        if membership.role != Membership.Role.ADMIN:
            target_employee = serializer.validated_data.get("employee")
            employee_profile = self.request.employee_profile
            if not employee_profile or target_employee.id != employee_profile.id:
                raise PermissionDenied("Nur Admin darf Schwangerschaften für andere Mitarbeitende anlegen.")
        serializer.save(tenant=self.request.tenant)


class ShiftPreferenceViewSet(TenantScopedViewSet):
    """
    Wunschfrei/Wunschdienst (MVP-Fahrplan Block 2.13). Unterstützt
    ?employee=<id>, um die Wünsche eines Mitarbeiters zu laden -- Lesen ist
    für alle Rollen offen (der Planer sieht die Wünsche aller Mitarbeitenden
    der aktuell betrachteten Station, um sie bei der Zuweisung im
    Planblatt/Jahresplan berücksichtigen zu können).

    Anders als bei Absence gibt es hier KEINEN Manager-Override: jede Rolle
    (auch Admin/Planer) darf nur für die eigene Person schreiben, siehe
    ShiftPreferencePermission. employee wird deshalb serverseitig immer aus
    request.employee_profile gesetzt statt aus dem Payload übernommen.
    """

    permission_classes = [permissions.IsAuthenticated, ShiftPreferencePermission]
    queryset = ShiftPreference.all_objects.all()
    serializer_class = ShiftPreferenceSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee", "template")
        employee = self.request.query_params.get("employee")
        if employee:
            qs = qs.filter(employee_id=employee)
        return qs

    def perform_create(self, serializer):
        employee_profile = self.request.employee_profile
        if not employee_profile:
            raise PermissionDenied("Nur mit eigenem Mitarbeiterprofil möglich.")
        serializer.save(tenant=self.request.tenant, employee=employee_profile)


class ShiftTradeRequestViewSet(TenantScopedViewSet):
    """
    Diensttausch-Anfragen (Abschnitt 7). Zweistufiger Genehmigungs-Workflow
    (Block 2.3, siehe ShiftTradeRequest-Docstring): `accept` (Zielperson)
    markiert nur die Zustimmung, `approve` (Admin/Planer) vollzieht den
    Tausch tatsächlich -- nicht über ein PATCH auf `status`, damit die volle
    Regel-Engine (siehe ShiftTradeRequest.approve()) dabei zwingend
    durchlaufen wird statt sich auf Client-Disziplin zu verlassen.

    Admin/Planer dürfen alles, inkl. approve/reject. Mitarbeitende dürfen
    nur eigene Schichten zum Tausch anbieten (geprüft in perform_create) und
    über die übrigen Actions reagieren -- `cancel` als anbietende Person,
    `accept`/`decline` als Zielperson (geprüft in
    ShiftTradeRequestPermission.has_object_permission, da get_object() in
    jeder Action aufgerufen wird).
    """

    permission_classes = [permissions.IsAuthenticated, ShiftTradeRequestPermission]
    queryset = ShiftTradeRequest.all_objects.all()
    serializer_class = ShiftTradeRequestSerializer

    def get_queryset(self):
        return super().get_queryset().select_related(
            "requester_assignment", "target_employee", "target_assignment"
        )

    def perform_create(self, serializer):
        if self.request.membership.role == Membership.Role.EMPLOYEE:
            requester_assignment = serializer.validated_data.get("requester_assignment")
            employee_profile = self.request.employee_profile
            if not employee_profile or requester_assignment.employee_id != employee_profile.id:
                raise PermissionDenied("Mitarbeitende dürfen nur eigene Schichten zum Tausch anbieten.")
        super().perform_create(serializer)
        notify_new_trade_request(serializer.instance)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Zielperson stimmt zu -- vollzieht den Tausch noch nicht, siehe `approve`."""
        trade_request = self.get_object()
        try:
            trade_request.accept()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        notify_trade_accepted_by_employee(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Admin/Planer-Freigabe -- vollzieht den Tausch (siehe ShiftTradeRequest.approve())."""
        trade_request = self.get_object()
        try:
            trade_request.approve()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        notify_trade_decision(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Admin/Planer lehnt ab (zu unterscheiden von `decline`, das die Zielperson selbst auslöst)."""
        trade_request = self.get_object()
        try:
            trade_request.reject()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        notify_trade_decision(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        trade_request = self.get_object()
        if trade_request.status != ShiftTradeRequest.Status.PENDING:
            raise ValidationError("Nur offene Tauschanfragen können abgelehnt werden.")
        trade_request.status = ShiftTradeRequest.Status.DECLINED
        trade_request.resolved_at = timezone.now()
        trade_request.save(update_fields=["status", "resolved_at"])
        notify_trade_declined(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        trade_request = self.get_object()
        if trade_request.status not in (
            ShiftTradeRequest.Status.PENDING,
            ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED,
        ):
            raise ValidationError("Nur offene oder angenommene Tauschanfragen können zurückgezogen werden.")
        trade_request.status = ShiftTradeRequest.Status.CANCELLED
        trade_request.resolved_at = timezone.now()
        trade_request.save(update_fields=["status", "resolved_at"])
        return Response(self.get_serializer(trade_request).data)


class TimeRecordViewSet(TenantScopedViewSet):
    """
    Ist-Arbeitszeiterfassung (Art. 73 ArGV 1, MVP-Fahrplan Block 1.9).
    Unterstützt ?assignment=<id>, ?employee=<id> und ?date_from=/?date_to=
    (gegen assignment__date) zum Filtern -- letzteres, damit Planblatt-Grid
    und Zeiterfassungs-Tab nur den sichtbaren Monat statt aller Ist-Zeiten
    des gesamten Tenants laden (sonst wächst der Response mit der Zeit
    unnötig, siehe README Block 1 Frontend-Anmerkung).

    Admin/Planer dürfen für jede Schicht Ist-Zeiten anlegen/ändern/löschen
    und über `confirm` bestätigen. Mitarbeitende dürfen nur für die eigene
    Schicht schreiben (geprüft in perform_create, das Objekt existiert bei
    create noch nicht) und ihren Eintrag bearbeiten/löschen, solange er
    noch nicht bestätigt ist (TimeRecordPermission).

    Nutzer-Feedback (2026-08): "ich muss die Stationen durchsuchen, bis ich
    die zu bestätigende Erfassung finde" -- ?status= sowie ?node=/Scoping und
    Suche/Sortierung (analog EmployeeViewSet/TimeTemplateViewSet) erlauben
    jetzt eine stationsübergreifende "Zu bestätigen"-Tabelle statt Station
    für Station manuell nachzuschauen (siehe TimeRecordOverview.jsx).
    """

    permission_classes = [permissions.IsAuthenticated, TimeRecordPermission]
    queryset = TimeRecord.all_objects.all()
    serializer_class = TimeRecordSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["assignment__employee__first_name", "assignment__employee__last_name", "assignment__node__name"]
    ordering_fields = ["assignment__date", "assignment__employee__last_name", "assignment__node__name", "status"]
    ordering = ["-assignment__date"]

    def get_queryset(self):
        qs = super().get_queryset().select_related(
            "assignment", "assignment__employee", "assignment__node", "assignment__template"
        )
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            qs = qs.filter(assignment__node_id__in=node_ids)
        node = self.request.query_params.get("node")
        if node:
            qs = qs.filter(assignment__node_id=node)
        assignment = self.request.query_params.get("assignment")
        if assignment:
            qs = qs.filter(assignment_id=assignment)
        employee = self.request.query_params.get("employee")
        if employee:
            qs = qs.filter(assignment__employee_id=employee)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        date_from = self.request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(assignment__date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(assignment__date__lte=date_to)
        return qs

    def perform_create(self, serializer):
        if self.request.membership.role == Membership.Role.EMPLOYEE:
            assignment = serializer.validated_data.get("assignment")
            employee_profile = self.request.employee_profile
            if not employee_profile or assignment.employee_id != employee_profile.id:
                raise PermissionDenied("Mitarbeitende dürfen nur für eigene Schichten Ist-Zeiten erfassen.")
        serializer.save(tenant=self.request.tenant, recorded_by=self.request.user)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        time_record = self.get_object()
        try:
            time_record.confirm()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        return Response(self.get_serializer(time_record).data)


class MissingTimeRecordViewSet(TenantScopedViewSet):
    """
    Nutzer-Feedback (2026-08): Planer/HR sollen vergangene Schichten ohne
    Ist-Erfassung stationsübergreifend als eigene, durchsuchbare/sortierbare
    Tabelle sehen ("Noch nicht erfasst" in TimeRecordOverview.jsx) statt
    Station für Station manuell zu suchen. Rein lesend -- die eigentliche
    Erfassung läuft weiterhin über TimeRecordViewSet.create(); dieses
    ViewSet listet nur, was dafür noch fehlt.
    """

    http_method_names = ["get", "head", "options"]
    permission_classes = [permissions.IsAuthenticated, IsTenantManagerOrHR]
    queryset = ShiftAssignment.all_objects.all()
    serializer_class = ShiftAssignmentSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["employee__first_name", "employee__last_name", "node__name"]
    ordering_fields = ["date", "employee__last_name", "node__name"]
    ordering = ["date"]

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related("employee", "node", "template")
            .filter(date__lte=timezone.localdate(), time_record__isnull=True)
        )
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            qs = qs.filter(node_id__in=node_ids)
        node = self.request.query_params.get("node")
        if node:
            qs = qs.filter(node_id=node)
        employee = self.request.query_params.get("employee")
        if employee:
            qs = qs.filter(employee_id=employee)
        date_from = self.request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs


class UnderstaffedShiftsView(TenantScopedAPIMixin, APIView):
    """
    Für das Admin/Planer-Dashboard (README MVP-Fahrplan Block 2, Punkt 21):
    die Mindestbesetzungs-Auswertung (Block 9/2.9) läuft sonst rein
    clientseitig in PlanGrid.jsx, aber nur für die einzeln ausgewählte
    Station -- fürs Dashboard braucht es den ganzen Tenant auf einen Blick.
    Ein Frontend-Loop über alle Stationen wäre N Requests; hier stattdessen
    eine einzelne GROUP BY (template, date)-Auswertung serverseitig, über
    alle Stationen hinweg, für die nächsten UPCOMING_DAYS Tage (bewusst
    kurz -- weiter in der Zukunft ist typischerweise noch nicht geplant,
    ein "unterbesetzt" wäre dort nur Rauschen statt Signal, siehe README).

    Lesen bleibt wie überall in der App für alle vier Rollen offen (siehe
    core.permissions-Docstring) -- die Einschränkung auf Admin/Planer
    passiert rein im Frontend (das Dashboard ist dort kein sichtbarer Tab
    für andere Rollen), nicht hier.

    README (2026-08, UX-Bugfix): eine leere Liste war für Dashboard.jsx
    nicht von "kein einziger Schichttyp hat eine Mindestbesetzung
    konfiguriert" zu unterscheiden -- beide sahen identisch aus, obwohl es
    inhaltlich zwei ganz verschiedene Zustände sind ("alles im grünen
    Bereich" vs. "dieses Feature ist noch nicht eingerichtet"). Response
    daher jetzt ein Objekt mit `has_configured_templates` statt einer
    nackten Liste.
    """

    permission_classes = [permissions.IsAuthenticated]
    UPCOMING_DAYS = 7

    def get(self, request):
        tenant = request.tenant
        if tenant is None:
            return Response({"has_configured_templates": False, "shortfalls": []})

        today = timezone.localdate()
        end = today + timedelta(days=self.UPCOMING_DAYS - 1)

        templates = list(
            TimeTemplate.all_objects.filter(tenant=tenant, minimum_staffing__gt=0).select_related("node")
        )
        if not templates:
            return Response({"has_configured_templates": False, "shortfalls": []})

        counts_qs = (
            ShiftAssignment.all_objects.filter(
                tenant=tenant, template__in=templates, date__range=[today, end]
            )
            .values("template_id", "date")
            .annotate(count=Count("id"))
        )
        counts_by_key = {(c["template_id"], c["date"]): c["count"] for c in counts_qs}

        results = []
        for template in templates:
            d = today
            while d <= end:
                count = counts_by_key.get((template.id, d), 0)
                if count < template.minimum_staffing:
                    results.append(
                        {
                            "date": d.isoformat(),
                            "node_id": template.node_id,
                            "node_name": template.node.name,
                            "template_id": template.id,
                            "template_name": template.name,
                            "template_color": template.color,
                            "count": count,
                            "minimum_staffing": template.minimum_staffing,
                        }
                    )
                d += timedelta(days=1)

        results.sort(key=lambda r: (r["date"], r["node_name"], r["template_name"]))
        return Response({"has_configured_templates": True, "shortfalls": results})


class PayrollExportView(TenantScopedAPIMixin, APIView):
    """
    MVP-Fahrplan Block 2, Punkt 31: Lohn-Rohdaten eines Kalendermonats für
    alle aktiven Mitarbeitenden, übersetzt über PayrollCategoryMapping
    (Punkt 30) in (Lohnart-Code, Bezeichnung, Menge, Einheit).

    Admin-only -- bewusst NICHT über eine Permission-Klasse mit
    SAFE_METHODS-Ausnahme (wie IsTenantAdmin, das Lesen für alle Rollen
    offen lässt, siehe dessen Docstring), sondern ein expliziter Check hier
    wie bei EmployeeViewSet.monthly_summary: das hier sind fertig
    übersetzte Lohn-Rohdaten über ALLE Mitarbeitenden hinweg, kein
    Selbstauskunfts-Endpoint wie balance/night-work.

    ?month=YYYY-MM (Pflicht). ?output=csv liefert einen Datei-Download
    (Content-Disposition: attachment) statt JSON -- erster CSV-Export der
    App, siehe README. Bewusst NICHT `?format=csv`: DRF reserviert den
    Query-Parameter `format` selbst für die Content-Negotiation
    (URL_FORMAT_OVERRIDE) -- ein unbekannter Wert dort lässt die
    Content-Negotiation in initial() fehlschlagen, BEVOR get() überhaupt
    läuft (404 statt der erwarteten CSV-Antwort, empirisch geprüft).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = request.membership
        if not membership or membership.role != Membership.Role.ADMIN:
            raise PermissionDenied("Nur Admin darf den Lohn-Export einsehen.")
        tenant = request.tenant
        if tenant is None:
            raise PermissionDenied("Kein aktiver Tenant.")

        month_param = request.query_params.get("month")
        if not month_param:
            raise ValidationError({"month": "Pflichtfeld, erwartet YYYY-MM."})
        try:
            year_str, month_str = month_param.split("-")
            year, month = int(year_str), int(month_str)
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            raise ValidationError({"month": "Ungültiges Format, erwartet YYYY-MM."})

        # Nutzer-Feedback (2026-08): "deaktivierte Kategorien gelten als
        # bewusst ausgeschlossen" -- eine Kategorie mit is_active=False hat
        # der Admin absichtlich abgewählt (z. B. "Sonntagszuschlag lösen wir
        # anders") und braucht deshalb KEINE Warnung, anders als eine nie
        # konfigurierte Kategorie (mögliches Versehen). `mappings` enthält
        # deshalb bewusst ALLE Zeilen (aktiv + inaktiv): configured_*
        # entscheidet über die Warnung, active_by_* über die tatsächliche
        # Code-Zuordnung.
        mappings = list(PayrollCategoryMapping.all_objects.filter(tenant=tenant))
        active_by_category = {m.category: m for m in mappings if m.category and m.is_active}
        active_by_template = {m.special_template_id: m for m in mappings if m.special_template_id and m.is_active}
        active_by_absence_type = {m.absence_type_id: m for m in mappings if m.absence_type_id and m.is_active}
        configured_categories = {m.category for m in mappings if m.category}
        configured_templates = {m.special_template_id for m in mappings if m.special_template_id}
        configured_absence_types = {m.absence_type_id for m in mappings if m.absence_type_id}
        special_template_names = {
            t.id: t.name
            for t in TimeTemplate.all_objects.filter(tenant=tenant, category=TimeTemplate.Category.SPECIAL)
        }
        absence_type_names = {t.id: t.name for t in AbsenceType.all_objects.filter(tenant=tenant)}

        sick_categories = {
            PayrollCategoryMapping.Category.SICK_DAYS,
            PayrollCategoryMapping.Category.SICK_DAYS_EXHAUSTED,
        }
        month_end = date(year, month, calendar.monthrange(year, month)[1])

        employees = Employee.all_objects.filter(tenant=tenant, is_active=True).order_by("last_name", "first_name")
        result_employees = []
        warnings = set()
        for employee in employees:
            lines = []
            has_sick_days = False
            for raw in employee.payroll_raw_lines(year, month):
                if raw["category"]:
                    if raw["category"] in sick_categories:
                        has_sick_days = True
                    mapping = active_by_category.get(raw["category"])
                    label = PayrollCategoryMapping.Category(raw["category"]).label
                    is_configured = raw["category"] in configured_categories
                elif raw["special_template_id"]:
                    mapping = active_by_template.get(raw["special_template_id"])
                    label = special_template_names.get(raw["special_template_id"], "?")
                    is_configured = raw["special_template_id"] in configured_templates
                else:
                    mapping = active_by_absence_type.get(raw["absence_type_id"])
                    label = absence_type_names.get(raw["absence_type_id"], "?")
                    is_configured = raw["absence_type_id"] in configured_absence_types
                if mapping is None:
                    if not is_configured:
                        warnings.add(f"{label}: kein Lohnart-Code konfiguriert")
                    continue
                lines.append(
                    {
                        "payroll_code": mapping.payroll_code,
                        "payroll_label": mapping.payroll_label or label,
                        "amount": raw["amount"],
                        "unit": raw["unit"],
                    }
                )
            if lines:
                result_employees.append(
                    {
                        "employee_id": employee.id,
                        "employee_name": f"{employee.first_name} {employee.last_name}",
                        # MVP-Fahrplan Block 2 Punkt 30/31 (Nutzer-Feedback
                        # 2026-08): Kostenstelle der Station(en), siehe
                        # Employee.effective_cost_center() -- None, wenn
                        # mehrdeutig oder nirgends konfiguriert.
                        "cost_center": employee.effective_cost_center(),
                        "lines": lines,
                        # Nachbesserung 2026-08 ("Sick-Pay-Skala einbauen"):
                        # nur gesetzt, wenn dieser Monat Krankheitstage
                        # enthält -- Kontext für die Lohnbuchhaltung, WARUM
                        # SICK_DAYS/SICK_DAYS_EXHAUSTED so aufgeteilt wurden
                        # (Employee.sick_pay_summary(), Punkt 16).
                        "sick_pay_context": (
                            self._sick_pay_context(employee, month_end) if has_sick_days else None
                        ),
                    }
                )

        if request.query_params.get("output") == "csv":
            return self._csv_response(year, month, result_employees)

        return Response(
            {"year": year, "month": month, "employees": result_employees, "warnings": sorted(warnings)}
        )

    def _sick_pay_context(self, employee, month_end):
        summary = employee.sick_pay_summary(reference_date=month_end)
        return {
            "model": summary["model"],
            "scale": summary["scale"],
            "entitlement_days": summary["entitlement_days"],
            "remaining_days": summary["remaining_days"],
            "waiting_days": summary["waiting_days"],
        }

    def _csv_response(self, year, month, result_employees):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="lohn-export-{year}-{month:02d}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["Personalnummer", "Name", "Kostenstelle", "Lohnart-Code", "Bezeichnung", "Menge", "Einheit", "Periode"]
        )
        period = f"{year}-{month:02d}"
        for employee in result_employees:
            for line in employee["lines"]:
                writer.writerow(
                    [
                        employee["employee_id"],
                        employee["employee_name"],
                        employee["cost_center"] or "",
                        line["payroll_code"],
                        line["payroll_label"],
                        line["amount"],
                        line["unit"],
                        period,
                    ]
                )
        return response


_GERMAN_MONTHS = [
    "",
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]
_GERMAN_WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _shift_short_label(template):
    return template.icon or template.name[:3].upper()


def _absence_short_label(absence_type):
    return absence_type.icon or absence_type.name[:1].upper()


class PlanExportView(TenantScopedAPIMixin, APIView):
    """
    MVP-Fahrplan Block 2, Punkt 5: Export des Planblatts (eine Station/ein
    Team, ein Kalendermonat) als PDF (Aushang in der Praxis) oder CSV
    (Übergabe an eine nicht API-angebundene Lohnbuchhaltung).

    Sichtbarkeit identisch zum Planblatt selbst (siehe
    ShiftAssignmentViewSet.get_queryset) -- anders als PayrollExportView
    (Punkt 31, Admin-only) sind das dieselben Daten, die Mitarbeitende im
    Planblatt für ihre eigene(n) Station(en) ohnehin schon sehen, deshalb
    keine eigene Rollen-Einschränkung, nur der bestehende
    Stations-Scope-Check über `_employee_scoped_node_ids`.

    ?node=<id> und ?month=YYYY-MM sind Pflicht. ?output=pdf (Default) oder
    ?output=csv -- bewusst NICHT ?format=, siehe PayrollExportView-Docstring
    zur DRF-Content-Negotiation-Falle mit dem reservierten `format`-Query-
    Parameter.

    Vereinfachung ggü. dem Planblatt selbst: eine Zeile pro Mitarbeiter
    (nicht pro Employment/Team wie bei mehreren Teams in einer Station,
    siehe PlanGrid.jsx-Docstring zu Punkt 17) -- für einen Aushang/Export
    ist eine flache "wer arbeitet wann"-Liste über alle Zuweisungen der
    Person hinweg (unabhängig davon, auf welchem Team-Knoten sie liegen)
    lesbarer als die Team-Trennzeilen der interaktiven Planblatt-Ansicht.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        tenant = request.tenant
        if tenant is None:
            raise PermissionDenied("Kein aktiver Tenant.")

        node_param = request.query_params.get("node")
        if not node_param:
            raise ValidationError({"node": "Pflichtfeld."})
        month_param = request.query_params.get("month")
        if not month_param:
            raise ValidationError({"month": "Pflichtfeld, erwartet YYYY-MM."})
        try:
            year_str, month_str = month_param.split("-")
            year, month = int(year_str), int(month_str)
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            raise ValidationError({"month": "Ungültiges Format, erwartet YYYY-MM."})

        target = Node.all_objects.filter(tenant=tenant, pk=node_param).first()
        if target is None:
            raise ValidationError({"node": "Unbekannte Station."})
        scope_ids = [target.id] + [c.id for c in target.get_children()]

        # TenantScopedAPIMixin (core.views) setzt request.employee_profile
        # bewusst nicht (core darf scheduling nicht importieren, siehe dessen
        # Docstring) -- anders als bei scheduling.views.TenantScopedViewSet
        # hier direkt aufgelöst, exakt dieselbe Abfrage.
        employee_profile = Employee.all_objects.filter(tenant=tenant, user=request.user).first()
        allowed_ids = _employee_scoped_node_ids(request.membership, employee_profile)
        if allowed_ids is not None and not (set(scope_ids) & set(allowed_ids)):
            raise PermissionDenied("Keine Berechtigung für diese Station.")

        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])

        employees = list(
            Employee.all_objects.filter(tenant=tenant, is_active=True, nodes__in=scope_ids)
            .distinct()
            .order_by("last_name", "first_name")
        )

        assignments_by_key = {}
        assignments = (
            ShiftAssignment.all_objects.filter(
                tenant=tenant,
                node_id__in=scope_ids,
                employee__in=employees,
                date__gte=month_start,
                date__lte=month_end,
            )
            .select_related("template")
            .order_by("template__start_time")
        )
        for assignment in assignments:
            assignments_by_key.setdefault((assignment.employee_id, assignment.date), []).append(assignment)

        absences_by_key = {}
        absences = Absence.all_objects.filter(
            tenant=tenant,
            employee__in=employees,
            status=Absence.Status.APPROVED,
            start_date__lte=month_end,
            end_date__gte=month_start,
        ).select_related("type")
        for absence in absences:
            day = max(absence.start_date, month_start)
            last_day = min(absence.end_date, month_end)
            while day <= last_day:
                absences_by_key.setdefault((absence.employee_id, day), []).append(absence)
                day += timedelta(days=1)

        output = request.query_params.get("output", "pdf")
        if output == "csv":
            return self._csv_response(
                tenant, target, year, month, month_start, month_end, employees, assignments_by_key, absences_by_key
            )
        return self._pdf_response(
            tenant, target, year, month, month_start, month_end, employees, assignments_by_key, absences_by_key
        )

    def _csv_response(
        self, tenant, node, year, month, month_start, month_end, employees, assignments_by_key, absences_by_key
    ):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="plan-export-{year}-{month:02d}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Personalnummer", "Name", "Datum", "Wochentag", "Typ", "Bezeichnung", "Von", "Bis"])
        day = month_start
        while day <= month_end:
            weekday = _GERMAN_WEEKDAYS[day.weekday()]
            for employee in employees:
                name = f"{employee.first_name} {employee.last_name}"
                for assignment in assignments_by_key.get((employee.id, day), []):
                    writer.writerow(
                        [
                            employee.id,
                            name,
                            day.isoformat(),
                            weekday,
                            "Dienst",
                            assignment.template.name,
                            assignment.template.start_time.strftime("%H:%M"),
                            assignment.template.end_time.strftime("%H:%M"),
                        ]
                    )
                for absence in absences_by_key.get((employee.id, day), []):
                    label = absence.type.name
                    if absence.day_portion != Absence.DayPortion.FULL:
                        label = f"{label} ({absence.get_day_portion_display()})"
                    writer.writerow([employee.id, name, day.isoformat(), weekday, "Absenz", label, "", ""])
            day += timedelta(days=1)
        return response

    def _pdf_response(
        self, tenant, node, year, month, month_start, month_end, employees, assignments_by_key, absences_by_key
    ):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        import io

        num_days = month_end.day
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )
        styles = getSampleStyleSheet()
        title = Paragraph(f"{tenant.name} – {node.name} – {_GERMAN_MONTHS[month]} {year}", styles["Title"])

        header_days = ["Mitarbeiter"] + [str(d) for d in range(1, num_days + 1)]
        header_weekdays = [""] + [_GERMAN_WEEKDAYS[date(year, month, d).weekday()] for d in range(1, num_days + 1)]
        rows = [header_days, header_weekdays]
        weekend_columns = set()
        for d in range(1, num_days + 1):
            if date(year, month, d).weekday() >= 5:
                weekend_columns.add(d)

        for employee in employees:
            row = [f"{employee.last_name} {employee.first_name}"]
            for d in range(1, num_days + 1):
                day = date(year, month, d)
                parts = [_shift_short_label(a.template) for a in assignments_by_key.get((employee.id, day), [])]
                for absence in absences_by_key.get((employee.id, day), []):
                    parts.append(_absence_short_label(absence.type))
                row.append("+".join(parts))
            rows.append(row)

        name_col_width = 32 * mm
        day_col_width = (landscape(A4)[0] - 20 * mm - name_col_width) / num_days
        col_widths = [name_col_width] + [day_col_width] * num_days

        table = Table(rows, colWidths=col_widths, repeatRows=2)
        style = [
            ("FONTSIZE", (0, 0), (-1, -1), 6),
            ("FONTSIZE", (0, 0), (0, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#e2e8f0")),
            ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        for d in weekend_columns:
            style.append(("BACKGROUND", (d, 2), (d, -1), colors.HexColor("#f1f5f9")))
        table.setStyle(TableStyle(style))

        doc.build([title, table])
        pdf_bytes = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="plan-export-{year}-{month:02d}.pdf"'
        return response

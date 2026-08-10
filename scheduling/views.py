from datetime import date, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
    IsTenantManager,
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
    EmployeeBalanceSerializer,
    EmployeeSerializer,
    MonthlySummarySerializer,
    NightWorkSummarySerializer,
    NodeSerializer,
    PregnancySerializer,
    ShiftAssignmentSerializer,
    ShiftPreferenceSerializer,
    ShiftTradeRequestSerializer,
    SkillSerializer,
    TimeRecordSerializer,
    TimeTemplateSerializer,
    WeeklyOvertimeSerializer,
)


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


def _employee_scoped_node_ids(request):
    """
    Ein Mitarbeiter darf nur seine eigene(n) Station(en) sehen -- anders als
    beim übrigen Planblatt (siehe ShiftAssignmentViewSet: "Mitarbeitende
    sehen den ganzen Plan (Transparenz)") gilt diese Transparenz nur
    INNERHALB der eigenen Station(en), nicht tenant-weit über alle Stationen
    hinweg. Gibt None zurück, wenn keine Einschränkung gilt (Admin/Planer/HR
    sehen weiterhin alle Stationen des Tenants), sonst die Liste der
    Node-IDs, auf die die Mitarbeiter-Rolle beschränkt ist (leere Liste, falls
    kein Employee-Profil existiert). Von NodeViewSet, TimeTemplateViewSet und
    ShiftAssignmentViewSet gleich ausgewertet, damit alle drei
    node-bezogenen Ressourcen konsistent eingeschränkt sind.

    README Punkt 17: employee_profile.nodes enthält bei Team-Anstellungen
    Team- statt Stations-Ids. Damit TimeTemplates (die bewusst stationsweit
    bleiben, siehe ShiftAssignmentViewSet-Docstring) und die Station selbst
    im NodeViewSet weiterhin sichtbar sind, wird hier zusätzlich der direkte
    Elternknoten jedes eigenen Knotens aufgenommen (eine Ebene, konsistent
    mit der "genau ein Team-Level"-Entscheidung). Für jede heutige, flache
    Station ohne Eltern-Knoten ist das ein No-Op -- identisches Verhalten zu
    vorher.
    """
    membership = request.membership
    if not membership or membership.role != Membership.Role.EMPLOYEE:
        return None
    employee_profile = request.employee_profile
    if not employee_profile:
        return []
    own_nodes = list(employee_profile.nodes.all())
    node_ids = {n.id for n in own_nodes}
    for n in own_nodes:
        parent = n.get_parent()
        if parent is not None:
            node_ids.add(parent.id)
    return list(node_ids)


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
        node_ids = _employee_scoped_node_ids(self.request)
        if node_ids is not None:
            qs = qs.filter(id__in=node_ids)
        return qs

    def perform_create(self, serializer):
        tenant = self.request.tenant
        parent_id = serializer.validated_data.pop("parent", None)
        name = serializer.validated_data["name"]

        if parent_id:
            parent = Node.all_objects.filter(tenant=tenant, pk=parent_id).first()
            if parent is None:
                raise ValidationError({"parent": "Ungültiger oder fremder Knoten."})
            node = parent.add_child(name=name, tenant=tenant)
        else:
            node = Node.add_root(name=name, tenant=tenant)

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
                node.move(target, pos="sorted-child")
            except InvalidMoveToDescendant:
                raise ValidationError(
                    {"parent": "Eine Station kann nicht in eine ihrer eigenen Unterstationen verschoben werden."}
                )
            except PathOverflow:
                raise ValidationError({"parent": "Zu viele Stationen auf dieser Ebene -- Verschieben nicht möglich."})
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


class AbsenceTypeViewSet(TenantScopedViewSet):
    """
    Nutzer-Feedback (2026-08): Absenzarten (bisher hartcodiert Ferien/
    Krankheit/Sonstiges) sind jetzt ein tenant-eigener Katalog, analog
    TimeTemplateViewSet -- Lesen für alle Rollen, Schreiben nur Admin/Planer.
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = AbsenceType.all_objects.all()
    serializer_class = AbsenceTypeSerializer


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
        node_ids = _employee_scoped_node_ids(self.request)
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
        node_ids = _employee_scoped_node_ids(self.request)
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
    """

    permission_classes = [permissions.IsAuthenticated, TimeRecordPermission]
    queryset = TimeRecord.all_objects.all()
    serializer_class = TimeRecordSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("assignment", "assignment__employee", "assignment__template")
        assignment = self.request.query_params.get("assignment")
        if assignment:
            qs = qs.filter(assignment_id=assignment)
        employee = self.request.query_params.get("employee")
        if employee:
            qs = qs.filter(assignment__employee_id=employee)
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

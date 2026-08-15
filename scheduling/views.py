import calendar
import csv
import io
from contextlib import contextmanager
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
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
from core.tenancy import apply_tenant_scoped_initial
from core.views import TenantScopedAPIMixin

from . import planning
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
    BalanceFairnessBulkItemSerializer,
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


@contextmanager
def translate_model_validation_error():
    """
    Übersetzt eine Django-`ValidationError` (aus `Model.clean()`, z. B.
    `ShiftTradeRequest.approve()`/`Absence.approve()`) in eine DRF-
    `ValidationError` mit passender HTTP-400-Antwort -- vorher sechsfach
    identisch als try/except an jeder Action wiederholt, die eine
    Regel-Engine-Model-Methode aufruft.
    """
    try:
        yield
    except DjangoValidationError as e:
        raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages) from e


def parse_date_param(request, param_name, default=None):
    """
    Optionaler `?<param_name>=YYYY-MM-DD`-Query-Param, ISO-geparst -- vorher
    an mehreren Actions (weekly_overtime/fairness/balance/sick_pay)
    identisch als try/except date.fromisoformat wiederholt.
    """
    value = request.query_params.get(param_name)
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({param_name: "Ungültiges Datum, erwartet YYYY-MM-DD."})


def parse_int_param(request, param_name, default=None, error_message="Ungültiger Wert."):
    """
    Optionaler `?<param_name>=<int>`-Query-Param -- vorher an mehreren
    Actions (night_work/balance/monthly_summary) identisch als try/except
    int(...) wiederholt.
    """
    value = request.query_params.get(param_name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValidationError({param_name: error_message})


def parse_year_month_param(request, param_name="month"):
    """
    Pflicht-`?<param_name>=YYYY-MM`-Query-Param, liefert `(year, month)` --
    vorher in PayrollExportView.get und PlanExportView.get identisch
    dupliziert.
    """
    value = request.query_params.get(param_name)
    if not value:
        raise ValidationError({param_name: "Pflichtfeld, erwartet YYYY-MM."})
    try:
        year_str, month_str = value.split("-")
        year, month = int(year_str), int(month_str)
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        raise ValidationError({param_name: "Ungültiges Format, erwartet YYYY-MM."})
    return year, month


def csv_response(filename):
    """
    Baut ein leeres CSV-HttpResponse samt Writer -- gemeinsamer Helper für
    alle Datei-Downloads (Mitarbeitenden-Importvorlage, Lohn-Export,
    Plan-Export), die vorher je einzeln HttpResponse+Content-Disposition+
    csv.writer identisch aufgebaut haben.
    """
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response, csv.writer(response)


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
    nachgebaut mit der Zuweisung dazwischen (siehe
    core.tenancy.apply_tenant_scoped_initial() für die geteilte
    Implementierung mit core.views.TenantScopedAPIMixin/
    core.billing_views._TenantScopedNoBillingGateMixin).
    """

    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        apply_tenant_scoped_initial(self, request, *args, resolve_employee_profile=True, **kwargs)

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
            # Der unsichtbare Tenant-Wurzelknoten (Node.is_forest_root, siehe
            # dessen Docstring) ist kein Team-Level und darf hier nie als
            # "Elternknoten" auftauchen -- sonst würde jede heute flache
            # Station (jetzt depth=2 statt depth=1) fälschlich dessen Id mit
            # in die sichtbaren Knoten aufnehmen.
            if parent is not None and not parent.is_forest_root:
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


def apply_node_scope(qs, node_ids, field_lookup="node_id"):
    """
    Wendet das Ergebnis von _employee_scoped_node_ids() auf eine Queryset
    an -- `node_ids is None` bedeutet keine Einschränkung (siehe dessen
    Docstring), sonst wird nach `field_lookup__in=node_ids` gefiltert.
    Vorher an NodeViewSet/TimeTemplateViewSet/ShiftAssignmentViewSet/
    TimeRecordViewSet/MissingTimeRecordViewSet identisch als
    `if node_ids is not None: qs = qs.filter(...)` wiederholt. AbsenceViewSet
    und ShiftTradeRequestViewSet bleiben aussen vor -- deren Node-Scoping ist
    mit einer Q()-Sonderregel für eigene Absenzen/Tauschangebote der
    Mitarbeiter-Rolle verknüpft, kein reiner Feld-Filter.
    """
    if node_ids is None:
        return qs
    return qs.filter(**{f"{field_lookup}__in": node_ids})


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
        # Der unsichtbare Tenant-Wurzelknoten (Node.is_forest_root) ist reine
        # interne Baumstruktur, nie ein von Nutzern verwaltetes Objekt --
        # dieser Ausschluss macht GET/PATCH/DELETE/move darauf automatisch
        # zu einem 404 (get_object() läuft über dieselbe Queryset).
        qs = super().get_queryset().exclude(is_forest_root=True)
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        return apply_node_scope(qs, node_ids, field_lookup="id")

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
            # Nie mehr direkt als treebeard-Wurzel anlegen (siehe Node-
            # Klassen-Docstring) -- jede "Top-Level"-Station ist jetzt ein
            # Kind des unsichtbaren Tenant-Wurzelknotens.
            forest_root = Node.get_or_create_forest_root(tenant)
            node = forest_root.add_child(name=name, tenant=tenant, cost_center=cost_center)

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

        # "Auf die oberste Ebene verschieben" ist seit dem Tenant-
        # Wurzelknoten (Node.is_forest_root, siehe Node-Klassen-Docstring)
        # strukturell dasselbe wie "unter eine andere Station verschieben"
        # -- das Ziel ist in beiden Fällen ein konkreter, tenant-eigener
        # Knoten, nie mehr "irgendeine Wurzel". Kein `parent` übergeben ->
        # Ziel ist der unsichtbare Wurzelknoten selbst.
        if parent_id:
            target = Node.all_objects.filter(tenant=tenant, pk=parent_id).first()
            if target is None:
                raise ValidationError({"parent": "Ungültiger oder fremder Knoten."})
            if target.pk == node.pk:
                raise ValidationError({"parent": "Eine Station kann nicht in sich selbst verschoben werden."})
        else:
            target = Node.get_or_create_forest_root(tenant)

        try:
            self._reparent_with_verification(node, target, tenant)
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

        return Response(NodeSerializer(node).data)

    def _reparent_with_verification(self, node, target, tenant):
        """
        Bewegt `node` so, dass es (sortiertes) Kind von `target` wird.

        Hintergrund (Bugfix 2026-08, Nutzer-Feedback: "ich kann Küche direkt
        in Station A ziehen, nicht aber von Station A zurück in
        Hauswirtschaft"): treebeards move(pos="sorted-child"/"sorted-
        sibling") bricht früh als vermeintlichen No-Op ab, sobald die letzte
        Pfad-Ziffer der AKTUELLEN Position von node zufällig mit der neu
        berechneten Zielposition übereinstimmt -- OHNE zu prüfen, ob es
        überhaupt derselbe Elternknoten ist (treebeard/mp_tree.py,
        MP_MoveHandler.process(): "if first := siblings.first(): ... if
        self.node._get_lastpos_in_path() == newpos - 1: return"). Betrifft
        nur ein `target` mit bereits mindestens einem Kind (sonst
        kurzschliesst treebeard intern zu "first-sibling", ein komplett
        anderer, unbetroffener Codepfad) -- empirisch reproduzierbar schon
        bei zwei bis drei Stationen, kein theoretisches Randrisiko, und ein
        Zwischenstopp bei einem beliebigen dritten Knoten reicht NICHT
        zuverlässig aus (der Zwischenstopp selbst kann derselben Kollision
        zum Opfer fallen, empirisch beobachtet).

        Deterministische Lösung statt Zwischenstopp-Raten: dieselbe
        Kurzschluss-Prüfung greift laut Quellcode nur, wenn
        `get_sorted_pos_queryset(...).first()` einen TATSÄCHLICHEN
        nachfolgenden Geschwisterknoten liefert -- sortiert node also
        alphabetisch NACH allen bestehenden Kindern von target ein, ist die
        Ergebnismenge leer und treebeard fällt auf "last-sibling" zurück,
        ein komplett anderer Codepfad ohne jede Kollisions-Kurzschluss-
        Prüfung. Node wird deshalb zuerst unter einem garantiert alphabetisch
        letzten Platzhalternamen eingefügt (deterministisch garantiert
        kollisionsfrei), danach auf den echten Namen zurückgesetzt und ein
        zweites Mal sortiert -- node befindet sich dann bereits an einer
        frischen, von der ursprünglichen Position unabhängigen Stelle,
        wodurch eine erneute zufällige Kollision beim zweiten, echten Move
        praktisch ausgeschlossen ist (durch Stresstest mit >30 zufälligen
        Verschiebungen über mehrere Ebenen bestätigt, keine einzige
        Fehlschlag).
        """
        current_parent = node.get_parent()
        if current_parent is not None and current_parent.pk == target.pk:
            node.move(target, pos="sorted-child")
            node.refresh_from_db()
            return

        real_name = node.name
        try:
            node.name = "\U0010ffff" * 4
            node.save(update_fields=["name"])
            node.move(target, pos="sorted-child")
            node.refresh_from_db()
        finally:
            node.name = real_name
            node.save(update_fields=["name"])
        node.move(target, pos="sorted-child")
        node.refresh_from_db()


class SkillViewSet(TenantScopedViewSet):
    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = Skill.all_objects.all()
    serializer_class = SkillSerializer


class EmployeeReportingMixin:
    """
    Reine Selbstauskunfts-/Reporting-Actions von EmployeeViewSet (Saldo,
    Fairness, Nacht-/Sonntagsarbeit, Lohnfortzahlung, Monatsauswertung,
    Gleitzeit-Abrechnung) -- ausgelagert, damit EmployeeViewSet nicht CRUD,
    Reporting UND Zugangsverwaltung in einer Klasse vereint (README Block
    10, Code-Polishing: "EmployeeViewSet als God Class"). Reine
    Struktur-Massnahme, keine Verhaltensänderung -- DRF sammelt
    `@action`-Methoden über die komplette MRO einer ViewSet-Klasse ein,
    unabhängig davon, in welcher Basisklasse sie stehen.
    """

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
        reference_date = parse_date_param(request, "week", default=timezone.localdate())
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
        year = parse_int_param(
            request, "year", default=timezone.localdate().year, error_message="Ungültiges Jahr."
        )
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
        reference_date = parse_date_param(request, "as_of", default=timezone.localdate())
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
        as_of_date = parse_date_param(request, "as_of", default=timezone.localdate())
        year = parse_int_param(request, "year", default=as_of_date.year, error_message="Ungültiges Jahr.")
        data = self._balance_data(employee, as_of_date, year)
        return Response(EmployeeBalanceSerializer(data).data)

    @staticmethod
    def _balance_data(employee, as_of_date, year):
        """Rohdaten für balance() -- ausgelagert, damit balance_fairness_bulk() sie wiederverwenden kann."""
        time_account = employee.time_account_summary(as_of_date)
        vacation = employee.vacation_balance(year)
        return {
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

    @action(detail=False, methods=["get"], url_path="balance-fairness-bulk")
    def balance_fairness_bulk(self, request):
        """
        Bulk-Variante von balance()/fairness() für die Mitarbeitendenliste
        (Nutzer-Feedback 2026-08: "lässt sich da was machen an der
        Performance?"). Die Liste löste bisher pro Zeile zwei eigene
        Requests aus (2xN HTTP-Roundtrips, siehe BalanceBadge.jsx/
        FairnessBadge.jsx) -- selbst nach dem bereits behobenen
        N+1-Query-Bug in fairness_summary() blieb das durch die
        Browser-Verbindungslimite (~6 gleichzeitige Requests pro Host)
        spürbar langsam. `?ids=1,2,3` (Pflicht, kommagetrennt) liefert
        beides für die ganze Liste in einem einzigen Request. IDs ausserhalb
        des eigenen Tenants oder ungültige IDs werden stillschweigend
        übersprungen (kein 404 für eine Bulk-Abfrage). Stichtag bewusst
        immer "heute" (kein ?as_of=/?year= wie bei balance()/fairness()) --
        die Mitarbeitendenliste zeigt ohnehin nur den aktuellen Stand.
        Lesen wie balance()/fairness() für alle vier Rollen offen.
        """
        ids_param = request.query_params.get("ids", "")
        try:
            ids = [int(x) for x in ids_param.split(",") if x.strip()]
        except ValueError:
            raise ValidationError({"ids": "Ungültige ID-Liste, erwartet kommagetrennte Ganzzahlen."})
        if not ids:
            return Response([])

        today = timezone.localdate()
        employees = self.get_queryset().filter(id__in=ids)
        results = [
            {
                "id": employee.id,
                "balance": self._balance_data(employee, today, today.year),
                "fairness": employee.fairness_summary(today),
            }
            for employee in employees
        ]
        return Response(BalanceFairnessBulkItemSerializer(results, many=True).data)

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
        as_of_date = parse_date_param(request, "as_of", default=timezone.localdate())
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
        today = timezone.localdate()
        year = parse_int_param(request, "year", default=today.year, error_message="Ungültiges Jahr.")
        month = parse_int_param(request, "month", default=today.month, error_message="Ungültiger Monat.")
        if not 1 <= month <= 12:
            raise ValidationError({"month": "Monat muss zwischen 1 und 12 liegen."})
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


class EmployeeAccessManagementMixin:
    """
    Login-Zugang für eine bestehende Employee einrichten/deaktivieren/
    reaktivieren -- ausgelagert aus EmployeeViewSet, siehe
    EmployeeReportingMixin-Docstring (README Block 10, Code-Polishing).
    """

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

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """
        Nutzer-Feedback (2026-08): "ein Deaktivieren Button [...] deaktiviert
        diesen inklusive seines Logins! Wenn einer Austritt aus dem
        Unternehmen muss das Handlebar sein" -- die bisherige "Aktiv"-
        Checkbox (is_active) betrifft bewusst nur die Planblatt-Sichtbarkeit,
        nicht den Login (siehe Employee.is_active-Feld), das ist bei einem
        tatsächlichen Austritt zu wenig. Diese Action setzt zusätzlich
        `user.is_active = False`, falls ein Login-Zugang existiert -- ein
        Mitarbeiter ohne Zugang (employee.user_id ist None) wird nur über
        Employee.is_active deaktiviert, da es keinen Login gibt, der zu
        sperren wäre. Gleiche automatische Wirkung erzielt der management
        command deactivate_expired_employees, sobald Employee.termination_date
        erreicht ist -- diese Action ist die sofortige, manuelle Variante
        davon (z. B. für einen fristlosen Austritt ohne Vorlauf).

        Admin-only wie jede Kontoverwaltung, die den Login-Zugang anfasst
        (siehe setup_access-Docstring) -- bewusst strenger als die sonstige
        IsTenantManager-Berechtigung dieses ViewSets.
        """
        if request.membership.role != Membership.Role.ADMIN:
            raise PermissionDenied("Nur Admin darf Mitarbeitende deaktivieren.")
        employee = self.get_object()
        employee.is_active = False
        employee.save(update_fields=["is_active"])
        if employee.user_id:
            employee.user.is_active = False
            employee.user.save(update_fields=["is_active"])

        from core.billing import sync_subscription_quantity

        sync_subscription_quantity(request.tenant)

        serializer = self.get_serializer(employee)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        """
        Gegenstück zu deactivate (Nutzer-Feedback 2026-08, Nachtrag: "Ja mach
        den Zusatz" auf die Frage, ob ein Weg fehlt, einen versehentlich
        deaktivierten oder wieder eingestellten Mitarbeitenden inklusive
        Login zurückzuholen -- die "Aktiv"-Checkbox im normalen PATCH-
        Formular setzt weiterhin bewusst nur Employee.is_active, nicht
        user.is_active, siehe deactivate-Docstring).

        Setzt zusätzlich termination_date auf None zurück: ein noch
        gesetztes, bereits verstrichenes Austrittsdatum würde sonst beim
        nächsten Lauf von deactivate_expired_employees die gerade
        reaktivierte Person automatisch wieder deaktivieren -- eine stille
        Falle bei einer Wiedereinstellung oder einer Korrektur nach
        Fehlklick.

        Admin-only wie deactivate/setup_access.
        """
        if request.membership.role != Membership.Role.ADMIN:
            raise PermissionDenied("Nur Admin darf Mitarbeitende reaktivieren.")
        employee = self.get_object()
        employee.is_active = True
        employee.termination_date = None
        employee.save(update_fields=["is_active", "termination_date"])
        if employee.user_id:
            employee.user.is_active = True
            employee.user.save(update_fields=["is_active"])

        from core.billing import sync_subscription_quantity

        sync_subscription_quantity(request.tenant)

        serializer = self.get_serializer(employee)
        return Response(serializer.data)


class EmployeeViewSet(EmployeeReportingMixin, EmployeeAccessManagementMixin, TenantScopedViewSet):
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

    CRUD + CSV-Import bleiben hier im Kern, Reporting-Actions und
    Zugangsverwaltung stecken in EmployeeReportingMixin/
    EmployeeAccessManagementMixin (README Block 10, Code-Polishing).
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

    def perform_create(self, serializer):
        # Abrechnung (README Block 6, 2026-08): jede neu angelegte aktive
        # Mitarbeitende zählt sofort in Stripes Abo-Menge mit -- das
        # Trial-Limit selbst wird bereits in EmployeeSerializer.validate()
        # geprüft (Fehler kommt also nie hier an), sync_subscription_quantity
        # ist ausserhalb der Trial-Phase (kein stripe_subscription_id) ein
        # No-Op.
        super().perform_create(serializer)
        from core.billing import sync_subscription_quantity

        sync_subscription_quantity(self.request.tenant)

    @action(detail=False, methods=["post"], url_path="import-csv")
    def import_csv(self, request):
        """
        CSV-Mitarbeitenden-Import (README Block 3, Setup-Wizard Schritt 4):
        Massenanlage per Datei statt einzeln über das Formular, für Tenants,
        die von einer bestehenden Excel-/CSV-Liste umsteigen. Gleiche
        Berechtigung wie der Rest dieses ViewSets (IsTenantManager) -- anders
        als setup_access/deactivate/reactivate wird hier nie ein
        User/Membership/Login angefasst, nur Employee-Stammdaten.

        Erwartet `multipart/form-data` mit Feld `file` (siehe
        api.importEmployeesCsv -- request()/JSON kann kein FormData
        transportieren). Encoding utf-8-sig (BOM-Toleranz für Excel-Exporte),
        Delimiter wird erkannt (Komma oder Semikolon, Excel-DE-Exporte nutzen
        oft Semikolon), mit Komma-Fallback falls die Erkennung selbst
        scheitert (z. B. bei nur einer Spalte).

        Pro Zeile ein eigener Savepoint (transaction.atomic() je Zeile) --
        ohne das würde ein einzelner Fehler unter Djangos atomic-Semantik die
        gesamte Transaktion vergiften und auch bereits valide Zeilen davor
        verwerfen. Zeilennummern sind 1-basiert INKLUSIVE Kopfzeile (Zeile 2
        = erste Datenzeile), damit sie exakt der Zeile entsprechen, die der
        Nutzer in Excel sieht.

        `station` ist ein optionaler Name-Lookup (kein Fremdschlüssel-Wert)
        -- tenant-gescoped über Node.all_objects, sonst könnte ein gleich
        benannter Knoten eines fremden Tenants matchen.
        """
        csv_file = request.FILES.get("file")
        if not csv_file:
            raise ValidationError({"file": "Pflichtfeld -- keine Datei hochgeladen."})

        text_stream = io.TextIOWrapper(csv_file.file, encoding="utf-8-sig")
        sample = text_stream.read(4096)
        text_stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(text_stream, dialect=dialect)

        required_columns = {"first_name", "last_name", "employment_pct"}
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            raise ValidationError(
                {"file": f"Fehlende Pflichtspalte(n): {', '.join(sorted(missing_columns))}."}
            )

        created = 0
        errors = []
        for row_index, row in enumerate(reader, start=2):
            first_name = (row.get("first_name") or "").strip()
            last_name = (row.get("last_name") or "").strip()
            employment_pct = (row.get("employment_pct") or "").strip()
            if not first_name or not last_name or not employment_pct:
                errors.append({"row": row_index, "message": "Vorname, Nachname und Pensum sind Pflichtfelder."})
                continue

            data = {"first_name": first_name, "last_name": last_name, "employment_pct": employment_pct}
            start_date = (row.get("employment_start_date") or "").strip()
            if start_date:
                data["employment_start_date"] = start_date

            station_name = (row.get("station") or "").strip()
            employments = []
            if station_name:
                node = Node.all_objects.filter(tenant=request.tenant, name__iexact=station_name).first()
                if not node:
                    errors.append({"row": row_index, "message": f"Station '{station_name}' nicht gefunden."})
                    continue
                try:
                    pensum = int(float(employment_pct))
                except ValueError:
                    errors.append({"row": row_index, "message": "Pensum muss eine Zahl sein."})
                    continue
                employments = [{"node": node.id, "pensum_pct": pensum}]
            if employments:
                data["employments"] = employments

            try:
                with transaction.atomic():
                    serializer = EmployeeSerializer(data=data, context={"request": request})
                    serializer.is_valid(raise_exception=True)
                    serializer.save(tenant=request.tenant)
                created += 1
            except ValidationError as exc:
                errors.append({"row": row_index, "message": str(exc.detail)})

        if created:
            from core.billing import sync_subscription_quantity

            sync_subscription_quantity(request.tenant)

        return Response({"created": created, "errors": errors})

    @action(detail=False, methods=["get"], url_path="import-csv-template")
    def import_csv_template(self, request):
        """
        Beispiel-CSV zum Download für import_csv.
        """
        response, writer = csv_response("mitarbeitende-vorlage.csv")
        writer.writerow(["first_name", "last_name", "employment_pct", "employment_start_date", "station"])
        writer.writerow(["Anna", "Muster", "100", "2026-01-01", "Pflege Tag"])
        writer.writerow(["Peter", "Beispiel", "80", "", ""])
        return response


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
        qs = apply_node_scope(qs, node_ids)
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
        qs = apply_node_scope(qs, node_ids)
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
        with translate_model_validation_error():
            first, second = ShiftAssignment.swap(first_id, second_id)
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

    Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
    aufgebaut sein wie Zeiterfassung" -- ?search=/?ordering=/?node=/?status=
    sowie stationsbasiertes Scoping (analog TimeRecordViewSet) erlauben jetzt
    eine stationsübergreifende, durchsuch-/sortierbare "Zu genehmigen"/"Alle"-
    Übersicht (siehe AbsenceOverview.jsx) statt Station für Station manuell
    nachzuschauen. Schliesst nebenbei eine Lücke: Planer/HR mit
    Membership.scoped_nodes sahen bisher trotzdem immer den ganzen Tenant --
    inkonsistent zu allen anderen stationsbezogenen Ressourcen.
    """

    permission_classes = [permissions.IsAuthenticated, OwnEmployeeRecordPermission]
    queryset = Absence.all_objects.all()
    serializer_class = AbsenceSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["employee__first_name", "employee__last_name"]
    ordering_fields = ["start_date", "employee__last_name", "status"]
    ordering = ["-start_date"]

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee", "type").prefetch_related("employee__nodes")
        employee = self.request.query_params.get("employee")
        if employee:
            qs = qs.filter(employee_id=employee)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        # Employee.nodes ist M2M -- ohne .distinct() würde eine Person mit
        # mehreren, alle im Scope liegenden Stationen mehrfach auftauchen.
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            node_filter = Q(employee__nodes__id__in=node_ids)
            employee_profile = self.request.employee_profile
            if self.request.membership.role == Membership.Role.EMPLOYEE and employee_profile:
                # Eigene Absenzen bleiben immer sichtbar/verwaltbar, auch wenn
                # dem eigenen Employee-Profil (noch) keine Station zugeordnet
                # ist -- sonst würde get_object() für z.B. approve()/reject()/
                # delete() auf die eigene Absenz 404 statt 403 liefern, weil
                # DRF Objektberechtigungen erst NACH dem Queryset-Filter prüft.
                node_filter |= Q(employee_id=employee_profile.id)
            qs = qs.filter(node_filter).distinct()
        node = self.request.query_params.get("node")
        if node:
            qs = qs.filter(employee__nodes__id=node).distinct()
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
        with translate_model_validation_error():
            absence.approve()
        notify_absence_decision(absence)
        return Response(self.get_serializer(absence).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        absence = self.get_object()
        with translate_model_validation_error():
            absence.reject()
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

    Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
    aufgebaut sein wie Zeiterfassung" -- ?search=/?ordering=/?node=/?open=
    sowie stationsbasiertes Scoping (analog TimeRecordViewSet) erlauben jetzt
    eine stationsübergreifende "Offen"/"Alle"-Übersicht (siehe
    TradeRequestOverview.jsx). Beim Scoping ein wichtiger Sonderfall für die
    Mitarbeiter-Rolle: eine an sie persönlich adressierte Anfrage
    (target_employee) oder eine von ihnen selbst angebotene (requester_
    assignment.employee) bleibt IMMER sichtbar, auch wenn die Station der
    anbietenden Schicht ausserhalb des eigenen Stations-Scopes liegt --
    sonst könnte die Zielperson eine an sie adressierte Anfrage weder sehen
    noch über accept/decline (siehe ShiftTradeRequestPermission.
    has_object_permission) darauf reagieren, weil get_object() vorher 404
    liefern würde.
    """

    permission_classes = [permissions.IsAuthenticated, ShiftTradeRequestPermission]
    queryset = ShiftTradeRequest.all_objects.all()
    serializer_class = ShiftTradeRequestSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "requester_assignment__employee__first_name",
        "requester_assignment__employee__last_name",
        "target_employee__first_name",
        "target_employee__last_name",
        "requester_assignment__node__name",
    ]
    ordering_fields = [
        "requester_assignment__date",
        "requester_assignment__employee__last_name",
        "requester_assignment__node__name",
        "status",
        "created_at",
    ]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset().select_related(
            "requester_assignment",
            "requester_assignment__employee",
            "requester_assignment__node",
            "requester_assignment__template",
            "target_employee",
            "target_assignment",
            "target_assignment__template",
        )
        if self.request.query_params.get("open") == "true":
            qs = qs.filter(
                status__in=[ShiftTradeRequest.Status.PENDING, ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED]
            )
        node_ids = _employee_scoped_node_ids(self.request.membership, self.request.employee_profile)
        if node_ids is not None:
            node_filter = Q(requester_assignment__node_id__in=node_ids)
            employee_profile = self.request.employee_profile
            if self.request.membership.role == Membership.Role.EMPLOYEE and employee_profile:
                node_filter |= Q(requester_assignment__employee_id=employee_profile.id)
                node_filter |= Q(target_employee_id=employee_profile.id)
            qs = qs.filter(node_filter)
        node = self.request.query_params.get("node")
        if node:
            qs = qs.filter(requester_assignment__node_id=node)
        return qs

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
        with translate_model_validation_error():
            trade_request.accept()
        notify_trade_accepted_by_employee(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Admin/Planer-Freigabe -- vollzieht den Tausch (siehe ShiftTradeRequest.approve())."""
        trade_request = self.get_object()
        with translate_model_validation_error():
            trade_request.approve()
        notify_trade_decision(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Admin/Planer lehnt ab (zu unterscheiden von `decline`, das die Zielperson selbst auslöst)."""
        trade_request = self.get_object()
        with translate_model_validation_error():
            trade_request.reject()
        notify_trade_decision(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        """Zielperson lehnt selbst ab (zu unterscheiden von `reject`, das Admin/Planer auslöst)."""
        trade_request = self.get_object()
        with translate_model_validation_error():
            trade_request.decline()
        notify_trade_declined(trade_request)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Anbietende Person zieht die Anfrage selbst zurück."""
        trade_request = self.get_object()
        with translate_model_validation_error():
            trade_request.cancel()
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
        qs = apply_node_scope(qs, node_ids, field_lookup="assignment__node_id")
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
        with translate_model_validation_error():
            time_record.confirm()
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
        qs = apply_node_scope(qs, node_ids)
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

        year, month = parse_year_month_param(request)
        category_lookup = self._build_category_lookup(tenant)
        result_employees, warnings = self._build_employee_lines(tenant, year, month, category_lookup)

        if request.query_params.get("output") == "csv":
            return self._csv_response(year, month, result_employees)

        return Response(
            {"year": year, "month": month, "employees": result_employees, "warnings": sorted(warnings)}
        )

    def _build_category_lookup(self, tenant):
        # Nutzer-Feedback (2026-08): "deaktivierte Kategorien gelten als
        # bewusst ausgeschlossen" -- eine Kategorie mit is_active=False hat
        # der Admin absichtlich abgewählt (z. B. "Sonntagszuschlag lösen wir
        # anders") und braucht deshalb KEINE Warnung, anders als eine nie
        # konfigurierte Kategorie (mögliches Versehen). `mappings` enthält
        # deshalb bewusst ALLE Zeilen (aktiv + inaktiv): configured_*
        # entscheidet über die Warnung, active_by_* über die tatsächliche
        # Code-Zuordnung.
        mappings = list(PayrollCategoryMapping.all_objects.filter(tenant=tenant))
        return {
            "active_by_category": {m.category: m for m in mappings if m.category and m.is_active},
            "active_by_template": {
                m.special_template_id: m for m in mappings if m.special_template_id and m.is_active
            },
            "active_by_absence_type": {
                m.absence_type_id: m for m in mappings if m.absence_type_id and m.is_active
            },
            "configured_categories": {m.category for m in mappings if m.category},
            "configured_templates": {m.special_template_id for m in mappings if m.special_template_id},
            "configured_absence_types": {m.absence_type_id for m in mappings if m.absence_type_id},
            "special_template_names": {
                t.id: t.name
                for t in TimeTemplate.all_objects.filter(tenant=tenant, category=TimeTemplate.Category.SPECIAL)
            },
            "absence_type_names": {t.id: t.name for t in AbsenceType.all_objects.filter(tenant=tenant)},
        }

    def _build_employee_lines(self, tenant, year, month, category_lookup):
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
                    mapping = category_lookup["active_by_category"].get(raw["category"])
                    label = PayrollCategoryMapping.Category(raw["category"]).label
                    is_configured = raw["category"] in category_lookup["configured_categories"]
                elif raw["special_template_id"]:
                    mapping = category_lookup["active_by_template"].get(raw["special_template_id"])
                    label = category_lookup["special_template_names"].get(raw["special_template_id"], "?")
                    is_configured = raw["special_template_id"] in category_lookup["configured_templates"]
                else:
                    mapping = category_lookup["active_by_absence_type"].get(raw["absence_type_id"])
                    label = category_lookup["absence_type_names"].get(raw["absence_type_id"], "?")
                    is_configured = raw["absence_type_id"] in category_lookup["configured_absence_types"]
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
        return result_employees, warnings

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
        response, writer = csv_response(f"lohn-export-{year}-{month:02d}.csv")
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
        year, month = parse_year_month_param(request)

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
        response, writer = csv_response(f"plan-export-{year}-{month:02d}.csv")
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


def _resolve_plan_scope(request, node_param):
    """
    Löst Tenant/Ziel-Node/Berechtigungs-Scope für einen Automatisierte-
    Planung-Lauf auf -- identisches Muster wie PlanExportView.get()
    (scope_ids = Station + direkte Team-Kinder, _employee_scoped_node_ids-
    Schnittmengen-Check), gemeinsam genutzt von GeneratePlanView und
    CommitPlanView.
    """
    tenant = request.tenant
    if tenant is None:
        raise PermissionDenied("Kein aktiver Tenant.")
    if not node_param:
        raise ValidationError({"node": "Pflichtfeld."})

    target = Node.all_objects.filter(tenant=tenant, pk=node_param).first()
    if target is None:
        raise ValidationError({"node": "Unbekannte Station."})
    scope_ids = [target.id] + [c.id for c in target.get_children()]

    employee_profile = Employee.all_objects.filter(tenant=tenant, user=request.user).first()
    allowed_ids = _employee_scoped_node_ids(request.membership, employee_profile)
    if allowed_ids is not None and not (set(scope_ids) & set(allowed_ids)):
        raise PermissionDenied("Keine Berechtigung für diese Station.")

    return tenant, target, scope_ids


class GeneratePlanView(TenantScopedAPIMixin, APIView):
    """
    README Block 2 Punkt 19 (Automatisierte Planung): erzeugt einen
    CP-SAT-Entwurf für eine Station/einen Monat (scheduling.planning.
    generate_draft_plan()) -- liest ausschliesslich, schreibt nichts
    ("Vorschau statt Blindautomatik", siehe Modul-Docstring von
    scheduling/planning.py). Antwort ist immer HTTP 200, auch bei
    Unlösbarkeit -- ein unvollständiger Entwurf ist ein normales Ergebnis,
    kein Fehler.

    Admin/Planer-only wie PayrollExportView (kein Selbstauskunfts-
    Endpoint, sondern stationsweite Massendaten über alle Mitarbeitenden).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = request.membership
        if not membership or membership.role not in (Membership.Role.ADMIN, Membership.Role.PLANNER):
            raise PermissionDenied("Nur Admin/Planer dürfen die automatisierte Planung starten.")

        node_param = request.query_params.get("node")
        year, month = parse_year_month_param(request)
        tenant, target, scope_ids = _resolve_plan_scope(request, node_param)

        result = planning.generate_draft_plan(tenant, scope_ids, year, month)

        return Response(
            {
                "status": result.status,
                "solver_status": result.solver_status,
                "assignments": [
                    {
                        "employee_id": a.employee_id,
                        "date": a.date.isoformat(),
                        "template_id": a.template_id,
                        "node_id": a.node_id,
                    }
                    for a in result.assignments
                ],
                "warnings": result.warnings,
                "shortfalls": result.shortfalls,
            }
        )


class CommitPlanView(TenantScopedAPIMixin, APIView):
    """
    README Block 2 Punkt 19: persistiert eine (vom Planer ggf. reduzierte)
    Liste von Entwurfs-Zuweisungen aus GeneratePlanView --
    scheduling.planning.commit_draft_assignments() validiert und speichert
    jede Zeile einzeln (eigener Savepoint, siehe dessen Docstring), damit
    eine einzelne zwischenzeitlich kollidierende Zeile nicht die ganze
    Übernahme verhindert.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        membership = request.membership
        if not membership or membership.role not in (Membership.Role.ADMIN, Membership.Role.PLANNER):
            raise PermissionDenied("Nur Admin/Planer dürfen die automatisierte Planung übernehmen.")

        node_param = request.data.get("node")
        tenant, target, scope_ids = _resolve_plan_scope(request, node_param)

        raw_assignments = request.data.get("assignments")
        if not isinstance(raw_assignments, list):
            raise ValidationError({"assignments": "Pflichtfeld, erwartet eine Liste."})

        specs = []
        for entry in raw_assignments:
            try:
                specs.append(
                    {
                        "employee_id": int(entry["employee_id"]),
                        "node_id": int(entry["node_id"]),
                        "date": date.fromisoformat(entry["date"]),
                        "template_id": int(entry["template_id"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                raise ValidationError({"assignments": f"Ungültiger Eintrag: {entry!r}."})

        created, skipped = planning.commit_draft_assignments(tenant, specs)

        return Response(
            {
                "created": ShiftAssignmentSerializer(created, many=True).data,
                "skipped": [
                    {
                        "employee_id": s["employee_id"],
                        "node_id": s["node_id"],
                        "date": s["date"].isoformat(),
                        "template_id": s["template_id"],
                        "error": s["error"],
                    }
                    for s in skipped
                ],
            }
        )


class EmployeeDataExportView(TenantScopedAPIMixin, APIView):
    """
    README Block 5 (Datenschutz & Rechtliches, revDSG): Umsetzung des Auskunftsrechts
    (Art. 25 revDSG) und des Rechts auf Datenherausgabe/-übertragung (Art. 28 revDSG) --
    liefert ALLE personenbezogenen Daten des eingeloggten Users als JSON. Ausschliesslich
    Selbstauskunft (request.user, keine Query-Parameter für eine andere Person) -- anders als
    PayrollExportView (Admin-only, alle Mitarbeitenden) ist das hier für JEDE Rolle offen, weil
    jede Person ein Recht auf Auskunft über die eigenen Daten hat, unabhängig von ihrer Rolle im
    Tenant.

    Reine Selbstauskunft, kein CSV-Modus wie bei Payroll-/Plan-Export -- der Zweck ist "meine
    eigenen Daten einsehen/mitnehmen", nicht "Daten an ein externes System übergeben", JSON allein
    genügt dafür (Frontend löst den Datei-Download client-seitig über denselben Blob-Mechanismus
    wie downloadPayrollExportCsv/downloadPlanExport aus, siehe api.js).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        membership = request.membership
        user = request.user

        employee = None
        if membership:
            employee = Employee.all_objects.filter(tenant=membership.tenant, user=user).first()

        data = {
            "exported_at": timezone.now().isoformat(),
            "account": {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "date_joined": user.date_joined.isoformat() if user.date_joined else None,
            },
            "membership": (
                {"role": membership.role, "tenant_name": membership.tenant.name} if membership else None
            ),
            "employee": None,
            "absences": [],
            "shift_assignments": [],
            "time_records": [],
            "shift_preferences": [],
            "pregnancies": [],
        }

        if employee is not None:
            data["employee"] = {
                "first_name": employee.first_name,
                "last_name": employee.last_name,
                "birth_date": employee.birth_date.isoformat() if employee.birth_date else None,
                "employment_pct": employee.employment_pct,
                "employment_start_date": employee.employment_start_date.isoformat(),
                "termination_date": (
                    employee.termination_date.isoformat() if employee.termination_date else None
                ),
                "nodes": [n.name for n in employee.nodes.all()],
                "skills": [s.name for s in employee.skills.all()],
            }
            data["absences"] = AbsenceSerializer(
                Absence.all_objects.filter(employee=employee), many=True
            ).data
            data["shift_assignments"] = ShiftAssignmentSerializer(
                ShiftAssignment.all_objects.filter(employee=employee), many=True
            ).data
            data["time_records"] = TimeRecordSerializer(
                TimeRecord.all_objects.filter(assignment__employee=employee), many=True
            ).data
            data["shift_preferences"] = ShiftPreferenceSerializer(
                ShiftPreference.all_objects.filter(employee=employee), many=True
            ).data
            data["pregnancies"] = PregnancySerializer(
                Pregnancy.all_objects.filter(employee=employee), many=True
            ).data

        return Response(data)

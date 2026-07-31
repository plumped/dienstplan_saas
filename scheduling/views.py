from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from core.context import set_current_tenant
from core.models import Membership
from core.permissions import (
    IsTenantManager,
    OwnEmployeeRecordPermission,
    ShiftTradeRequestPermission,
    TimeRecordPermission,
)
from core.tenancy import resolve_membership_for_user

from .models import Absence, Employee, Node, ShiftAssignment, ShiftTradeRequest, Skill, TimeRecord, TimeTemplate
from .serializers import (
    AbsenceSerializer,
    EmployeeBalanceSerializer,
    EmployeeSerializer,
    NodeSerializer,
    ShiftAssignmentSerializer,
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
    """
    membership = request.membership
    if not membership or membership.role != Membership.Role.EMPLOYEE:
        return None
    employee_profile = request.employee_profile
    if not employee_profile:
        return []
    return list(employee_profile.nodes.values_list("id", flat=True))


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


class SkillViewSet(TenantScopedViewSet):
    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = Skill.all_objects.all()
    serializer_class = SkillSerializer


class EmployeeViewSet(TenantScopedViewSet):
    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = Employee.all_objects.all()
    serializer_class = EmployeeSerializer

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

    @action(detail=True, methods=["get"])
    def balance(self, request, pk=None):
        """
        Saldo-Übersicht (MVP-Fahrplan Block 2.7): kumulierter Überstunden-
        Saldo inkl. overtime_is_provisional-Flag (Employee.overtime_summary)
        + Feriensaldo für ein Kalenderjahr (Employee.vacation_balance).
        ?as_of=YYYY-MM-DD (Default heute)
        bestimmt sowohl den Stichtag für den Überstunden-Saldo als auch,
        falls ?year nicht gesetzt ist, das Ferienjahr. Lesen wie bei
        weekly_overtime für alle Rollen offen, nicht nur für den betroffenen
        Mitarbeiter selbst -- Admin/Planer sollen dieselbe Ansicht auch für
        andere Mitarbeitende sehen können.
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

        overtime = employee.overtime_summary(as_of_date)
        vacation = employee.vacation_balance(year)
        data = {
            "as_of": as_of_date,
            "overtime_balance_hours": overtime["balance_hours"],
            "overtime_is_provisional": overtime["is_provisional"],
            "vacation_year": vacation["year"],
            "vacation_entitlement_days": vacation["entitlement_days"],
            "vacation_used_days": vacation["used_days"],
            "vacation_remaining_days": vacation["remaining_days"],
        }
        return Response(EmployeeBalanceSerializer(data).data)


class TimeTemplateViewSet(TenantScopedViewSet):
    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = TimeTemplate.all_objects.all()
    serializer_class = TimeTemplateSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        node_ids = _employee_scoped_node_ids(self.request)
        if node_ids is not None:
            qs = qs.filter(node_id__in=node_ids)
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
            qs = qs.filter(node_id=node)
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs


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
        serializer.save(
            tenant=self.request.tenant,
            status=Absence.Status.APPROVED if is_manager else Absence.Status.PENDING,
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        absence = self.get_object()
        if absence.status != Absence.Status.PENDING:
            raise ValidationError("Nur offene Absenzanträge können genehmigt werden.")
        absence.status = Absence.Status.APPROVED
        absence.save(update_fields=["status"])
        return Response(self.get_serializer(absence).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        absence = self.get_object()
        if absence.status != Absence.Status.PENDING:
            raise ValidationError("Nur offene Absenzanträge können abgelehnt werden.")
        absence.status = Absence.Status.REJECTED
        absence.save(update_fields=["status"])
        return Response(self.get_serializer(absence).data)


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

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Zielperson stimmt zu -- vollzieht den Tausch noch nicht, siehe `approve`."""
        trade_request = self.get_object()
        try:
            trade_request.accept()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Admin/Planer-Freigabe -- vollzieht den Tausch (siehe ShiftTradeRequest.approve())."""
        trade_request = self.get_object()
        try:
            trade_request.approve()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Admin/Planer lehnt ab (zu unterscheiden von `decline`, das die Zielperson selbst auslöst)."""
        trade_request = self.get_object()
        try:
            trade_request.reject()
        except DjangoValidationError as e:
            raise ValidationError(e.message_dict if hasattr(e, "message_dict") else e.messages)
        return Response(self.get_serializer(trade_request).data)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        trade_request = self.get_object()
        if trade_request.status != ShiftTradeRequest.Status.PENDING:
            raise ValidationError("Nur offene Tauschanfragen können abgelehnt werden.")
        trade_request.status = ShiftTradeRequest.Status.DECLINED
        trade_request.resolved_at = timezone.now()
        trade_request.save(update_fields=["status", "resolved_at"])
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

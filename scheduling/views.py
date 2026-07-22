from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from core.context import set_current_tenant
from core.models import Membership
from core.permissions import IsTenantManager, OwnEmployeeRecordPermission, ShiftTradeRequestPermission
from core.tenancy import resolve_membership_for_user

from .models import Absence, Employee, Node, ShiftAssignment, ShiftTradeRequest, Skill, TimeTemplate
from .serializers import (
    AbsenceSerializer,
    EmployeeSerializer,
    NodeSerializer,
    ShiftAssignmentSerializer,
    ShiftTradeRequestSerializer,
    SkillSerializer,
    TimeTemplateSerializer,
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


class TimeTemplateViewSet(TenantScopedViewSet):
    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = TimeTemplate.all_objects.all()
    serializer_class = TimeTemplateSerializer


class ShiftAssignmentViewSet(TenantScopedViewSet):
    """
    Unterstützt ?node=<id>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD, um genau
    das Planblatt-Grid (ein Knoten, ein Monat) effizient zu laden.

    Schreiben ist Admin/Planer vorbehalten -- Mitarbeitende sehen den ganzen
    Plan (Transparenz), ändern ihn aber nicht direkt, sondern nur über einen
    genehmigten Diensttausch (ShiftTradeRequestViewSet).
    """

    permission_classes = [permissions.IsAuthenticated, IsTenantManager]
    queryset = ShiftAssignment.all_objects.all()
    serializer_class = ShiftAssignmentSerializer

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee", "template", "node")
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

    Admin/Planer dürfen Absenzen für jeden anlegen/ändern/löschen.
    Mitarbeitende dürfen nur für sich selbst (request.employee_profile)
    schreiben -- durchgesetzt hier in perform_create (das Objekt existiert
    bei create noch nicht, deshalb reicht has_object_permission allein
    nicht) sowie über OwnEmployeeRecordPermission.has_object_permission für
    update/destroy. HR ist aussen vor (nur Reporting).
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
        if self.request.membership.role == Membership.Role.EMPLOYEE:
            target_employee = serializer.validated_data.get("employee")
            employee_profile = self.request.employee_profile
            if not employee_profile or target_employee.id != employee_profile.id:
                raise PermissionDenied("Mitarbeitende dürfen nur eigene Absenzen anlegen.")
        super().perform_create(serializer)


class ShiftTradeRequestViewSet(TenantScopedViewSet):
    """
    Diensttausch-Anfragen (Abschnitt 7). Die eigentliche Umsetzung des
    Tauschs läuft über die Custom-Action `accept`, nicht über ein PATCH auf
    `status`, damit die volle Regel-Engine (siehe ShiftTradeRequest.accept())
    dabei zwingend durchlaufen wird statt sich auf Client-Disziplin zu
    verlassen.

    Admin/Planer dürfen alles. Mitarbeitende dürfen nur eigene Schichten zum
    Tausch anbieten (geprüft in perform_create) und über die Actions
    reagieren -- `cancel` als anbietende Person, `accept`/`decline` als
    Zielperson (geprüft in ShiftTradeRequestPermission.has_object_permission,
    da get_object() in jeder Action aufgerufen wird).
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
        trade_request = self.get_object()
        try:
            trade_request.accept()
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
        if trade_request.status != ShiftTradeRequest.Status.PENDING:
            raise ValidationError("Nur offene Tauschanfragen können zurückgezogen werden.")
        trade_request.status = ShiftTradeRequest.Status.CANCELLED
        trade_request.resolved_at = timezone.now()
        trade_request.save(update_fields=["status", "resolved_at"])
        return Response(self.get_serializer(trade_request).data)

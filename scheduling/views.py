from rest_framework import permissions, viewsets
from rest_framework.exceptions import ValidationError

from core.context import set_current_tenant
from core.tenancy import resolve_tenant_for_user

from .models import Employee, Node, ShiftAssignment, Skill, TimeTemplate
from .serializers import (
    EmployeeSerializer,
    NodeSerializer,
    ShiftAssignmentSerializer,
    SkillSerializer,
    TimeTemplateSerializer,
)


class TenantScopedViewSet(viewsets.ModelViewSet):
    """
    Basis-ViewSet: filtert explizit über all_objects nach request.tenant,
    statt sich allein auf die ContextVar-Manager in core.models zu verlassen
    (doppelte Absicherung gegen Datenlecks zwischen Mandanten).

    request.tenant wird bewusst hier in initial() gesetzt, NACH
    super().initial() (das führt die DRF-Authentifizierung, also Token- oder
    Session-Login, aus) -- nicht in einer Django-Middleware, wo request.user
    bei Token-Logins noch nicht aufgelöst wäre. Siehe core/tenancy.py.
    """

    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant = resolve_tenant_for_user(request.user)
        request.tenant = tenant
        set_current_tenant(tenant)

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
    queryset = Skill.all_objects.all()
    serializer_class = SkillSerializer


class EmployeeViewSet(TenantScopedViewSet):
    queryset = Employee.all_objects.all()
    serializer_class = EmployeeSerializer


class TimeTemplateViewSet(TenantScopedViewSet):
    queryset = TimeTemplate.all_objects.all()
    serializer_class = TimeTemplateSerializer


class ShiftAssignmentViewSet(TenantScopedViewSet):
    """
    Unterstützt ?node=<id>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD, um genau
    das Planblatt-Grid (ein Knoten, ein Monat) effizient zu laden.
    """

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

from rest_framework import serializers

from .models import (
    Absence,
    Employee,
    Node,
    ShiftAssignment,
    ShiftTradeRequest,
    Skill,
    TimeTemplate,
)


class NodeSerializer(serializers.ModelSerializer):
    # Bewusst kein PrimaryKeyRelatedField(queryset=Node.objects...): der
    # TenantScopedManager würde die Queryset-Filterung beim Laden dieses
    # Moduls (Import-Zeit) einfrieren, also BEVOR ein Request-Tenant bekannt
    # ist -> das Feld würde nie tenant-gefiltert sein. Stattdessen wird die
    # ID hier nur roh entgegengenommen und in NodeViewSet.perform_create
    # explizit gegen request.tenant geprüft.
    parent = serializers.IntegerField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text="ID des übergeordneten Knotens. Leer lassen für einen Wurzelknoten (z. B. Standort).",
    )

    class Meta:
        model = Node
        fields = ["id", "name", "path", "depth", "parent"]
        read_only_fields = ["path", "depth"]


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name"]


class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = [
            "id",
            "first_name",
            "last_name",
            "employment_pct",
            "nodes",
            "skills",
            "is_active",
        ]


class TimeTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeTemplate
        fields = [
            "id",
            "node",
            "name",
            "start_time",
            "end_time",
            "break_minutes",
            "icon",
            "color",
            "required_skill",
        ]


class ShiftAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftAssignment
        fields = ["id", "employee", "node", "date", "template", "note"]

    def validate(self, attrs):
        """
        Baut eine (unsaved) Instanz mit den neuen + bestehenden Werten und
        ruft model.clean() auf, damit der Ruhezeit-Check aus dem Model auch
        über die API greift (nicht nur im Django Admin).
        """
        instance = self.instance or ShiftAssignment()
        for field in ["employee", "node", "date", "template"]:
            if field in attrs:
                setattr(instance, field, attrs[field])
        instance.tenant = self.context["request"].tenant
        instance.clean()
        return attrs


class AbsenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Absence
        fields = ["id", "employee", "start_date", "end_date", "type", "note"]

    def validate(self, attrs):
        instance = self.instance or Absence()
        for field in ["employee", "start_date", "end_date", "type"]:
            if field in attrs:
                setattr(instance, field, attrs[field])
        instance.clean()
        return attrs


class ShiftTradeRequestSerializer(serializers.ModelSerializer):
    # Bewusst all_objects (ungefiltert) als Feld-Queryset -- gleiches Muster
    # wie beim Ruhezeit-Check in ShiftAssignmentSerializer: die eigentliche
    # Tenant-Prüfung passiert explizit unten in validate(), nicht implizit
    # über eine (zur Importzeit eingefrorene) gefilterte Queryset.
    requester_assignment = serializers.PrimaryKeyRelatedField(queryset=ShiftAssignment.all_objects.all())
    target_employee = serializers.PrimaryKeyRelatedField(queryset=Employee.all_objects.all())
    target_assignment = serializers.PrimaryKeyRelatedField(
        queryset=ShiftAssignment.all_objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = ShiftTradeRequest
        fields = [
            "id",
            "requester_assignment",
            "target_employee",
            "target_assignment",
            "status",
            "note",
            "created_at",
            "resolved_at",
        ]
        read_only_fields = ["status", "created_at", "resolved_at"]

    def validate(self, attrs):
        tenant = self.context["request"].tenant
        for field_name in ("requester_assignment", "target_employee", "target_assignment"):
            obj = attrs.get(field_name)
            if obj is not None and obj.tenant_id != tenant.id:
                raise serializers.ValidationError({field_name: "Gehört nicht zu diesem Tenant."})

        instance = self.instance or ShiftTradeRequest()
        for field in ["requester_assignment", "target_employee", "target_assignment"]:
            if field in attrs:
                setattr(instance, field, attrs[field])
        instance.tenant = tenant
        instance.clean()
        return attrs

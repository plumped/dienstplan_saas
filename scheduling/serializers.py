from rest_framework import serializers

from .models import (
    Absence,
    Employee,
    Node,
    ShiftAssignment,
    ShiftTradeRequest,
    Skill,
    TimeRecord,
    TimeRecordSegment,
    TimeTemplate,
    TimeTemplateSegment,
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
            "birth_date",
            "employment_pct",
            "nodes",
            "skills",
            "is_active",
        ]


class TimeTemplateSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeTemplateSegment
        fields = ["id", "order", "start_time", "end_time"]
        read_only_fields = ["id"]
        extra_kwargs = {"order": {"required": False}}


class TimeTemplateSerializer(serializers.ModelSerializer):
    # Block 1.12: optionale Blockstruktur (z. B. Vormittag/Nachmittag mit
    # fixer Mittagspause dazwischen). Ein Template ohne Segmente verhält
    # sich weiterhin wie bisher (ein Zeitfenster + break_minutes pauschal,
    # siehe TimeTemplate.effective_segments()). Wird bei jedem Schreiben
    # vollständig ersetzt (delete+recreate in create()/update()), da die
    # Liste kurz ist und Umsortieren/Diffen keinen echten Nutzen bringt.
    segments = TimeTemplateSegmentSerializer(many=True, required=False)

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
            "segments",
        ]

    def validate_segments(self, value):
        ordered = sorted(value, key=lambda s: s.get("order", 0))
        for i in range(len(ordered) - 1):
            if ordered[i]["end_time"] > ordered[i + 1]["start_time"]:
                raise serializers.ValidationError(
                    "Segmente dürfen sich nicht überlappen und müssen chronologisch geordnet sein "
                    "(Segmente über Mitternacht werden aktuell nicht unterstützt)."
                )
        return value

    def create(self, validated_data):
        segments_data = validated_data.pop("segments", None)
        instance = TimeTemplate.objects.create(**validated_data)
        if segments_data:
            self._sync_segments(instance, segments_data)
        return instance

    def update(self, instance, validated_data):
        segments_data = validated_data.pop("segments", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if segments_data is not None:
            self._sync_segments(instance, segments_data)
        return instance

    @staticmethod
    def _sync_segments(instance, segments_data):
        instance.segments.all().delete()
        TimeTemplateSegment.objects.bulk_create(
            TimeTemplateSegment(
                template=instance,
                tenant=instance.tenant,
                order=seg.get("order", i),
                start_time=seg["start_time"],
                end_time=seg["end_time"],
            )
            for i, seg in enumerate(sorted(segments_data, key=lambda s: s.get("order", 0)))
        )


class ShiftAssignmentSerializer(serializers.ModelSerializer):
    # Informativ, nicht Teil der Validierung -- siehe ShiftAssignment.clean()
    # Docstring: Nacht-/Sonntagsarbeit werden erkannt statt blockiert, damit
    # Planer und eine spätere Lohnauswertung Zuschläge/Ersatzruhetage
    # berücksichtigen können.
    night_hours = serializers.FloatField(read_only=True)
    is_sunday = serializers.BooleanField(read_only=True)

    class Meta:
        model = ShiftAssignment
        fields = ["id", "employee", "node", "date", "template", "note", "night_hours", "is_sunday"]

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
        fields = ["id", "employee", "start_date", "end_date", "type", "status", "note"]
        # status wird nicht direkt gesetzt, sondern über perform_create
        # (Admin/Planer -> sofort APPROVED, sonst PENDING) bzw. die
        # approve/reject-Actions (siehe AbsenceViewSet, Block 2.3).
        read_only_fields = ["status"]

    def validate(self, attrs):
        instance = self.instance or Absence()
        for field in ["employee", "start_date", "end_date", "type"]:
            if field in attrs:
                setattr(instance, field, attrs[field])
        instance.clean()
        return attrs


class TimeRecordSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeRecordSegment
        fields = ["id", "order", "actual_start", "actual_end"]
        read_only_fields = ["id"]
        extra_kwargs = {"order": {"required": False}}


class TimeRecordSerializer(serializers.ModelSerializer):
    # Informativ, berechnet aus assignment.template -- siehe TimeRecord-Docstring.
    deviation_minutes = serializers.IntegerField(read_only=True)
    end_deviation_minutes = serializers.IntegerField(read_only=True)
    actual_hours = serializers.FloatField(read_only=True)
    break_minutes_total = serializers.IntegerField(read_only=True)
    break_below_minimum = serializers.BooleanField(read_only=True)
    # Block 1.12: bei Templates mit Segmenten (siehe TimeTemplate.segments)
    # wird hierüber pro Block eine Ist-Zeit erfasst, statt der klassischen
    # actual_start/actual_end/actual_break_minutes-Felder (die dann leer
    # bleiben). Anzahl/Reihenfolge sind vom Template vorgegeben -- geprüft in
    # TimeRecord.clean(), nicht hier im Serializer.
    segments = TimeRecordSegmentSerializer(many=True, required=False)

    class Meta:
        model = TimeRecord
        fields = [
            "id",
            "assignment",
            "actual_start",
            "actual_end",
            "actual_break_minutes",
            "note",
            "status",
            "recorded_by",
            "recorded_at",
            "deviation_minutes",
            "end_deviation_minutes",
            "actual_hours",
            "break_minutes_total",
            "break_below_minimum",
            "segments",
        ]
        # status/recorded_by/recorded_at werden nicht direkt gesetzt, sondern
        # über TimeRecordViewSet.perform_create (recorded_by) bzw. die
        # confirm-Action (status) gesteuert (siehe Block 1.9 im README).
        read_only_fields = ["status", "recorded_by", "recorded_at"]
        extra_kwargs = {
            "actual_start": {"required": False},
            "actual_end": {"required": False},
        }

    def validate(self, attrs):
        # segments wird separat behandelt (nested write, siehe create/update)
        # -- nicht Teil der einfachen setattr-Schleife wie die Skalarfelder.
        segments_data = attrs.get("segments")
        instance = self.instance or TimeRecord()
        for field in ["assignment", "actual_start", "actual_end", "actual_break_minutes", "note"]:
            if field in attrs:
                setattr(instance, field, attrs[field])
        instance.tenant = self.context["request"].tenant
        # "segments" fehlt im Payload (PATCH ohne Zeitänderung) -> None ->
        # TimeRecord.effective_segments() greift auf bereits gespeicherte
        # Segmente zurück statt auf einen leeren Pending-Zustand.
        instance._pending_segments = segments_data
        instance.clean()
        return attrs

    def create(self, validated_data):
        segments_data = validated_data.pop("segments", None)
        instance = TimeRecord.objects.create(**validated_data)
        if segments_data:
            self._sync_segments(instance, segments_data)
        return instance

    def update(self, instance, validated_data):
        segments_data = validated_data.pop("segments", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if segments_data is not None:
            self._sync_segments(instance, segments_data)
        return instance

    @staticmethod
    def _sync_segments(instance, segments_data):
        instance.segments.all().delete()
        TimeRecordSegment.objects.bulk_create(
            TimeRecordSegment(
                time_record=instance,
                tenant=instance.tenant,
                order=seg.get("order", i),
                actual_start=seg["actual_start"],
                actual_end=seg["actual_end"],
            )
            for i, seg in enumerate(sorted(segments_data, key=lambda s: s.get("order", 0)))
        )


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

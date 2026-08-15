from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator
from rest_framework import serializers

from core.models import Membership
from core.serializers import validate_unique_username

User = get_user_model()

from .models import (
    Absence,
    AbsenceType,
    Employee,
    Employment,
    Node,
    PayrollCategoryMapping,
    Pregnancy,
    ShiftAssignment,
    ShiftPreference,
    ShiftTradeRequest,
    Skill,
    TimeRecord,
    TimeRecordSegment,
    TimeTemplate,
    TimeTemplateSegment,
)


def pop_m2m_fields(model, validated_data):
    """
    Trennt ManyToMany-Feldwerte aus validated_data heraus, BEVOR sie an
    Model.objects.create(**validated_data) oder eine generische
    setattr(instance, field, value)-Schleife weitergereicht werden -- beide
    Wege akzeptieren M2M-Werte nicht direkt und würden mit
    `TypeError: Direct assignment to the forward side of a many-to-many set
    is prohibited. Use <feld>.set() instead.` abbrechen (Django-Modelle
    erlauben M2M-Zuweisung nur über den Manager, nie per Konstruktor-Kwarg
    oder Attribut-Zuweisung).

    Wird `model._meta.many_to_many` befragt statt eine feste Feldliste zu
    pflegen, damit jedes künftige M2M-Feld auf diesem Modell automatisch
    sicher behandelt wird -- ein Serializer mit eigener create()/update()-
    Logik muss dafür nicht mehr wissen, welche seiner Felder M2M sind.
    Rückgabe: (validated_data ohne M2M-Einträge, {feldname: wert, ...} der
    herausgetrennten M2M-Einträge, jeweils nur falls im Payload enthalten).

    Verwendung (Standard-Pattern für jeden ModelSerializer mit eigener
    create()/update()-Methode -- siehe EmployeeSerializer/
    TimeTemplateSerializer/TimeRecordSerializer unten):

        def create(self, validated_data):
            validated_data, m2m_data = pop_m2m_fields(Employee, validated_data)
            instance = Employee.objects.create(**validated_data)
            set_m2m_fields(instance, m2m_data)
            return instance

        def update(self, instance, validated_data):
            validated_data, m2m_data = pop_m2m_fields(Employee, validated_data)
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            set_m2m_fields(instance, m2m_data)
            return instance
    """
    m2m_field_names = {f.name for f in model._meta.many_to_many}
    m2m_data = {name: validated_data.pop(name) for name in list(validated_data) if name in m2m_field_names}
    return validated_data, m2m_data


def set_m2m_fields(instance, m2m_data):
    """
    Wendet die von pop_m2m_fields() abgetrennten M2M-Werte über die
    korrekte Relation-Methode (.set()) an -- erst NACHDEM instance
    gespeichert ist (M2M braucht einen Primärschlüssel). .set() statt
    .add()/.remove(), weil ein PATCH/PUT den kompletten Zielzustand des
    Feldes im Payload trägt (DRF-Konvention bei many=True-Feldern), nicht
    ein einzelnes Hinzufügen/Entfernen -- .set() synchronisiert das in
    einem Aufruf (überzählige Einträge werden entfernt, fehlende ergänzt).
    Ein Feld, das im Payload fehlt (z. B. bei einem partiellen PATCH ohne
    dieses Feld), taucht in m2m_data gar nicht erst auf und bleibt
    unangetastet.
    """
    for field_name, value in m2m_data.items():
        getattr(instance, field_name).set(value)


def build_instance_for_clean(serializer, attrs, model_cls, fields, set_tenant=False):
    """
    Baut eine (unsaved) Instanz für serializer.validate(): bei Update die
    bestehende Instanz (serializer.instance), bei Create ein leeres
    model_cls() -- setzt darauf die übergebenen `fields`, sofern im Payload
    vorhanden (`if field in attrs`), und optional den Tenant aus dem
    Request-Kontext. Ruft NICHT selbst instance.clean() auf, damit
    Aufrufer, die davor/danach noch eigene Logik brauchen (z. B. eine
    Status-Vorbelegung vor dem Absenz/Zuweisungs-Konflikt-Check), das
    weiterhin selbst steuern. Vorher an fünf Serializern (ShiftAssignment/
    Absence/ShiftPreference/TimeRecord/ShiftTradeRequest) identisch als
    "Instanz bauen + setattr-Schleife"-Gerüst wiederholt.
    """
    instance = serializer.instance or model_cls()
    for field in fields:
        if field in attrs:
            setattr(instance, field, attrs[field])
    if set_tenant:
        instance.tenant = serializer.context["request"].tenant
    return instance


class NestedWritableSerializerMixin:
    """
    create()/update() für einen ModelSerializer mit genau einem "Kind-Liste
    vollständig ersetzen"-Nested-Feld (z. B. employments/segments) --
    dasselbe Gerüst (M2M-Felder poppen, Nested-Feld poppen,
    Objekt anlegen/aktualisieren, M2M setzen, bei vorhandenen Daten
    synchronisieren) war zuvor in EmployeeSerializer/TimeTemplateSerializer/
    TimeRecordSerializer dreifach dupliziert. Erwartet auf der Subklasse:
    `_nested_field` (Name des Feldes in validated_data) und
    `_sync_nested(instance, nested_data)` (die model-eigene Sync-Logik,
    z. B. bestehende Kinder löschen + per bulk_create neu anlegen).
    """

    _nested_field = None

    def create(self, validated_data):
        model = self.Meta.model
        validated_data, m2m_data = pop_m2m_fields(model, validated_data)
        nested_data = validated_data.pop(self._nested_field, None)
        instance = model.objects.create(**validated_data)
        set_m2m_fields(instance, m2m_data)
        if nested_data:
            self._sync_nested(instance, nested_data)
        return instance

    def update(self, instance, validated_data):
        model = self.Meta.model
        validated_data, m2m_data = pop_m2m_fields(model, validated_data)
        nested_data = validated_data.pop(self._nested_field, None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        set_m2m_fields(instance, m2m_data)
        if nested_data is not None:
            self._sync_nested(instance, nested_data)
        return instance


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
    # MVP-Fahrplan Block 2, Punkt 30/31: effective_cost_center zeigt den
    # tatsächlich beim Lohn-Export verwendeten Wert (eigener oder von der
    # Station geerbt, siehe Node.effective_cost_center()) -- ohne dieses
    # Feld könnte ein Admin einer leeren cost_center nicht ansehen, ob damit
    # "keine Kostenstelle" oder "geerbt von X" gemeint ist.
    effective_cost_center = serializers.SerializerMethodField()

    class Meta:
        model = Node
        fields = ["id", "name", "path", "depth", "parent", "cost_center", "effective_cost_center"]
        read_only_fields = ["path", "depth", "effective_cost_center"]

    def get_effective_cost_center(self, obj):
        return obj.effective_cost_center()


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name"]


class EmploymentSerializer(serializers.ModelSerializer):
    """
    Eine einzelne Anstellung (README Punkt 17) -- immer nur verschachtelt
    unter EmployeeSerializer gelesen/geschrieben, kein eigener Endpoint
    (analog zu TimeTemplateSegment unter TimeTemplateSerializer). `node`
    bewusst ein rohes IntegerField statt PrimaryKeyRelatedField(queryset=...),
    aus demselben Grund wie NodeSerializer.parent: der
    TenantScopedManager-Queryset würde sonst zur Modul-Importzeit
    eingefroren. Die Tenant-/Existenz-Prüfung passiert explizit in
    EmployeeSerializer._sync_nested.
    """

    node = serializers.IntegerField(source="node_id")

    class Meta:
        model = Employment
        fields = ["id", "node", "pensum_pct", "title", "is_team_lead"]
        read_only_fields = ["id"]


class EmployeeSerializer(NestedWritableSerializerMixin, serializers.ModelSerializer):
    # README Punkt 17: Team-Mitgliedschaft läuft jetzt ausschliesslich über
    # employments (siehe _sync_nested) -- nodes wird daraus serverseitig
    # abgeleitet und ist nur noch lesbar, damit es genau einen Änderungsweg
    # gibt (kein Auseinanderlaufen zwischen employee.nodes und den
    # tatsächlichen Employment-Zeilen).
    nodes = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    employments = EmploymentSerializer(many=True, required=False)

    # Nutzer-Feedback (2026-08): "Es gibt nun Tab Mitarbeitende, Tab
    # Mitglieder und Zugriff [...] Das muss doch intuitiver gelöst werden?"
    # -- Login-Zugang (User+Membership, core-App) ist jetzt direkt am
    # Mitarbeitenden-Datensatz sichtbar/editierbar (EmployeeSettings.jsx)
    # statt in einem eigenen Tab. Diese vier Felder sind rein lesend (der
    # Schreibweg läuft über EmployeeViewSet.setup_access für die Erstanlage
    # bzw. weiterhin core.views.MembershipViewSet für Rollenwechsel/
    # scoped_nodes, siehe deren Docstrings) -- alle `None`/leer, solange
    # `employee.user` nicht gesetzt ist (Mitarbeiter ohne Login).
    username = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    membership_id = serializers.SerializerMethodField()
    scoped_nodes = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "id",
            "first_name",
            "last_name",
            "birth_date",
            "employment_pct",
            "employment_start_date",
            "termination_date",
            "nodes",
            "employments",
            "skills",
            "is_active",
            "maximum_weekly_hours",
            "standard_weekly_hours",
            "overtime_balance_carryover_hours",
            "vacation_days_per_year",
            "last_night_work_medical_exam_date",
            "username",
            "role",
            "membership_id",
            "scoped_nodes",
        ]

    def validate(self, attrs):
        # Abrechnung (README Block 6, 2026-08): Trial-Mitarbeiterlimit gegen
        # Missbrauch der kostenlosen Phase mit einem grossen Team -- gilt nur
        # für NEUE Mitarbeitende (self.instance is None) während TRIALING,
        # nicht für Updates bestehender Datensätze. Zentral hier statt in
        # EmployeeViewSet.perform_create, damit sowohl der normale POST-Weg
        # als auch der CSV-Import (EmployeeViewSet.import_csv, nutzt
        # denselben Serializer) mit einem einzigen Check abgedeckt sind.
        if self.instance is None:
            tenant = self.context["request"].tenant
            if (
                tenant is not None
                and tenant.subscription_status == tenant.SubscriptionStatus.TRIALING
                and tenant.active_employee_count() >= tenant.trial_employee_limit
            ):
                raise serializers.ValidationError(
                    f"In der Testphase sind maximal {tenant.trial_employee_limit} aktive "
                    "Mitarbeitende möglich -- für mehr bitte ein Abo abschliessen."
                )
        return attrs

    def _membership(self, obj):
        # Ein Lookup pro Objekt genügt für alle vier Felder -- da
        # EmployeeSerializer bislang nicht mit `select_related`/Prefetch auf
        # `user`/Membership optimiert ist (Stammdaten-Tabelle lädt ohnehin
        # serverseitig paginiert, siehe EmployeeViewSet-Docstring), ist ein
        # zusätzlicher Query pro Zeile hier kein neues Problem, nur eines,
        # das schon vorher für `user` selbst bestand.
        if not obj.user_id:
            return None
        tenant = self.context["request"].tenant
        return Membership.objects.filter(user_id=obj.user_id, tenant=tenant).first()

    def get_username(self, obj):
        return obj.user.username if obj.user_id else None

    def get_role(self, obj):
        membership = self._membership(obj)
        return membership.role if membership else None

    def get_membership_id(self, obj):
        membership = self._membership(obj)
        return membership.id if membership else None

    def get_scoped_nodes(self, obj):
        membership = self._membership(obj)
        return list(membership.scoped_nodes.values_list("id", flat=True)) if membership else []

    # Bugfix: `skills` (ManyToManyField) darf nicht als Konstruktor-Kwarg an
    # Employee.objects.create() durchgereicht werden -- siehe
    # pop_m2m_fields()-Docstring oben. `nodes` ist zwar ebenfalls M2M, aber
    # oben read_only deklariert und taucht daher nie in validated_data auf;
    # pop_m2m_fields() findet trotzdem nur, was tatsächlich vorhanden ist.
    # create()/update() kommen von NestedWritableSerializerMixin.
    _nested_field = "employments"

    def _sync_nested(self, instance, employments_data):
        tenant = self.context["request"].tenant
        node_ids = [e["node_id"] for e in employments_data]
        valid_node_ids = set(
            Node.all_objects.filter(tenant=tenant, pk__in=node_ids).values_list("id", flat=True)
        )
        invalid = set(node_ids) - valid_node_ids
        if invalid:
            raise serializers.ValidationError({"employments": "Ungültiger oder fremder Team-Knoten."})
        instance.employments.all().delete()
        Employment.objects.bulk_create(
            Employment(
                employee=instance,
                tenant=tenant,
                node_id=e["node_id"],
                pensum_pct=e["pensum_pct"],
                title=e.get("title", ""),
                is_team_lead=e.get("is_team_lead", False),
            )
            for e in employments_data
        )
        instance.nodes.set(node_ids)


class EmployeeAccessSetupSerializer(serializers.Serializer):
    """
    Validierung für EmployeeViewSet.setup_access (POST .../setup-access/) --
    Direktanlage eines Login-Zugangs für eine bereits bestehende Employee
    (Nutzer-Feedback 2026-08: "ein Ort, ein Formular pro Person" statt eines
    separaten "Mitglieder"-Tabs). Nur Validierung hier, keine eigene
    create()/save() -- die eigentliche Anlage (User+Membership+Verknüpfung)
    passiert in der View, weil sie zwei fremde Modelle (core.User/
    core.Membership) UND das Employee-Objekt selbst anfasst, was für einen
    einzelnen ModelSerializer keine saubere Zuständigkeit wäre.
    """

    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(choices=Membership.Role.choices)

    def validate_username(self, value):
        UnicodeUsernameValidator()(value)
        return validate_unique_username(value)


class EmployeeBalanceSerializer(serializers.Serializer):
    """
    Read-only: Arbeitszeitmodell (README Block 2.7 Punkt 7) -- kombiniert
    Employee.time_account_summary() (laufender Saldo + Jahresrestsoll) mit
    Employee.vacation_balance() (Feriensaldo, unverändertes älteres Modell).
    """

    as_of = serializers.DateField()
    saldo_hours = serializers.FloatField()
    # README (2026-08, Redesign): primäre Anzeige -- schliesst bereits
    # eingeplante künftige Zuweisungen bis Jahresende mit ein, siehe
    # Employee.time_account_summary()-Docstring.
    plan_saldo_hours = serializers.FloatField()
    annual_target_hours = serializers.FloatField()
    annual_remaining_hours = serializers.FloatField()
    # True, solange der Saldo mindestens eine Schicht ohne geprüfte
    # (CONFIRMED) Zeiterfassung enthält -- siehe Employee.time_account_summary().
    # Der Saldo ist trotzdem schon aktuell.
    is_provisional = serializers.BooleanField()
    vacation_year = serializers.IntegerField()
    vacation_entitlement_days = serializers.IntegerField()
    # Bugfix (2026-08, Nutzer-Feedback: "halbtags Ferien zieht einen ganzen
    # Tag ab"): used_days/remaining_days können seit den Halbtags-Absenzen
    # 0.5-Schritte enthalten (Employee.vacation_balance()) -- als
    # IntegerField wurde das beim Serialisieren stillschweigend zu int()
    # abgeschnitten (24.5 -> 24), wodurch die API einen vollen statt einen
    # halben Tag Abzug meldete, obwohl das Modell selbst korrekt rechnete.
    vacation_used_days = serializers.FloatField()
    vacation_remaining_days = serializers.FloatField()


class SickPaySummarySerializer(serializers.Serializer):
    """Read-only: Ergebnis von Employee.sick_pay_summary (Block 1 Punkt 16, Art. 324a OR)."""

    reference_date = serializers.DateField()
    service_year_number = serializers.IntegerField()
    service_year_start = serializers.DateField()
    service_year_end = serializers.DateField()
    used_days = serializers.FloatField()
    model = serializers.CharField()
    scale = serializers.CharField(allow_null=True)
    entitlement_weeks = serializers.IntegerField(allow_null=True)
    entitlement_days = serializers.IntegerField(allow_null=True)
    remaining_days = serializers.FloatField(allow_null=True)
    waiting_days = serializers.IntegerField(allow_null=True)


class WeeklyOvertimeSerializer(serializers.Serializer):
    """Read-only: Ergebnis von Employee.weekly_hours_summary (Block 1.11, Art. 13 ArG)."""

    week_start = serializers.DateField()
    week_end = serializers.DateField()
    soll_hours = serializers.FloatField()
    ist_hours = serializers.FloatField()
    overtime_hours = serializers.FloatField()
    surcharge_hours = serializers.FloatField()
    # Sonntagszuschlag (Block 1.6, Art. 19 Abs. 3 ArG).
    sunday_hours = serializers.FloatField()
    sunday_surcharge_hours = serializers.FloatField()
    is_provisional = serializers.BooleanField()


class NightWorkSummarySerializer(serializers.Serializer):
    """Read-only: Ergebnis von Employee.night_work_summary (Block 1.5/1.17, Art. 17b/17c ArG)."""

    year = serializers.IntegerField()
    nights_count = serializers.IntegerField()
    night_hours = serializers.FloatField()
    is_regular = serializers.BooleanField()
    surcharge_hours = serializers.FloatField()
    occasional_night_hours = serializers.FloatField()
    occasional_night_surcharge_pct = serializers.IntegerField()
    permit_warning = serializers.BooleanField()
    medical_exam_due = serializers.BooleanField()


class FairnessSummarySerializer(serializers.Serializer):
    """Read-only: Ergebnis von Employee.fairness_summary() (Block 2, Punkt 20)."""

    window_start = serializers.DateField()
    window_end = serializers.DateField()
    sunday_hours = serializers.FloatField()
    sunday_points = serializers.FloatField()
    night_hours = serializers.FloatField()
    night_points = serializers.FloatField()
    points = serializers.FloatField()
    team_average_points = serializers.FloatField(allow_null=True)


class BalanceFairnessBulkItemSerializer(serializers.Serializer):
    """
    Read-only: ein Eintrag aus EmployeeViewSet.balance_fairness_bulk() --
    Bulk-Variante von balance()/fairness() für die Mitarbeitendenliste
    (Nutzer-Feedback 2026-08: "lässt sich da was machen an der
    Performance?"). Statt 2xN Einzelrequests (je einer pro Badge und Zeile)
    liefert dieser Endpoint beides für eine ganze Liste von Mitarbeitenden-
    IDs in einer einzigen Antwort -- die Browser-Verbindungslimite (~6
    gleichzeitige Requests pro Host) war nach dem bereits behobenen
    N+1-Query-Bug in fairness_summary() der verbleibende Flaschenhals.
    """

    id = serializers.IntegerField()
    balance = EmployeeBalanceSerializer()
    fairness = FairnessSummarySerializer()


class SpecialSurchargeBreakdownSerializer(serializers.Serializer):
    """
    Read-only: ein Eintrag aus Employee.monthly_summary()["special_surcharge_breakdown"]
    -- Zuschlagsstunden für eine einzelne Spezialität (z. B. Pikett) mit
    TimeTemplate.surcharge_pct > 0, nicht zu einer Summe zusammengefasst
    (README Punkt 30: das künftige Lohnart-Mapping braucht pro Spezialität
    einen eigenen Lohnart-Code).
    """

    template_id = serializers.IntegerField()
    template_name = serializers.CharField()
    surcharge_pct = serializers.IntegerField()
    hours = serializers.FloatField()
    surcharge_hours = serializers.FloatField()


class MonthlySummarySerializer(serializers.Serializer):
    """Read-only: Ergebnis von Employee.monthly_summary (Block 2.6, Lohnlauf-Basis)."""

    year = serializers.IntegerField()
    month = serializers.IntegerField()
    month_start = serializers.DateField()
    month_end = serializers.DateField()
    soll_hours = serializers.FloatField()
    ist_hours = serializers.FloatField()
    overtime_hours = serializers.FloatField()
    overtime_surcharge_hours = serializers.FloatField()
    saldo_hours = serializers.FloatField()
    flextime_corridor_hours = serializers.IntegerField()
    flextime_corridor_excess_hours = serializers.FloatField()
    is_overtime_settled = serializers.BooleanField()
    night_hours = serializers.FloatField()
    night_surcharge_hours = serializers.FloatField()
    occasional_night_hours = serializers.FloatField()
    occasional_night_surcharge_hours = serializers.FloatField()
    sunday_hours = serializers.FloatField()
    sunday_surcharge_hours = serializers.FloatField()
    special_surcharge_hours = serializers.FloatField()
    special_surcharge_breakdown = SpecialSurchargeBreakdownSerializer(many=True)
    holiday_days = serializers.IntegerField()
    is_provisional = serializers.BooleanField()


class TimeTemplateSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeTemplateSegment
        fields = ["id", "order", "start_time", "end_time"]
        read_only_fields = ["id"]
        extra_kwargs = {"order": {"required": False}}


class TimeTemplateSerializer(NestedWritableSerializerMixin, serializers.ModelSerializer):
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
            "minimum_staffing",
            "category",
            "surcharge_pct",
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

    # TimeTemplate hat aktuell kein ManyToManyField -- pop_m2m_fields() (in
    # NestedWritableSerializerMixin.create()/update()) ist hier ein No-Op,
    # hält das Muster aber konsistent mit EmployeeSerializer und schützt
    # automatisch, falls hier je ein M2M-Feld ergänzt wird (siehe
    # pop_m2m_fields()-Docstring oben).
    _nested_field = "segments"

    @staticmethod
    def _sync_nested(instance, segments_data):
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
    # Block 1.6: nur aussagekräftig, wenn is_sunday True ist -- vereinfachte
    # Ersatzruhetag-Kontrolle, siehe Employee.sunday_replacement_rest_missing.
    sunday_replacement_rest_missing = serializers.BooleanField(read_only=True)

    class Meta:
        model = ShiftAssignment
        fields = [
            "id",
            "employee",
            "node",
            "date",
            "template",
            "note",
            "night_hours",
            "is_sunday",
            "sunday_replacement_rest_missing",
        ]

    def validate(self, attrs):
        """
        Baut eine (unsaved) Instanz mit den neuen + bestehenden Werten und
        ruft model.clean() auf, damit der Ruhezeit-Check aus dem Model auch
        über die API greift (nicht nur im Django Admin).
        """
        instance = build_instance_for_clean(
            self, attrs, ShiftAssignment, ["employee", "node", "date", "template"], set_tenant=True
        )
        instance.clean()
        return attrs


class AbsenceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AbsenceType
        fields = ["id", "name", "color", "icon", "deducts_vacation_days", "counts_as_sick_leave"]


class PayrollCategoryMappingSerializer(serializers.ModelSerializer):
    """
    MVP-Fahrplan Block 2, Punkt 30: Admin-only Verwaltung der Lohnart-
    Zuordnung (siehe PayrollCategoryMapping-Docstring). `special_template`
    wird von ModelSerializer automatisch als
    PrimaryKeyRelatedField(queryset=TimeTemplate._default_manager.all())
    erzeugt -- scheduling.views.TenantScopedViewSet.initial() setzt die
    Tenant-ContextVar (anders als core.views.TenantScopedAPIMixin, siehe
    dessen Docstring), das Default-Queryset ist also bereits korrekt
    gescoped. Trotzdem eine explizite Prüfung hier, analog
    core.serializers.MembershipSerializer.validate_scoped_nodes -- explizite
    Filterung ist die eigentliche Tenant-Grenze, nicht nur die ContextVar.
    """

    class Meta:
        model = PayrollCategoryMapping
        fields = ["id", "category", "special_template", "absence_type", "payroll_code", "payroll_label", "is_active"]

    def validate_special_template(self, value):
        if value is None:
            return value
        tenant = self.context["request"].tenant
        if value.tenant_id != tenant.id:
            raise serializers.ValidationError("Ungültige oder fremde Spezialität.")
        return value

    def validate_absence_type(self, value):
        if value is None:
            return value
        tenant = self.context["request"].tenant
        if value.tenant_id != tenant.id:
            raise serializers.ValidationError("Ungültige oder fremde Absenzart.")
        return value

    def validate(self, attrs):
        category = attrs.get("category", getattr(self.instance, "category", None))
        special_template = attrs.get("special_template", getattr(self.instance, "special_template", None))
        absence_type = attrs.get("absence_type", getattr(self.instance, "absence_type", None))
        if sum(bool(v) for v in (category, special_template, absence_type)) != 1:
            raise serializers.ValidationError(
                "Genau eines von Kategorie, Spezialität oder Absenzart muss gesetzt sein."
            )
        return attrs


class AbsenceSerializer(serializers.ModelSerializer):
    # Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
    # aufgebaut sein wie Zeiterfassung" -- die stationsübergreifende
    # AbsenceOverview.jsx zeigt Mitarbeiter/Station(en)/Art direkt in der
    # Zeile, ohne pro Zeile einen zusätzlichen Request (gleiches Muster wie
    # TimeRecordSerializer.assignment_employee_name).
    employee_name = serializers.SerializerMethodField()
    # Ein Employee kann mehreren Stationen zugeordnet sein (Team-Anstellungen,
    # README Punkt 17) -- anders als bei ShiftAssignment/TimeRecord gibt es
    # keinen einzelnen Stations-FK, deshalb ein einfacher, kommagetrennter
    # Text statt eines einzelnen Felds (analog nodeNames() in
    # EmployeeSettings.jsx), NICHT sortierbar (kein eindeutiger Sortierwert).
    employee_node_names = serializers.SerializerMethodField()
    type_name = serializers.CharField(source="type.name", read_only=True)
    type_color = serializers.CharField(source="type.color", read_only=True)
    type_icon = serializers.CharField(source="type.icon", read_only=True)

    class Meta:
        model = Absence
        fields = [
            "id",
            "employee",
            "employee_name",
            "employee_node_names",
            "start_date",
            "end_date",
            "day_portion",
            "type",
            "type_name",
            "type_color",
            "type_icon",
            "status",
            "note",
        ]
        # status wird nicht direkt gesetzt, sondern über perform_create
        # (Admin/Planer -> sofort APPROVED, sonst PENDING) bzw. die
        # approve/reject-Actions (siehe AbsenceViewSet, Block 2.3).
        read_only_fields = ["status"]

    def get_employee_name(self, obj):
        return f"{obj.employee.first_name} {obj.employee.last_name}"

    def get_employee_node_names(self, obj):
        names = [n.name for n in obj.employee.nodes.all()]
        return ", ".join(names) if names else "—"

    def validate(self, attrs):
        instance = build_instance_for_clean(
            self, attrs, Absence, ["employee", "start_date", "end_date", "day_portion", "type"]
        )
        if self.instance is None:
            # Neuanlage: status ist read_only und wird erst in
            # AbsenceViewSet.perform_create() gesetzt (Admin/Planer ->
            # sofort APPROVED, sonst PENDING) -- ohne diesen Vorgriff würde
            # instance.clean() hier immer mit dem Model-Default PENDING
            # prüfen und den Absenz/Zuweisungs-Konflikt-Check (nur bei
            # APPROVED aktiv, siehe Absence.clean()) für von Admin/Planer
            # sofort genehmigte Absenzen nie auslösen.
            request = self.context.get("request")
            is_manager = bool(
                request
                and request.membership
                and request.membership.role in (Membership.Role.ADMIN, Membership.Role.PLANNER)
            )
            instance.status = Absence.Status.APPROVED if is_manager else Absence.Status.PENDING
        instance.clean()
        return attrs


class PregnancySerializer(serializers.ModelSerializer):
    class Meta:
        model = Pregnancy
        fields = ["id", "employee", "expected_birth_date", "actual_birth_date", "notes"]


class ShiftPreferenceSerializer(serializers.ModelSerializer):
    # employee wird serverseitig immer auf das eigene Employee-Profil
    # gezwungen (siehe ShiftPreferenceViewSet.perform_create) -- read_only
    # hier, damit ein im Payload mitgeschicktes fremdes employee gar nicht
    # erst als "gültiger, aber ignorierter" Wert durchgeht, sondern die
    # Absicht des Feldes im Schema klar ist.
    employee = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ShiftPreference
        fields = ["id", "employee", "date", "type", "template", "note"]

    def validate(self, attrs):
        instance = build_instance_for_clean(self, attrs, ShiftPreference, ["date", "type", "template"])
        instance.clean()

        # unique_together (employee, date) greift hier nicht automatisch als
        # DRF-Validator, weil employee read_only ist (DRF generiert
        # UniqueTogetherValidator nur für Felder, die es selbst aus dem
        # Payload entgegennimmt) -- ohne diesen Check würde ein Duplikat erst
        # als roher IntegrityError (HTTP 500) statt als saubere
        # Validierungsmeldung auffliegen.
        request = self.context.get("request")
        employee_profile = getattr(request, "employee_profile", None) if request else None
        date = attrs.get("date", self.instance.date if self.instance else None)
        if employee_profile and date:
            conflict = ShiftPreference.all_objects.filter(employee=employee_profile, date=date)
            if self.instance:
                conflict = conflict.exclude(pk=self.instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"date": "Für diesen Tag besteht bereits ein Wunsch -- zuerst löschen oder direkt ändern."}
                )
        return attrs


class TimeRecordSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeRecordSegment
        fields = ["id", "order", "actual_start", "actual_end"]
        read_only_fields = ["id"]
        extra_kwargs = {"order": {"required": False}}


class TimeRecordSerializer(NestedWritableSerializerMixin, serializers.ModelSerializer):
    # Informativ, berechnet aus assignment.template -- siehe TimeRecord-Docstring.
    deviation_minutes = serializers.IntegerField(read_only=True)
    end_deviation_minutes = serializers.IntegerField(read_only=True)
    actual_hours = serializers.FloatField(read_only=True)
    # Nutzer-Feedback (2026-08): "Sichtbarkeit der IST-Zeit... grün für +,
    # rot für -" -- Netto-Stunden-Differenz Ist ggü. Soll (siehe
    # TimeRecord.hours_deviation-Docstring), für die farbliche Kennzeichnung
    # in TimeRecordOverview.jsx.
    hours_deviation = serializers.FloatField(read_only=True)
    break_minutes_total = serializers.IntegerField(read_only=True)
    break_below_minimum = serializers.BooleanField(read_only=True)
    # Block 1.12: bei Templates mit Segmenten (siehe TimeTemplate.segments)
    # wird hierüber pro Block eine Ist-Zeit erfasst, statt der klassischen
    # actual_start/actual_end/actual_break_minutes-Felder (die dann leer
    # bleiben). Anzahl/Reihenfolge sind vom Template vorgegeben -- geprüft in
    # TimeRecord.clean(), nicht hier im Serializer.
    segments = TimeRecordSegmentSerializer(many=True, required=False)
    # Nutzer-Feedback (2026-08): die stationsübergreifende
    # "Zu bestätigen"-Tabelle (TimeRecordOverview.jsx) zeigt Station/
    # Mitarbeiter/Datum direkt in der Zeile -- ohne diese Felder bräuchte das
    # Frontend pro Zeile einen zusätzlichen Request auf die zugehörige
    # ShiftAssignment, nur um denselben Wert zu lesen.
    assignment_date = serializers.DateField(source="assignment.date", read_only=True)
    assignment_employee_id = serializers.IntegerField(source="assignment.employee_id", read_only=True)
    assignment_employee_name = serializers.SerializerMethodField()
    assignment_node_id = serializers.IntegerField(source="assignment.node_id", read_only=True)
    assignment_node_name = serializers.CharField(source="assignment.node.name", read_only=True)
    assignment_template_id = serializers.IntegerField(source="assignment.template_id", read_only=True)
    assignment_template_name = serializers.CharField(source="assignment.template.name", read_only=True)

    class Meta:
        model = TimeRecord
        fields = [
            "id",
            "assignment",
            "assignment_date",
            "assignment_employee_id",
            "assignment_employee_name",
            "assignment_node_id",
            "assignment_node_name",
            "assignment_template_id",
            "assignment_template_name",
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
            "hours_deviation",
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

    def get_assignment_employee_name(self, obj):
        employee = obj.assignment.employee
        return f"{employee.first_name} {employee.last_name}"

    def validate(self, attrs):
        # segments wird separat behandelt (nested write, siehe create/update)
        # -- nicht Teil der einfachen setattr-Schleife wie die Skalarfelder.
        segments_data = attrs.get("segments")
        instance = build_instance_for_clean(
            self,
            attrs,
            TimeRecord,
            ["assignment", "actual_start", "actual_end", "actual_break_minutes", "note"],
            set_tenant=True,
        )
        # "segments" fehlt im Payload (PATCH ohne Zeitänderung) -> None ->
        # TimeRecord.effective_segments() greift auf bereits gespeicherte
        # Segmente zurück statt auf einen leeren Pending-Zustand.
        instance._pending_segments = segments_data
        instance.clean()
        return attrs

    # TimeRecord hat aktuell kein ManyToManyField -- siehe Kommentar in
    # TimeTemplateSerializer für die Begründung, warum das Muster trotzdem
    # konsistent angewendet wird.
    _nested_field = "segments"

    @staticmethod
    def _sync_nested(instance, segments_data):
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

    # Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
    # aufgebaut sein wie Zeiterfassung" -- die stationsübergreifende
    # TradeRequestOverview.jsx zeigt Personen/Station/Schicht direkt in der
    # Zeile (gleiches Muster wie TimeRecordSerializer.assignment_*).
    requester_employee_id = serializers.IntegerField(source="requester_assignment.employee_id", read_only=True)
    requester_employee_name = serializers.SerializerMethodField()
    requester_node_id = serializers.IntegerField(source="requester_assignment.node_id", read_only=True)
    requester_node_name = serializers.CharField(source="requester_assignment.node.name", read_only=True)
    requester_date = serializers.DateField(source="requester_assignment.date", read_only=True)
    requester_template_id = serializers.IntegerField(source="requester_assignment.template_id", read_only=True)
    requester_template_name = serializers.CharField(source="requester_assignment.template.name", read_only=True)
    requester_template_color = serializers.CharField(source="requester_assignment.template.color", read_only=True)
    target_employee_name = serializers.SerializerMethodField()
    # target_assignment ist optional (leer = "Zielperson übernimmt die Schicht
    # einfach", siehe Modell-Docstring) -- source="target_assignment.xxx"
    # würde bei None mit AttributeError abbrechen statt sauber None zu
    # liefern, deshalb SerializerMethodField mit expliziter None-Prüfung.
    target_assignment_date = serializers.SerializerMethodField()
    target_assignment_template_id = serializers.SerializerMethodField()
    target_assignment_template_name = serializers.SerializerMethodField()
    target_assignment_template_color = serializers.SerializerMethodField()

    class Meta:
        model = ShiftTradeRequest
        fields = [
            "id",
            "requester_assignment",
            "requester_employee_id",
            "requester_employee_name",
            "requester_node_id",
            "requester_node_name",
            "requester_date",
            "requester_template_id",
            "requester_template_name",
            "requester_template_color",
            "target_employee",
            "target_employee_name",
            "target_assignment",
            "target_assignment_date",
            "target_assignment_template_id",
            "target_assignment_template_name",
            "target_assignment_template_color",
            "status",
            "note",
            "created_at",
            "resolved_at",
        ]
        read_only_fields = ["status", "created_at", "resolved_at"]

    def get_requester_employee_name(self, obj):
        employee = obj.requester_assignment.employee
        return f"{employee.first_name} {employee.last_name}"

    def get_target_employee_name(self, obj):
        return f"{obj.target_employee.first_name} {obj.target_employee.last_name}"

    def get_target_assignment_date(self, obj):
        return obj.target_assignment.date if obj.target_assignment_id else None

    def get_target_assignment_template_id(self, obj):
        return obj.target_assignment.template_id if obj.target_assignment_id else None

    def get_target_assignment_template_name(self, obj):
        return obj.target_assignment.template.name if obj.target_assignment_id else None

    def get_target_assignment_template_color(self, obj):
        return obj.target_assignment.template.color if obj.target_assignment_id else None

    def validate(self, attrs):
        tenant = self.context["request"].tenant
        for field_name in ("requester_assignment", "target_employee", "target_assignment"):
            obj = attrs.get(field_name)
            if obj is not None and obj.tenant_id != tenant.id:
                raise serializers.ValidationError({field_name: "Gehört nicht zu diesem Tenant."})

        instance = build_instance_for_clean(
            self,
            attrs,
            ShiftTradeRequest,
            ["requester_assignment", "target_employee", "target_assignment"],
            set_tenant=True,
        )
        instance.clean()
        return attrs

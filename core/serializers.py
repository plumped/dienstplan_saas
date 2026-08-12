from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.crypto import get_random_string
from rest_framework import serializers

from core.models import SWISS_CANTON_CHOICES, Membership, Tenant, TenantHolidayOverride

User = get_user_model()


class TenantSerializer(serializers.ModelSerializer):
    """
    Tenant-Konfiguration (MVP-Fahrplan Block 2, Punkt 14): die numerischen
    ArG-/Zuschlags-Grenzwerte, bislang nur im Django-Admin editierbar (siehe
    core.admin.TenantAdmin für dieselben Felder mit help_text zu den
    jeweiligen Gesetzesartikeln). `name`/`slug` sind hier bewusst nicht
    enthalten -- Umbenennung/Slug-Änderung ist ein eigenes Thema (potenziell
    URL-/Branding-relevant), nicht Teil dieser Konfiguration.
    """

    class Meta:
        model = Tenant
        fields = [
            "id",
            "name",
            "minimum_rest_hours",
            "maximum_weekly_hours",
            "maximum_daily_span_hours",
            "time_record_deviation_tolerance_minutes",
            "standard_weekly_hours",
            "overtime_surcharge_pct",
            "flextime_corridor_hours",
            "default_vacation_days_per_year",
            "night_work_surcharge_pct",
            "night_work_regular_threshold_nights",
            "night_work_permit_confirmed",
            "occasional_night_work_surcharge_pct",
            "sunday_work_surcharge_pct",
            "sunday_shift_bonus_points_per_hour",
            "night_shift_bonus_points_per_hour",
            "sick_pay_model",
            "sick_pay_scale",
            "sick_pay_waiting_days",
            "canton",
            "onboarding_completed",
        ]
        read_only_fields = ["id", "name"]


class TenantHolidayOverrideSerializer(serializers.ModelSerializer):
    """
    Manuelle Ausnahme zum kantonalen Feiertagskalender (Arbeitszeitmodell,
    README Block 2.7 Punkt 7) -- siehe Tenant.public_holidays().
    """

    class Meta:
        model = TenantHolidayOverride
        fields = ["id", "date", "name", "kind"]


class MembershipSerializer(serializers.ModelSerializer):
    """
    Nutzer-Feedback (2026-08): "kann man [Planer/HR] Stationen zuweisen?"
    -- Admin-only Verwaltung von Membership.scoped_nodes (siehe
    scheduling.views._employee_scoped_node_ids).

    Nutzer-Feedback (2026-08): "was am intuitivsten und effizientesten ist
    -- Applikationsmanager legt den Benutzer direkt an" -- `role` ist
    inzwischen ebenfalls schreibbar (Admin-only, siehe
    core.views.MembershipViewSet.perform_update für den Schutz vor
    "letzter Admin weg" und dem bestehenden Admin/scoped_nodes-Konflikt).
    Konten selbst werden über MembershipCreateSerializer angelegt, nicht
    hier -- dieser Serializer bearbeitet nur eine bereits bestehende
    Mitgliedschaft.

    `scoped_nodes` wird von ModelSerializer automatisch als
    PrimaryKeyRelatedField(queryset=Node._default_manager.all()) erzeugt --
    ohne gesetzte Tenant-ContextVar (core.views.TenantScopedAPIMixin setzt
    sie bewusst nicht, siehe deren Docstring) wäre das serverseitig
    ungefiltert über ALLE Tenants. validate_scoped_nodes() ist deshalb keine
    Kür, sondern die einzige echte Tenant-Grenze für dieses Feld.
    """

    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "username", "email", "employee_name", "role", "scoped_nodes"]
        read_only_fields = ["id", "username", "email", "employee_name"]

    def get_employee_name(self, obj):
        # Lokaler Import statt Modul-Level (core.views.MeView-Docstring):
        # core bleibt die "unterste" App, auf die scheduling aufbaut.
        from scheduling.models import Employee

        employee = Employee.all_objects.filter(tenant=obj.tenant, user=obj.user).first()
        return f"{employee.first_name} {employee.last_name}" if employee else None

    def validate_scoped_nodes(self, value):
        tenant = self.context["request"].tenant
        for node in value:
            if node.tenant_id != tenant.id:
                raise serializers.ValidationError("Ungültige oder fremde Station.")
        return value


class MembershipCreateSerializer(serializers.ModelSerializer):
    """
    Direktanlage eines neuen Kontos + Mitgliedschaft (MVP-Fahrplan Block 2.1/
    3.4). Nutzer-Feedback (2026-08): "Ist das state of the art mit
    Mailversand? [...] Applikationsmanager wird den Benutzer anlegen und
    nicht per Mail einladen -- was ist am effizientesten und intuitivsten?"
    -- bewusst KEIN E-Mail-Einladungs-/Self-Signup-Flow, siehe
    core.models.User.must_change_password-Docstring für die Begründung
    (Zielbranche ohne durchgängig gepflegte private E-Mail-Adressen).

    Erzeugt User + Membership in einem Schritt: der Admin vergibt nur
    Benutzername + Rolle (+ optional Name), ein Temp-Passwort wird
    serverseitig generiert und in der Response EINMALIG zurückgegeben
    (`temporary_password`, danach nirgends mehr abrufbar -- nur als Hash in
    der DB) -- der Admin gibt es dem Mitarbeitenden mündlich/auf Papier
    weiter, analog zu etablierten Schichtplanungs-Tools (Deputy, When I
    Work, Planday) für Personal ohne Firmen-Mail.
    """

    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()], write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, write_only=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, write_only=True)
    temporary_password = serializers.CharField(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "username", "first_name", "last_name", "role", "temporary_password"]
        read_only_fields = ["id"]

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Dieser Benutzername ist bereits vergeben.")
        return value

    def create(self, validated_data):
        tenant = self.context["request"].tenant
        temp_password = get_random_string(12)
        user = User.objects.create_user(
            username=validated_data["username"],
            password=temp_password,
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
            must_change_password=True,
        )
        membership = Membership.objects.create(user=user, tenant=tenant, role=validated_data["role"])
        # Transientes Attribut, nicht Teil des Modells -- nur für diese eine
        # Response verfügbar (temporary_password.read_only greift über
        # getattr(), siehe Serializer-Docstring).
        membership.temporary_password = temp_password
        return membership


class SignupSerializer(serializers.Serializer):
    """
    Self-Signup (README Block 3): reine Validierung für core.views.SignupView --
    die eigentliche Erzeugung spannt vier Modelle auf (Tenant/User/Membership/
    Demo-Daten) und gehört deshalb in die View, nicht in .create() hier.

    Anders als MembershipCreateSerializer (Admin legt Konto für jemand anderen an,
    Temp-Passwort) setzt hier die signup-ausführende Person direkt ihr eigenes
    Passwort -- konsistent mit der Nutzer-Entscheidung gegen jeden
    Magic-Link/E-Mail-Versand-Flow, siehe core/onboarding.py-Docstring.
    """

    tenant_name = serializers.CharField(max_length=200)
    canton = serializers.ChoiceField(choices=SWISS_CANTON_CHOICES, required=False, default="", allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    password = serializers.CharField(write_only=True)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Dieser Benutzername ist bereits vergeben.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

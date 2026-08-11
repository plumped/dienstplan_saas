from rest_framework import serializers

from core.models import Membership, Tenant, TenantHolidayOverride


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
            "canton",
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
    scheduling.views._employee_scoped_node_ids). Rollenvergabe selbst bleibt
    bewusst ausserhalb dieses Endpoints (weiterhin nur Django-Admin, siehe
    EmployeeSettings.jsx-Kommentar) -- hier geht es nur um die
    Stations-Einschränkung einer bereits bestehenden Mitgliedschaft.

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
        read_only_fields = ["id", "username", "email", "employee_name", "role"]

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

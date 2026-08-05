from rest_framework import serializers

from core.models import Tenant, TenantHolidayOverride


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
            "default_vacation_days_per_year",
            "night_work_surcharge_pct",
            "night_work_regular_threshold_nights",
            "night_work_permit_confirmed",
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

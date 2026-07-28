from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Membership, Tenant, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    pass


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "is_active",
        "minimum_rest_hours",
        "maximum_weekly_hours",
        "maximum_daily_span_hours",
        "created_at",
    ]
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        (
            "Regel-Engine (Schweizer Arbeitsgesetz, siehe README)",
            {
                "fields": ("minimum_rest_hours", "maximum_weekly_hours", "maximum_daily_span_hours"),
                "description": "Defaults entsprechen Art. 9/10/15a ArG für Gesundheits-/Büropersonal. "
                "Bei abweichendem GAV (Gesamtarbeitsvertrag) hier pro Klinik/Praxis anpassen.",
            },
        ),
        (
            "Ist-Zeiterfassung",
            {"fields": ("time_record_deviation_tolerance_minutes",)},
        ),
        (
            "Überzeitarbeit (Art. 13 ArG)",
            {
                "fields": ("standard_weekly_hours", "overtime_surcharge_pct"),
                "description": "standard_weekly_hours ist die Normalarbeitszeit eines 100%-Pensums "
                "(Soll), nicht die gesetzliche Höchstgrenze (maximum_weekly_hours oben).",
            },
        ),
        (
            "Ferien (Art. 329a OR)",
            {"fields": ("default_vacation_days_per_year",)},
        ),
    )


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role"]
    list_filter = ["tenant", "role"]

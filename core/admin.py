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
    )


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role"]
    list_filter = ["tenant", "role"]

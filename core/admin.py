from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.context import get_current_tenant

from .models import Membership, Tenant, User


class TenantScopedAdminMixin:
    """
    Beschränkt ein ModelAdmin auf den aktiv gewählten Tenant (siehe
    core.middleware.AdminActiveTenantMiddleware, core.admin_views.
    tenant_switch) -- der eigentliche Fix für das Datenschutzproblem, dass
    der Django-Admin sonst ALLE Tenants ungefiltert gemischt anzeigt (siehe
    README, Architektur-Abschnitt).

    `get_queryset` filtert explizit selbst nach `tenant`, statt sich allein
    auf `TenantScopedManager` (den Default-Manager der meisten hier
    betroffenen Modelle) und dessen ContextVar zu verlassen -- exakt
    dasselbe Prinzip wie bei der API (`TenantScopedViewSet.get_queryset`):
    explizite Filterung ist die eigentliche Sicherheitsgrenze. Das deckt
    nebenbei auch Modelle ab, die (wie `Membership`) nicht über
    `TenantScopedManager` laufen. Ohne aktiven Tenant: leere Liste UND
    gesperrtes "Hinzufügen" -- sonst wäre "alles anzeigen" der Default, das
    genaue Gegenteil von sicher.

    Das `tenant`-Feld selbst wird beim Anlegen/Bearbeiten auf den aktiven
    Tenant festgelegt (Dropdown zeigt nur diesen einen Eintrag) -- sonst
    liesse sich trotz gewähltem Tenant versehentlich ein Datensatz für
    einen ANDEREN Tenant anlegen.

    Setzt NICHT voraus, dass das Modell `TenantScopedModel` erbt (deckt
    auch `Membership` ab) -- verlangt nur ein `tenant`-Feld.

    Wichtig für FK-/M2M-Dropdowns anderer tenant-gescopter Modelle (z. B.
    Node/Skill beim Anlegen eines Employee): die reine ContextVar-Filterung
    über `TenantScopedManager` als Default-Manager reicht dafür NICHT in
    jedem Fall aus. `Node` z. B. erbt sowohl von treebeard's `MP_Node`
    (eigener `MP_NodeManager`) als auch von `TenantScopedModel` -- durch die
    Erbfolge gewinnt `MP_NodeManager` als `_default_manager`, der die
    ContextVar gar nicht kennt. `formfield_for_foreignkey`/
    `formfield_for_manytomany` filtern deshalb das von Django gebaute
    Formfeld-Queryset zusätzlich EXPLIZIT nach `tenant`, statt sich auf den
    jeweiligen Default-Manager zu verlassen -- funktioniert unabhängig
    davon, welcher Manager tatsächlich "gewinnt".
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        tenant = get_current_tenant()
        if tenant is None:
            return qs.none()
        return qs.filter(tenant=tenant)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and get_current_tenant() is not None

    @staticmethod
    def _scope_to_active_tenant(queryset):
        tenant = get_current_tenant()
        if tenant is not None and hasattr(queryset.model, "tenant_id"):
            queryset = queryset.filter(tenant=tenant)
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "tenant":
            tenant = get_current_tenant()
            if tenant is not None:
                kwargs["queryset"] = Tenant.objects.filter(pk=tenant.pk)
                kwargs.setdefault("initial", tenant.pk)
            return super().formfield_for_foreignkey(db_field, request, **kwargs)
        formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if formfield is not None and hasattr(formfield, "queryset"):
            formfield.queryset = self._scope_to_active_tenant(formfield.queryset)
        return formfield

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if formfield is not None and hasattr(formfield, "queryset"):
            formfield.queryset = self._scope_to_active_tenant(formfield.queryset)
        return formfield


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
        (
            "Nacht-/Sonntagsarbeit (Art. 17b/17c/19/20 ArG)",
            {
                "fields": (
                    "night_work_surcharge_pct",
                    "night_work_regular_threshold_nights",
                    "night_work_permit_confirmed",
                    "sunday_work_surcharge_pct",
                ),
                "description": "Zuschläge/Schwellenwerte für Employee.night_work_summary() und "
                "weekly_hours_summary() (siehe README).",
            },
        ),
    )


@admin.register(Membership)
class MembershipAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["user", "tenant", "role"]
    list_filter = ["tenant", "role"]

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.context import get_current_tenant

from .models import Membership, Tenant, TenantHolidayOverride, User


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
        "onboarding_completed",
        "subscription_status",
        "minimum_rest_hours",
        "maximum_weekly_hours",
        "maximum_daily_span_hours",
        "created_at",
    ]
    list_filter = ["subscription_status"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["stripe_customer_id", "stripe_subscription_id"]
    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        (
            "Self-Signup / Onboarding",
            {
                "fields": ("onboarding_completed",),
                "description": "False nur für frisch per Self-Signup angelegte Tenants (core.views."
                "SignupView) -- steuert, ob das Frontend den Einrichtungsassistenten (OnboardingWizard."
                "jsx) statt der normalen App zeigt. Hier manuell auf True setzbar, falls ein Tenant im "
                "Assistenten feststeckt (Support-Fall).",
            },
        ),
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
                "fields": ("standard_weekly_hours", "overtime_surcharge_pct", "flextime_corridor_hours"),
                "description": "standard_weekly_hours ist die Normalarbeitszeit eines 100%-Pensums "
                "(Soll), nicht die gesetzliche Höchstgrenze (maximum_weekly_hours oben). "
                "flextime_corridor_hours ist die Gleitzeit-Bandbreite, innerhalb der sich der laufende "
                "Saldo frei bewegt, ohne Zuschlag/Auszahlung auszulösen (siehe Employee."
                "time_account_summary()).",
            },
        ),
        (
            "Ferien (Art. 329a OR)",
            {"fields": ("default_vacation_days_per_year",)},
        ),
        (
            "Feiertagskalender (Arbeitszeitmodell)",
            {
                "fields": ("canton",),
                "description": "Grundlage für Employee.annual_target_hours()/time_account_summary(). "
                "Lokale Sonderfälle über TenantHolidayOverride pflegen.",
            },
        ),
        (
            "Nacht-/Sonntagsarbeit (Art. 17b/17c/19/20 ArG)",
            {
                "fields": (
                    "night_work_surcharge_pct",
                    "occasional_night_work_surcharge_pct",
                    "night_work_regular_threshold_nights",
                    "night_work_permit_confirmed",
                    "sunday_work_surcharge_pct",
                ),
                "description": "Zuschläge/Schwellenwerte für Employee.night_work_summary() und "
                "weekly_hours_summary() (siehe README). night_work_surcharge_pct ist die "
                "Zeitgutschrift bei REGELMÄSSIGER Nachtarbeit (Art. 17b Abs. 1), "
                "occasional_night_work_surcharge_pct der Lohnzuschlag bei GELEGENTLICHER "
                "Nachtarbeit (Art. 17b Abs. 2).",
            },
        ),
        (
            "Lohnfortzahlung bei Krankheit (Art. 324a OR)",
            {
                "fields": ("sick_pay_model", "sick_pay_scale", "sick_pay_waiting_days"),
                "description": "Grundlage für Employee.sick_pay_summary(). Die Skala-Werte sind "
                "Näherungen -- vor Produktivnutzung mit einer Rechts-/Treuhandstelle verifizieren.",
            },
        ),
        (
            "Fairness-Punktesystem (unpopuläre Schichten)",
            {
                "fields": ("sunday_shift_bonus_points_per_hour", "night_shift_bonus_points_per_hour"),
                "description": "Grundlage für Employee.fairness_summary() -- rein interne "
                "Transparenz-/Planungsgrösse, keine gesetzliche Vorgabe.",
            },
        ),
        (
            "Abrechnung (README Block 6, Stripe)",
            {
                "fields": (
                    "subscription_status",
                    "trial_ends_at",
                    "trial_employee_limit",
                    "stripe_customer_id",
                    "stripe_subscription_id",
                ),
                "description": "subscription_status/trial_ends_at sind hier manuell überschreibbar "
                "für Support-Fälle (z. B. Trial verlängern) -- im Normalbetrieb pflegt sich beides "
                "selbst über core.billing.handle_webhook_event() aus Stripe-Webhook-Events. "
                "stripe_customer_id/stripe_subscription_id sind rein informativ (read-only), da sie "
                "ausschliesslich von core/billing.py gesetzt werden.",
            },
        ),
    )


@admin.register(Membership)
class MembershipAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["user", "tenant", "role"]
    list_filter = ["tenant", "role"]


@admin.register(TenantHolidayOverride)
class TenantHolidayOverrideAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["date", "name", "kind", "tenant"]
    list_filter = ["tenant", "kind"]

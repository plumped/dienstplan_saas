from django.contrib import admin
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from core.admin import TenantScopedAdminMixin

from .models import (
    Absence,
    AbsenceType,
    Employee,
    Employment,
    Node,
    OvertimeSettlement,
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


@admin.register(Node)
class NodeAdmin(TenantScopedAdminMixin, TreeAdmin):
    """
    Mandanten-Isolation für den Node-Baum (Nutzer-Feedback: "Es muss ALLES
    tenant unabhängig sein schon rein datenschutz technisch"): der
    unsichtbare Tenant-Wurzelknoten (Node.is_forest_root, siehe dessen
    Docstring) ist reine interne Baumstruktur, kein von Menschen verwaltetes
    Objekt -- get_queryset() blendet ihn aus Liste und Move-Zielauswahl aus
    (Verteidigung in der Tiefe, auch wenn er über die API ohnehin nie
    sichtbar ist, siehe NodeViewSet.get_queryset).

    has_add_permission ist bewusst False: treebeards eigenes Tree-UI
    (movenodeform_factory) ruft bei "neuen Knoten ohne Ziel hinzufügen"
    intern selbst Node.add_root() auf und würde damit Node.
    get_or_create_forest_root() (und dessen Tenant-Isolation) komplett
    umgehen -- die App-eigene Settings-Oberfläche (NodeSettings.jsx ->
    NodeViewSet) ist die vorgesehene Verwaltungsfläche für neue Stationen,
    der Admin bleibt nur für Ansicht/Umbenennen/Verschieben/Löschen
    bestehender Knoten nutzbar.
    """

    form = movenodeform_factory(Node)
    list_display = ["name", "tenant"]
    list_filter = ["tenant"]

    def get_queryset(self, request):
        return super().get_queryset(request).exclude(is_forest_root=True)

    def has_add_permission(self, request):
        return False


@admin.register(Skill)
class SkillAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "tenant"]
    list_filter = ["tenant"]


@admin.register(Employee)
class EmployeeAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["last_name", "first_name", "tenant", "birth_date", "employment_pct", "is_active"]
    list_filter = ["tenant", "is_active"]
    filter_horizontal = ["nodes", "skills"]


@admin.register(Employment)
class EmploymentAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "node", "pensum_pct", "title", "is_team_lead", "tenant"]
    list_filter = ["tenant", "is_team_lead"]


class TimeTemplateSegmentInline(admin.TabularInline):
    """
    Block 1.12: optionale Blockstruktur (z. B. Vormittag/Nachmittag mit
    fixer Mittagspause dazwischen) -- leer lassen für das bisherige
    Verhalten (ein Zeitfenster + break_minutes pauschal). Bis Block 2.9
    (Frontend-Oberfläche für Planer) ist dies der einzige Ort, um Segmente
    zu pflegen.
    """

    model = TimeTemplateSegment
    extra = 0
    exclude = ["tenant"]  # wird beim Speichern vom Template übernommen, siehe save_formset unten


@admin.register(TimeTemplate)
class TimeTemplateAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "node", "start_time", "end_time", "tenant"]
    list_filter = ["tenant", "node"]
    inlines = [TimeTemplateSegmentInline]


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "date", "template", "node", "tenant"]
    list_filter = ["tenant", "node", "date"]
    date_hierarchy = "date"


@admin.register(AbsenceType)
class AbsenceTypeAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "color", "deducts_vacation_days", "counts_as_sick_leave", "tenant"]
    list_filter = ["tenant"]


@admin.register(Absence)
class AbsenceAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "type", "start_date", "end_date", "day_portion", "tenant"]
    list_filter = ["tenant", "type", "day_portion"]
    date_hierarchy = "start_date"


@admin.register(OvertimeSettlement)
class OvertimeSettlementAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "year", "month", "hours", "surcharge_hours", "confirmed_at", "tenant"]
    list_filter = ["tenant", "year"]


@admin.register(PayrollCategoryMapping)
class PayrollCategoryMappingAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "category",
        "special_template",
        "absence_type",
        "payroll_code",
        "payroll_label",
        "is_active",
        "tenant",
    ]
    list_filter = ["tenant", "is_active"]


@admin.register(Pregnancy)
class PregnancyAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "expected_birth_date", "actual_birth_date", "tenant"]
    list_filter = ["tenant"]
    date_hierarchy = "expected_birth_date"


@admin.register(ShiftPreference)
class ShiftPreferenceAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "type", "date", "template", "tenant"]
    list_filter = ["tenant", "type"]
    date_hierarchy = "date"


@admin.register(ShiftTradeRequest)
class ShiftTradeRequestAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["requester_assignment", "target_employee", "status", "tenant", "created_at"]
    list_filter = ["tenant", "status"]


class TimeRecordSegmentInline(admin.TabularInline):
    model = TimeRecordSegment
    extra = 0
    exclude = ["tenant"]


@admin.register(TimeRecord)
class TimeRecordAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["assignment", "actual_start", "actual_end", "status", "tenant"]
    list_filter = ["tenant", "status"]
    inlines = [TimeRecordSegmentInline]

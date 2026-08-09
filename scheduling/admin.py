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
    form = movenodeform_factory(Node)
    list_display = ["name", "tenant"]
    list_filter = ["tenant"]


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

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            instance.tenant = form.instance.tenant
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "date", "template", "node", "tenant"]
    list_filter = ["tenant", "node", "date"]
    date_hierarchy = "date"


@admin.register(AbsenceType)
class AbsenceTypeAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "color", "deducts_vacation_days", "tenant"]
    list_filter = ["tenant"]


@admin.register(Absence)
class AbsenceAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ["employee", "type", "start_date", "end_date", "day_portion", "tenant"]
    list_filter = ["tenant", "type", "day_portion"]
    date_hierarchy = "start_date"


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

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            instance.tenant = form.instance.tenant
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()

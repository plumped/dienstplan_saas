from django.contrib import admin
from treebeard.admin import TreeAdmin
from treebeard.forms import movenodeform_factory

from .models import Employee, Node, ShiftAssignment, Skill, TimeTemplate


@admin.register(Node)
class NodeAdmin(TreeAdmin):
    form = movenodeform_factory(Node)
    list_display = ["name", "tenant"]
    list_filter = ["tenant"]


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ["name", "tenant"]
    list_filter = ["tenant"]


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ["last_name", "first_name", "tenant", "employment_pct", "is_active"]
    list_filter = ["tenant", "is_active"]
    filter_horizontal = ["nodes", "skills"]


@admin.register(TimeTemplate)
class TimeTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "node", "start_time", "end_time", "tenant"]
    list_filter = ["tenant", "node"]


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ["employee", "date", "template", "node", "tenant"]
    list_filter = ["tenant", "node", "date"]
    date_hierarchy = "date"

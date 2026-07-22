from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Membership, Tenant, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    pass


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active", "created_at"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role"]
    list_filter = ["tenant", "role"]

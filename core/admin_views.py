from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from core.middleware import ADMIN_TENANT_SESSION_KEY
from core.models import Tenant


@staff_member_required
def tenant_switch(request):
    """
    Aktiven Tenant für die laufende Admin-Session wählen (siehe
    core.middleware.AdminActiveTenantMiddleware) -- ohne das zeigen
    tenant-gescopte ModelAdmins (core.admin.TenantScopedAdminMixin)
    explizit nichts an. Bewusst ein eigenständiger, einfacher View statt
    Teil des Admin-URL-Baums, weil er vor jedem einzelnen ModelAdmin
    greifen muss, nicht objektbezogen ist.
    """
    if request.method == "POST":
        tenant_id = request.POST.get("tenant")
        if tenant_id and Tenant.objects.filter(pk=tenant_id).exists():
            request.session[ADMIN_TENANT_SESSION_KEY] = tenant_id
        else:
            request.session.pop(ADMIN_TENANT_SESSION_KEY, None)
        next_url = request.POST.get("next") or reverse("admin:index")
        return HttpResponseRedirect(next_url)

    return render(
        request,
        "admin/tenant_switch.html",
        {
            "tenants": Tenant.objects.order_by("name"),
            "active_id": request.session.get(ADMIN_TENANT_SESSION_KEY),
            "next": request.GET.get("next", ""),
        },
    )

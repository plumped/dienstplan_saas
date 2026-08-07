from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

from core.admin_views import tenant_switch
from core.views import MeView, TenantHolidaysView, TenantView
from scheduling.views import UnderstaffedShiftsView

urlpatterns = [
    # Muss VOR 'admin/' stehen: admin.site.urls fängt sonst alles unter
    # admin/ selbst ab (siehe core.middleware.AdminActiveTenantMiddleware,
    # core.admin_views.tenant_switch).
    path('admin/tenant-switch/', tenant_switch, name='admin-tenant-switch'),
    path('admin/', admin.site.urls),
    path('api/', include('scheduling.urls')),
    path('api/', include('core.urls')),
    path('api/me/', MeView.as_view()),
    path('api/tenant/', TenantView.as_view()),
    path('api/tenant/holidays/', TenantHolidaysView.as_view()),
    # README Punkt 21 (Dashboard): einziger neuer Endpoint für das
    # Admin/Planer-Dashboard -- alle anderen Dashboard-Daten (offene
    # Absenzen/Tauschanfragen, wer abwesend ist) kommen aus bereits
    # bestehenden Endpoints (siehe Dashboard.jsx), nur die
    # stationsübergreifende Mindestbesetzungs-Auswertung braucht neue
    # Backend-Logik (siehe UnderstaffedShiftsView-Docstring).
    path('api/understaffed-shifts/', UnderstaffedShiftsView.as_view()),
    path('api/auth/token/', obtain_auth_token),
    path('api-auth/', include('rest_framework.urls')),
]

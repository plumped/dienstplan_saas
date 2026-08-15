from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

from core.admin_views import tenant_switch
from core.billing_views import (
    BillingStatusView,
    CreateBillingPortalSessionView,
    CreateCheckoutSessionView,
    stripe_webhook,
)
from core.views import ChangePasswordView, MeView, SignupView, TenantHolidaysView, TenantView
from scheduling.views import (
    EmployeeDataExportView,
    PayrollExportView,
    PlanExportView,
    UnderstaffedShiftsView,
)

urlpatterns = [
    # Muss VOR 'admin/' stehen: admin.site.urls fängt sonst alles unter
    # admin/ selbst ab (siehe core.middleware.AdminActiveTenantMiddleware,
    # core.admin_views.tenant_switch).
    path('admin/tenant-switch/', tenant_switch, name='admin-tenant-switch'),
    path('admin/', admin.site.urls),
    path('api/', include('scheduling.urls')),
    path('api/', include('core.urls')),
    path('api/me/', MeView.as_view()),
    path('api/me/change-password/', ChangePasswordView.as_view()),
    # README Block 5 (Datenschutz & Rechtliches): Auskunftsrecht/Datenherausgabe, siehe
    # EmployeeDataExportView-Docstring.
    path('api/me/data-export/', EmployeeDataExportView.as_view()),
    path('api/tenant/', TenantView.as_view()),
    path('api/tenant/holidays/', TenantHolidaysView.as_view()),
    # README Punkt 21 (Dashboard): einziger neuer Endpoint für das
    # Admin/Planer-Dashboard -- alle anderen Dashboard-Daten (offene
    # Absenzen/Tauschanfragen, wer abwesend ist) kommen aus bereits
    # bestehenden Endpoints (siehe Dashboard.jsx), nur die
    # stationsübergreifende Mindestbesetzungs-Auswertung braucht neue
    # Backend-Logik (siehe UnderstaffedShiftsView-Docstring).
    path('api/understaffed-shifts/', UnderstaffedShiftsView.as_view()),
    # README Block 2 Punkt 31: Lohn-Rohdaten-Export, siehe PayrollExportView-Docstring.
    path('api/payroll-export/', PayrollExportView.as_view()),
    # README Block 2 Punkt 5: Planblatt-Export (PDF/CSV), siehe PlanExportView-Docstring.
    path('api/plan-export/', PlanExportView.as_view()),
    path('api/auth/token/', obtain_auth_token),
    # README Block 3: Self-Signup (Direkt-Registrierung ohne E-Mail-Versand),
    # siehe core.views.SignupView-Docstring -- neben obtain_auth_token der
    # einzige bewusst unauthentifizierte Schreib-Endpoint.
    path('api/signup/', SignupView.as_view()),
    # README Block 6: Abrechnung -- siehe core/billing_views.py-Docstrings.
    path('api/billing/status/', BillingStatusView.as_view()),
    path('api/billing/checkout/', CreateCheckoutSessionView.as_view()),
    path('api/billing/portal/', CreateBillingPortalSessionView.as_view()),
    path('api/billing/webhook/', stripe_webhook),
    path('api-auth/', include('rest_framework.urls')),
]

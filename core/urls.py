from rest_framework.routers import DefaultRouter

from .views import MembershipViewSet, TenantHolidayOverrideViewSet

router = DefaultRouter()
router.register("tenant-holiday-overrides", TenantHolidayOverrideViewSet, basename="tenantholidayoverride")
router.register("memberships", MembershipViewSet, basename="membership")

urlpatterns = router.urls

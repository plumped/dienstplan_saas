from rest_framework.routers import DefaultRouter

from .views import TenantHolidayOverrideViewSet

router = DefaultRouter()
router.register("tenant-holiday-overrides", TenantHolidayOverrideViewSet, basename="tenantholidayoverride")

urlpatterns = router.urls

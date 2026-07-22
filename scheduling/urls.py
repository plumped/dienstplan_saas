from rest_framework.routers import DefaultRouter

from .views import (
    EmployeeViewSet,
    NodeViewSet,
    ShiftAssignmentViewSet,
    SkillViewSet,
    TimeTemplateViewSet,
)

router = DefaultRouter()
router.register("nodes", NodeViewSet, basename="node")
router.register("skills", SkillViewSet, basename="skill")
router.register("employees", EmployeeViewSet, basename="employee")
router.register("time-templates", TimeTemplateViewSet, basename="timetemplate")
router.register("shift-assignments", ShiftAssignmentViewSet, basename="shiftassignment")

urlpatterns = router.urls

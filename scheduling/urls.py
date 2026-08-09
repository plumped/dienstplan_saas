from rest_framework.routers import DefaultRouter

from .views import (
    AbsenceTypeViewSet,
    AbsenceViewSet,
    EmployeeViewSet,
    NodeViewSet,
    PregnancyViewSet,
    ShiftAssignmentViewSet,
    ShiftPreferenceViewSet,
    ShiftTradeRequestViewSet,
    SkillViewSet,
    TimeRecordViewSet,
    TimeTemplateViewSet,
)

router = DefaultRouter()
router.register("nodes", NodeViewSet, basename="node")
router.register("skills", SkillViewSet, basename="skill")
router.register("employees", EmployeeViewSet, basename="employee")
router.register("time-templates", TimeTemplateViewSet, basename="timetemplate")
router.register("absence-types", AbsenceTypeViewSet, basename="absencetype")
router.register("shift-assignments", ShiftAssignmentViewSet, basename="shiftassignment")
router.register("absences", AbsenceViewSet, basename="absence")
router.register("pregnancies", PregnancyViewSet, basename="pregnancy")
router.register("shift-preferences", ShiftPreferenceViewSet, basename="shiftpreference")
router.register("shift-trade-requests", ShiftTradeRequestViewSet, basename="shifttraderequest")
router.register("time-records", TimeRecordViewSet, basename="timerecord")

urlpatterns = router.urls

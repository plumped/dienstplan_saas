from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import IntegrityError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core.models import Membership, Tenant

from .models import (
    Absence,
    AbsenceType,
    Employee,
    Employment,
    Node,
    ShiftAssignment,
    ShiftPreference,
    ShiftTradeRequest,
    Skill,
    TimeRecord,
    TimeRecordSegment,
    TimeTemplate,
    TimeTemplateSegment,
)

User = get_user_model()


def make_tenant_with_planner(slug, username):
    tenant = Tenant.objects.create(name=slug, slug=slug)
    user = User.objects.create_user(username=username, password="s3cret-not-real!")
    Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.PLANNER)
    return tenant, user


class TwoTenantFixtureMixin:
    """
    Baut zwei komplett unabhängige Tenants mit je einem Standort, einer
    Qualifikation, einem Mitarbeiter und einem Schichttyp auf -- die
    Grundlage für alle Cross-Tenant-Sicherheitstests unten.
    """

    def setUp(self):
        super().setUp()
        self.tenant_a, self.user_a = make_tenant_with_planner("klinik-a", "planner_a")
        self.tenant_b, self.user_b = make_tenant_with_planner("klinik-b", "planner_b")

        self.node_a = Node.add_root(name="Station A", tenant=self.tenant_a)
        self.node_b = Node.add_root(name="Station B", tenant=self.tenant_b)

        self.skill_a = Skill.objects.create(tenant=self.tenant_a, name="Nachtdienst")
        self.skill_b = Skill.objects.create(tenant=self.tenant_b, name="Nachtdienst")

        self.employee_a = Employee.objects.create(
            tenant=self.tenant_a, first_name="Anna", last_name="A", employment_pct=100
        )
        self.employee_a.nodes.add(self.node_a)
        self.employee_b = Employee.objects.create(
            tenant=self.tenant_b, first_name="Bea", last_name="B", employment_pct=100
        )
        self.employee_b.nodes.add(self.node_b)

        self.template_a = TimeTemplate.objects.create(
            tenant=self.tenant_a,
            node=self.node_a,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
        )
        self.template_b = TimeTemplate.objects.create(
            tenant=self.tenant_b,
            node=self.node_b,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
        )

        self.absence_type_a = AbsenceType.objects.create(
            tenant=self.tenant_a, name="Ferien", deducts_vacation_days=True
        )
        self.absence_type_b = AbsenceType.objects.create(
            tenant=self.tenant_b, name="Ferien", deducts_vacation_days=True
        )

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")


class AuthenticationTests(APITestCase):
    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")

    def test_anonymous_request_is_rejected(self):
        # SessionAuthentication steht in DEFAULT_AUTHENTICATION_CLASSES an
        # erster Stelle und bietet keinen WWW-Authenticate-Header an -> DRF
        # liefert dafür bewusst 403 statt 401 (siehe DRF APIView.handle_exception).
        response = self.client.get("/api/nodes/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_token_obtain_with_valid_credentials(self):
        response = self.client.post(
            "/api/auth/token/", {"username": "planner_a", "password": "s3cret-not-real!"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)

    def test_token_obtain_with_invalid_credentials(self):
        response = self.client.post(
            "/api/auth/token/", {"username": "planner_a", "password": "wrong"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authenticated_user_without_membership_sees_empty_lists(self):
        user = User.objects.create_user(username="orphan", password="irrelevant-123")
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get("/api/nodes/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])


class CrossTenantIsolationTests(TwoTenantFixtureMixin, APITestCase):
    """
    Kern-Sicherheitsgarantie der Multi-Tenancy: ein eingeloggter Planer einer
    Klinik darf unter keinen Umständen Daten einer anderen Klinik sehen,
    lesen oder referenzieren können.
    """

    def test_node_list_is_scoped_to_own_tenant(self):
        self.auth_as(self.user_a)
        response = self.client.get("/api/nodes/")
        ids = [n["id"] for n in response.data["results"]]
        self.assertEqual(ids, [self.node_a.id])

    def test_node_retrieve_of_other_tenant_is_not_found(self):
        self.auth_as(self.user_a)
        response = self.client.get(f"/api/nodes/{self.node_b.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_node_create_with_foreign_parent_is_rejected(self):
        self.auth_as(self.user_a)
        response = self.client.post("/api/nodes/", {"name": "Unterstation", "parent": self.node_b.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_list_is_scoped_to_own_tenant(self):
        self.auth_as(self.user_b)
        response = self.client.get("/api/employees/")
        ids = [e["id"] for e in response.data["results"]]
        self.assertEqual(ids, [self.employee_b.id])

    def test_skill_list_is_scoped_to_own_tenant(self):
        self.auth_as(self.user_a)
        response = self.client.get("/api/skills/")
        ids = [s["id"] for s in response.data["results"]]
        self.assertEqual(ids, [self.skill_a.id])

    def test_time_template_list_is_scoped_to_own_tenant(self):
        self.auth_as(self.user_a)
        response = self.client.get("/api/time-templates/")
        ids = [t["id"] for t in response.data["results"]]
        self.assertEqual(ids, [self.template_a.id])

    def test_shift_assignment_create_with_foreign_employee_is_rejected(self):
        # employee gehört zu Tenant B, angemeldet ist Planer von Tenant A --
        # die (automatisch generierte) tenant-gefilterte Queryset des
        # PrimaryKeyRelatedField muss das PK schon vor jeder eigenen Logik
        # als "existiert nicht" ablehnen.
        self.auth_as(self.user_a)
        response = self.client.post(
            "/api/shift-assignments/",
            {
                "employee": self.employee_b.id,
                "node": self.node_a.id,
                "date": "2026-08-03",
                "template": self.template_a.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_shift_assignment_list_is_scoped_to_own_tenant(self):
        a1 = ShiftAssignment.objects.create(
            tenant=self.tenant_a,
            employee=self.employee_a,
            node=self.node_a,
            date=date(2026, 8, 3),
            template=self.template_a,
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant_b,
            employee=self.employee_b,
            node=self.node_b,
            date=date(2026, 8, 3),
            template=self.template_b,
        )
        self.auth_as(self.user_a)
        response = self.client.get("/api/shift-assignments/")
        ids = [a["id"] for a in response.data["results"]]
        self.assertEqual(ids, [a1.id])

    def test_absence_list_is_scoped_to_own_tenant(self):
        absence_a = Absence.objects.create(
            tenant=self.tenant_a,
            employee=self.employee_a,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            type=self.absence_type_a,
        )
        Absence.objects.create(
            tenant=self.tenant_b,
            employee=self.employee_b,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            type=self.absence_type_b,
        )
        self.auth_as(self.user_a)
        response = self.client.get("/api/absences/")
        ids = [a["id"] for a in response.data["results"]]
        self.assertEqual(ids, [absence_a.id])

    def test_shift_trade_request_create_with_foreign_target_is_rejected(self):
        assignment_a = ShiftAssignment.objects.create(
            tenant=self.tenant_a,
            employee=self.employee_a,
            node=self.node_a,
            date=date(2026, 8, 3),
            template=self.template_a,
        )
        self.auth_as(self.user_a)
        response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": assignment_a.id, "target_employee": self.employee_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RuleEngineTests(TestCase):
    """Modell-Ebene: ShiftAssignment.clean() -- Ruhezeit, Höchstarbeitszeit, Qualifikation, Absenzen."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.day_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,  # 8h Spanne -> 7.5h netto -> Art. 15 ArG verlangt 30 Min.
        )
        self.night_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Nachtdienst",
            start_time=time(20, 0),
            end_time=time(8, 0),
            break_minutes=60,  # 12h Spanne -> 11h netto -> Art. 15 ArG verlangt 60 Min.
        )

    def test_valid_assignment_passes_clean(self):
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.day_template,
        )
        assignment.clean()  # keine Exception

    def test_rest_period_violation_is_rejected(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.night_template,  # 20:00-08:00
        )
        # Direkt anschliessender Frühdienst am nächsten Tag -> nur 0h Ruhezeit
        next_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 4),
            template=self.day_template,  # 08:00-16:00
        )
        with self.assertRaises(ValidationError):
            next_shift.clean()

    def test_sufficient_rest_period_passes(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.day_template,  # endet 16:00
        )
        next_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 4),
            template=self.day_template,  # beginnt 08:00 -> 16h Pause
        )
        next_shift.clean()  # keine Exception

    def test_required_skill_missing_is_rejected(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Nachtdienst-berechtigt")
        self.night_template.required_skill = skill
        self.night_template.save()

        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.night_template,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_required_skill_present_passes(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Nachtdienst-berechtigt")
        self.night_template.required_skill = skill
        self.night_template.save()
        self.employee.skills.add(skill)

        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.night_template,
        )
        assignment.clean()  # keine Exception

    def test_maximum_weekly_hours_violation_is_rejected(self):
        monday = date(2026, 8, 3)
        for offset in range(6):  # Mo-Sa, je 7.5h netto = 45h
            ShiftAssignment.objects.create(
                tenant=self.tenant,
                employee=self.employee,
                node=self.node,
                date=monday + timedelta(days=offset),
                template=self.day_template,
            )
        sunday_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=monday + timedelta(days=6),
            template=self.day_template,  # weitere 7.5h -> 52.5h, über dem 45h-Limit des Tenants
        )
        with self.assertRaises(ValidationError):
            sunday_shift.clean()

    def test_employee_override_raises_maximum_weekly_hours(self):
        # Block 1.14: z. B. Ärzteschaft mit vertraglich 60h statt der
        # 45h-Tenant-Vorgabe -- Employee.maximum_weekly_hours überschreibt
        # den Tenant-Wert nur für diesen Mitarbeiter. 6 Schichten a 9h netto
        # (Sonntag bleibt frei, damit nur der Wochenstunden-Check greift,
        # nicht der wöchentliche Ruhetag).
        doctor = Employee.objects.create(
            tenant=self.tenant,
            first_name="Doktor",
            last_name="D",
            employment_pct=100,
            maximum_weekly_hours=60,
        )
        long_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Langer Dienst",
            start_time=time(8, 0),
            end_time=time(18, 0),
            break_minutes=60,  # 10h Spanne - 1h Pause = 9h netto
        )
        monday = date(2026, 8, 3)
        for offset in range(5):  # Mo-Fr, je 9h netto = 45h
            ShiftAssignment.objects.create(
                tenant=self.tenant,
                employee=doctor,
                node=self.node,
                date=monday + timedelta(days=offset),
                template=long_template,
            )
        saturday_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=doctor,
            node=self.node,
            date=monday + timedelta(days=5),
            template=long_template,  # 54h gesamt -- über Tenant-45h, aber unter Override-60h
        )
        saturday_shift.clean()  # keine Exception

    def test_absence_conflict_is_rejected(self):
        vacation = AbsenceType.objects.create(tenant=self.tenant, name="Ferien", deducts_vacation_days=True)
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 10),
            type=vacation,
            status=Absence.Status.APPROVED,
        )
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 5),
            template=self.day_template,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_api_validate_runs_rule_engine(self):
        # Bestätigt, dass die Regel-Engine auch über den ShiftAssignmentSerializer
        # greift (validate() ruft clean() auf), nicht nur im Admin.
        vacation = AbsenceType.objects.create(tenant=self.tenant, name="Ferien", deducts_vacation_days=True)
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 10),
            type=vacation,
            status=Absence.Status.APPROVED,
        )
        from unittest.mock import MagicMock

        from .serializers import ShiftAssignmentSerializer

        request = MagicMock()
        request.tenant = self.tenant
        serializer = ShiftAssignmentSerializer(
            data={
                "employee": self.employee.id,
                "node": self.node.id,
                "date": "2026-08-05",
                "template": self.day_template.id,
            },
            context={"request": request},
        )
        self.assertFalse(serializer.is_valid())

    def test_break_minutes_violation_is_rejected(self):
        # 8h Spanne (>7h netto), aber keine Pause hinterlegt -> Art. 15 ArG verlangt 30 Min.
        template_without_break = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Ohne Pause", start_time=time(8, 0), end_time=time(16, 0)
        )
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=template_without_break,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_break_minutes_sufficient_passes(self):
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.day_template,  # hat bereits 30 Min. Pause (siehe setUp)
        )
        assignment.clean()  # keine Exception

    def test_daily_span_violation_is_rejected(self):
        # 15h Spanne -> über der Tenant-Grenze von 14h (Art. 10 ArG), Pause ausreichend
        # hoch angesetzt, damit gezielt nur die Tagesspanne greift.
        long_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Marathon-Schicht",
            start_time=time(6, 0),
            end_time=time(21, 0),
            break_minutes=60,
        )
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=long_template,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_weekly_rest_day_violation_is_rejected(self):
        # Kurze Schichten (2h/Tag), damit nicht schon die Wochenhöchstarbeitszeit greift --
        # gezielter Test für Art. 21 ArG (mind. 1 freier Tag pro Woche).
        short_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Kurzeinsatz", start_time=time(9, 0), end_time=time(11, 0)
        )
        monday = date(2026, 8, 3)
        for offset in range(6):  # Mo-Sa belegt
            ShiftAssignment.objects.create(
                tenant=self.tenant,
                employee=self.employee,
                node=self.node,
                date=monday + timedelta(days=offset),
                template=short_template,
            )
        sunday_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=monday + timedelta(days=6),
            template=short_template,  # 7. Tag derselben Woche -> kein freier Tag mehr übrig
        )
        with self.assertRaises(ValidationError):
            sunday_shift.clean()

    def test_weekly_rest_day_with_one_free_day_passes(self):
        short_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Kurzeinsatz", start_time=time(9, 0), end_time=time(11, 0)
        )
        monday = date(2026, 8, 3)
        for offset in range(5):  # Mo-Fr belegt, Sa+So frei
            ShiftAssignment.objects.create(
                tenant=self.tenant,
                employee=self.employee,
                node=self.node,
                date=monday + timedelta(days=offset),
                template=short_template,
            )
        saturday_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=monday + timedelta(days=5),
            template=short_template,  # Sonntag bleibt frei
        )
        saturday_shift.clean()  # keine Exception

    def test_maximum_weekly_hours_is_tenant_configurable(self):
        strict_tenant = Tenant.objects.create(
            name="Klinik streng", slug="klinik-streng", maximum_weekly_hours=10
        )
        node = Node.add_root(name="Station", tenant=strict_tenant)
        employee = Employee.objects.create(
            tenant=strict_tenant, first_name="Chris", last_name="C", employment_pct=100
        )
        template = TimeTemplate.objects.create(
            tenant=strict_tenant,
            node=node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,  # 7.5h netto
        )
        ShiftAssignment.objects.create(
            tenant=strict_tenant, employee=employee, node=node, date=date(2026, 8, 3), template=template
        )
        second_assignment = ShiftAssignment(
            tenant=strict_tenant,
            employee=employee,
            node=node,
            date=date(2026, 8, 4),
            template=template,
        )
        with self.assertRaises(ValidationError):
            second_assignment.clean()  # 2x7.5h = 15h > 10h-Limit dieses Tenants

        # Zum Vergleich: derselbe Fall wäre unter dem Standard-Tenant-Limit (45h) unproblematisch.
        default_assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 4),
            template=self.day_template,
        )
        default_assignment.clean()  # keine Exception

    def test_night_hours_covers_full_night_window(self):
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.night_template,  # 20:00-08:00, deckt 23:00-06:00 vollständig ab
        )
        self.assertEqual(assignment.night_hours, 7.0)

    def test_night_hours_zero_for_day_shift(self):
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.day_template,
        )
        self.assertEqual(assignment.night_hours, 0.0)

    def test_is_sunday_property(self):
        sunday = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 2),  # ein Sonntag
            template=self.day_template,
        )
        monday = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.day_template,
        )
        self.assertTrue(sunday.is_sunday)
        self.assertFalse(monday.is_sunday)

    def test_adult_may_work_nights_and_sundays(self):
        # Gegenprobe zu den Jugendschutz-Tests unten: für Erwachsene (kein
        # birth_date) sind Nacht-/Sonntagsarbeit nur informativ, nicht blockiert.
        night_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.night_template,
        )
        night_shift.clean()  # keine Exception

        sunday_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 2),  # ein Sonntag
            template=self.day_template,
        )
        sunday_shift.clean()  # keine Exception

    def test_youth_minimum_rest_hours_is_stricter(self):
        minor = Employee.objects.create(
            tenant=self.tenant,
            first_name="Nina",
            last_name="Jung",
            birth_date=date(2009, 1, 1),  # 17 Jahre alt am 2026-08-04
            employment_pct=100,
        )
        early_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Frühdienst kurz", start_time=time(3, 0), end_time=time(7, 0)
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=minor,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.day_template,  # endet 16:00
        )
        next_shift = ShiftAssignment(
            tenant=self.tenant,
            employee=minor,
            node=self.node,
            date=date(2026, 8, 4),
            template=early_template,  # beginnt 03:00 -> 11h Pause
        )
        # 11h Ruhezeit reicht für Erwachsene (Tenant-Default), aber nicht für
        # Jugendliche (ArGV 5 verlangt 12h).
        with self.assertRaises(ValidationError):
            next_shift.clean()

    def test_youth_no_night_work_is_rejected(self):
        minor = Employee.objects.create(
            tenant=self.tenant,
            first_name="Nina",
            last_name="Jung",
            birth_date=date(2009, 1, 1),
            employment_pct=100,
        )
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=minor,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.night_template,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_youth_no_sunday_work_is_rejected(self):
        minor = Employee.objects.create(
            tenant=self.tenant,
            first_name="Nina",
            last_name="Jung",
            birth_date=date(2009, 1, 1),
            employment_pct=100,
        )
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=minor,
            node=self.node,
            date=date(2026, 8, 2),  # ein Sonntag
            template=self.day_template,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_is_minor_on_boundary(self):
        employee = Employee.objects.create(
            tenant=self.tenant,
            first_name="Bea",
            last_name="B",
            birth_date=date(2008, 8, 4),  # wird am 2026-08-04 genau 18
            employment_pct=100,
        )
        self.assertTrue(employee.is_minor_on(date(2026, 8, 3)))
        self.assertFalse(employee.is_minor_on(date(2026, 8, 4)))

    def test_employee_without_birth_date_is_not_minor(self):
        self.assertFalse(self.employee.is_minor_on(date(2026, 8, 3)))


class AbsenceModelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(17, 0),
            break_minutes=60,
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )

    def test_end_date_before_start_date_is_rejected(self):
        absence = Absence(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 1),
        )
        with self.assertRaises(ValidationError):
            absence.clean()

    # --- Bugfix 2026-08: Absenz darf nicht mit bestehenden Zuweisungen überlappen
    # (umgekehrte Richtung zu ShiftAssignment._check_no_absence_conflict) ---

    def test_approved_absence_overlapping_assignment_is_rejected(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 5), template=self.template
        )
        absence = Absence(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 7),
            status=Absence.Status.APPROVED,
        )
        with self.assertRaises(ValidationError):
            absence.clean()

    def test_pending_absence_overlapping_assignment_is_allowed(self):
        # Nur genehmigte Absenzen blockieren -- ein offener Antrag soll die
        # Planung nicht vorab einschränken (analog zu ShiftAssignment, das
        # ebenfalls nur gegen APPROVED-Absenzen prüft).
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 5), template=self.template
        )
        absence = Absence(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 7),
            status=Absence.Status.PENDING,
        )
        absence.clean()  # darf nicht werfen

    def test_approved_absence_without_conflict_passes(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 10), template=self.template
        )
        absence = Absence(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 7),
            status=Absence.Status.APPROVED,
        )
        absence.clean()  # kein überlappender Tag -- darf nicht werfen


class AbsenceTypeTests(TestCase):
    """
    Nutzer-Feedback (2026-08): Absenzarten sollen frei definierbar sein statt
    hartcodiert (Ferien/Krankheit/Sonstiges) -- analog zu TimeTemplate als
    tenant-eigener Katalog. deducts_vacation_days ersetzt den alten
    `type=Absence.Type.VACATION`-Vergleich in Employee.vacation_balance().
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def test_defaults(self):
        absence_type = AbsenceType.objects.create(tenant=self.tenant, name="Sonstiges")
        self.assertEqual(absence_type.color, "#64748b")
        self.assertFalse(absence_type.deducts_vacation_days)

    def test_str_returns_name(self):
        absence_type = AbsenceType.objects.create(tenant=self.tenant, name="Militärdienst")
        self.assertEqual(str(absence_type), "Militärdienst")

    def test_ordering_is_alphabetical_by_name(self):
        AbsenceType.objects.create(tenant=self.tenant, name="Sonstiges")
        AbsenceType.objects.create(tenant=self.tenant, name="Ferien")
        AbsenceType.objects.create(tenant=self.tenant, name="Krankheit")
        names = list(AbsenceType.objects.filter(tenant=self.tenant).values_list("name", flat=True))
        self.assertEqual(names, ["Ferien", "Krankheit", "Sonstiges"])


class AbsenceTypeAPITests(APITestCase):
    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_create_and_round_trip_through_serializer(self):
        create_response = self.client.post(
            "/api/absence-types/",
            {"name": "Militärdienst", "color": "#336699", "deducts_vacation_days": False},
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["name"], "Militärdienst")
        self.assertEqual(create_response.data["color"], "#336699")

        type_id = create_response.data["id"]
        patch_response = self.client.patch(f"/api/absence-types/{type_id}/", {"deducts_vacation_days": True})
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertTrue(patch_response.data["deducts_vacation_days"])

    def test_list_is_scoped_to_own_tenant(self):
        AbsenceType.objects.create(tenant=self.tenant, name="Ferien")
        other_tenant, _ = make_tenant_with_planner("klinik-b", "planner_b")
        AbsenceType.objects.create(tenant=other_tenant, name="Ferien B")

        response = self.client.get("/api/absence-types/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [item["name"] for item in response.data["results"]]
        self.assertEqual(names, ["Ferien"])

    def test_employee_can_read_but_not_write(self):
        employee_user = User.objects.create_user(username="employee_a", password="pw-not-real-123!")
        Membership.objects.create(user=employee_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        token, _ = Token.objects.get_or_create(user=employee_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        self.assertEqual(self.client.get("/api/absence-types/").status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.post("/api/absence-types/", {"name": "Sonstiges"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class TimeRecordTests(TestCase):
    """Ist-Arbeitszeiterfassung (Art. 73 ArGV 1, MVP-Fahrplan Block 1.9)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Fruehdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            break_minutes=30,
        )
        self.yesterday = timezone.localdate() - timedelta(days=1)
        self.assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=self.yesterday, template=self.template
        )

    def test_valid_time_record_within_tolerance_passes_clean(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 5),
            actual_end=time(15, 5),
            actual_break_minutes=30,
        )
        record.clean()  # 5 Min. Abweichung, unter der Default-Toleranz von 15 Min.

    def test_future_assignment_is_rejected(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        future_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=tomorrow, template=self.template
        )
        record = TimeRecord(
            tenant=self.tenant,
            assignment=future_assignment,
            actual_start=time(7, 0),
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        with self.assertRaises(ValidationError):
            record.clean()

    def test_large_deviation_without_note_is_rejected(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 30),  # 30 Min. Abweichung > Default-Toleranz von 15 Min.
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        with self.assertRaises(ValidationError):
            record.clean()

    def test_large_deviation_with_note_passes(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 30),
            actual_end=time(15, 0),
            actual_break_minutes=30,
            note="Verspätung wegen Stau",
        )
        record.clean()  # keine Exception, Begründung vorhanden

    def test_deviation_minutes_property(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 12),
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        self.assertEqual(record.deviation_minutes, 12)

    def test_actual_hours_property(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 0),
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        self.assertEqual(record.actual_hours, 7.5)

    def test_break_below_minimum_is_flagged(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 0),
            actual_end=time(15, 0),
            actual_break_minutes=10,  # 8h brutto -> netto >7h verlangt 30 Min. Pause (Art. 15 ArG)
            note="Pause verkürzt",
        )
        self.assertTrue(record.break_below_minimum)

    def test_sufficient_break_is_not_flagged(self):
        record = TimeRecord(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 0),
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        self.assertFalse(record.break_below_minimum)

    def test_confirm_transitions_status(self):
        record = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 0),
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        record.confirm()
        record.refresh_from_db()
        self.assertEqual(record.status, TimeRecord.Status.CONFIRMED)

    def test_confirm_twice_is_rejected(self):
        record = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=self.assignment,
            actual_start=time(7, 0),
            actual_end=time(15, 0),
            actual_break_minutes=30,
        )
        record.confirm()
        with self.assertRaises(ValidationError):
            record.confirm()


class SegmentedTimeTemplateTests(TestCase):
    """
    Block 1.12: TimeTemplate mit expliziter Blockstruktur (z. B. Vormittag/
    Nachmittag mit fixer Mittagspause dazwischen) statt eines einzelnen
    Zeitfensters + pauschaler break_minutes. Die Pause ergibt sich aus der
    Lücke zwischen zwei Segmenten.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        # Frühdienst mit Segmenten: 07:00-12:00 / 12:45-16:00 -> Pause 45 Min.
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Fruehdienst", start_time=time(7, 0), end_time=time(16, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=self.template, order=0, start_time=time(7, 0), end_time=time(12, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=self.template, order=1, start_time=time(12, 45), end_time=time(16, 0)
        )

    def test_effective_segments_returns_defined_segments(self):
        self.assertEqual(
            self.template.effective_segments(), [(time(7, 0), time(12, 0)), (time(12, 45), time(16, 0))]
        )

    def test_effective_segments_falls_back_without_segments(self):
        plain = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Spaetdienst", start_time=time(15, 0), end_time=time(23, 0)
        )
        self.assertEqual(plain.effective_segments(), [(time(15, 0), time(23, 0))])

    def test_shift_hours_sums_segment_durations(self):
        # 5h (07:00-12:00) + 3.25h (12:45-16:00) = 8.25h, Pause zählt nicht mit.
        self.assertEqual(ShiftAssignment._shift_hours(date(2026, 8, 3), self.template), 8.25)

    def test_break_check_passes_with_sufficient_gap(self):
        # Netto 8.25h -> Art. 15 verlangt 30 Min., Lücke zwischen den Segmenten ist 45 Min.
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        assignment.clean()  # keine Exception

    def test_break_check_rejects_insufficient_gap(self):
        short_gap_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Knappe Pause", start_time=time(7, 0), end_time=time(16, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=short_gap_template, order=0, start_time=time(7, 0), end_time=time(12, 0)
        )
        TimeTemplateSegment.objects.create(
            # Nur 10 Min. Lücke -> unter den 30 Min., die Art. 15 ArG bei >7h Nettoarbeitszeit verlangt.
            tenant=self.tenant,
            template=short_gap_template,
            order=1,
            start_time=time(12, 10),
            end_time=time(16, 0),
        )
        assignment = ShiftAssignment(
            tenant=self.tenant,
            employee=self.employee,
            node=self.node,
            date=date(2026, 8, 3),
            template=short_gap_template,
        )
        with self.assertRaises(ValidationError):
            assignment.clean()


class SegmentedTimeRecordTests(TestCase):
    """
    Block 1.12: Ist-Zeiterfassung für ein Template mit vorgegebener
    Blockstruktur -- der Mitarbeiter verschiebt nur die Uhrzeiten je Block
    (z. B. "07:03 statt 07:00", "Mittagspause wegen Notfallpatient erst um
    12:23 statt 12:00"), nicht die Anzahl der Blöcke.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Fruehdienst", start_time=time(7, 0), end_time=time(16, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=self.template, order=0, start_time=time(7, 0), end_time=time(12, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=self.template, order=1, start_time=time(12, 45), end_time=time(16, 0)
        )
        self.yesterday = timezone.localdate() - timedelta(days=1)
        self.assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=self.yesterday, template=self.template
        )

    def _record_with_pending_segments(self, segments, note=""):
        record = TimeRecord(tenant=self.tenant, assignment=self.assignment, note=note)
        record._pending_segments = segments
        return record

    def test_matching_segments_within_tolerance_pass_clean(self):
        record = self._record_with_pending_segments(
            [
                {"order": 0, "actual_start": time(7, 3), "actual_end": time(12, 23)},
                {"order": 1, "actual_start": time(13, 8), "actual_end": time(16, 5)},
            ]
        )
        record.clean()  # 3/5 Min. Abweichung, unter der Default-Toleranz von 15 Min.

    def test_segment_count_mismatch_is_rejected(self):
        record = self._record_with_pending_segments(
            [{"order": 0, "actual_start": time(7, 0), "actual_end": time(16, 0)}]
        )
        with self.assertRaises(ValidationError):
            record.clean()

    def test_overlapping_segments_are_rejected(self):
        record = self._record_with_pending_segments(
            [
                {"order": 0, "actual_start": time(7, 0), "actual_end": time(13, 0)},
                {"order": 1, "actual_start": time(12, 30), "actual_end": time(16, 0)},
            ]
        )
        with self.assertRaises(ValidationError):
            record.clean()

    def test_deviation_and_hours_properties_from_segments(self):
        record = self._record_with_pending_segments(
            [
                {"order": 0, "actual_start": time(7, 3), "actual_end": time(12, 23)},
                {"order": 1, "actual_start": time(13, 8), "actual_end": time(16, 5)},
            ]
        )
        self.assertEqual(record.deviation_minutes, 3)
        self.assertEqual(record.end_deviation_minutes, 5)
        self.assertEqual(record.actual_hours, 8.28)  # (5h20 + 2h57) = 8.2833h, gerundet
        self.assertEqual(record.break_minutes_total, 45)  # 13:08 - 12:23
        self.assertFalse(record.break_below_minimum)  # 45 Min. >= die geforderten 30 Min.


class SegmentedTimeTemplateAndRecordAPITests(APITestCase):
    """API-Ebene: verschachteltes Schreiben von Segmenten über die Serializer."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)
        self.alice_user = User.objects.create_user(username="alice", password="pw-not-real-123!")
        Membership.objects.create(user=self.alice_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.alice = Employee.objects.create(
            tenant=self.tenant, user=self.alice_user, first_name="Alice", last_name="A", employment_pct=100
        )

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_planner_can_create_time_template_with_segments(self):
        self.auth_as(self.planner_user)
        response = self.client.post(
            "/api/time-templates/",
            {
                "node": self.node.id,
                "name": "Fruehdienst",
                "start_time": "07:00",
                "end_time": "16:00",
                "segments": [
                    {"order": 0, "start_time": "07:00", "end_time": "12:00"},
                    {"order": 1, "start_time": "12:45", "end_time": "16:00"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["segments"]), 2)
        template = TimeTemplate.all_objects.get(pk=response.data["id"])
        self.assertEqual(template.effective_segments(), [(time(7, 0), time(12, 0)), (time(12, 45), time(16, 0))])

    def test_overlapping_segments_rejected_by_api(self):
        self.auth_as(self.planner_user)
        response = self.client.post(
            "/api/time-templates/",
            {
                "node": self.node.id,
                "name": "Ungueltig",
                "start_time": "07:00",
                "end_time": "16:00",
                "segments": [
                    {"order": 0, "start_time": "07:00", "end_time": "13:00"},
                    {"order": 1, "start_time": "12:00", "end_time": "16:00"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_updating_segments_replaces_previous_set(self):
        self.auth_as(self.planner_user)
        create_response = self.client.post(
            "/api/time-templates/",
            {
                "node": self.node.id,
                "name": "Fruehdienst",
                "start_time": "07:00",
                "end_time": "16:00",
                "segments": [
                    {"order": 0, "start_time": "07:00", "end_time": "12:00"},
                    {"order": 1, "start_time": "12:45", "end_time": "16:00"},
                ],
            },
            format="json",
        )
        template_id = create_response.data["id"]
        update_response = self.client.patch(
            f"/api/time-templates/{template_id}/",
            {"segments": [{"order": 0, "start_time": "07:00", "end_time": "16:00"}]},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(update_response.data["segments"]), 1)
        template = TimeTemplate.all_objects.get(pk=template_id)
        self.assertEqual(template.segments.count(), 1)

    def test_employee_can_record_own_segmented_time(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Fruehdienst", start_time=time(7, 0), end_time=time(16, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=template, order=0, start_time=time(7, 0), end_time=time(12, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=template, order=1, start_time=time(12, 45), end_time=time(16, 0)
        )
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.alice,
            node=self.node,
            date=timezone.localdate() - timedelta(days=1),
            template=template,
        )
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/time-records/",
            {
                "assignment": assignment.id,
                "segments": [
                    {"order": 0, "actual_start": "07:03", "actual_end": "12:23"},
                    {"order": 1, "actual_start": "13:08", "actual_end": "16:05"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["deviation_minutes"], 3)
        self.assertEqual(response.data["end_deviation_minutes"], 5)
        self.assertEqual(response.data["break_minutes_total"], 45)
        record = TimeRecord.all_objects.get(pk=response.data["id"])
        self.assertEqual(record.segments.count(), 2)

    def test_employee_segment_overlap_rejected_by_api(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Fruehdienst", start_time=time(7, 0), end_time=time(16, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=template, order=0, start_time=time(7, 0), end_time=time(12, 0)
        )
        TimeTemplateSegment.objects.create(
            tenant=self.tenant, template=template, order=1, start_time=time(12, 45), end_time=time(16, 0)
        )
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.alice,
            node=self.node,
            date=timezone.localdate() - timedelta(days=1),
            template=template,
        )
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/time-records/",
            {
                "assignment": assignment.id,
                "segments": [
                    {"order": 0, "actual_start": "07:00", "actual_end": "13:00"},
                    {"order": 1, "actual_start": "12:30", "actual_end": "16:00"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class WeeklyOvertimeTests(APITestCase):
    """
    Überzeitarbeit (Art. 13 ArG, MVP-Fahrplan Block 1.11): Soll/Ist-Vergleich pro
    Woche + Zuschlag. Bewusst getrennt von der Regel-Engine (ShiftAssignment.clean)
    -- reine Auswertung, keine Ablehnung von Zuweisungen.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(17, 0),
            break_minutes=60,  # 9h Spanne - 1h Pause = 8h netto pro Schicht
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)

        today = timezone.localdate()
        self.monday = today - timedelta(days=today.weekday())

    def _assign(self, employee, day_offset, template=None):
        return ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=employee,
            node=self.node,
            date=self.monday + timedelta(days=day_offset),
            template=template or self.template,
        )

    def test_no_shifts_means_no_overtime(self):
        summary = self.employee.weekly_hours_summary(self.monday)
        self.assertEqual(summary["soll_hours"], 42.0)  # Tenant-Default standard_weekly_hours
        self.assertEqual(summary["ist_hours"], 0)
        self.assertEqual(summary["overtime_hours"], 0)
        self.assertEqual(summary["surcharge_hours"], 0)

    def test_hours_under_soll_yield_no_overtime(self):
        for day in range(5):  # Mo-Fr, 5 * 8h = 40h < 42h Soll
            self._assign(self.employee, day)
        summary = self.employee.weekly_hours_summary(self.monday)
        self.assertEqual(summary["ist_hours"], 40.0)
        self.assertEqual(summary["overtime_hours"], 0)

    def test_hours_over_soll_yield_overtime_and_surcharge(self):
        for day in range(6):  # Mo-Sa, 6 * 8h = 48h > 42h Soll -> 6h Überzeit
            self._assign(self.employee, day)
        summary = self.employee.weekly_hours_summary(self.monday)
        self.assertEqual(summary["ist_hours"], 48.0)
        self.assertEqual(summary["overtime_hours"], 6.0)
        self.assertEqual(summary["surcharge_hours"], 1.5)  # 25% Zuschlag (Tenant-Default)

    def test_part_time_soll_is_scaled_by_employment_pct(self):
        part_time = Employee.objects.create(
            tenant=self.tenant, first_name="Bob", last_name="B", employment_pct=50
        )
        summary = part_time.weekly_hours_summary(self.monday)
        self.assertEqual(summary["soll_hours"], 21.0)  # 50% von 42h

    def test_employee_override_replaces_tenant_standard_weekly_hours(self):
        # Block 1.14: z. B. Ärzteschaft mit 50h statt der 42h-Tenant-Vorgabe.
        doctor = Employee.objects.create(
            tenant=self.tenant,
            first_name="Doktor",
            last_name="D",
            employment_pct=100,
            standard_weekly_hours=50,
        )
        summary = doctor.weekly_hours_summary(self.monday)
        self.assertEqual(summary["soll_hours"], 50.0)

    def test_time_record_overrides_planned_hours(self):
        assignment = self._assign(self.employee, 0)
        for day in range(1, 5):
            self._assign(self.employee, day)  # weitere 4 Tage a 8h geplant = 32h
        TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=assignment,
            actual_start=time(8, 0),
            actual_end=time(19, 0),  # 11h brutto
            actual_break_minutes=60,  # 10h netto statt geplanter 8h
        )
        summary = self.employee.weekly_hours_summary(self.monday)
        self.assertEqual(summary["ist_hours"], 42.0)  # 10h (Ist) + 4*8h (Planung) = 42h

    def test_shifts_outside_week_are_excluded(self):
        self._assign(self.employee, 0)  # Montag dieser Woche
        self._assign(self.employee, 7)  # Montag nächster Woche
        summary = self.employee.weekly_hours_summary(self.monday)
        self.assertEqual(summary["ist_hours"], 8.0)

    def test_week_normalizes_to_monday_regardless_of_reference_weekday(self):
        for day in range(6):
            self._assign(self.employee, day)
        summary_from_saturday = self.employee.weekly_hours_summary(self.monday + timedelta(days=5))
        self.assertEqual(summary_from_saturday["week_start"], self.monday)
        self.assertEqual(summary_from_saturday["ist_hours"], 48.0)

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_api_returns_weekly_overtime_for_given_week(self):
        for day in range(6):
            self._assign(self.employee, day)
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/weekly-overtime/?week={self.monday}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["overtime_hours"], 6.0)
        self.assertEqual(response.data["surcharge_hours"], 1.5)
        self.assertEqual(response.data["week_start"], str(self.monday))

    def test_api_rejects_invalid_week_param(self):
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/weekly-overtime/?week=not-a-date")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class NightAndSundayWorkTests(APITestCase):
    """
    Nacht-/Sonntagsarbeit (MVP-Fahrplan Block 1.5/1.6, Art. 17b/17c/19/20 ArG):
    Zeitgutschrift bei regelmässiger Nachtarbeit, Bewilligungs-Warnhinweis,
    arbeitsmedizinische Untersuchungspflicht, Sonntagszuschlag und die
    (vereinfachte) Ersatzruhetag-Kontrolle. Alles informativ, wie
    night_hours/is_sunday selbst -- nichts davon blockiert eine Zuweisung.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        # 23:00-06:00 deckt sich exakt mit dem Nachtarbeitszeitraum (Art. 16
        # ArG) -> 7h Nachtstunden pro Schicht, einfache Erwartungswerte.
        self.night_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nachtdienst", start_time=time(23, 0), end_time=time(6, 0)
        )
        self.day_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _assign_nights(self, employee, count, start=date(2026, 1, 5)):
        for i in range(count):
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=employee, node=self.node, date=start + timedelta(days=i),
                template=self.night_template,
            )

    # -- Nachtarbeit (Block 1.5) --

    def test_few_night_shifts_are_not_regular(self):
        self._assign_nights(self.employee, 5)
        summary = self.employee.night_work_summary(2026)
        self.assertEqual(summary["nights_count"], 5)
        self.assertFalse(summary["is_regular"])
        self.assertEqual(summary["surcharge_hours"], 0)
        self.assertFalse(summary["permit_warning"])  # nicht regelmässig -> keine Bewilligungspflicht
        self.assertFalse(summary["medical_exam_due"])

    def test_25_night_shifts_are_regular_with_surcharge(self):
        self._assign_nights(self.employee, 25)  # Schwellenwert (Tenant-Default)
        summary = self.employee.night_work_summary(2026)
        self.assertEqual(summary["nights_count"], 25)
        self.assertTrue(summary["is_regular"])
        self.assertEqual(summary["night_hours"], 175.0)  # 25 * 7h
        self.assertEqual(summary["surcharge_hours"], 17.5)  # 10% Zeitgutschrift (Tenant-Default)

    def test_permit_warning_when_regular_and_not_confirmed(self):
        self._assign_nights(self.employee, 25)
        summary = self.employee.night_work_summary(2026)
        self.assertTrue(summary["permit_warning"])
        self.tenant.night_work_permit_confirmed = True
        self.tenant.save()
        summary = self.employee.night_work_summary(2026)
        self.assertFalse(summary["permit_warning"])

    def test_medical_exam_due_without_prior_exam(self):
        self._assign_nights(self.employee, 25)
        self.assertIsNone(self.employee.last_night_work_medical_exam_date)
        self.assertTrue(self.employee.night_work_medical_exam_due(date(2026, 6, 1)))

    def test_medical_exam_not_due_within_two_year_interval(self):
        self._assign_nights(self.employee, 25)
        self.employee.last_night_work_medical_exam_date = date(2025, 1, 1)
        self.employee.save()
        self.assertFalse(self.employee.night_work_medical_exam_due(date(2026, 6, 1)))  # < 2 Jahre her

    def test_medical_exam_due_after_two_year_interval(self):
        self._assign_nights(self.employee, 25)
        self.employee.last_night_work_medical_exam_date = date(2023, 1, 1)
        self.employee.save()
        self.assertTrue(self.employee.night_work_medical_exam_due(date(2026, 6, 1)))  # > 2 Jahre her

    def test_medical_exam_interval_is_yearly_from_45(self):
        self.employee.birth_date = date(1980, 1, 1)  # wird 2026 bereits 45+
        self._assign_nights(self.employee, 25)
        self.employee.last_night_work_medical_exam_date = date(2025, 1, 1)
        self.employee.save()
        self.assertTrue(self.employee.night_work_medical_exam_due(date(2026, 6, 1)))  # > 1 Jahr her, ab 45 Pflicht

    def test_medical_exam_not_due_when_not_regular(self):
        self._assign_nights(self.employee, 5)
        self.assertFalse(self.employee.night_work_medical_exam_due(date(2026, 6, 1)))

    def test_api_night_work_endpoint(self):
        self._assign_nights(self.employee, 25)
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/night-work/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["nights_count"], 25)
        self.assertTrue(response.data["is_regular"])
        self.assertEqual(response.data["surcharge_hours"], 17.5)

    def test_api_night_work_rejects_invalid_year(self):
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/night-work/?year=not-a-year")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # -- Sonntagsarbeit (Block 1.6) --

    def test_sunday_shift_adds_surcharge_to_weekly_summary(self):
        sunday = date(2026, 8, 9)  # Sonntag
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=sunday, template=self.day_template
        )
        summary = self.employee.weekly_hours_summary(sunday)
        self.assertEqual(summary["sunday_hours"], 7.5)  # 8h Spanne - 30min Pause
        self.assertEqual(summary["sunday_surcharge_hours"], 3.75)  # 50% Zuschlag (Tenant-Default)

    def test_non_sunday_shift_has_no_sunday_surcharge(self):
        monday = date(2026, 8, 3)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=monday, template=self.day_template
        )
        summary = self.employee.weekly_hours_summary(monday)
        self.assertEqual(summary["sunday_hours"], 0)
        self.assertEqual(summary["sunday_surcharge_hours"], 0)

    def test_replacement_rest_missing_when_no_free_days_in_window(self):
        sunday = date(2026, 8, 9)
        for i in range(14):  # gesamtes 14-Tage-Fenster durchgehend belegt
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=self.employee, node=self.node, date=sunday + timedelta(days=i),
                template=self.day_template,
            )
        self.assertTrue(self.employee.sunday_replacement_rest_missing(sunday))

    def test_replacement_rest_ok_with_two_free_days_in_window(self):
        sunday = date(2026, 8, 9)
        for i in range(14):
            if i in (3, 10):  # zwei freie Tage im Fenster
                continue
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=self.employee, node=self.node, date=sunday + timedelta(days=i),
                template=self.day_template,
            )
        self.assertFalse(self.employee.sunday_replacement_rest_missing(sunday))

    def test_shift_assignment_property_only_relevant_for_sundays(self):
        monday = date(2026, 8, 3)
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=monday, template=self.day_template
        )
        self.assertFalse(assignment.sunday_replacement_rest_missing)

    def test_api_shift_assignment_exposes_replacement_rest_flag(self):
        sunday = date(2026, 8, 9)
        for i in range(14):  # keine freien Tage -> Ersatzruhetag fehlt
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=self.employee, node=self.node, date=sunday + timedelta(days=i),
                template=self.day_template,
            )
        self.auth_as(self.planner_user)
        response = self.client.get(
            f"/api/shift-assignments/?node={self.node.id}&date_from={sunday}&date_to={sunday}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["results"][0]["sunday_replacement_rest_missing"])


class EmployeeBalanceTests(APITestCase):
    """
    Arbeitszeitmodell (README Block 2.7 Punkt 7): Jahressoll
    (annual_target_hours), laufender Saldo + Jahresrestsoll
    (time_account_summary) + Feriensaldo (vacation_balance, unverändert).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(17, 0),
            break_minutes=60,  # 9h Spanne - 1h Pause = 8h netto pro Schicht
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant,
            first_name="Anna",
            last_name="A",
            employment_pct=100,
            employment_start_date=date(2026, 1, 1),
        )
        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)
        self.vacation_type = AbsenceType.objects.create(
            tenant=self.tenant, name="Ferien", deducts_vacation_days=True
        )
        self.sick_type = AbsenceType.objects.create(tenant=self.tenant, name="Krankheit")

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _assign(self, employee, day, template=None):
        return ShiftAssignment.objects.create(
            tenant=self.tenant, employee=employee, node=self.node, date=day, template=template or self.template
        )

    # --- Jahressoll (annual_target_hours) ---

    def test_annual_target_hours_without_canton(self):
        # 261 Mo-Fr-Arbeitstage 2026 * 8.4h Tagessoll (42h/5) - 20 Ferientage
        # * 8.4h = 2024.4h. Kein Kanton -> kein Feiertagsabzug.
        self.assertEqual(self.employee.annual_target_hours(2026), 2024.4)

    def test_annual_target_hours_deducts_canton_holidays(self):
        self.tenant.canton = "ZH"
        self.tenant.save(update_fields=["canton"])
        # Wie oben, aber 7 der 9 ZH-Feiertage 2026 fallen auf Mo-Fr -> 254
        # Arbeitstage * 8.4h - 20*8.4h Ferien = 1965.6h.
        self.assertEqual(self.employee.annual_target_hours(2026), 1965.6)

    def test_annual_target_hours_prorated_for_midyear_employment_start(self):
        self.employee.employment_start_date = date(2026, 6, 1)  # Montag
        self.employee.save(update_fields=["employment_start_date"])
        # 154 Mo-Fr-Arbeitstage Jun-Dez 2026 * 8.4h - voller Ferienanspruch
        # (bewusst NICHT anteilig gekürzt, siehe Docstring) = 1125.6h.
        self.assertEqual(self.employee.annual_target_hours(2026), 1125.6)

    def test_annual_target_hours_zero_before_employment_start(self):
        self.employee.employment_start_date = date(2027, 1, 1)
        self.employee.save(update_fields=["employment_start_date"])
        self.assertEqual(self.employee.annual_target_hours(2026), 0.0)

    # --- Laufender Saldo + Jahresrestsoll (time_account_summary) ---

    def test_saldo_after_full_workweek(self):
        for offset in range(5):  # Mo-Fr 2026-01-05..09, je 8h
            self._assign(self.employee, date(2026, 1, 5) + timedelta(days=offset))
        summary = self.employee.time_account_summary(date(2026, 1, 9))
        # Soll bis 9.1. (Fr): 7 Mo-Fr-Tage seit 1.1. (Do) * 8.4h = 58.8h.
        # Ist: 5*8h = 40h. Saldo = 40 - 58.8 = -18.8h.
        self.assertEqual(summary["saldo_hours"], -18.8)
        self.assertEqual(summary["annual_target_hours"], 2024.4)
        self.assertEqual(summary["annual_remaining_hours"], 1984.4)  # 2024.4 - 40

    def test_saldo_strictly_ignores_assignments_after_as_of_date(self):
        # saldo_hours (Stand heute, streng) zaehlt weiterhin NUR bis (inkl.)
        # as_of_date -- eine kuenftig eingeplante Schicht wirkt sich hier
        # erst aus, sobald ihr Datum erreicht ist (klassisches Gleitzeitkonto).
        # plan_saldo_hours/annual_remaining_hours SOLLEN sich dagegen bereits
        # aendern, siehe test_plan_saldo_includes_already_planned_future_assignments
        # unten (README, Redesign 2026-08 nach Nutzer-Feedback).
        self._assign(self.employee, date(2026, 1, 2))  # Fr, vor as_of
        without_future = self.employee.time_account_summary(date(2026, 1, 2))
        self._assign(self.employee, date(2026, 6, 15))  # weit in der Zukunft, selbes Jahr
        with_future = self.employee.time_account_summary(date(2026, 1, 2))
        self.assertEqual(without_future["saldo_hours"], with_future["saldo_hours"])

    def test_plan_saldo_includes_already_planned_future_assignments(self):
        # README (2026-08, Redesign): bei festem Pensum entscheidet der
        # Planer, WANN die Stunden anfallen -- plan_saldo_hours/
        # annual_remaining_hours sollen deshalb bereits eingeplante
        # kuenftige Zuweisungen desselben Jahres beruecksichtigen, damit ein
        # vollstaendig durchgeplantes Jahr nahe 0 zeigt statt eines
        # irrefuehrenden grossen Minus-Werts.
        self._assign(self.employee, date(2026, 1, 2))  # Fr, vergangen, 8h
        without_future = self.employee.time_account_summary(date(2026, 1, 2))
        self._assign(self.employee, date(2026, 6, 15))  # Mo, kuenftig, selbes Jahr, 8h
        with_future = self.employee.time_account_summary(date(2026, 1, 2))
        self.assertEqual(with_future["plan_saldo_hours"], round(without_future["plan_saldo_hours"] + 8, 2))
        self.assertEqual(
            with_future["annual_remaining_hours"], round(without_future["annual_remaining_hours"] - 8, 2)
        )
        self.assertTrue(with_future["is_provisional"])  # kuenftige Schicht kann nie CONFIRMED sein

    def test_plan_saldo_ignores_assignments_beyond_current_year(self):
        # Eine Zuweisung in einem anderen Kalenderjahr gehoert nicht zum
        # Jahresplan des betrachteten Jahres.
        before = self.employee.time_account_summary(date(2026, 1, 2))
        self._assign(self.employee, date(2030, 1, 7))
        after = self.employee.time_account_summary(date(2026, 1, 2))
        self.assertEqual(before["plan_saldo_hours"], after["plan_saldo_hours"])
        self.assertEqual(before["annual_remaining_hours"], after["annual_remaining_hours"])

    def test_plan_saldo_excludes_future_assignment_on_approved_absence_day(self):
        # Spiegelt test_saldo_ignores_ist_from_assignment_conflicting_with_approved_absence
        # fuer den kuenftigen Zweig: eine Zuweisung an einem genehmigten
        # Absenztag zaehlt auch in der Zukunft nicht als geplante Ist-Zeit.
        baseline = self.employee.time_account_summary(date(2026, 1, 2))
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 6, 15),
            end_date=date(2026, 6, 15),
            type=self.vacation_type,
            status=Absence.Status.APPROVED,
        )
        ShiftAssignment.objects.create(  # .create() bewusst am Absence.clean()-Schutz vorbei
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 6, 15), template=self.template,
        )
        summary = self.employee.time_account_summary(date(2026, 1, 2))
        self.assertEqual(summary["plan_saldo_hours"], baseline["plan_saldo_hours"])

    def test_saldo_approved_absence_is_soll_neutral(self):
        # Krankheit Di+Mi (6./7.1.) -- diese 2 Tage duerfen NICHT als
        # verpasste Sollzeit zaehlen, nur Mo/Do/Fr (5./8./9.1.) sind
        # tatsaechlich Arbeitstage im Soll.
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 7),
            type=self.sick_type,
            status=Absence.Status.APPROVED,
        )
        for d in (date(2026, 1, 5), date(2026, 1, 8), date(2026, 1, 9)):
            self._assign(self.employee, d)
        summary = self.employee.time_account_summary(date(2026, 1, 9))
        # Soll: (7 Mo-Fr-Tage - 2 Krankheitstage) * 8.4h = 42h. Ist: 3*8h=24h.
        self.assertEqual(summary["saldo_hours"], -18.0)

    def test_saldo_ignores_ist_from_assignment_conflicting_with_approved_absence(self):
        # Bugfix 2026-08: Absence.clean() verhindert seit diesem Fix NEUE
        # Überschneidungen, aber bereits bestehende (z. B. per Fixture/vor dem
        # Fix angelegte) Daten dürfen den Saldo nicht verfälschen -- eine
        # Zuweisung an einem genehmigten Absenztag darf NICHT als Ist-Zeit
        # zählen (sonst "gratis" Überstunden ohne Gegen-Soll).
        for d in (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9)):
            self._assign(self.employee, d)
        # .create() statt full_clean() -- bewusst am neuen Validierungs-Schutz
        # vorbei, um den "alten"/fehlerhaften Datenzustand nachzustellen.
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 7),
            type=self.vacation_type,
            status=Absence.Status.APPROVED,
        )
        summary = self.employee.time_account_summary(date(2026, 1, 9))
        # Soll: (7 Mo-Fr-Tage - 2 Ferientage) * 8.4h = 42h. Ist zählt nur die
        # 3 NICHT durch die Absenz abgedeckten Tage -- 3*8h=24h, nicht 5*8h=40h.
        self.assertEqual(summary["saldo_hours"], -18.0)

    def test_saldo_pending_absence_does_not_reduce_soll(self):
        # Nur GENEHMIGTE Absenzen sind Soll-neutral -- eine offene Anfrage
        # darf den Saldo nicht schon beeinflussen.
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 7),
            type=self.sick_type,
            status=Absence.Status.PENDING,
        )
        self._assign(self.employee, date(2026, 1, 5))
        summary = self.employee.time_account_summary(date(2026, 1, 9))
        # Soll: 7 Mo-Fr-Tage * 8.4h = 58.8h (keine Absenz-Kuerzung). Ist: 8h.
        self.assertEqual(summary["saldo_hours"], -50.8)

    def test_saldo_canton_holiday_is_soll_neutral(self):
        self.tenant.canton = "ZH"
        self.tenant.save(update_fields=["canton"])
        self._assign(self.employee, date(2026, 1, 2))  # Fr, einziger Arbeitstag
        summary = self.employee.time_account_summary(date(2026, 1, 2))
        # Soll: (2 Mo-Fr-Tage [1./2.1.] - 1 Feiertag [Neujahr]) * 8.4h = 8.4h.
        # Ist: 8h. Saldo = 8 - 8.4 = -0.4h.
        self.assertEqual(summary["saldo_hours"], -0.4)

    def test_saldo_zero_before_employment_start_in_year(self):
        self.employee.employment_start_date = date(2026, 6, 1)
        self.employee.save(update_fields=["employment_start_date"])
        summary = self.employee.time_account_summary(date(2026, 3, 1))
        self.assertEqual(summary["saldo_hours"], 0.0)
        self.assertEqual(summary["annual_remaining_hours"], summary["annual_target_hours"])

    def test_saldo_includes_carryover_as_starting_offset(self):
        self.employee.overtime_balance_carryover_hours = 15.5
        self.employee.save(update_fields=["overtime_balance_carryover_hours"])
        summary = self.employee.time_account_summary(date(2026, 1, 1))
        # Kein Arbeitstag verplant -> Soll = 0 (1.1. ist selbst der einzige
        # Tag und ohne Kanton kein Feiertag) - Soll (1 Tag * 8.4h) + Ist (0h)
        # + Carryover.
        self.assertEqual(summary["saldo_hours"], round(15.5 - 8.4, 2))

    # --- is_provisional (Saldo ist rechnerisch sofort aktuell, auch vor der Prüfung --
    # das Flag macht das im Frontend nur transparent) ---

    def test_saldo_is_provisional_when_based_on_planned_hours_only(self):
        self._assign(self.employee, date(2026, 1, 5))  # keine Zeiterfassung -> Schätzung aus Planung
        summary = self.employee.time_account_summary(date(2026, 1, 5))
        self.assertTrue(summary["is_provisional"])

    def test_saldo_is_provisional_when_time_record_not_confirmed(self):
        assignment = self._assign(self.employee, date(2026, 1, 5))
        TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=assignment,
            actual_start=time(8, 0),
            actual_end=time(17, 0),
            actual_break_minutes=60,
        )  # Status bleibt SUBMITTED
        summary = self.employee.time_account_summary(date(2026, 1, 5))
        self.assertTrue(summary["is_provisional"])

    def test_saldo_not_provisional_once_all_shifts_confirmed(self):
        assignment = self._assign(self.employee, date(2026, 1, 5))
        record = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=assignment,
            actual_start=time(8, 0),
            actual_end=time(17, 0),
            actual_break_minutes=60,
        )
        record.confirm()
        summary = self.employee.time_account_summary(date(2026, 1, 5))
        self.assertFalse(summary["is_provisional"])

    def test_saldo_without_any_assignment_is_not_provisional(self):
        summary = self.employee.time_account_summary(date(2026, 1, 5))
        self.assertFalse(summary["is_provisional"])

    # --- Feriensaldo ---

    def test_vacation_balance_defaults_to_tenant_entitlement_without_absences(self):
        summary = self.employee.vacation_balance(2026)
        self.assertEqual(summary["entitlement_days"], 20)  # Tenant-Default
        self.assertEqual(summary["used_days"], 0)
        self.assertEqual(summary["remaining_days"], 20)

    def test_vacation_balance_employee_override_replaces_tenant_default(self):
        self.employee.vacation_days_per_year = 25
        self.employee.save(update_fields=["vacation_days_per_year"])
        summary = self.employee.vacation_balance(2026)
        self.assertEqual(summary["entitlement_days"], 25)

    def test_vacation_balance_counts_only_workdays(self):
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 3),  # Montag
            end_date=date(2026, 8, 9),  # Sonntag -- volle Woche, aber nur 5 Werktage
            type=self.vacation_type,
            status=Absence.Status.APPROVED,
        )
        summary = self.employee.vacation_balance(2026)
        self.assertEqual(summary["used_days"], 5)
        self.assertEqual(summary["remaining_days"], 15)

    def test_vacation_balance_ignores_pending_and_non_vacation_absences(self):
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 3),
            end_date=date(2026, 8, 7),
            type=self.vacation_type,
            status=Absence.Status.PENDING,
        )
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 14),
            type=self.sick_type,
            status=Absence.Status.APPROVED,
        )
        summary = self.employee.vacation_balance(2026)
        self.assertEqual(summary["used_days"], 0)

    def test_vacation_balance_clips_absence_spanning_year_boundary(self):
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 12, 28),  # Montag
            end_date=date(2027, 1, 2),  # Samstag -- 4 Werktage 2026, 1 Werktag 2027
            type=self.vacation_type,
            status=Absence.Status.APPROVED,
        )
        self.assertEqual(self.employee.vacation_balance(2026)["used_days"], 4)
        self.assertEqual(self.employee.vacation_balance(2027)["used_days"], 1)

    # --- API ---

    def test_api_returns_combined_balance(self):
        for offset in range(5):  # Mo-Fr 2026-01-05..09
            self._assign(self.employee, date(2026, 1, 5) + timedelta(days=offset))
        Absence.objects.create(  # nicht überlappend mit den Diensten oben
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 1, 12),
            end_date=date(2026, 1, 16),
            type=self.vacation_type,
            status=Absence.Status.APPROVED,
        )
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/balance/?as_of=2026-01-09")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["saldo_hours"], -18.8)
        self.assertEqual(response.data["plan_saldo_hours"], -1984.4)
        self.assertEqual(response.data["annual_target_hours"], 2024.4)
        self.assertEqual(response.data["annual_remaining_hours"], 1984.4)
        self.assertTrue(response.data["is_provisional"])  # keine Zeiterfassung erfasst
        self.assertEqual(response.data["vacation_year"], 2026)
        self.assertEqual(response.data["vacation_entitlement_days"], 20)
        self.assertEqual(response.data["vacation_used_days"], 5)
        self.assertEqual(response.data["vacation_remaining_days"], 15)

    def test_api_rejects_invalid_as_of_param(self):
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/balance/?as_of=not-a-date")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_without_as_of_ignores_future_assignments(self):
        # Kernentscheidung des neuen Modells: ohne ?as_of= nutzt die API
        # "heute" als Stichtag -- eine weit in der Zukunft eingeplante
        # Schicht darf den Saldo nicht vorzeitig verändern (Gleitzeitkonto,
        # siehe Employee.time_account_summary()).
        self.auth_as(self.planner_user)
        before = self.client.get(f"/api/employees/{self.employee.id}/balance/").data
        self._assign(self.employee, date(2030, 1, 7))  # weit in der Zukunft
        after = self.client.get(f"/api/employees/{self.employee.id}/balance/").data
        self.assertEqual(before["saldo_hours"], after["saldo_hours"])
        self.assertEqual(before["plan_saldo_hours"], after["plan_saldo_hours"])
        self.assertEqual(before["annual_remaining_hours"], after["annual_remaining_hours"])


class MonthlySummaryTests(APITestCase):
    """
    Monatsauswertung (README Block 2.6, "Basis für den Lohnlauf"):
    Soll/Ist-Vergleich, Überzeit- sowie Nacht-/Sonntagszuschlag für einen
    Kalendermonat. Juni 2026 hat 22 Mo-Fr-Arbeitstage, beginnt an einem
    Montag und enthält 4 Sonntage (7./14./21./28.6.) -- gewählt, weil kein
    Kanton gesetzt ist (kein Feiertagsabzug) und die Zahlen dadurch
    handrechenbar bleiben.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        # 08:00-16:30, 30min Pause -> 8h Spanne netto pro Schicht (klare Zahlen).
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 30),
            break_minutes=30,
        )
        self.night_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nachtdienst", start_time=time(23, 0), end_time=time(6, 0)
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant,
            first_name="Anna",
            last_name="A",
            employment_pct=100,
            employment_start_date=date(2026, 1, 1),
            standard_weekly_hours=40,  # Override fuer klare 8h/Tag (40/5), siehe Block 1.14
        )
        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)
        self.employee_user = User.objects.create_user(username="anna-user", password="pw-not-real-123!")
        Membership.objects.create(user=self.employee_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _assign(self, day, template=None):
        return ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=day, template=template or self.template
        )

    def _june_weekdays(self):
        return [date(2026, 6, d) for d in range(1, 31) if date(2026, 6, d).weekday() < 5]

    def test_no_shifts_means_full_soll_and_no_overtime(self):
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["month_start"], date(2026, 6, 1))
        self.assertEqual(summary["month_end"], date(2026, 6, 30))
        self.assertEqual(summary["soll_hours"], 176.0)  # 22 Arbeitstage * 8h
        self.assertEqual(summary["ist_hours"], 0)
        self.assertEqual(summary["overtime_hours"], 0)
        self.assertEqual(summary["overtime_surcharge_hours"], 0)
        self.assertFalse(summary["is_provisional"])

    def test_full_month_worked_exactly_meets_soll(self):
        for day in self._june_weekdays():
            self._assign(day)
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["ist_hours"], 176.0)
        self.assertEqual(summary["overtime_hours"], 0)
        self.assertEqual(summary["overtime_surcharge_hours"], 0)
        self.assertTrue(summary["is_provisional"])  # keine Zeiterfassung erfasst

    def test_extra_shift_yields_overtime_and_surcharge(self):
        for day in self._june_weekdays():
            self._assign(day)
        self._assign(date(2026, 6, 6))  # Samstag, zusaetzliche 8h
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["ist_hours"], 184.0)
        self.assertEqual(summary["overtime_hours"], 8.0)
        self.assertEqual(summary["overtime_surcharge_hours"], 2.0)  # 25% Zuschlag (Tenant-Default)

    def test_shifts_outside_month_are_excluded(self):
        self._assign(date(2026, 6, 1))
        self._assign(date(2026, 7, 1))  # ausserhalb Juni
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["ist_hours"], 8.0)

    def test_time_record_overrides_planned_hours_and_clears_provisional(self):
        assignment = self._assign(date(2026, 6, 1))
        TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=assignment,
            actual_start=time(8, 0),
            actual_end=time(17, 0),  # 9h brutto
            actual_break_minutes=60,  # 8h netto statt geplanter 8h -- bewusst identisch
            status=TimeRecord.Status.CONFIRMED,
        )
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["ist_hours"], 8.0)
        self.assertFalse(summary["is_provisional"])  # einzige Schicht ist CONFIRMED erfasst

    def test_submitted_time_record_still_counts_as_provisional(self):
        assignment = self._assign(date(2026, 6, 1))
        TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=assignment,
            actual_start=time(8, 0),
            actual_end=time(17, 0),
            actual_break_minutes=60,
            status=TimeRecord.Status.SUBMITTED,
        )
        summary = self.employee.monthly_summary(2026, 6)
        self.assertTrue(summary["is_provisional"])

    def test_approved_absence_is_soll_neutral(self):
        # Ferien Mo-Fr 1.-5.6. -- 5 Arbeitstage weniger Soll, keine Ist-Zeit
        # dafuer erwartet.
        vacation_type = AbsenceType.objects.create(tenant=self.tenant, name="Ferien", deducts_vacation_days=True)
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 5),
            type=vacation_type,
            status=Absence.Status.APPROVED,
        )
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["soll_hours"], 136.0)  # (22 - 5) * 8h

    def test_zero_before_employment_start(self):
        self.employee.employment_start_date = date(2026, 7, 1)
        self.employee.save(update_fields=["employment_start_date"])
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["soll_hours"], 0)
        self.assertEqual(summary["ist_hours"], 0)
        self.assertFalse(summary["is_provisional"])

    def test_sunday_shift_adds_sunday_surcharge(self):
        self._assign(date(2026, 6, 7))  # Sonntag
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["sunday_hours"], 8.0)
        self.assertEqual(summary["sunday_surcharge_hours"], 4.0)  # 50% Zuschlag (Tenant-Default)

    def test_few_night_shifts_in_month_have_no_surcharge(self):
        # 5 Naechte im Jahr -- unter der Jahresschwelle (Tenant-Default 25)
        # fuer "regelmaessige" Nachtarbeit -> keine Zeitgutschrift trotz
        # vorhandener Nachtstunden.
        for offset in range(5):
            self._assign(date(2026, 6, 1) + timedelta(days=offset), template=self.night_template)
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["night_hours"], 35.0)  # 5 * 7h
        self.assertEqual(summary["night_surcharge_hours"], 0)

    def test_25_night_shifts_in_year_yield_surcharge_for_month(self):
        # Alle 25 Naechte liegen im Juni selbst (1.-25.6.) -> "regelmaessig"
        # fuers ganze Jahr 2026, die Zeitgutschrift bezieht sich hier aber
        # nur auf die Nachtstunden DIESES Monats.
        for offset in range(25):
            self._assign(date(2026, 6, 1) + timedelta(days=offset), template=self.night_template)
        summary = self.employee.monthly_summary(2026, 6)
        self.assertEqual(summary["night_hours"], 175.0)  # 25 * 7h
        self.assertEqual(summary["night_surcharge_hours"], 17.5)  # 10% Zeitgutschrift (Tenant-Default)

    # --- API ---

    def test_api_returns_monthly_summary_for_planner(self):
        for day in self._june_weekdays():
            self._assign(day)
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/monthly-summary/?year=2026&month=6")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["ist_hours"], 176.0)
        self.assertEqual(response.data["soll_hours"], 176.0)
        self.assertEqual(response.data["month"], 6)

    def test_api_defaults_to_current_year_and_month(self):
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/monthly-summary/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        today = timezone.localdate()
        self.assertEqual(response.data["year"], today.year)
        self.assertEqual(response.data["month"], today.month)

    def test_api_rejects_invalid_month_param(self):
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/monthly-summary/?year=2026&month=13")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_rejects_invalid_year_param(self):
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/monthly-summary/?year=not-a-year")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_api_forbidden_for_employee_role(self):
        # Anders als balance()/weekly-overtime()/night-work() bewusst KEINE
        # Mitarbeiter-Selbstauskunft -- das hier ist Lohnlauf-Vorbereitung,
        # nur Admin/Planer duerfen sie einsehen (siehe EmployeeViewSet.
        # monthly_summary-Docstring).
        self.auth_as(self.employee_user)
        response = self.client.get(f"/api/employees/{self.employee.id}/monthly-summary/?year=2026&month=6")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ShiftTradeRequestTests(TestCase):
    """Diensttausch: sowohl einfache Übernahme als auch echter Tausch, jeweils inkl. Regel-Engine."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee_1 = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.employee_2 = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,  # 8h Spanne -> 7.5h netto -> Art. 15 ArG verlangt 30 Min.
        )
        self.assignment_1 = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee_1,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.template,
        )

    def test_clean_rejects_trade_with_self(self):
        trade = ShiftTradeRequest(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_1,
        )
        with self.assertRaises(ValidationError):
            trade.clean()

    def test_clean_rejects_target_assignment_of_wrong_employee(self):
        other_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee_1,
            node=self.node,
            date=date(2026, 8, 10),
            template=self.template,
        )
        trade = ShiftTradeRequest(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
            target_assignment=other_assignment,  # gehört employee_1, nicht employee_2
        )
        with self.assertRaises(ValidationError):
            trade.clean()

    def test_accept_marks_employee_accepted_without_swapping(self):
        # accept() ist nur die Zustimmung der Zielperson (Block 2.3) -- der
        # eigentliche Tausch passiert erst in approve() durch Admin/Planer.
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.accept()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED)
        self.assertIsNone(trade.resolved_at)
        self.assertEqual(self.assignment_1.employee_id, self.employee_1.id)

    def test_approve_after_accept_reassigns_employee(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.accept()
        trade.approve()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.ACCEPTED)
        self.assertIsNotNone(trade.resolved_at)
        self.assertEqual(self.assignment_1.employee_id, self.employee_2.id)

    def test_approve_directly_from_pending(self):
        # Admin/Planer können die Zustimmung der Zielperson überspringen
        # (z. B. telefonisch eingeholt) und direkt aus PENDING freigeben.
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.approve()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.ACCEPTED)
        self.assertEqual(self.assignment_1.employee_id, self.employee_2.id)

    def test_approve_full_swap_exchanges_employees(self):
        assignment_2 = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee_2,
            node=self.node,
            date=date(2026, 8, 20),
            template=self.template,
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
            target_assignment=assignment_2,
        )
        trade.accept()
        trade.approve()

        self.assignment_1.refresh_from_db()
        assignment_2.refresh_from_db()
        self.assertEqual(self.assignment_1.employee_id, self.employee_2.id)
        self.assertEqual(assignment_2.employee_id, self.employee_1.id)

    def test_approve_blocked_by_rule_engine_leaves_state_unchanged(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Nachtdienst-berechtigt")
        self.template.required_skill = skill
        self.template.save()
        self.employee_1.skills.add(skill)
        # employee_2 hat den Skill NICHT -> Übernahme muss an der Qualifikationsprüfung scheitern.

        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.accept()
        with self.assertRaises(ValidationError):
            trade.approve()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED)
        self.assertEqual(self.assignment_1.employee_id, self.employee_1.id)

    def test_accept_twice_is_rejected(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.accept()
        with self.assertRaises(ValidationError):
            trade.accept()

    def test_reject_by_planner(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.reject()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.REJECTED)
        self.assertIsNotNone(trade.resolved_at)
        self.assertEqual(self.assignment_1.employee_id, self.employee_1.id)

    def test_reject_after_employee_accepted(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.accept()
        trade.reject()

        trade.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.REJECTED)

    def test_reject_already_accepted_trade_is_rejected(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.approve()
        with self.assertRaises(ValidationError):
            trade.reject()


class ShiftTradeRequestAPITests(APITestCase):
    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee_1 = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.employee_2 = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,  # 8h Spanne -> 7.5h netto -> Art. 15 ArG verlangt 30 Min.
        )
        self.assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee_1,
            node=self.node,
            date=date(2026, 8, 3),
            template=self.template,
        )
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_accept_endpoint_marks_employee_accepted(self):
        create_response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.assignment.id, "target_employee": self.employee_2.id},
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        trade_id = create_response.data["id"]

        accept_response = self.client.post(f"/api/shift-trade-requests/{trade_id}/accept/")
        self.assertEqual(accept_response.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_response.data["status"], "employee_accepted")

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.employee_id, self.employee_1.id)

    def test_approve_endpoint_reassigns_and_returns_updated_status(self):
        create_response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.assignment.id, "target_employee": self.employee_2.id},
        )
        trade_id = create_response.data["id"]
        self.client.post(f"/api/shift-trade-requests/{trade_id}/accept/")

        approve_response = self.client.post(f"/api/shift-trade-requests/{trade_id}/approve/")
        self.assertEqual(approve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(approve_response.data["status"], "accepted")

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.employee_id, self.employee_2.id)

    def test_approve_endpoint_works_directly_from_pending(self):
        create_response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.assignment.id, "target_employee": self.employee_2.id},
        )
        trade_id = create_response.data["id"]

        approve_response = self.client.post(f"/api/shift-trade-requests/{trade_id}/approve/")
        self.assertEqual(approve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(approve_response.data["status"], "accepted")

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.employee_id, self.employee_2.id)

    def test_reject_endpoint(self):
        create_response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.assignment.id, "target_employee": self.employee_2.id},
        )
        trade_id = create_response.data["id"]

        reject_response = self.client.post(f"/api/shift-trade-requests/{trade_id}/reject/")
        self.assertEqual(reject_response.status_code, status.HTTP_200_OK)
        self.assertEqual(reject_response.data["status"], "rejected")

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.employee_id, self.employee_1.id)

    def test_decline_endpoint_leaves_assignment_untouched(self):
        create_response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.assignment.id, "target_employee": self.employee_2.id},
        )
        trade_id = create_response.data["id"]

        decline_response = self.client.post(f"/api/shift-trade-requests/{trade_id}/decline/")
        self.assertEqual(decline_response.status_code, status.HTTP_200_OK)
        self.assertEqual(decline_response.data["status"], "declined")

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.employee_id, self.employee_1.id)


class RoleBasedPermissionTests(APITestCase):
    """
    core.permissions: Admin/Planer dürfen den Dienstplan/Stammdaten
    bearbeiten, HR nur lesen ("nur Reporting"), Mitarbeitende dürfen lesen
    sowie eigene Absenzen und eigenen Diensttausch verwalten.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
        )

        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)

        self.hr_user = User.objects.create_user(username="hr", password="pw-not-real-123!")
        Membership.objects.create(user=self.hr_user, tenant=self.tenant, role=Membership.Role.HR)

        self.alice_user = User.objects.create_user(username="alice", password="pw-not-real-123!")
        Membership.objects.create(user=self.alice_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.alice = Employee.objects.create(
            tenant=self.tenant, user=self.alice_user, first_name="Alice", last_name="A", employment_pct=100
        )

        self.bob_user = User.objects.create_user(username="bob", password="pw-not-real-123!")
        Membership.objects.create(user=self.bob_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.bob = Employee.objects.create(
            tenant=self.tenant, user=self.bob_user, first_name="Bob", last_name="B", employment_pct=100
        )

        self.alice_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        self.bob_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.bob, node=self.node, date=date(2026, 8, 4), template=self.template
        )
        self.vacation_type = AbsenceType.objects.create(
            tenant=self.tenant, name="Ferien", deducts_vacation_days=True
        )
        # Für TimeRecord-Tests: eine bereits stattgefundene Schicht (TimeRecord.clean()
        # lehnt Ist-Erfassung für Schichten in der Zukunft ab). Bewusst ein fixes Datum
        # statt "gestern" (timezone.localdate() - 1 Tag): das kollidierte mit dem
        # ebenfalls fixen alice_assignment-Datum (2026-08-03) genau an dem Tag, an dem
        # "heute" real 2026-08-04 war (UNIQUE-constraint employee+date).
        self.alice_past_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.alice,
            node=self.node,
            date=date(2026, 7, 27),
            template=self.template,
        )

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_employee_cannot_create_node(self):
        self.auth_as(self.alice_user)
        response = self.client.post("/api/nodes/", {"name": "Station B"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_planner_can_create_node(self):
        self.auth_as(self.planner_user)
        response = self.client.post("/api/nodes/", {"name": "Station B"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_hr_cannot_write_but_can_read(self):
        self.auth_as(self.hr_user)
        write_response = self.client.post("/api/nodes/", {"name": "Station B"})
        self.assertEqual(write_response.status_code, status.HTTP_403_FORBIDDEN)
        read_response = self.client.get("/api/nodes/")
        self.assertEqual(read_response.status_code, status.HTTP_200_OK)

    def test_employee_cannot_create_shift_assignment(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-assignments/",
            {
                "employee": self.alice.id,
                "node": self.node.id,
                "date": "2026-08-10",
                "template": self.template.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_cannot_write_skill_time_template_or_employee(self):
        self.auth_as(self.alice_user)
        self.assertEqual(
            self.client.post("/api/skills/", {"name": "Neu"}).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.post(
                "/api/time-templates/",
                {"node": self.node.id, "name": "X", "start_time": "08:00", "end_time": "16:00"},
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                "/api/employees/", {"first_name": "X", "last_name": "Y", "employment_pct": 100}
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_employee_can_create_own_absence(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/absences/",
            {
                "employee": self.alice.id,
                "start_date": "2026-09-01",
                "end_date": "2026-09-05",
                "type": self.vacation_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_employee_cannot_create_absence_for_other_employee(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/absences/",
            {
                "employee": self.bob.id,
                "start_date": "2026-09-01",
                "end_date": "2026-09-05",
                "type": self.vacation_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_can_delete_own_absence_not_others(self):
        own_absence = Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
            type=self.vacation_type,
        )
        other_absence = Absence.objects.create(
            tenant=self.tenant, employee=self.bob, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
            type=self.vacation_type,
        )
        self.auth_as(self.alice_user)
        self.assertEqual(
            self.client.delete(f"/api/absences/{other_absence.id}/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.delete(f"/api/absences/{own_absence.id}/").status_code, status.HTTP_204_NO_CONTENT
        )

    def test_employee_can_offer_own_shift_for_trade(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.alice_assignment.id, "target_employee": self.bob.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_employee_cannot_offer_someone_elses_shift(self):
        carla_user = User.objects.create_user(username="carla2", password="pw-not-real-123!")
        Membership.objects.create(user=carla_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        carla = Employee.objects.create(
            tenant=self.tenant, user=carla_user, first_name="Carla", last_name="C", employment_pct=100
        )

        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-trade-requests/",
            # Alice bietet Bobs Schicht an -- Ziel ist Carla, nicht Bob, damit
            # nicht die "kein Tausch mit sich selbst"-Regel (400) statt der
            # Berechtigungsprüfung (403) greift.
            {"requester_assignment": self.bob_assignment.id, "target_employee": carla.id},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_only_target_employee_can_accept_trade_offer(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=self.alice_assignment, target_employee=self.bob
        )
        third_user = User.objects.create_user(username="carla", password="pw-not-real-123!")
        Membership.objects.create(user=third_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        Employee.objects.create(
            tenant=self.tenant, user=third_user, first_name="Carla", last_name="C", employment_pct=100
        )

        self.auth_as(third_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/accept/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.auth_as(self.bob_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/accept/").status_code,
            status.HTTP_200_OK,
        )

    def test_only_requester_can_cancel_own_trade_offer(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=self.alice_assignment, target_employee=self.bob
        )
        self.auth_as(self.bob_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/cancel/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.auth_as(self.alice_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/cancel/").status_code,
            status.HTTP_200_OK,
        )

    def test_planner_can_act_on_behalf_of_any_employee(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=self.alice_assignment, target_employee=self.bob
        )
        self.auth_as(self.planner_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/accept/").status_code,
            status.HTTP_200_OK,
        )

    def test_only_manager_can_approve_or_reject_trade(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=self.alice_assignment, target_employee=self.bob
        )
        # Bob ist die Zielperson, darf aber trotzdem nicht selbst freigeben/ablehnen --
        # das bleibt Admin/Planer vorbehalten (Block 2.3).
        self.auth_as(self.bob_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/approve/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/reject/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.auth_as(self.planner_user)
        self.assertEqual(
            self.client.post(f"/api/shift-trade-requests/{trade.id}/approve/").status_code,
            status.HTTP_200_OK,
        )

    def test_employee_cannot_approve_or_reject_own_absence(self):
        absence = Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
            type=self.vacation_type,
        )
        self.auth_as(self.alice_user)
        self.assertEqual(
            self.client.post(f"/api/absences/{absence.id}/approve/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(f"/api/absences/{absence.id}/reject/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_planner_can_approve_absence(self):
        absence = Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 9, 1), end_date=date(2026, 9, 2),
            type=self.vacation_type,
        )
        self.auth_as(self.planner_user)
        response = self.client.post(f"/api/absences/{absence.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "approved")

    def test_approve_rejects_absence_conflicting_with_existing_assignment(self):
        # Bugfix 2026-08: alice_assignment (aus setUp) liegt am 2026-08-03 --
        # eine Absenz über diesen Zeitraum darf nicht genehmigt werden,
        # solange die Zuweisung nicht zuerst entfernt wurde.
        absence = Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 8, 1), end_date=date(2026, 8, 5),
            type=self.vacation_type,
        )
        self.auth_as(self.planner_user)
        response = self.client.post(f"/api/absences/{absence.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        absence.refresh_from_db()
        self.assertEqual(absence.status, Absence.Status.PENDING)  # Status bleibt unverändert

    def test_planner_created_absence_conflicting_with_existing_assignment_is_rejected(self):
        # Bugfix 2026-08: von Admin/Planer angelegte Absenzen sind sofort
        # APPROVED (siehe AbsenceViewSet.perform_create) -- der Konflikt-
        # Check muss deshalb schon beim direkten Anlegen greifen, nicht erst
        # bei approve() (AbsenceSerializer.validate() musste dafür den
        # späteren Status vorwegnehmen, siehe Serializer-Docstring).
        self.auth_as(self.planner_user)
        response = self.client.post(
            "/api/absences/",
            {"employee": self.alice.id, "start_date": "2026-08-01", "end_date": "2026-08-05", "type": self.vacation_type.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Absence.all_objects.filter(employee=self.alice, start_date=date(2026, 8, 1)).exists())

    def test_employee_created_absence_starts_pending_planner_created_is_approved(self):
        self.auth_as(self.alice_user)
        employee_response = self.client.post(
            "/api/absences/",
            {"employee": self.alice.id, "start_date": "2026-09-10", "end_date": "2026-09-11", "type": self.vacation_type.id},
        )
        self.assertEqual(employee_response.data["status"], "pending")

        self.auth_as(self.planner_user)
        planner_response = self.client.post(
            "/api/absences/",
            {"employee": self.bob.id, "start_date": "2026-09-10", "end_date": "2026-09-11", "type": self.vacation_type.id},
        )
        self.assertEqual(planner_response.data["status"], "approved")

    def test_employee_cannot_edit_own_absence_once_decided(self):
        absence = Absence.objects.create(
            tenant=self.tenant,
            employee=self.alice,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            type=self.vacation_type,
            status=Absence.Status.APPROVED,
        )
        self.auth_as(self.alice_user)
        response = self.client.delete(f"/api/absences/{absence.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_can_record_own_time_record(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/time-records/",
            {
                "assignment": self.alice_past_assignment.id,
                "actual_start": "08:05",
                "actual_end": "16:00",
                "actual_break_minutes": 30,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "submitted")
        self.assertEqual(response.data["deviation_minutes"], 5)

    def test_time_records_can_be_filtered_by_date_range(self):
        # Planblatt-Grid und Zeiterfassungs-Tab laden Ist-Zeiten nur für den
        # sichtbaren Monat statt aller Einträge des Tenants (Performance).
        in_range = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=self.alice_past_assignment,
            actual_start=time(8, 0),
            actual_end=time(16, 0),
            actual_break_minutes=30,
        )
        out_of_range_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.alice,
            node=self.node,
            date=timezone.localdate() - timedelta(days=60),
            template=self.template,
        )
        TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=out_of_range_assignment,
            actual_start=time(8, 0),
            actual_end=time(16, 0),
            actual_break_minutes=30,
        )
        self.auth_as(self.planner_user)
        date_from = self.alice_past_assignment.date - timedelta(days=1)
        date_to = self.alice_past_assignment.date + timedelta(days=1)
        response = self.client.get(f"/api/time-records/?date_from={date_from}&date_to={date_to}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(ids, [in_range.id])

    def test_employee_cannot_record_time_for_others_shift(self):
        # Bewusst ein fixes Datum statt "gestern" (timezone.localdate() -
        # 1 Tag): dieselbe Kollisionsgefahr wie beim alice_past_assignment
        # oben -- sobald "heute" (Europe/Zurich) auf das fixe bob_assignment-
        # Datum (2026-08-04) fällt, wäre "gestern" == "heute" und würde
        # ebenfalls gegen den UNIQUE-constraint employee+date laufen.
        bob_past_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.bob,
            node=self.node,
            date=date(2026, 7, 20),
            template=self.template,
        )
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/time-records/",
            {
                "assignment": bob_past_assignment.id,
                "actual_start": "08:00",
                "actual_end": "16:00",
                "actual_break_minutes": 30,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_cannot_confirm_own_time_record(self):
        record = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=self.alice_past_assignment,
            actual_start=time(8, 0),
            actual_end=time(16, 0),
            actual_break_minutes=30,
        )
        self.auth_as(self.alice_user)
        response = self.client.post(f"/api/time-records/{record.id}/confirm/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_planner_can_confirm_time_record(self):
        record = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=self.alice_past_assignment,
            actual_start=time(8, 0),
            actual_end=time(16, 0),
            actual_break_minutes=30,
        )
        self.auth_as(self.planner_user)
        response = self.client.post(f"/api/time-records/{record.id}/confirm/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "confirmed")

    def test_employee_cannot_edit_time_record_once_confirmed(self):
        record = TimeRecord.objects.create(
            tenant=self.tenant,
            assignment=self.alice_past_assignment,
            actual_start=time(8, 0),
            actual_end=time(16, 0),
            actual_break_minutes=30,
            status=TimeRecord.Status.CONFIRMED,
        )
        self.auth_as(self.alice_user)
        response = self.client.delete(f"/api/time-records/{record.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- Ein Mitarbeiter darf nur seine eigene(n) Station(en) sehen ---

    def test_employee_sees_only_own_station_in_node_list(self):
        other_node = Node.add_root(name="Station B", tenant=self.tenant)
        self.alice.nodes.add(self.node)
        self.auth_as(self.alice_user)
        response = self.client.get("/api/nodes/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {n["id"] for n in response.data["results"]}
        self.assertEqual(ids, {self.node.id})
        self.assertNotIn(other_node.id, ids)

    def test_employee_without_any_station_sees_no_nodes(self):
        # self.alice ist in dieser Testklasse standardmässig an keine
        # Station gebunden (kein .nodes.add()) -- muss dann konsequenterweise
        # eine leere Liste sehen statt versehentlich den ganzen Tenant.
        self.auth_as(self.alice_user)
        response = self.client.get("/api/nodes/")
        self.assertEqual(response.data["results"], [])

    def test_admin_planner_and_hr_still_see_all_stations(self):
        other_node = Node.add_root(name="Station B", tenant=self.tenant)
        for user in (self.planner_user, self.hr_user):
            self.auth_as(user)
            response = self.client.get("/api/nodes/")
            ids = {n["id"] for n in response.data["results"]}
            self.assertIn(self.node.id, ids)
            self.assertIn(other_node.id, ids)

    def test_employee_only_sees_shift_assignments_of_own_station(self):
        other_node = Node.add_root(name="Station B", tenant=self.tenant)
        other_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=other_node,
            name="Nachtdienst",
            start_time=time(20, 0),
            end_time=time(6, 0),
            break_minutes=30,
        )
        other_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.bob, node=other_node, date=date(2026, 8, 5), template=other_template
        )
        self.alice.nodes.add(self.node)
        self.auth_as(self.alice_user)
        response = self.client.get("/api/shift-assignments/")
        ids = {a["id"] for a in response.data["results"]}
        # Innerhalb der eigenen Station bleibt es bei voller Transparenz
        # (auch Bobs Zuweisung auf derselben Station ist sichtbar) -- nur die
        # fremde Station ist ausgeblendet.
        self.assertIn(self.alice_assignment.id, ids)
        self.assertIn(self.bob_assignment.id, ids)
        self.assertNotIn(other_assignment.id, ids)

    def test_employee_only_sees_time_templates_of_own_station(self):
        other_node = Node.add_root(name="Station B", tenant=self.tenant)
        other_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=other_node,
            name="Nachtdienst",
            start_time=time(20, 0),
            end_time=time(6, 0),
            break_minutes=30,
        )
        self.alice.nodes.add(self.node)
        self.auth_as(self.alice_user)
        response = self.client.get("/api/time-templates/")
        ids = {t["id"] for t in response.data["results"]}
        self.assertIn(self.template.id, ids)
        self.assertNotIn(other_template.id, ids)


class EmploymentModelTests(TestCase):
    """README Punkt 17: Employment als additive Team-/Pensum-/Rollen-Ebene."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Peter", last_name="Meier", employment_pct=100
        )

    def test_unique_together_employee_node(self):
        Employment.objects.create(tenant=self.tenant, employee=self.employee, node=self.node, pensum_pct=60)
        with self.assertRaises(IntegrityError):
            Employment.objects.create(
                tenant=self.tenant, employee=self.employee, node=self.node, pensum_pct=40
            )

    def test_pensum_pct_range_rejected_below_and_above(self):
        for invalid in (0, 101):
            employment = Employment(
                tenant=self.tenant, employee=self.employee, node=self.node, pensum_pct=invalid
            )
            with self.assertRaises(ValidationError):
                employment.full_clean()


class EmploymentMigrationTests(TransactionTestCase):
    """
    README Punkt 17: die Backfill-Datenmigration (0013_employment) muss für
    jede bestehende Employee.nodes-Zuordnung eine gleichwertige
    Employment-Zeile anlegen -- kritisch, weil die App bereits mit echten,
    produktiven Praxisdaten läuft (Employee.nodes darf nicht manuell
    nachgepflegt werden müssen). Erster Migrations-State-Test in diesem
    Projekt: migriert die Test-DB explizit auf den Stand VOR 0013, baut dort
    Fixture-Daten am gefrorenen (historischen) Modell auf, migriert dann auf
    0013 und prüft das Ergebnis -- danach zurück auf den aktuellsten Stand,
    damit nachfolgende Tests wieder auf der vollen, aktuellen DB-Struktur
    laufen. TransactionTestCase statt TestCase, weil SQLite den
    Schema-Editor (für die rückwärts/vorwärts laufenden Migrationen) nicht
    innerhalb einer von TestCase automatisch offenen Transaktion erlaubt.
    """

    def test_backfills_employment_from_existing_employee_nodes(self):
        executor = MigrationExecutor(connection)
        # core bleibt explizit auf seinem tatsächlich angewendeten, neuesten
        # Stand (core 0012 hängt nicht davon ab, core zurückzurollen) --
        # sonst würde project_state() das core-Modell nur bis zu dem älteren
        # Stand einfrieren, den scheduling 0012 selbst als Abhängigkeit
        # deklariert (z. B. ohne Tenant.night_work_permit_confirmed/.canton),
        # während die reale SQLite-Tabelle bereits die volle, aktuelle
        # core-Struktur hat -- ein NOT-NULL-Mismatch beim Anlegen der
        # Fixture.
        before = [
            ("scheduling", "0012_employee_employment_start_date_and_more"),
            ("core", "0007_tenant_canton_tenantholidayoverride"),
        ]
        executor.migrate(before)
        executor.loader.build_graph()

        old_apps = executor.loader.project_state(before).apps
        OldTenant = old_apps.get_model("core", "Tenant")
        OldNode = old_apps.get_model("scheduling", "Node")
        OldEmployee = old_apps.get_model("scheduling", "Employee")

        tenant = OldTenant.objects.create(name="Migrationstest", slug="migrationstest")
        # MP_Node.add_root() existiert am gefrorenen Modell nicht mehr --
        # Baumfelder für einen einzelnen Root-Knoten von Hand setzen (das
        # reicht für diesen Test, treebeard braucht dafür kein Setup).
        node = OldNode.objects.create(tenant=tenant, name="Station A", path="0001", depth=1, numchild=0)
        employee = OldEmployee.objects.create(
            tenant=tenant, first_name="Peter", last_name="Meier", employment_pct=70
        )
        employee.nodes.add(node)

        after = [("scheduling", "0013_employment")]
        executor.migrate(after)
        executor.loader.build_graph()

        new_apps = executor.loader.project_state(after).apps
        NewEmployment = new_apps.get_model("scheduling", "Employment")
        employments = list(NewEmployment.objects.filter(employee_id=employee.id))
        self.assertEqual(len(employments), 1)
        self.assertEqual(employments[0].node_id, node.id)
        self.assertEqual(employments[0].pensum_pct, 70)
        self.assertEqual(employments[0].title, "")
        self.assertFalse(employments[0].is_team_lead)

        # Aufräumen: zurück auf den aktuellsten Migrationsstand, sonst bleibt
        # die Test-DB für nachfolgende Tests auf altem Schema hängen.
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


class TeamNestingPermissionTests(APITestCase):
    """
    README Punkt 17: eine Station mit Team-Kind-Knoten -- Mitarbeiter-Scoping
    (_employee_scoped_node_ids), stationsweiter Zuweisungs-Abruf
    (ShiftAssignmentViewSet ?node=) und das Verbot der Direktbuchung auf eine
    Station mit Teams (ShiftAssignment._check_node_has_no_children).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik ICT", slug="klinik-ict")
        self.station = Node.add_root(name="ICT", tenant=self.tenant)
        self.team_a = self.station.add_child(name="Infrastruktur", tenant=self.tenant)
        self.team_b = self.station.add_child(name="Support", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.station,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
        )

        self.planner_user = User.objects.create_user(username="planner-ict", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)

        self.alice_user = User.objects.create_user(username="alice-ict", password="pw-not-real-123!")
        Membership.objects.create(user=self.alice_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.alice = Employee.objects.create(
            tenant=self.tenant, user=self.alice_user, first_name="Alice", last_name="A", employment_pct=100
        )

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_employee_with_team_employment_sees_team_and_station_not_sibling_team(self):
        Employment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.team_a, pensum_pct=100
        )
        self.alice.nodes.add(self.team_a)
        self.auth_as(self.alice_user)
        response = self.client.get("/api/nodes/")
        ids = {n["id"] for n in response.data["results"]}
        self.assertEqual(ids, {self.team_a.id, self.station.id})
        self.assertNotIn(self.team_b.id, ids)

    def test_employee_with_team_employment_still_sees_station_wide_templates(self):
        self.alice.nodes.add(self.team_a)
        self.auth_as(self.alice_user)
        response = self.client.get("/api/time-templates/")
        ids = {t["id"] for t in response.data["results"]}
        self.assertIn(self.template.id, ids)

    def test_shift_assignment_query_by_station_includes_both_teams(self):
        assignment_a = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        bob_user = User.objects.create_user(username="bob-ict", password="pw-not-real-123!")
        Membership.objects.create(user=bob_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        bob = Employee.objects.create(tenant=self.tenant, user=bob_user, first_name="Bob", last_name="B", employment_pct=100)
        assignment_b = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=bob, node=self.team_b, date=date(2026, 8, 3), template=self.template
        )
        self.auth_as(self.planner_user)
        response = self.client.get(f"/api/shift-assignments/?node={self.station.id}")
        ids = {a["id"] for a in response.data["results"]}
        self.assertEqual(ids, {assignment_a.id, assignment_b.id})

    def test_direct_booking_on_station_with_teams_is_rejected(self):
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=self.alice, node=self.station, date=date(2026, 8, 3), template=self.template
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_booking_on_team_is_still_allowed(self):
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=self.alice, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        assignment.clean()  # keine Exception

    def test_multi_employment_does_not_relax_one_shift_per_day_rule(self):
        # Zwei Anstellungen derselben Person in verschiedenen Teams heben die
        # bestehende unique_together("employee", "date")-Regel nicht auf --
        # das ist die Grundannahme, auf der die additive Employment-Ebene
        # (statt einer ShiftAssignment->Employment-FK-Umstellung) beruht.
        Employment.objects.create(tenant=self.tenant, employee=self.alice, node=self.team_a, pensum_pct=60)
        Employment.objects.create(tenant=self.tenant, employee=self.alice, node=self.team_b, pensum_pct=40)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        with self.assertRaises(IntegrityError):
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=self.alice, node=self.team_b, date=date(2026, 8, 3), template=self.template
            )


class EmployeeSerializerEmploymentSyncTests(APITestCase):
    """
    README Punkt 17: employments ist der einzige Änderungsweg für
    Employee.nodes (nodes selbst ist über die API nur noch lesbar) -- siehe
    EmployeeSerializer._sync_employments.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node_a = Node.add_root(name="Team A", tenant=self.tenant)
        self.node_b = Node.add_root(name="Team B", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Peter", last_name="Meier", employment_pct=100
        )
        self.planner_user = User.objects.create_user(username="planner-sync", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)
        token, _ = Token.objects.get_or_create(user=self.planner_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_nodes_in_payload_is_ignored(self):
        response = self.client.patch(f"/api/employees/{self.employee.id}/", {"nodes": [self.node_a.id]})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.employee.refresh_from_db()
        self.assertEqual(list(self.employee.nodes.all()), [])

    def test_employments_on_create_syncs_nodes(self):
        response = self.client.post(
            "/api/employees/",
            {
                "first_name": "Anna",
                "last_name": "Berger",
                "employment_pct": 100,
                "employments": [{"node": self.node_a.id, "pensum_pct": 60, "title": "Arzt", "is_team_lead": True}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        employee = Employee.objects.get(id=response.data["id"])
        self.assertEqual(list(employee.nodes.values_list("id", flat=True)), [self.node_a.id])
        employment = employee.employments.get()
        self.assertEqual(employment.pensum_pct, 60)
        self.assertEqual(employment.title, "Arzt")
        self.assertTrue(employment.is_team_lead)

    def test_employments_replace_on_update(self):
        Employment.objects.create(tenant=self.tenant, employee=self.employee, node=self.node_a, pensum_pct=100)
        self.employee.nodes.add(self.node_a)

        response = self.client.patch(
            f"/api/employees/{self.employee.id}/",
            {"employments": [{"node": self.node_b.id, "pensum_pct": 40, "title": "Dozent", "is_team_lead": False}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(list(self.employee.nodes.values_list("id", flat=True)), [self.node_b.id])
        employment = self.employee.employments.get()
        self.assertEqual(employment.node_id, self.node_b.id)
        self.assertEqual(employment.pensum_pct, 40)

    def test_patch_without_employments_key_leaves_existing_untouched(self):
        Employment.objects.create(tenant=self.tenant, employee=self.employee, node=self.node_a, pensum_pct=100)
        self.employee.nodes.add(self.node_a)

        response = self.client.patch(f"/api/employees/{self.employee.id}/", {"first_name": "Peter"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employments.count(), 1)
        self.assertEqual(list(self.employee.nodes.values_list("id", flat=True)), [self.node_a.id])

    def test_employments_with_foreign_node_rejected(self):
        other_tenant = Tenant.objects.create(name="Klinik B", slug="klinik-b")
        foreign_node = Node.add_root(name="Fremde Station", tenant=other_tenant)
        response = self.client.patch(
            f"/api/employees/{self.employee.id}/",
            {"employments": [{"node": foreign_node.id, "pensum_pct": 50, "title": "", "is_team_lead": False}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class EmployeeSkillsM2MTests(APITestCase):
    """
    Bugfix: Employee.skills (ManyToManyField) darf nie direkt per
    Konstruktor-Kwarg oder setattr() gesetzt werden -- EmployeeSerializer.
    create()/update() reichten `skills` bisher ungefiltert an
    Employee.objects.create(**validated_data) bzw. eine generische
    setattr()-Schleife durch und liessen dabei jeden Request mit einem
    `skills`-Feld (das Frontend schickt es immer mit, auch als leere Liste)
    mit `TypeError: Direct assignment to the forward side of a
    many-to-many set is prohibited` abstürzen -- siehe pop_m2m_fields()/
    set_m2m_fields() in serializers.py für den Fix.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.skill_a = Skill.objects.create(tenant=self.tenant, name="Reanimation")
        self.skill_b = Skill.objects.create(tenant=self.tenant, name="Wundversorgung")
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Peter", last_name="Meier", employment_pct=100
        )
        self.planner_user = User.objects.create_user(username="planner-skills", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)
        token, _ = Token.objects.get_or_create(user=self.planner_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_post_with_empty_skills_list_does_not_crash(self):
        # Der wichtigste Regressionstest: das Frontend schickt `skills`
        # IMMER mit (auch `[]`), das liess bislang jede Neuanlage scheitern.
        response = self.client.post(
            "/api/employees/",
            {"first_name": "Anna", "last_name": "Berger", "employment_pct": 100, "skills": []},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        employee = Employee.objects.get(id=response.data["id"])
        self.assertEqual(list(employee.skills.all()), [])

    def test_post_with_initial_skills_assignment(self):
        response = self.client.post(
            "/api/employees/",
            {
                "first_name": "Anna",
                "last_name": "Berger",
                "employment_pct": 100,
                "skills": [self.skill_a.id, self.skill_b.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        employee = Employee.objects.get(id=response.data["id"])
        self.assertEqual(
            set(employee.skills.values_list("id", flat=True)), {self.skill_a.id, self.skill_b.id}
        )

    def test_patch_changes_skills(self):
        self.employee.skills.add(self.skill_a)
        response = self.client.patch(
            f"/api/employees/{self.employee.id}/", {"skills": [self.skill_b.id]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(list(self.employee.skills.values_list("id", flat=True)), [self.skill_b.id])

    def test_patch_removes_all_skills(self):
        self.employee.skills.add(self.skill_a, self.skill_b)
        response = self.client.patch(f"/api/employees/{self.employee.id}/", {"skills": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(list(self.employee.skills.all()), [])

    def test_patch_combines_scalar_and_m2m_fields(self):
        self.employee.skills.add(self.skill_a)
        response = self.client.patch(
            f"/api/employees/{self.employee.id}/",
            {"first_name": "Petra", "skills": [self.skill_b.id]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.first_name, "Petra")
        self.assertEqual(list(self.employee.skills.values_list("id", flat=True)), [self.skill_b.id])

    def test_patch_without_skills_key_leaves_existing_untouched(self):
        self.employee.skills.add(self.skill_a)
        response = self.client.patch(f"/api/employees/{self.employee.id}/", {"first_name": "Petra"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(list(self.employee.skills.values_list("id", flat=True)), [self.skill_a.id])

    def test_patch_combines_skills_and_employments_in_one_request(self):
        # Zwei unterschiedliche Sonderlogiken (M2M-Sync + verschachtelte
        # employments-Zuweisung, README Punkt 17) in einem Request dürfen
        # sich nicht gegenseitig stören.
        node = Node.add_root(name="Team A", tenant=self.tenant)
        response = self.client.patch(
            f"/api/employees/{self.employee.id}/",
            {
                "skills": [self.skill_a.id],
                "employments": [{"node": node.id, "pensum_pct": 80, "title": "", "is_team_lead": False}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.employee.refresh_from_db()
        self.assertEqual(list(self.employee.skills.values_list("id", flat=True)), [self.skill_a.id])
        self.assertEqual(list(self.employee.nodes.values_list("id", flat=True)), [node.id])


class ShiftPreferenceTests(APITestCase):
    """Wunschfrei/Wunschdienst (MVP-Fahrplan Block 2.13): höchstpersönliche Selbstauskunft."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Fruehdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            break_minutes=30,
        )

        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER)

        self.hr_user = User.objects.create_user(username="hr", password="pw-not-real-123!")
        Membership.objects.create(user=self.hr_user, tenant=self.tenant, role=Membership.Role.HR)

        self.alice_user = User.objects.create_user(username="alice", password="pw-not-real-123!")
        Membership.objects.create(user=self.alice_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.alice = Employee.objects.create(
            tenant=self.tenant, user=self.alice_user, first_name="Alice", last_name="A", employment_pct=100
        )

        self.bob_user = User.objects.create_user(username="bob", password="pw-not-real-123!")
        Membership.objects.create(user=self.bob_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.bob = Employee.objects.create(
            tenant=self.tenant, user=self.bob_user, first_name="Bob", last_name="B", employment_pct=100
        )

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_employee_can_create_own_wunschfrei(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-preferences/", {"date": "2026-08-10", "type": "wunschfrei"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["employee"], self.alice.id)
        self.assertIsNone(response.data["template"])

    def test_employee_can_create_own_wunschdienst(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-preferences/",
            {"date": "2026-08-10", "type": "wunschdienst", "template": self.template.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["template"], self.template.id)

    def test_wunschdienst_without_template_is_rejected(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-preferences/", {"date": "2026-08-10", "type": "wunschdienst"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wunschfrei_with_template_is_rejected(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-preferences/",
            {"date": "2026-08-10", "type": "wunschfrei", "template": self.template.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_preference_same_day_returns_400_not_500(self):
        self.auth_as(self.alice_user)
        first = self.client.post("/api/shift-preferences/", {"date": "2026-08-10", "type": "wunschfrei"})
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self.client.post("/api/shift-preferences/", {"date": "2026-08-10", "type": "wunschfrei"})
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_ignores_submitted_employee_and_forces_own(self):
        # Höchstpersönlich (Block 2.13): selbst wenn ein fremdes employee im
        # Payload mitgeschickt wird, entsteht der Eintrag trotzdem für die
        # eingeloggte Person -- niemand kann für jemand anderen "wünschen".
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-preferences/", {"employee": self.bob.id, "date": "2026-08-10", "type": "wunschfrei"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["employee"], self.alice.id)

    def test_planner_without_own_employee_profile_cannot_create(self):
        # planner_user hat kein Employee-Profil (nur Membership) -- selbst
        # Admin/Planer dürfen hier nicht für andere anlegen, und ohne eigenes
        # Profil bleibt ihnen das Feature schlicht verwehrt.
        self.auth_as(self.planner_user)
        response = self.client.post(
            "/api/shift-preferences/", {"employee": self.alice.id, "date": "2026-08-10", "type": "wunschfrei"}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_can_delete_own_preference(self):
        pref = ShiftPreference.objects.create(
            tenant=self.tenant, employee=self.alice, date=date(2026, 8, 10), type=ShiftPreference.Type.FREE
        )
        self.auth_as(self.alice_user)
        response = self.client.delete(f"/api/shift-preferences/{pref.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_employee_cannot_delete_others_preference(self):
        pref = ShiftPreference.objects.create(
            tenant=self.tenant, employee=self.bob, date=date(2026, 8, 10), type=ShiftPreference.Type.FREE
        )
        self.auth_as(self.alice_user)
        response = self.client.delete(f"/api/shift-preferences/{pref.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_planner_cannot_delete_employees_preference(self):
        # Kein Manager-Override wie bei Absence -- auch Admin/Planer dürfen
        # fremde Wünsche nicht löschen.
        pref = ShiftPreference.objects.create(
            tenant=self.tenant, employee=self.alice, date=date(2026, 8, 10), type=ShiftPreference.Type.FREE
        )
        self.auth_as(self.planner_user)
        response = self.client.delete(f"/api/shift-preferences/{pref.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_all_roles_can_read(self):
        ShiftPreference.objects.create(
            tenant=self.tenant, employee=self.alice, date=date(2026, 8, 10), type=ShiftPreference.Type.FREE
        )
        for user in (self.planner_user, self.hr_user, self.alice_user, self.bob_user):
            self.auth_as(user)
            response = self.client.get("/api/shift-preferences/")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data["results"]), 1)


class NotificationsAndTaskCountsTests(APITestCase):
    """
    E-Mail-Benachrichtigungen (MVP-Fahrplan Block 2.4, core.notifications)
    und task_counts in GET /api/me/ (Grundlage der Header-Badges bei
    Abwesenheiten/Diensttausch/Zeiterfassung).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
        )
        self.admin_user = User.objects.create_user(
            username="admin", password="pw-not-real-123!", email="admin@example.com"
        )
        Membership.objects.create(user=self.admin_user, tenant=self.tenant, role=Membership.Role.ADMIN)

        self.alice_user = User.objects.create_user(
            username="alice", password="pw-not-real-123!", email="alice@example.com"
        )
        Membership.objects.create(user=self.alice_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.alice = Employee.objects.create(
            tenant=self.tenant, user=self.alice_user, first_name="Alice", last_name="A", employment_pct=100
        )

        self.bob_user = User.objects.create_user(
            username="bob", password="pw-not-real-123!", email="bob@example.com"
        )
        Membership.objects.create(user=self.bob_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        self.bob = Employee.objects.create(
            tenant=self.tenant, user=self.bob_user, first_name="Bob", last_name="B", employment_pct=100
        )
        self.vacation_type = AbsenceType.objects.create(
            tenant=self.tenant, name="Ferien", deducts_vacation_days=True
        )
        mail.outbox.clear()

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    # -- E-Mail-Benachrichtigungen --

    def test_new_absence_request_notifies_managers(self):
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/absences/",
            {
                "employee": self.alice.id,
                "start_date": "2026-08-10",
                "end_date": "2026-08-12",
                "type": self.vacation_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.admin_user.email, mail.outbox[0].to)

    def test_admin_created_absence_sends_no_mail(self):
        self.auth_as(self.admin_user)
        response = self.client.post(
            "/api/absences/",
            {
                "employee": self.alice.id,
                "start_date": "2026-08-10",
                "end_date": "2026-08-12",
                "type": self.vacation_type.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 0)

    def test_absence_approval_notifies_requester(self):
        absence = Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 8, 10), end_date=date(2026, 8, 12),
            type=self.vacation_type,
        )
        mail.outbox.clear()
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/absences/{absence.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.alice_user.email, mail.outbox[0].to)

    def test_absence_rejection_notifies_requester(self):
        absence = Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 8, 10), end_date=date(2026, 8, 12),
            type=self.vacation_type,
        )
        mail.outbox.clear()
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/absences/{absence.id}/reject/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.alice_user.email, mail.outbox[0].to)

    def test_new_trade_request_notifies_target(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": assignment.id, "target_employee": self.bob.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.bob_user.email, mail.outbox[0].to)

    def test_trade_accept_notifies_managers(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=assignment, target_employee=self.bob
        )
        mail.outbox.clear()
        self.auth_as(self.bob_user)
        response = self.client.post(f"/api/shift-trade-requests/{trade.id}/accept/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.admin_user.email, mail.outbox[0].to)

    def test_trade_decline_notifies_requester(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=assignment, target_employee=self.bob
        )
        mail.outbox.clear()
        self.auth_as(self.bob_user)
        response = self.client.post(f"/api/shift-trade-requests/{trade.id}/decline/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.alice_user.email, mail.outbox[0].to)

    def test_trade_approve_notifies_both_parties(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=assignment,
            target_employee=self.bob,
            status=ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED,
        )
        mail.outbox.clear()
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/shift-trade-requests/{trade.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.alice_user.email, mail.outbox[0].to)
        self.assertIn(self.bob_user.email, mail.outbox[0].to)

    def test_trade_reject_notifies_both_parties(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=assignment,
            target_employee=self.bob,
            status=ShiftTradeRequest.Status.EMPLOYEE_ACCEPTED,
        )
        mail.outbox.clear()
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/shift-trade-requests/{trade.id}/reject/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.alice_user.email, mail.outbox[0].to)
        self.assertIn(self.bob_user.email, mail.outbox[0].to)

    def test_employee_without_email_is_silently_skipped(self):
        no_email_user = User.objects.create_user(username="noemail", password="pw-not-real-123!")
        Membership.objects.create(user=no_email_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        no_email_employee = Employee.objects.create(
            tenant=self.tenant, user=no_email_user, first_name="Kein", last_name="Mail", employment_pct=100
        )
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        self.auth_as(self.alice_user)
        response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": assignment.id, "target_employee": no_email_employee.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 0)

    # -- task_counts (Header-Badges) --

    def auth_and_get_me(self, user):
        self.auth_as(user)
        return self.client.get("/api/me/")

    def test_admin_sees_pending_absence_count(self):
        Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 8, 10), end_date=date(2026, 8, 12),
            type=self.vacation_type,
        )
        response = self.auth_and_get_me(self.admin_user)
        self.assertEqual(response.data["task_counts"]["absences"], 1)

    def test_employee_sees_zero_absence_count(self):
        Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 8, 10), end_date=date(2026, 8, 12),
            type=self.vacation_type,
        )
        response = self.auth_and_get_me(self.alice_user)
        self.assertEqual(response.data["task_counts"]["absences"], 0)

    def test_admin_trade_count_only_counts_employee_accepted(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=assignment, target_employee=self.bob
        )
        response = self.auth_and_get_me(self.admin_user)
        self.assertEqual(response.data["task_counts"]["trades"], 0)  # noch PENDING

        trade.accept()
        response = self.auth_and_get_me(self.admin_user)
        self.assertEqual(response.data["task_counts"]["trades"], 1)

    def test_employee_trade_count_only_own_pending_as_target(self):
        assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.alice, node=self.node, date=date(2026, 8, 3), template=self.template
        )
        ShiftTradeRequest.objects.create(
            tenant=self.tenant, requester_assignment=assignment, target_employee=self.bob
        )
        bob_response = self.auth_and_get_me(self.bob_user)
        self.assertEqual(bob_response.data["task_counts"]["trades"], 1)

        alice_response = self.auth_and_get_me(self.alice_user)
        self.assertEqual(alice_response.data["task_counts"]["trades"], 0)

    def test_admin_sees_submitted_time_record_count(self):
        past_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.alice,
            node=self.node,
            date=date(2026, 7, 27),
            template=self.template,
        )
        TimeRecord.objects.create(
            tenant=self.tenant, assignment=past_assignment, actual_start=time(8, 0), actual_end=time(16, 0)
        )
        response = self.auth_and_get_me(self.admin_user)
        self.assertEqual(response.data["task_counts"]["time_records"], 1)

    def test_hr_sees_zero_task_counts(self):
        Absence.objects.create(
            tenant=self.tenant, employee=self.alice, start_date=date(2026, 8, 10), end_date=date(2026, 8, 12),
            type=self.vacation_type,
        )
        hr_user = User.objects.create_user(username="hr", password="pw-not-real-123!")
        Membership.objects.create(user=hr_user, tenant=self.tenant, role=Membership.Role.HR)
        response = self.auth_and_get_me(hr_user)
        self.assertEqual(response.data["task_counts"], {"absences": 0, "trades": 0, "time_records": 0})


class ShiftAssignmentSwapTests(TestCase):
    """README Block 2.8: echter Swap zweier Zuweisungen (ShiftAssignment.swap())."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.station = Node.add_root(name="Station A", tenant=self.tenant)
        self.team_a = self.station.add_child(name="Team A", tenant=self.tenant)
        self.team_b = self.station.add_child(name="Team B", tenant=self.tenant)
        self.employee_1 = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.employee_2 = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.station,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
        )

    def test_swap_exchanges_employee_date_node(self):
        # Unterschiedliche Tage UND unterschiedliche Team-Knoten -- der
        # allgemeine Fall eines Grid-Drags (nicht nur der Sonderfall
        # "gleicher Tag", siehe Test unten).
        a1 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        a2 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_2, node=self.team_b, date=date(2026, 8, 5), template=self.template
        )
        first, second = ShiftAssignment.swap(a1.id, a2.id)
        self.assertEqual(first.employee_id, self.employee_2.id)
        self.assertEqual(first.date, date(2026, 8, 5))
        self.assertEqual(first.node_id, self.team_b.id)
        self.assertEqual(second.employee_id, self.employee_1.id)
        self.assertEqual(second.date, date(2026, 8, 3))
        self.assertEqual(second.node_id, self.team_a.id)
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.employee_id, self.employee_2.id)
        self.assertEqual(a1.date, date(2026, 8, 5))
        self.assertEqual(a2.employee_id, self.employee_1.id)
        self.assertEqual(a2.date, date(2026, 8, 3))

    def test_swap_same_date_different_employees(self):
        # Regressionstest für den beim Planen gefundenen Bug in
        # ShiftTradeRequest.approve(): der eingebaute validate_unique()
        # sieht während der Transaktion noch den unveränderten DB-Stand der
        # jeweils anderen Zeile und meldet sonst einen falschen Konflikt --
        # gerade der häufigste Tauschfall (zwei Personen tauschen denselben
        # Tag) darf hier nicht scheitern.
        a1 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        a2 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_2, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        first, second = ShiftAssignment.swap(a1.id, a2.id)
        self.assertEqual(first.employee_id, self.employee_2.id)
        self.assertEqual(second.employee_id, self.employee_1.id)
        self.assertEqual(first.date, date(2026, 8, 3))
        self.assertEqual(second.date, date(2026, 8, 3))

    def test_swap_with_self_is_rejected(self):
        a1 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        with self.assertRaises(ValidationError):
            ShiftAssignment.swap(a1.id, a1.id)

    def test_swap_blocked_by_rule_engine_leaves_state_unchanged(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Nachtdienst-berechtigt")
        self.template.required_skill = skill
        self.template.save()
        self.employee_1.skills.add(skill)
        # employee_2 hat den Skill NICHT -> nach dem Swap würde employee_2
        # die qualifikationspflichtige Schicht übernehmen.
        a1 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        a2 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_2, node=self.team_a, date=date(2026, 8, 5), template=self.template
        )
        with self.assertRaises(ValidationError):
            ShiftAssignment.swap(a1.id, a2.id)
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.employee_id, self.employee_1.id)
        self.assertEqual(a2.employee_id, self.employee_2.id)

    def test_swap_unaffected_by_unrelated_third_party_assignment(self):
        # employee_1 hat eine völlig unbeteiligte dritte Zuweisung an einem
        # anderen Tag -- das Tauschen von a1/a2 tauscht employee+date+node
        # als Einheit zwischen genau diesen beiden Zeilen, die Menge der
        # belegten (employee, date)-Paare bleibt dabei unverändert (nur die
        # Zeilen-Zuordnung ändert sich), ein Dritter kann also nie in
        # Konflikt geraten -- der manuelle Check in ShiftAssignment.swap()
        # ist hier bewusst nur Absicherung, nicht die eigentliche Prüfung.
        a1 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.team_a, date=date(2026, 8, 3), template=self.template
        )
        a2 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_2, node=self.team_a, date=date(2026, 8, 5), template=self.template
        )
        third = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.team_a, date=date(2026, 8, 10), template=self.template
        )
        ShiftAssignment.swap(a1.id, a2.id)
        third.refresh_from_db()
        self.assertEqual(third.employee_id, self.employee_1.id)
        self.assertEqual(third.date, date(2026, 8, 10))


class ShiftAssignmentSwapAPITests(APITestCase):
    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")
        self.station = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee_1 = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.employee_2 = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.station,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
        )
        self.a1 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_1, node=self.station, date=date(2026, 8, 3), template=self.template
        )
        self.a2 = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee_2, node=self.station, date=date(2026, 8, 5), template=self.template
        )
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_swap_endpoint_exchanges_both_assignments(self):
        response = self.client.post(
            "/api/shift-assignments/swap/", {"first": self.a1.id, "second": self.a2.id}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["first"]["employee"], self.employee_2.id)
        self.assertEqual(response.data["second"]["employee"], self.employee_1.id)
        self.a1.refresh_from_db()
        self.a2.refresh_from_db()
        self.assertEqual(self.a1.employee_id, self.employee_2.id)
        self.assertEqual(self.a2.employee_id, self.employee_1.id)

    def test_swap_endpoint_requires_both_ids(self):
        response = self.client.post("/api/shift-assignments/swap/", {"first": self.a1.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_swap_endpoint_returns_400_on_rule_violation(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Nachtdienst-berechtigt")
        self.template.required_skill = skill
        self.template.save()
        self.employee_1.skills.add(skill)
        response = self.client.post(
            "/api/shift-assignments/swap/", {"first": self.a1.id, "second": self.a2.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_swap_endpoint_returns_404_for_foreign_tenant_assignment(self):
        other_tenant, other_user = make_tenant_with_planner("klinik-b", "planner_b")
        other_node = Node.add_root(name="Station B", tenant=other_tenant)
        other_employee = Employee.objects.create(
            tenant=other_tenant, first_name="Carla", last_name="C", employment_pct=100
        )
        other_template = TimeTemplate.objects.create(
            tenant=other_tenant, node=other_node, name="Tagdienst", start_time=time(8, 0), end_time=time(16, 0)
        )
        foreign_assignment = ShiftAssignment.objects.create(
            tenant=other_tenant, employee=other_employee, node=other_node, date=date(2026, 8, 3), template=other_template
        )
        response = self.client.post(
            "/api/shift-assignments/swap/", {"first": self.a1.id, "second": foreign_assignment.id}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_swap_endpoint_denied_for_employee_role(self):
        employee_user = User.objects.create_user(username="alice", password="pw-not-real-123!")
        Membership.objects.create(user=employee_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        token, _ = Token.objects.get_or_create(user=employee_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.post(
            "/api/shift-assignments/swap/", {"first": self.a1.id, "second": self.a2.id}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ShiftAssignmentOtherTeamConflictsAPITests(APITestCase):
    """
    Regressionstest für einen Nutzer-gemeldeten "massiven Bug": eine sonst
    leere Zelle im Planblatt liess sich trotzdem nicht beplanen ("... hat am
    ... bereits 'Frühschicht' ... überschneidet"), obwohl weder das Grid
    noch die Admin-Liste einen Dienst zeigten. Ursache: bei einer
    Mehrfachanstellung (README Punkt 17) prüft
    ShiftAssignment._check_no_overlap() tenant-weit über alle Teams/
    Stationen, aber das Planblatt lädt nur die aktuell gewählte Station --
    ein blockierender Dienst in einer ANDEREN Station war unsichtbar. Der
    neue Endpoint /api/shift-assignments/other-team-conflicts/ deckt genau
    diese Lücke: proaktive Warnung statt kryptischer Fehlermeldung erst beim
    gescheiterten Beplanungsversuch.
    """

    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-conflicts", "planner_conflicts")
        self.station_a = Node.add_root(name="Station A", tenant=self.tenant)
        self.station_b = Node.add_root(name="Station B", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Nina", last_name="Kaufmann", employment_pct=40
        )
        self.template_a = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.station_a, name="Küchendienst", start_time=time(6, 30), end_time=time(14, 30)
        )
        self.template_b = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.station_b, name="Frühschicht", start_time=time(7, 0), end_time=time(17, 0)
        )
        # Der eigentliche "unsichtbare" Konflikt: ein Dienst in Station B,
        # während das Planblatt Station A anzeigt.
        self.conflict = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.station_b, date=date(2026, 8, 11), template=self.template_b
        )
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _get(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/shift-assignments/other-team-conflicts/?{query}")

    def test_finds_conflict_in_a_different_station(self):
        response = self._get(
            employees=self.employee.id, date_from="2026-08-01", date_to="2026-08-31", exclude_node=self.station_a.id
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["employee"], self.employee.id)
        self.assertEqual(entry["date"], "2026-08-11")
        self.assertEqual(entry["template_name"], "Frühschicht")
        self.assertEqual(entry["start_time"], "07:00")
        self.assertEqual(entry["end_time"], "17:00")
        self.assertEqual(entry["node_name"], "Station B")

    def test_excludes_conflicts_within_the_excluded_station_itself(self):
        # Ein Dienst INNERHALB der ausgeschlossenen Station ist schon über
        # den normalen Grid-Fetch sichtbar -- braucht keine Extra-Warnung.
        response = self._get(
            employees=self.employee.id, date_from="2026-08-01", date_to="2026-08-31", exclude_node=self.station_b.id
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_special_category_assignments_never_count_as_conflict(self):
        # Wie bei _overlapping_conflict() im Modell: eine Spezialität
        # (Pikett) ist additiv, kein Slot-Konkurrent, taucht daher hier nie
        # als Konflikt auf.
        special_template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.station_b,
            name="Pikett",
            start_time=time(18, 0),
            end_time=time(22, 0),
            category=TimeTemplate.Category.SPECIAL,
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            node=self.station_b,
            date=date(2026, 8, 12),
            template=special_template,
        )
        response = self._get(
            employees=self.employee.id, date_from="2026-08-01", date_to="2026-08-31", exclude_node=self.station_a.id
        )
        dates = [entry["date"] for entry in response.data]
        self.assertNotIn("2026-08-12", dates)

    def test_date_range_filters_out_conflicts_outside_it(self):
        response = self._get(
            employees=self.employee.id, date_from="2026-09-01", date_to="2026-09-30", exclude_node=self.station_a.id
        )
        self.assertEqual(response.data, [])

    def test_missing_params_return_empty_list_instead_of_error(self):
        response = self.client.get("/api/shift-assignments/other-team-conflicts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_denied_for_employee_role(self):
        employee_user = User.objects.create_user(username="nina-login", password="pw-not-real-456!")
        Membership.objects.create(user=employee_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        token, _ = Token.objects.get_or_create(user=employee_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self._get(
            employees=self.employee.id, date_from="2026-08-01", date_to="2026-08-31", exclude_node=self.station_a.id
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_does_not_leak_other_tenants_data(self):
        other_tenant, other_user = make_tenant_with_planner("klinik-conflicts-b", "planner_conflicts_b")
        other_station = Node.add_root(name="Fremdstation", tenant=other_tenant)
        other_employee = Employee.objects.create(
            tenant=other_tenant, first_name="Fremd", last_name="Person", employment_pct=100
        )
        other_template = TimeTemplate.objects.create(
            tenant=other_tenant, node=other_station, name="Fremddienst", start_time=time(6, 0), end_time=time(14, 0)
        )
        ShiftAssignment.objects.create(
            tenant=other_tenant, employee=other_employee, node=other_station, date=date(2026, 8, 11), template=other_template
        )
        # Gleiche numerische Employee-Id wie self.employee wäre der
        # gefährlichste Fall -- hier stattdessen einfach eine fremde Id, die
        # zufällig im selben Query mitgesendet wird, um sicherzustellen,
        # dass tenant=self.request.tenant im Endpoint tatsächlich greift.
        response = self._get(
            employees=f"{self.employee.id},{other_employee.id}",
            date_from="2026-08-01",
            date_to="2026-08-31",
            exclude_node=self.station_a.id,
        )
        employee_ids_in_response = {entry["employee"] for entry in response.data}
        self.assertNotIn(other_employee.id, employee_ids_in_response)


class ShiftTradeRequestFullSwapSameDateTests(TestCase):
    """
    Regressionstest für den beim Planen von Block 2.8 gefundenen Bug: der
    Voll-Swap-Zweig von ShiftTradeRequest.approve() scheiterte bislang an
    einem falschen Unique-Konflikt, sobald beide getauschten Zuweisungen auf
    demselben Datum lagen (der Normalfall "wir tauschen unsere Mittwoch-
    Schichten"). Ergänzt test_approve_full_swap_exchanges_employees
    (ShiftTradeRequestTests), das bislang nur unterschiedliche Daten prüft.
    """

    def test_approve_full_swap_same_date_succeeds(self):
        tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        node = Node.add_root(name="Station A", tenant=tenant)
        employee_1 = Employee.objects.create(tenant=tenant, first_name="Anna", last_name="A", employment_pct=100)
        employee_2 = Employee.objects.create(tenant=tenant, first_name="Bea", last_name="B", employment_pct=100)
        template = TimeTemplate.objects.create(
            tenant=tenant, node=node, name="Tagdienst", start_time=time(8, 0), end_time=time(16, 0), break_minutes=30
        )
        a1 = ShiftAssignment.objects.create(
            tenant=tenant, employee=employee_1, node=node, date=date(2026, 8, 3), template=template
        )
        a2 = ShiftAssignment.objects.create(
            tenant=tenant, employee=employee_2, node=node, date=date(2026, 8, 3), template=template
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=tenant, requester_assignment=a1, target_employee=employee_2, target_assignment=a2
        )
        trade.approve()
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(a1.employee_id, employee_2.id)
        self.assertEqual(a2.employee_id, employee_1.id)
        self.assertEqual(a1.date, date(2026, 8, 3))
        self.assertEqual(a2.date, date(2026, 8, 3))


class TimeTemplateMinimumStaffingTests(TestCase):
    """README Block 2.9: Mindestbesetzung ist rein informativ, blockiert nichts."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)

    def test_minimum_staffing_defaults_to_zero(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Tagdienst", start_time=time(8, 0), end_time=time(16, 0)
        )
        self.assertEqual(template.minimum_staffing, 0)

    def test_understaffed_template_does_not_block_shift_assignment(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Tagdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            break_minutes=30,
            minimum_staffing=5,
        )
        employee = Employee.objects.create(tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100)
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=employee, node=self.node, date=date(2026, 8, 3), template=template
        )
        assignment.clean()  # keine Exception trotz nur einer von 5 Personen


class TimeTemplateMinimumStaffingAPITests(APITestCase):
    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_minimum_staffing_round_trips_through_serializer(self):
        create_response = self.client.post(
            "/api/time-templates/",
            {
                "node": self.node.id,
                "name": "Nachtdienst",
                "start_time": "22:00",
                "end_time": "06:00",
                "minimum_staffing": 3,
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["minimum_staffing"], 3)

        template_id = create_response.data["id"]
        patch_response = self.client.patch(f"/api/time-templates/{template_id}/", {"minimum_staffing": 4})
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["minimum_staffing"], 4)


class TimeTemplateCategoryTests(TestCase):
    """
    Nutzer-Feedback (2026-08): Stempelleisten im Planblatt/Jahresplan sollen
    reguläre Dienste und Spezialitäten (z. B. Pikettdienst) in getrennten
    Zeilen zeigen. category ist rein informativ (UI-Gruppierung), keine
    Regel-Engine-Auswirkung -- analog zu minimum_staffing oben.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)

    def test_category_defaults_to_shift(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Tagdienst", start_time=time(8, 0), end_time=time(16, 0)
        )
        self.assertEqual(template.category, TimeTemplate.Category.SHIFT)

    def test_special_category_does_not_block_shift_assignment(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Pikettdienst",
            start_time=time(20, 0),
            end_time=time(22, 0),
            category=TimeTemplate.Category.SPECIAL,
        )
        employee = Employee.objects.create(tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100)
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=employee, node=self.node, date=date(2026, 8, 3), template=template
        )
        assignment.clean()  # keine Exception -- category ist rein informativ


class TimeTemplateCategoryAPITests(APITestCase):
    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_category_round_trips_through_serializer(self):
        create_response = self.client.post(
            "/api/time-templates/",
            {
                "node": self.node.id,
                "name": "Pikettdienst",
                "start_time": "20:00",
                "end_time": "22:00",
                "category": "special",
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["category"], "special")

        template_id = create_response.data["id"]
        patch_response = self.client.patch(f"/api/time-templates/{template_id}/", {"category": "shift"})
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.data["category"], "shift")

    def test_omitting_category_defaults_to_shift(self):
        create_response = self.client.post(
            "/api/time-templates/",
            {"node": self.node.id, "name": "Frühdienst", "start_time": "07:00", "end_time": "15:00"},
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data["category"], "shift")


class SplitShiftTests(TestCase):
    """
    README Punkt 18: geteilte Dienste (Split-Shifts) -- mehrere Zuweisungen
    derselben Person am selben Tag, seit der Lockerung von unique_together
    auf ("employee", "date", "template") möglich. Praxisfall aus dem
    Feedback: Frühdienst 07:00-12:00 + Spätdienst 13:00-17:30 derselben
    Person am selben Tag (ICT).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="ICT", tenant=self.tenant)
        self.early = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Frühdienst",
            start_time=time(7, 0), end_time=time(12, 0), break_minutes=0,
        )
        self.late = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Spätdienst",
            start_time=time(13, 0), end_time=time(17, 30), break_minutes=0,
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )

    def _assign(self, template, day=date(2026, 8, 3)):
        return ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=day, template=template
        )

    def test_two_non_overlapping_shifts_same_day_are_valid(self):
        first = self._assign(self.early)
        second = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=first.date, template=self.late
        )
        second.full_clean()  # keine Exception
        second.save()
        self.assertEqual(
            ShiftAssignment.objects.filter(employee=self.employee, date=first.date).count(), 2
        )

    def test_overlapping_shifts_same_day_are_rejected(self):
        self._assign(self.early)  # 07:00-12:00
        overlapping = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Vormittag-Ueberlappung",
            start_time=time(11, 0), end_time=time(14, 0), break_minutes=0,
        )
        conflict = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=overlapping,
        )
        with self.assertRaises(ValidationError):
            conflict.full_clean()

    def test_identical_template_twice_same_day_is_rejected(self):
        self._assign(self.early)
        duplicate = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=self.early,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_adjacent_shifts_touching_exactly_are_not_overlapping(self):
        # Ende Frühdienst (12:00) == Beginn eines fiktiven Templates ab
        # 12:00 -- Grenzfall, gilt nicht als Überschneidung (halboffenes
        # Intervall).
        self._assign(self.early)  # 07:00-12:00
        touching = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Direkt-Anschluss",
            start_time=time(12, 0), end_time=time(17, 0), break_minutes=0,
        )
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=touching,
        )
        assignment.full_clean()  # keine Exception

    def test_daily_span_considers_all_shifts_of_the_day_combined(self):
        # Tenant-Default maximum_daily_span_hours ist 14h (siehe core.models).
        # Früh (07-12) + Spät (13-17:30) ergibt zusammen 07:00-17:30 = 10.5h
        # Gesamtspanne -- unproblematisch.
        self._assign(self.early)
        second = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=self.late,
        )
        second.full_clean()  # keine Exception

        # Ein dritter, sehr spaeter Dienst am selben Tag reisst die
        # Gesamtspanne (07:00 bis weit nach Mitternacht) ueber die
        # Tenant-Grenze.
        very_late = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Spaetester-Dienst",
            start_time=time(22, 0), end_time=time(23, 59), break_minutes=0,
        )
        third = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=very_late,
        )
        with self.assertRaises(ValidationError):
            third.full_clean()

    def test_rest_period_check_ignores_same_day_gap(self):
        # Kernentscheidung: die Mittagspause zwischen zwei Split-Shift-
        # Diensten desselben Tages ist KEINE Ruhezeit im Sinne von Art. 15a
        # ArG (die gilt zwischen Kalendertagen) -- nur 1h Luecke zwischen
        # Frueh-Ende (12:00) und Spaet-Beginn (13:00) darf NICHT als
        # Ruhezeit-Verstoss (Tenant-Default 11h) geahndet werden.
        self._assign(self.early)
        second = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=self.late,
        )
        second.full_clean()  # keine Exception -- wäre bei 11h-Ruhezeitpruefung sonst abgelehnt

    def test_rest_period_still_checked_against_previous_and_next_day(self):
        # Split-Shifts duerfen die normale Tag-zu-Tag-Ruhezeitpruefung nicht
        # aushebeln: ein Spaetdienst bis 17:30, gefolgt von einem
        # Fruehdienst am naechsten Tag ab 07:00, hat nur 13.5h Ruhezeit --
        # das ist zwar ueber dem 11h-Minimum, aber ein zu frueher naechster
        # Dienst (z. B. 04:00) muss weiterhin blockiert werden.
        self._assign(self.late, day=date(2026, 8, 3))  # bis 17:30
        too_early_next_day = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Zu-frueh",
            start_time=time(4, 0), end_time=time(8, 0), break_minutes=0,
        )
        assignment = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 4),
            template=too_early_next_day,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_weekly_and_monthly_summary_sum_both_shifts(self):
        self._assign(self.early)  # 5h netto (07-12, keine Pause)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=self.late,  # 4.5h netto (13-17:30, keine Pause)
        )
        weekly = self.employee.weekly_hours_summary(date(2026, 8, 3))
        self.assertEqual(weekly["ist_hours"], 9.5)
        monthly = self.employee.monthly_summary(2026, 8)
        self.assertEqual(monthly["ist_hours"], 9.5)

    def test_swap_one_of_two_same_day_shifts(self):
        # employee hat Frueh+Spaet am selben Tag; nur der Fruehdienst wird
        # mit einer fremden Zuweisung (anderer Tag, anderes Template)
        # getauscht -- der Spaetdienst bleibt als eigene Zeile unangetastet,
        # und der Fruehdienst-Platz bekommt das eingetauschte (andere)
        # Template.
        early_assignment = self._assign(self.early)
        late_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=self.late,
        )
        night = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nachtschicht",
            start_time=time(20, 0), end_time=time(23, 0), break_minutes=0,
        )
        other_employee = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )
        other_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=other_employee, node=self.node, date=date(2026, 8, 10),
            template=night,
        )
        first, second = ShiftAssignment.swap(early_assignment.id, other_assignment.id)
        self.assertEqual(first.employee_id, other_employee.id)
        self.assertEqual(first.template_id, self.early.id)
        self.assertEqual(second.employee_id, self.employee.id)
        self.assertEqual(second.template_id, night.id)
        # employee hat jetzt Spaet (unveraendert) + die eingetauschte Nachtschicht,
        # nicht mehr den Fruehdienst.
        remaining = set(
            ShiftAssignment.objects.filter(employee=self.employee, date=date(2026, 8, 3)).values_list(
                "id", "template_id"
            )
        )
        self.assertEqual(remaining, {(late_assignment.id, self.late.id), (second.id, night.id)})

    def test_swap_rejects_real_overlap_conflict(self):
        # employee hat Frueh+Spaet am selben Tag UND eine dritte, unbeteiligte
        # Nachtschicht an diesem Tag, die getauscht werden soll. Die
        # eingetauschte Zuweisung (15:00-19:00) ueberschneidet sich mit dem
        # bestehenden, NICHT am Tausch beteiligten Spaetdienst (13:00-17:30)
        # -- muss abgelehnt werden.
        self._assign(self.early, day=date(2026, 8, 3))
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=self.late,
        )
        night = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nachtschicht",
            start_time=time(20, 0), end_time=time(23, 0), break_minutes=0,
        )
        employee_night = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=date(2026, 8, 3),
            template=night,
        )
        overlapping = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Ueberlappt-mit-Spaet",
            start_time=time(15, 0), end_time=time(19, 0), break_minutes=0,
        )
        other_employee = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )
        other_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=other_employee, node=self.node, date=date(2026, 8, 10),
            template=overlapping,
        )
        with self.assertRaises(ValidationError):
            ShiftAssignment.swap(employee_night.id, other_assignment.id)


class SpecialAssignmentStackingTests(TestCase):
    """
    Nutzer-Feedback (2026-08): eine Spezialität (TimeTemplate.category ==
    "special", z. B. Pikettdienst) ist ein additiver Zusatz zu einem
    regulären Dienst, kein Slot-Konkurrent -- ein Frühdienst UND ein
    Pikettdienst gleichzeitig am selben Tag müssen möglich sein, ohne dass
    Ruhezeit-/Überschneidungs-/Tagesspannen-/Höchstarbeitszeit-Prüfung
    dazwischenfunkt, und ohne dass die Spezialität in Sollstunden/Ist-
    Stunden einfliesst (geklärte Design-Entscheidung: rein informativ).
    Qualifikation/Jugendschutz/Absenz-Konflikt gelten dagegen weiterhin.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.day_shift = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Frühdienst",
            start_time=time(7, 0), end_time=time(15, 0), break_minutes=30,
        )
        self.pikett = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Pikettdienst",
            start_time=time(7, 0), end_time=time(15, 0), break_minutes=0,
            category=TimeTemplate.Category.SPECIAL,
        )
        self.other_pikett = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Pikett Arzt",
            start_time=time(0, 0), end_time=time(23, 59), break_minutes=0,
            category=TimeTemplate.Category.SPECIAL,
        )
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )

    def test_special_alongside_regular_shift_same_time_is_valid(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.day_shift,
        )
        special = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.pikett,
        )
        special.full_clean()  # keine Exception trotz identischer Zeitspanne
        special.save()
        self.assertEqual(
            ShiftAssignment.objects.filter(employee=self.employee, date=date(2026, 8, 3)).count(), 2
        )

    def test_two_specials_same_day_are_valid(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.pikett,
        )
        second = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.other_pikett,
        )
        second.full_clean()  # keine Exception

    def test_special_does_not_block_next_day_rest_period(self):
        # Nachtschicht direkt gefolgt von einem Pikettdienst am nächsten Tag
        # wäre bei einem regulären Dienst eine Ruhezeit-Verletzung.
        night = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nachtdienst",
            start_time=time(20, 0), end_time=time(8, 0), break_minutes=60,
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=night,
        )
        special_next_day = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 4), template=self.pikett,
        )
        special_next_day.full_clean()  # keine Exception

    def test_special_does_not_block_weekly_hours_limit(self):
        self.tenant.maximum_weekly_hours = 45
        self.tenant.save()
        long_special = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Langer Pikett",
            start_time=time(0, 0), end_time=time(23, 59), break_minutes=0,
            category=TimeTemplate.Category.SPECIAL,
        )
        for offset in range(5):
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=self.employee, node=self.node,
                date=date(2026, 8, 3) + timedelta(days=offset), template=self.day_shift,
            )
        special = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=long_special,
        )
        special.full_clean()  # keine Exception trotz fast 24h Zusatz

    def test_special_only_day_still_counts_as_weekly_rest_day(self):
        monday = date(2026, 8, 3)
        for offset in range(6):  # Mo-Sa reguläre Dienste
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=self.employee, node=self.node,
                date=monday + timedelta(days=offset), template=self.day_shift,
            )
        sunday_special = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=monday + timedelta(days=6), template=self.pikett,
        )
        sunday_special.full_clean()  # keine Exception -- Sonntag bleibt "frei" trotz Pikett

    def test_special_hours_excluded_from_weekly_hours_summary(self):
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.day_shift,
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.pikett,
        )
        summary = self.employee.weekly_hours_summary(date(2026, 8, 3))
        # Nur der Frühdienst (7.5h netto), nicht zusätzlich der zeitgleiche Pikett.
        self.assertEqual(summary["ist_hours"], 7.5)

    def test_special_hours_excluded_from_time_account_summary(self):
        self.employee.employment_start_date = date(2026, 1, 1)
        self.employee.save()
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.day_shift,
        )
        with_pikett = self.employee.time_account_summary(as_of_date=date(2026, 8, 3))
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.pikett,
        )
        with_and_without = self.employee.time_account_summary(as_of_date=date(2026, 8, 3))
        self.assertEqual(with_pikett["saldo_hours"], with_and_without["saldo_hours"])

    def test_special_still_blocked_during_approved_absence(self):
        vacation_type = AbsenceType.objects.create(tenant=self.tenant, name="Ferien", deducts_vacation_days=True)
        Absence.objects.create(
            tenant=self.tenant, employee=self.employee,
            start_date=date(2026, 8, 3), end_date=date(2026, 8, 3),
            type=vacation_type, status=Absence.Status.APPROVED,
        )
        special = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.pikett,
        )
        with self.assertRaises(ValidationError):
            special.clean()

    def test_special_still_requires_skill(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Pikett-berechtigt")
        self.pikett.required_skill = skill
        self.pikett.save()
        special = ShiftAssignment(
            tenant=self.tenant, employee=self.employee, node=self.node,
            date=date(2026, 8, 3), template=self.pikett,
        )
        with self.assertRaises(ValidationError):
            special.clean()

    def test_special_still_blocked_for_minor_at_night(self):
        night_special = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nacht-Pikett",
            start_time=time(23, 0), end_time=time(6, 0), break_minutes=0,
            category=TimeTemplate.Category.SPECIAL,
        )
        minor = Employee.objects.create(
            tenant=self.tenant, first_name="Timo", last_name="T", employment_pct=100,
            birth_date=date(2010, 1, 1),  # minderjährig am 2026-08-03
        )
        special = ShiftAssignment(
            tenant=self.tenant, employee=minor, node=self.node,
            date=date(2026, 8, 3), template=night_special,
        )
        with self.assertRaises(ValidationError):
            special.clean()


class ShiftTradeRequestSplitShiftTests(TestCase):
    """README Punkt 18: ShiftTradeRequest.approve() mit Split-Shift-Tagen."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.node = Node.add_root(name="ICT", tenant=self.tenant)
        self.early = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Frühdienst",
            start_time=time(7, 0), end_time=time(12, 0), break_minutes=0,
        )
        self.late = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Spätdienst",
            start_time=time(13, 0), end_time=time(17, 30), break_minutes=0,
        )
        self.requester = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        self.target = Employee.objects.create(
            tenant=self.tenant, first_name="Bea", last_name="B", employment_pct=100
        )

    def test_approve_full_swap_same_day_with_existing_second_shift(self):
        # requester hat an diesem Tag bereits einen Spaetdienst zusaetzlich
        # zum zu tauschenden Fruehdienst -- der Tausch des Fruehdienstes
        # darf nicht faelschlich mit dem eigenen Spaetdienst kollidieren.
        requester_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.requester, node=self.node, date=date(2026, 8, 3),
            template=self.early,
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.requester, node=self.node, date=date(2026, 8, 3),
            template=self.late,
        )
        target_assignment = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.target, node=self.node, date=date(2026, 8, 3),
            template=self.early,
        )
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=requester_assignment,
            target_employee=self.target,
            target_assignment=target_assignment,
        )
        trade.approve()
        requester_assignment.refresh_from_db()
        target_assignment.refresh_from_db()
        self.assertEqual(requester_assignment.employee_id, self.target.id)
        self.assertEqual(target_assignment.employee_id, self.requester.id)
        # requesters Spaetdienst bleibt unveraendert bei ihr bestehen.
        self.assertTrue(
            ShiftAssignment.objects.filter(
                employee=self.requester, date=date(2026, 8, 3), template=self.late
            ).exists()
        )


class UnderstaffedShiftsViewTests(APITestCase):
    """
    README MVP-Fahrplan Block 2, Punkt 21 (Dashboard): serverseitige,
    stationsübergreifende Auswertung der Mindestbesetzung (Block 9/2.9) für
    die nächsten UPCOMING_DAYS Tage -- Grundlage für die Dashboard-Karte
    "Unterbesetzte Schichten".
    """

    def setUp(self):
        self.tenant, self.user = make_tenant_with_planner("klinik-a", "planner_a")
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        self.employee = Employee.objects.create(
            tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100
        )
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.today = timezone.localdate()

    def test_requires_authentication(self):
        # 403 statt 401: SessionAuthentication steht in
        # DEFAULT_AUTHENTICATION_CLASSES an erster Stelle und bietet keinen
        # WWW-Authenticate-Header an (siehe AuthenticationTests oben).
        self.client.credentials()
        response = self.client.get("/api/understaffed-shifts/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_templates_configured_reports_has_configured_templates_false(self):
        # README (2026-08, UX-Bugfix): "nichts konfiguriert" muss sich vom
        # Dashboard klar von "alles besetzt" unterscheiden lassen -- beide
        # sahen vorher identisch aus (leere Liste).
        response = self.client.get("/api/understaffed-shifts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["has_configured_templates"])
        self.assertEqual(response.data["shortfalls"], [])

    def test_template_without_minimum_staffing_never_listed(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Tagdienst", start_time=time(8, 0), end_time=time(16, 0)
        )
        self.assertEqual(template.minimum_staffing, 0)
        response = self.client.get("/api/understaffed-shifts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["has_configured_templates"])
        self.assertEqual(response.data["shortfalls"], [])

    def test_configured_templates_report_has_configured_templates_true(self):
        # has_configured_templates ist True, sobald mindestens ein Schichttyp
        # eine Mindestbesetzung hat -- unabhängig davon, ob es aktuell auch
        # tatsaechlich einen Engpass gibt (siehe die anderen Tests oben/unten
        # fuer den Engpass-Fall selbst).
        TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            minimum_staffing=1,
        )
        response = self.client.get("/api/understaffed-shifts/")
        self.assertTrue(response.data["has_configured_templates"])

    def test_reports_shortfall_within_upcoming_window(self):
        # README: die Auswertung läuft bewusst über ALLE Tage des Fensters,
        # nicht nur die mit bestehenden Zuweisungen (identische Semantik zum
        # bereits bestehenden PlanGrid.jsx-Badge, Block 2.9 -- "informativ,
        # nicht blockierend", kein Sonderfall für einen noch komplett leeren
        # Tag). Ein Tag mit einer von zwei nötigen Zuweisungen zeigt daher
        # als EINER von mehreren Einträgen count=1 auf.
        template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            minimum_staffing=2,
        )
        target_date = self.today + timedelta(days=2)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=target_date, template=template
        )
        response = self.client.get("/api/understaffed-shifts/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["has_configured_templates"])
        by_date = {e["date"]: e for e in response.data["shortfalls"]}
        entry = by_date[target_date.isoformat()]
        self.assertEqual(entry["node_id"], self.node.id)
        self.assertEqual(entry["node_name"], "Station A")
        self.assertEqual(entry["template_id"], template.id)
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["minimum_staffing"], 2)

    def test_sufficiently_staffed_date_absent_from_results(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            minimum_staffing=1,
        )
        target_date = self.today + timedelta(days=1)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=target_date, template=template
        )
        response = self.client.get("/api/understaffed-shifts/")
        dates = [e["date"] for e in response.data["shortfalls"]]
        self.assertNotIn(target_date.isoformat(), dates)

    def test_shortfall_outside_upcoming_window_not_listed(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            minimum_staffing=2,
        )
        far_future = self.today + timedelta(days=30)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=far_future, template=template
        )
        response = self.client.get("/api/understaffed-shifts/")
        dates = [e["date"] for e in response.data["shortfalls"]]
        self.assertNotIn(far_future.isoformat(), dates)
        self.assertTrue(all(d <= (self.today + timedelta(days=6)).isoformat() for d in dates))

    def test_past_dates_not_listed(self):
        template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            minimum_staffing=2,
        )
        yesterday = self.today - timedelta(days=1)
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=self.employee, node=self.node, date=yesterday, template=template
        )
        response = self.client.get("/api/understaffed-shifts/")
        dates = [e["date"] for e in response.data["shortfalls"]]
        self.assertNotIn(yesterday.isoformat(), dates)
        self.assertTrue(all(d >= self.today.isoformat() for d in dates))


class UnderstaffedShiftsTenantIsolationTests(TwoTenantFixtureMixin, APITestCase):
    def test_only_own_tenants_understaffed_shifts_are_returned(self):
        self.template_a.minimum_staffing = 2
        self.template_a.save(update_fields=["minimum_staffing"])
        self.template_b.minimum_staffing = 2
        self.template_b.save(update_fields=["minimum_staffing"])
        target_date = timezone.localdate() + timedelta(days=1)
        ShiftAssignment.objects.create(
            tenant=self.tenant_a, employee=self.employee_a, node=self.node_a, date=target_date, template=self.template_a
        )
        ShiftAssignment.objects.create(
            tenant=self.tenant_b, employee=self.employee_b, node=self.node_b, date=target_date, template=self.template_b
        )

        self.auth_as(self.user_a)
        response = self.client.get("/api/understaffed-shifts/")
        self.assertGreater(len(response.data["shortfalls"]), 0)
        self.assertTrue(all(e["node_name"] == "Station A" for e in response.data["shortfalls"]))

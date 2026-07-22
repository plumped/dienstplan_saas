from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core.models import Membership, Tenant

from .models import (
    Absence,
    Employee,
    Node,
    ShiftAssignment,
    ShiftTradeRequest,
    Skill,
    TimeTemplate,
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
        )
        Absence.objects.create(
            tenant=self.tenant_b,
            employee=self.employee_b,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
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

    def test_absence_conflict_is_rejected(self):
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 10),
            type=Absence.Type.VACATION,
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
        Absence.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 10),
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

    def test_accept_simple_handoff_reassigns_employee(self):
        trade = ShiftTradeRequest.objects.create(
            tenant=self.tenant,
            requester_assignment=self.assignment_1,
            target_employee=self.employee_2,
        )
        trade.accept()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.ACCEPTED)
        self.assertIsNotNone(trade.resolved_at)
        self.assertEqual(self.assignment_1.employee_id, self.employee_2.id)

    def test_accept_full_swap_exchanges_employees(self):
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

        self.assignment_1.refresh_from_db()
        assignment_2.refresh_from_db()
        self.assertEqual(self.assignment_1.employee_id, self.employee_2.id)
        self.assertEqual(assignment_2.employee_id, self.employee_1.id)

    def test_accept_blocked_by_rule_engine_leaves_state_unchanged(self):
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
        with self.assertRaises(ValidationError):
            trade.accept()

        trade.refresh_from_db()
        self.assignment_1.refresh_from_db()
        self.assertEqual(trade.status, ShiftTradeRequest.Status.PENDING)
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

    def test_accept_endpoint_reassigns_and_returns_updated_status(self):
        create_response = self.client.post(
            "/api/shift-trade-requests/",
            {"requester_assignment": self.assignment.id, "target_employee": self.employee_2.id},
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        trade_id = create_response.data["id"]

        accept_response = self.client.post(f"/api/shift-trade-requests/{trade_id}/accept/")
        self.assertEqual(accept_response.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_response.data["status"], "accepted")

        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.employee_id, self.employee_2.id)

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

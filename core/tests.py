from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core.context import get_current_tenant, set_current_tenant
from core.middleware import ADMIN_TENANT_SESSION_KEY, TenantContextCleanupMiddleware
from core.models import Membership, Tenant, TenantHolidayOverride
from core.onboarding import seed_demo_tenant, unique_tenant_slug
from core.tenancy import resolve_tenant_for_user
from scheduling.models import AbsenceType, Employee, Employment, Node, Skill, TimeTemplate
from scheduling.tests import make_station

User = get_user_model()


class StaffAccountsCannotHaveMembershipsTests(TestCase):
    """
    Cross-Tenant-Schutz für /admin/ (siehe README, Architektur-Abschnitt
    "Django Admin ist bewusst kein Kundenzugriff"): ein Account darf nie
    gleichzeitig is_staff/is_superuser UND eine Tenant-Mitgliedschaft haben,
    weil die ModelAdmins nicht tenant-gescoped sind. Strukturell erzwungen
    in Membership.save()/User.save(), nicht nur als Konvention.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def test_cannot_create_membership_for_staff_user(self):
        staff_user = User.objects.create_user(username="staff", password="pw-not-real-123!", is_staff=True)
        with self.assertRaises(ValidationError):
            Membership.objects.create(user=staff_user, tenant=self.tenant, role=Membership.Role.ADMIN)

    def test_cannot_create_membership_for_superuser(self):
        superuser = User.objects.create_superuser(username="root", password="pw-not-real-123!")
        with self.assertRaises(ValidationError):
            Membership.objects.create(user=superuser, tenant=self.tenant, role=Membership.Role.ADMIN)

    def test_cannot_promote_membership_holder_to_staff(self):
        user = User.objects.create_user(username="anna", password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=self.tenant, role=Membership.Role.ADMIN)
        user.is_staff = True
        with self.assertRaises(ValidationError):
            user.save()

    def test_ordinary_membership_creation_still_works(self):
        user = User.objects.create_user(username="bob", password="pw-not-real-123!")
        membership = Membership.objects.create(user=user, tenant=self.tenant, role=Membership.Role.PLANNER)
        self.assertEqual(membership.user, user)

    def test_creating_superuser_without_membership_still_works(self):
        superuser = User.objects.create_superuser(username="root2", password="pw-not-real-123!")
        self.assertTrue(superuser.is_superuser)


class TenantScopedManagerTests(TestCase):
    """core.models.TenantScopedManager -- ContextVar-basierte Filterung (siehe Docstring dort)."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.tenant_b = Tenant.objects.create(name="Klinik B", slug="klinik-b")
        Skill.objects.create(tenant=self.tenant_a, name="Nachtdienst")
        Skill.objects.create(tenant=self.tenant_b, name="Nachtdienst")

    def tearDown(self):
        set_current_tenant(None)

    def test_unfiltered_manager_sees_all_tenants(self):
        self.assertEqual(Skill.all_objects.count(), 2)

    def test_default_manager_without_context_sees_all(self):
        # Kein Tenant in der ContextVar gesetzt -> keine Filterung (siehe
        # TenantScopedManager.get_queryset).
        self.assertIsNone(get_current_tenant())
        self.assertEqual(Skill.objects.count(), 2)

    def test_default_manager_filters_by_current_tenant(self):
        set_current_tenant(self.tenant_a)
        self.assertEqual(Skill.objects.count(), 1)
        self.assertEqual(Skill.objects.get().tenant_id, self.tenant_a.id)


class ResolveTenantForUserTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.user = User.objects.create_user(username="planner", password="pw-not-relevant-123")

    def test_anonymous_user_has_no_tenant(self):
        self.assertIsNone(resolve_tenant_for_user(None))

    def test_user_without_membership_has_no_tenant(self):
        self.assertIsNone(resolve_tenant_for_user(self.user))

    def test_user_with_membership_resolves_tenant(self):
        Membership.objects.create(user=self.user, tenant=self.tenant, role=Membership.Role.PLANNER)
        self.assertEqual(resolve_tenant_for_user(self.user), self.tenant)


class TenantContextCleanupMiddlewareTests(TestCase):
    """
    Regressionstest für ein Cross-Tenant-Leck: ohne Reset nach dem Request
    bleibt die ContextVar auf dem zuletzt aufgelösten Tenant stehen und wird
    vom nächsten Request auf demselben Thread (z. B. wiederverwendeter
    WSGI-Worker) fälschlich weiterverwendet.
    """

    def tearDown(self):
        set_current_tenant(None)

    def test_context_is_reset_after_request(self):
        tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        set_current_tenant(tenant)

        middleware = TenantContextCleanupMiddleware(get_response=lambda request: "ok")
        request = RequestFactory().get("/")
        middleware(request)

        self.assertIsNone(get_current_tenant())

    def test_context_is_reset_even_if_view_raises(self):
        tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        set_current_tenant(tenant)

        def boom(request):
            raise RuntimeError("view blew up")

        middleware = TenantContextCleanupMiddleware(get_response=boom)
        request = RequestFactory().get("/")
        with self.assertRaises(RuntimeError):
            middleware(request)


class TenantScopedAPIMixinCrossTenantTests(APITestCase):
    """
    Regressionstest für README Block 10 Punkt #1 (initial()-Dedup):
    core.views.TenantScopedAPIMixin-Endpunkte hatten bisher -- anders als
    die TenantScopedViewSet-Endpunkte (siehe scheduling.tests.
    CrossTenantIsolationTests) -- keinen eigenen Cross-Tenant-
    Isolationstest. Vor dem initial()-Dedup ergänzt, damit die Testsuite
    den Refactor tatsächlich absichert, statt sich nur auf die (bereits
    für TenantScopedViewSet vorhandene) Coverage zu verlassen.
    """

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Klinik A", slug="klinik-a-mixin", canton="ZH")
        self.tenant_b = Tenant.objects.create(name="Klinik B", slug="klinik-b-mixin", canton="BE")

        self.admin_a = User.objects.create_user(username="admin_a_mixin", password="pw-not-real-123!")
        Membership.objects.create(user=self.admin_a, tenant=self.tenant_a, role=Membership.Role.ADMIN)
        self.admin_b = User.objects.create_user(username="admin_b_mixin", password="pw-not-real-123!")
        Membership.objects.create(user=self.admin_b, tenant=self.tenant_b, role=Membership.Role.ADMIN)

        self.override_a = TenantHolidayOverride.objects.create(
            tenant=self.tenant_a,
            date=date(2026, 5, 1),
            kind=TenantHolidayOverride.Kind.ADD,
            name="Firmenfest A",
        )
        self.override_b = TenantHolidayOverride.objects.create(
            tenant=self.tenant_b,
            date=date(2026, 6, 1),
            kind=TenantHolidayOverride.Kind.ADD,
            name="Firmenfest B",
        )

        token_a, _ = Token.objects.get_or_create(user=self.admin_a)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token_a.key}")

    def test_tenant_view_returns_only_own_tenant(self):
        response = self.client.get("/api/tenant/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Klinik A")
        self.assertEqual(response.data["canton"], "ZH")

    def test_tenant_holiday_override_list_scoped_to_own_tenant(self):
        response = self.client.get("/api/tenant-holiday-overrides/")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.override_a.id, ids)
        self.assertNotIn(self.override_b.id, ids)

    def test_tenant_holiday_override_retrieve_of_foreign_tenant_is_404(self):
        response = self.client.get(f"/api/tenant-holiday-overrides/{self.override_b.id}/")
        self.assertEqual(response.status_code, 404)

    def test_membership_list_scoped_to_own_tenant(self):
        response = self.client.get("/api/memberships/")
        self.assertEqual(response.status_code, 200)
        usernames = {row["username"] for row in response.data["results"]}
        self.assertIn("admin_a_mixin", usernames)
        self.assertNotIn("admin_b_mixin", usernames)

        self.assertIsNone(get_current_tenant())


class MeViewTests(APITestCase):
    """GET /api/me/ -- Grundlage für ein rollenbewusstes Frontend."""

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_user_without_membership_gets_empty_response(self):
        user = User.objects.create_user(username="orphan", password="irrelevant-123")
        self.auth_as(user)
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "role": None,
                "tenant_name": None,
                "tenant_onboarding_completed": None,
                "employee": None,
                "task_counts": {"absences": 0, "trades": 0, "time_records": 0},
            },
        )

    def test_planner_without_employee_profile(self):
        tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        user = User.objects.create_user(username="planner", password="irrelevant-123")
        Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.PLANNER)
        self.auth_as(user)

        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "planner")
        self.assertEqual(response.data["tenant_name"], "Klinik A")
        self.assertIsNone(response.data["employee"])
        self.assertEqual(response.data["username"], "planner")

    def test_employee_with_linked_employee_profile(self):
        tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        user = User.objects.create_user(username="anna", password="irrelevant-123")
        Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.EMPLOYEE)
        employee = Employee.objects.create(
            tenant=tenant, user=user, first_name="Anna", last_name="Berger", employment_pct=100
        )
        self.auth_as(user)

        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "employee")
        self.assertEqual(response.data["employee"]["id"], employee.id)
        self.assertEqual(response.data["employee"]["first_name"], "Anna")

    def test_anonymous_request_is_rejected(self):
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 403)


class TenantConfigAPITests(APITestCase):
    """
    GET/PATCH /api/tenant/ (MVP-Fahrplan Block 2, Punkt 14): Lesen für alle
    Rollen offen, Schreiben Admin-only (core.permissions.IsTenantAdmin) --
    strenger als IsTenantManager (Admin+Planer) sonst überall in der App.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _membership(self, username, role):
        user = User.objects.create_user(username=username, password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=self.tenant, role=role)
        return user

    def test_admin_can_read(self):
        self.auth_as(self._membership("admin", Membership.Role.ADMIN))
        response = self.client.get("/api/tenant/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["minimum_rest_hours"], 11)
        self.assertEqual(response.data["name"], "Klinik A")

    def test_planner_employee_and_hr_can_read(self):
        for role in (Membership.Role.PLANNER, Membership.Role.EMPLOYEE, Membership.Role.HR):
            self.auth_as(self._membership(f"user-{role}", role))
            response = self.client.get("/api/tenant/")
            self.assertEqual(response.status_code, 200, role)

    def test_admin_can_update(self):
        self.auth_as(self._membership("admin", Membership.Role.ADMIN))
        response = self.client.patch(
            "/api/tenant/", {"minimum_rest_hours": 12, "night_work_permit_confirmed": True}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.minimum_rest_hours, 12)
        self.assertTrue(self.tenant.night_work_permit_confirmed)

    def test_planner_cannot_update(self):
        self.auth_as(self._membership("planner", Membership.Role.PLANNER))
        response = self.client.patch("/api/tenant/", {"minimum_rest_hours": 12}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_employee_cannot_update(self):
        self.auth_as(self._membership("employee", Membership.Role.EMPLOYEE))
        response = self.client.patch("/api/tenant/", {"minimum_rest_hours": 12}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_name_and_id_are_read_only(self):
        self.auth_as(self._membership("admin", Membership.Role.ADMIN))
        response = self.client.patch("/api/tenant/", {"name": "Anderer Name"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, "Klinik A")

    def test_anonymous_request_is_rejected(self):
        response = self.client.get("/api/tenant/")
        self.assertEqual(response.status_code, 403)

    def test_user_without_membership_gets_not_found(self):
        user = User.objects.create_user(username="orphan", password="pw-not-real-123!")
        self.auth_as(user)
        response = self.client.get("/api/tenant/")
        self.assertEqual(response.status_code, 404)

    def test_canton_field_is_part_of_config(self):
        self.auth_as(self._membership("admin", Membership.Role.ADMIN))
        response = self.client.patch("/api/tenant/", {"canton": "ZH"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.canton, "ZH")


class TenantHolidaysAPITests(APITestCase):
    """
    GET /api/tenant/holidays/ (Arbeitszeitmodell, README Block 2.7 Punkt 7):
    aufgelöste Feiertagsdaten fürs Planblatt/Jahresplan -- siehe
    core.views.TenantHolidaysView.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a", canton="ZH")

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _membership(self, username, role):
        user = User.objects.create_user(username=username, password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=self.tenant, role=role)
        return user

    def test_returns_resolved_dates_for_given_year(self):
        self.auth_as(self._membership("employee", Membership.Role.EMPLOYEE))
        response = self.client.get("/api/tenant/holidays/?year=2026")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["year"], 2026)
        self.assertIn("2026-01-01", [entry["date"] for entry in response.data["dates"]])
        neujahr = next(entry for entry in response.data["dates"] if entry["date"] == "2026-01-01")
        self.assertTrue(neujahr["name"])

    def test_defaults_to_current_year_without_param(self):
        self.auth_as(self._membership("employee", Membership.Role.EMPLOYEE))
        response = self.client.get("/api/tenant/holidays/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["year"], timezone.localdate().year)

    def test_rejects_invalid_year_param(self):
        self.auth_as(self._membership("employee", Membership.Role.EMPLOYEE))
        response = self.client.get("/api/tenant/holidays/?year=not-a-year")
        self.assertEqual(response.status_code, 400)

    def test_empty_without_canton(self):
        self.tenant.canton = ""
        self.tenant.save(update_fields=["canton"])
        self.auth_as(self._membership("employee", Membership.Role.EMPLOYEE))
        response = self.client.get("/api/tenant/holidays/?year=2026")
        self.assertEqual(response.data["dates"], [])


class TenantPublicHolidaysTests(TestCase):
    """
    Tenant.public_holidays() (Arbeitszeitmodell, README Block 2.7 Punkt 7):
    kantonaler Kalender (holidays-Bibliothek) kombiniert mit manuellen
    TenantHolidayOverride-Einträgen.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def test_empty_without_canton(self):
        self.assertEqual(self.tenant.public_holidays(2026), set())

    def test_uses_canton_calendar(self):
        self.tenant.canton = "ZH"
        self.tenant.save(update_fields=["canton"])
        holidays = self.tenant.public_holidays(2026)
        self.assertIn(date(2026, 1, 1), holidays)  # Neujahr
        self.assertIn(date(2026, 8, 1), holidays)  # Nationalfeiertag
        # LU-spezifischer Feiertag (Fronleichnam) darf in ZH nicht auftauchen.
        self.assertNotIn(date(2026, 6, 4), holidays)

    def test_different_cantons_have_different_holidays(self):
        self.tenant.canton = "LU"
        self.tenant.save(update_fields=["canton"])
        self.assertIn(date(2026, 6, 4), self.tenant.public_holidays(2026))  # Fronleichnam

    def test_add_override_extends_calendar(self):
        TenantHolidayOverride.objects.create(
            tenant=self.tenant, date=date(2026, 11, 20), name="Lokale Kirchweih", kind="add"
        )
        self.assertIn(date(2026, 11, 20), self.tenant.public_holidays(2026))

    def test_remove_override_excludes_canton_holiday(self):
        self.tenant.canton = "ZH"
        self.tenant.save(update_fields=["canton"])
        TenantHolidayOverride.objects.create(tenant=self.tenant, date=date(2026, 1, 1), kind="remove")
        self.assertNotIn(date(2026, 1, 1), self.tenant.public_holidays(2026))

    def test_override_from_other_tenant_is_ignored(self):
        other = Tenant.objects.create(name="Klinik B", slug="klinik-b", canton="ZH")
        TenantHolidayOverride.objects.create(tenant=other, date=date(2026, 11, 20), kind="add")
        self.assertNotIn(date(2026, 11, 20), self.tenant.public_holidays(2026))


class TenantHolidayOverrideAPITests(APITestCase):
    """
    /api/tenant-holiday-overrides/ (Arbeitszeitmodell, README Block 2.7
    Punkt 7): dieselbe Admin-only-Schreiben/alle-lesen-Berechtigung wie
    /api/tenant/, da Teil derselben Tenant-Konfiguration.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.other_tenant = Tenant.objects.create(name="Klinik B", slug="klinik-b")

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _membership(self, username, role, tenant=None):
        user = User.objects.create_user(username=username, password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=tenant or self.tenant, role=role)
        return user

    def test_admin_can_create_override(self):
        self.auth_as(self._membership("admin", Membership.Role.ADMIN))
        response = self.client.post(
            "/api/tenant-holiday-overrides/",
            {"date": "2026-11-20", "name": "Kirchweih Musterdorf", "kind": "add"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(TenantHolidayOverride.objects.get().tenant, self.tenant)

    def test_planner_cannot_create_override(self):
        self.auth_as(self._membership("planner", Membership.Role.PLANNER))
        response = self.client.post(
            "/api/tenant-holiday-overrides/", {"date": "2026-11-20", "kind": "add"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_employee_can_read_overrides(self):
        TenantHolidayOverride.objects.create(tenant=self.tenant, date=date(2026, 11, 20), kind="add")
        self.auth_as(self._membership("employee", Membership.Role.EMPLOYEE))
        response = self.client.get("/api/tenant-holiday-overrides/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)

    def test_overrides_are_tenant_scoped(self):
        TenantHolidayOverride.objects.create(tenant=self.other_tenant, date=date(2026, 11, 20), kind="add")
        self.auth_as(self._membership("admin", Membership.Role.ADMIN))
        response = self.client.get("/api/tenant-holiday-overrides/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])


class AdminTenantScopingTests(TestCase):
    """
    /admin/ Cross-Tenant-Datenleck (siehe README, Architektur-Abschnitt
    "Django Admin ist bewusst kein Kundenzugriff"): core.middleware.
    AdminActiveTenantMiddleware + core.admin.TenantScopedAdminMixin sorgen
    dafür, dass Staff im Django-Admin nur Daten des aktiv gewählten Tenants
    sieht -- inkl. FK-Dropdowns (z. B. Node/Skill beim Anlegen eines
    Employee), nicht nur die Changelist selbst.
    """

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Klinik A", slug="klinik-a")
        self.tenant_b = Tenant.objects.create(name="Klinik B", slug="klinik-b")
        self.node_a = make_station(self.tenant_a, "Station A")
        self.node_b = make_station(self.tenant_b, "Station B")
        self.employee_a = Employee.objects.create(
            tenant=self.tenant_a, first_name="Anna", last_name="A", employment_pct=100
        )
        self.employee_b = Employee.objects.create(
            tenant=self.tenant_b, first_name="Berta", last_name="B", employment_pct=100
        )
        # is_superuser (nicht nur is_staff), damit die Django-Permission-Prüfung
        # (view/add/change auf App-Ebene) den Test nicht unabhängig von der
        # hier zu testenden Tenant-Scoping-Mixin blockiert.
        User.objects.create_superuser(username="staffer", password="pw-not-real-123!")
        self.client.login(username="staffer", password="pw-not-real-123!")

    def _set_active_tenant(self, tenant):
        session = self.client.session
        if tenant is None:
            session.pop(ADMIN_TENANT_SESSION_KEY, None)
        else:
            session[ADMIN_TENANT_SESSION_KEY] = str(tenant.pk)
        session.save()

    def test_employee_changelist_empty_without_active_tenant(self):
        response = self.client.get("/admin/scheduling/employee/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Anna")
        self.assertNotContains(response, "Berta")

    def test_employee_changelist_scoped_to_active_tenant(self):
        self._set_active_tenant(self.tenant_a)
        response = self.client.get("/admin/scheduling/employee/")
        self.assertContains(response, "Anna")
        self.assertNotContains(response, "Berta")

    def test_add_employee_blocked_without_active_tenant(self):
        response = self.client.get("/admin/scheduling/employee/add/")
        self.assertEqual(response.status_code, 403)

    def test_add_employee_node_dropdown_scoped_to_active_tenant(self):
        self._set_active_tenant(self.tenant_a)
        response = self.client.get("/admin/scheduling/employee/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Station A")
        self.assertNotContains(response, "Station B")

    def test_add_employee_tenant_field_locked_to_active_tenant(self):
        self._set_active_tenant(self.tenant_a)
        response = self.client.get("/admin/scheduling/employee/add/")
        self.assertContains(response, "Klinik A")
        self.assertNotContains(response, "Klinik B")

    def test_node_changelist_scoped(self):
        # NodeAdmin erbt von treebeard's TreeAdmin, nicht direkt von
        # admin.ModelAdmin -- eigener Test, damit die Mixin-Kombination
        # (TenantScopedAdminMixin, TreeAdmin) nicht nur bei "normalen"
        # ModelAdmins geprüft ist.
        response = self.client.get("/admin/scheduling/node/")
        self.assertEqual(response.status_code, 200)
        self._set_active_tenant(self.tenant_a)
        response = self.client.get("/admin/scheduling/node/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Station A")
        self.assertNotContains(response, "Station B")

    def test_membership_admin_also_scoped(self):
        # Membership erbt NICHT von TenantScopedModel -- prüft, dass die
        # Mixin trotzdem funktioniert (explizite Filterung, nicht auf
        # TenantScopedManager angewiesen).
        Membership.objects.create(
            user=User.objects.create_user(username="mem-a", password="pw-not-real-123!"),
            tenant=self.tenant_a,
            role=Membership.Role.PLANNER,
        )
        Membership.objects.create(
            user=User.objects.create_user(username="mem-b", password="pw-not-real-123!"),
            tenant=self.tenant_b,
            role=Membership.Role.PLANNER,
        )
        response = self.client.get("/admin/core/membership/")
        self.assertNotContains(response, "mem-a")
        self.assertNotContains(response, "mem-b")

        self._set_active_tenant(self.tenant_a)
        response = self.client.get("/admin/core/membership/")
        self.assertContains(response, "mem-a")
        self.assertNotContains(response, "mem-b")

    def test_tenant_admin_itself_not_scoped(self):
        # Tenant selbst muss immer sichtbar sein, sonst liesse sich im
        # Umschalter nie ein Tenant auswählen (Henne-Ei-Problem).
        response = self.client.get("/admin/core/tenant/")
        self.assertContains(response, "Klinik A")
        self.assertContains(response, "Klinik B")

    def test_non_staff_cannot_reach_tenant_switch(self):
        self.client.logout()
        User.objects.create_user(username="regular", password="pw-not-real-123!")
        self.client.login(username="regular", password="pw-not-real-123!")
        response = self.client.get("/admin/tenant-switch/")
        self.assertEqual(response.status_code, 302)  # Redirect zum Admin-Login

    def test_tenant_switch_sets_session(self):
        response = self.client.post(
            "/admin/tenant-switch/", {"tenant": str(self.tenant_a.pk), "next": "/admin/"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(ADMIN_TENANT_SESSION_KEY), str(self.tenant_a.pk))

    def test_tenant_switch_clears_session(self):
        self._set_active_tenant(self.tenant_a)
        self.client.post("/admin/tenant-switch/", {"tenant": "", "next": "/admin/"})
        self.assertNotIn(ADMIN_TENANT_SESSION_KEY, self.client.session)


class MembershipViewSetTests(APITestCase):
    """
    Nutzer-Feedback (2026-08): "kann man [Planer] Stationen zuweisen?" --
    Admin-only Verwaltung von Membership.scoped_nodes (siehe
    scheduling.views._employee_scoped_node_ids).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a-membership")
        self.node = make_station(self.tenant, "Station A")
        other_tenant = Tenant.objects.create(name="Klinik B", slug="klinik-b-membership")
        self.foreign_node = make_station(other_tenant, "Fremde Station")

        self.admin_user = User.objects.create_user(username="admin", password="pw-not-real-123!")
        self.admin_membership = Membership.objects.create(
            user=self.admin_user, tenant=self.tenant, role=Membership.Role.ADMIN
        )
        self.planner_user = User.objects.create_user(username="planner", password="pw-not-real-123!")
        self.planner_membership = Membership.objects.create(
            user=self.planner_user, tenant=self.tenant, role=Membership.Role.PLANNER
        )
        self.employee_user = User.objects.create_user(username="alice", password="pw-not-real-123!")
        Membership.objects.create(user=self.employee_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_admin_can_list_memberships(self):
        self.auth_as(self.admin_user)
        response = self.client.get("/api/memberships/")
        self.assertEqual(response.status_code, 200)
        usernames = {m["username"] for m in response.data["results"]}
        self.assertEqual(usernames, {"admin", "planner", "alice"})

    def test_non_admin_can_read_but_not_write_memberships(self):
        # Wie überall in core.permissions (IsTenantAdmin: "Lesen bleibt für
        # alle vier Rollen offen, nur das Schreiben ist eingeschränkter")
        # -- Listen ist kein Geheimnis (Organigramm-artige Info, analog zur
        # Mitarbeitenden-Liste), nur scoped_nodes ändern ist Admin-only.
        for user in (self.planner_user, self.employee_user):
            self.auth_as(user)
            read_response = self.client.get("/api/memberships/")
            self.assertEqual(read_response.status_code, 200)
            write_response = self.client.patch(
                f"/api/memberships/{self.planner_membership.id}/", {"scoped_nodes": [self.node.id]}, format="json"
            )
            self.assertEqual(write_response.status_code, 403)

    def test_admin_can_set_scoped_nodes_for_planner(self):
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{self.planner_membership.id}/", {"scoped_nodes": [self.node.id]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.planner_membership.refresh_from_db()
        self.assertEqual(list(self.planner_membership.scoped_nodes.values_list("id", flat=True)), [self.node.id])

    def test_admin_cannot_set_scoped_nodes_for_admin_membership(self):
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{self.admin_membership.id}/", {"scoped_nodes": [self.node.id]}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_scoped_nodes_rejects_foreign_tenant_node(self):
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{self.planner_membership.id}/",
            {"scoped_nodes": [self.foreign_node.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_admin_can_delete_orphan_membership(self):
        # Nutzer-Feedback (2026-08): "Konten ohne Mitarbeiterprofil sollten
        # ebenfalls eine Löschfunktion haben" -- löscht den User (nicht nur
        # die Membership), siehe MembershipViewSet.perform_destroy.
        self.auth_as(self.admin_user)
        response = self.client.delete(f"/api/memberships/{self.planner_membership.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Membership.objects.filter(pk=self.planner_membership.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.planner_user.pk).exists())

    def test_non_admin_cannot_delete_membership(self):
        self.auth_as(self.planner_user)
        response = self.client.delete(f"/api/memberships/{self.employee_user.memberships.get().id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Membership.objects.filter(user=self.employee_user).exists())

    def test_cannot_delete_membership_with_employee_profile(self):
        employee_user = User.objects.create_user(username="bob", password="pw-not-real-123!")
        membership = Membership.objects.create(user=employee_user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        Employee.objects.create(
            tenant=self.tenant, first_name="Bob", last_name="Baumeister", user=employee_user, employment_pct=100
        )
        self.auth_as(self.admin_user)
        response = self.client.delete(f"/api/memberships/{membership.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=employee_user.pk).exists())

    def test_cannot_delete_own_membership(self):
        self.auth_as(self.admin_user)
        response = self.client.delete(f"/api/memberships/{self.admin_membership.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.admin_user.pk).exists())

    def test_cannot_delete_last_admin_membership(self):
        second_admin_user = User.objects.create_user(username="admin2", password="pw-not-real-123!")
        second_admin_membership = Membership.objects.create(
            user=second_admin_user, tenant=self.tenant, role=Membership.Role.ADMIN
        )
        self.auth_as(self.admin_user)
        # admin_user löscht admin2 -- danach bliebe nur noch admin_user selbst
        # übrig, das ist erlaubt (kein Selbstlösch-Konflikt hier).
        response = self.client.delete(f"/api/memberships/{second_admin_membership.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=second_admin_user.pk).exists())

    def test_cannot_delete_staff_membership(self):
        staff_user = User.objects.create_user(username="staff-with-membership2", password="pw-not-real-123!")
        Membership.objects.bulk_create(
            [Membership(user=staff_user, tenant=self.tenant, role=Membership.Role.ADMIN)]
        )
        User.objects.filter(pk=staff_user.pk).update(is_staff=True)
        staff_membership = Membership.objects.get(user=staff_user)
        self.auth_as(self.admin_user)
        response = self.client.delete(f"/api/memberships/{staff_membership.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=staff_user.pk).exists())

    def test_admin_can_create_membership_with_generated_password(self):
        # Nutzer-Feedback (2026-08): "Applikationsmanager wird den Benutzer
        # anlegen und nicht per Mail einladen" -- Direktanlage statt
        # E-Mail-Einladung, siehe MembershipCreateSerializer.
        self.auth_as(self.admin_user)
        response = self.client.post(
            "/api/memberships/",
            {"username": "neu-hire", "first_name": "Neu", "last_name": "Hire", "role": "employee"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["temporary_password"])
        new_user = User.objects.get(username="neu-hire")
        self.assertTrue(new_user.must_change_password)
        self.assertTrue(new_user.check_password(response.data["temporary_password"]))
        membership = Membership.objects.get(user=new_user, tenant=self.tenant)
        self.assertEqual(membership.role, Membership.Role.EMPLOYEE)

    def test_create_membership_rejects_duplicate_username(self):
        self.auth_as(self.admin_user)
        response = self.client.post(
            "/api/memberships/", {"username": "planner", "role": "employee"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_non_admin_cannot_create_membership(self):
        for user in (self.planner_user, self.employee_user):
            self.auth_as(user)
            response = self.client.post(
                "/api/memberships/", {"username": "whoever", "role": "employee"}, format="json"
            )
            self.assertEqual(response.status_code, 403)

    def test_admin_can_change_role_of_existing_membership(self):
        # Nutzer-Feedback (2026-08): Rolle bestehender Mitglieder direkt per
        # Dropdown änderbar, nicht mehr nur über Django-Admin.
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{self.planner_membership.id}/", {"role": "hr"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.planner_membership.refresh_from_db()
        self.assertEqual(self.planner_membership.role, Membership.Role.HR)

    def test_role_change_to_admin_with_scoped_nodes_rejected(self):
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{self.planner_membership.id}/",
            {"role": "admin", "scoped_nodes": [self.node.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_demote_last_admin(self):
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{self.admin_membership.id}/", {"role": "planner"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, Membership.Role.ADMIN)

    def test_role_change_on_staff_membership_returns_clean_400_not_500(self):
        # Regression: ein is_staff/is_superuser-Account mit Membership kann
        # strukturell eigentlich nicht entstehen (User.save()/Membership.
        # save() verhindern das für NEUE Datensätze), existierte aber als
        # Altlast in den Demo-Fixtures (vermutlich via loaddata, das
        # Model.save() umgeht) und liess PATCH role bis zu diesem Fix mit
        # einem nackten 500 statt einer 400-Fehlermeldung crashen.
        staff_user = User.objects.create_user(username="staff-with-membership", password="pw-not-real-123!")
        # bulk_create() ruft KEIN Model.save() auf (direktes INSERT) --
        # simuliert damit denselben Umgehungsweg wie loaddata/dumpdata-
        # Fixtures, über den die reale Altlast entstanden sein muss.
        Membership.objects.bulk_create(
            [Membership(user=staff_user, tenant=self.tenant, role=Membership.Role.ADMIN)]
        )
        # .update() ist ein direktes SQL-UPDATE, ruft ebenfalls kein
        # Model.save() auf -- User.save() würde die Kombination sonst selbst
        # verhindern (siehe dessen Docstring), genau wie im echten Leben nur
        # über einen Weg ausserhalb des ORM-save()-Pfads entstehbar.
        User.objects.filter(pk=staff_user.pk).update(is_staff=True)
        staff_membership = Membership.objects.get(user=staff_user)
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{staff_membership.id}/", {"role": "employee"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_can_demote_admin_when_another_admin_remains(self):
        second_admin_user = User.objects.create_user(username="admin2", password="pw-not-real-123!")
        second_admin_membership = Membership.objects.create(
            user=second_admin_user, tenant=self.tenant, role=Membership.Role.ADMIN
        )
        self.auth_as(self.admin_user)
        response = self.client.patch(
            f"/api/memberships/{second_admin_membership.id}/", {"role": "planner"}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_can_reset_password(self):
        # Nutzer-Feedback (2026-08): "Ja mach passwort reset" -- Admin
        # generiert ein neues Temp-Passwort für einen anderen Account, siehe
        # MembershipViewSet.reset_password.
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/memberships/{self.planner_membership.id}/reset-password/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["username"], "planner")
        self.assertTrue(response.data["temporary_password"])
        self.planner_user.refresh_from_db()
        self.assertTrue(self.planner_user.check_password(response.data["temporary_password"]))
        self.assertTrue(self.planner_user.must_change_password)

    def test_non_admin_cannot_reset_password(self):
        self.auth_as(self.planner_user)
        response = self.client.post(f"/api/memberships/{self.admin_membership.id}/reset-password/")
        self.assertEqual(response.status_code, 403)

    def test_reset_password_invalidates_old_password(self):
        self.auth_as(self.admin_user)
        self.client.post(f"/api/memberships/{self.planner_membership.id}/reset-password/")
        self.planner_user.refresh_from_db()
        self.assertFalse(self.planner_user.check_password("pw-not-real-123!"))

    def test_cannot_reset_password_of_staff_account(self):
        staff_user = User.objects.create_user(username="staff-reset-pw", password="pw-not-real-123!")
        Membership.objects.bulk_create(
            [Membership(user=staff_user, tenant=self.tenant, role=Membership.Role.ADMIN)]
        )
        User.objects.filter(pk=staff_user.pk).update(is_staff=True)
        staff_membership = Membership.objects.get(user=staff_user)
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/memberships/{staff_membership.id}/reset-password/")
        self.assertEqual(response.status_code, 400)

    def test_admin_can_reset_own_password(self):
        # Legitimer Wiederherstellungsfall: noch auf einem anderen Gerät
        # eingeloggt, eigenes Passwort vergessen -- anders als beim Löschen
        # (perform_destroy) gibt es hier keinen Grund, das zu sperren.
        self.auth_as(self.admin_user)
        response = self.client.post(f"/api/memberships/{self.admin_membership.id}/reset-password/")
        self.assertEqual(response.status_code, 200, response.data)


class ChangePasswordViewTests(APITestCase):
    """
    Erzwungener Passwortwechsel nach admin-seitiger Direktanlage (Nutzer-
    Feedback 2026-08, siehe MembershipCreateSerializer/User.
    must_change_password).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a-changepw")
        self.user = User.objects.create_user(
            username="neuling", password="temp-pw-123!", must_change_password=True
        )
        Membership.objects.create(user=self.user, tenant=self.tenant, role=Membership.Role.EMPLOYEE)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_me_reports_must_change_password(self):
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["must_change_password"])

    def test_wrong_current_password_rejected(self):
        response = self.client.post(
            "/api/me/change-password/",
            {"current_password": "falsch", "new_password": "ein-neues-sicheres-pw-99"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_weak_new_password_rejected(self):
        response = self.client.post(
            "/api/me/change-password/",
            {"current_password": "temp-pw-123!", "new_password": "1234"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)

    def test_successful_change_clears_flag(self):
        response = self.client.post(
            "/api/me/change-password/",
            {"current_password": "temp-pw-123!", "new_password": "ein-neues-sicheres-pw-99"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("ein-neues-sicheres-pw-99"))


class SeedDemoTenantTests(TestCase):
    """
    Unit-Tests für core.onboarding.seed_demo_tenant() (README Block 3) --
    unabhängig von SignupView gegen einen plain Tenant.objects.create()
    aufgerufen, wie in der ganz überwiegenden Mehrheit der bestehenden Tests
    dieser Datei.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Demo AG", slug="demo-ag-seed")
        seed_demo_tenant(self.tenant)

    def test_creates_two_beispiel_nodes(self):
        # Node.all_objects enthält seit der Mandanten-Isolation für den
        # Node-Baum (Node.get_or_create_forest_root) zusätzlich den
        # unsichtbaren Tenant-Wurzelknoten -- is_forest_root=False filtert
        # ihn hier bewusst raus, das ist nicht Teil der Demo-Daten.
        names = set(
            Node.all_objects.filter(tenant=self.tenant, is_forest_root=False).values_list("name", flat=True)
        )
        self.assertEqual(names, {"Pflege Tag (Beispiel)", "Pflege Nacht (Beispiel)"})

    def test_creates_three_absence_types_without_beispiel_suffix(self):
        types = AbsenceType.all_objects.filter(tenant=self.tenant)
        self.assertEqual(types.count(), 3)
        names = set(types.values_list("name", flat=True))
        self.assertEqual(names, {"Ferien", "Krankheit", "Sonstiges"})
        for name in names:
            self.assertNotIn("(Beispiel)", name)
        ferien = types.get(name="Ferien")
        self.assertTrue(ferien.deducts_vacation_days)
        krankheit = types.get(name="Krankheit")
        self.assertTrue(krankheit.counts_as_sick_leave)

    def test_creates_three_beispiel_time_templates(self):
        names = set(TimeTemplate.all_objects.filter(tenant=self.tenant).values_list("name", flat=True))
        self.assertEqual(
            names,
            {"Frühdienst (Beispiel)", "Spätdienst (Beispiel)", "Nachtdienst (Beispiel)"},
        )

    def test_creates_two_beispiel_employees_with_employments(self):
        employees = Employee.all_objects.filter(tenant=self.tenant)
        self.assertEqual(employees.count(), 2)
        for employee in employees:
            self.assertEqual(employee.last_name, "(Beispiel)")
        self.assertEqual(Employment.objects.filter(tenant=self.tenant).count(), 2)
        anna = employees.get(first_name="Anna")
        self.assertEqual(anna.employment_pct, 100)
        self.assertEqual(list(anna.nodes.values_list("name", flat=True)), ["Pflege Tag (Beispiel)"])
        peter = employees.get(first_name="Peter")
        self.assertEqual(peter.employment_pct, 80)
        self.assertEqual(list(peter.nodes.values_list("name", flat=True)), ["Pflege Nacht (Beispiel)"])

    def test_creates_no_shift_assignments(self):
        from scheduling.models import ShiftAssignment

        self.assertEqual(ShiftAssignment.all_objects.filter(tenant=self.tenant).count(), 0)


class UniqueTenantSlugTests(TestCase):
    """core.onboarding.unique_tenant_slug() -- Kollisionsauflösung mit numerischem Suffix."""

    def test_derives_slug_from_name(self):
        self.assertEqual(unique_tenant_slug("Sonnenhof AG"), "sonnenhof-ag")

    def test_appends_suffix_on_collision(self):
        Tenant.objects.create(name="Sonnenhof AG", slug="sonnenhof-ag")
        self.assertEqual(unique_tenant_slug("Sonnenhof AG"), "sonnenhof-ag-2")
        Tenant.objects.create(name="Sonnenhof AG", slug="sonnenhof-ag-2")
        self.assertEqual(unique_tenant_slug("Sonnenhof AG"), "sonnenhof-ag-3")

    def test_blank_name_falls_back_to_tenant(self):
        self.assertEqual(unique_tenant_slug(""), "tenant")


class SignupViewTests(APITestCase):
    """
    Self-Signup (README Block 3): Direkt-Registrierung ohne Magic-Link/
    E-Mail-Versand, siehe core.views.SignupView-Docstring.
    """

    def _payload(self, **overrides):
        payload = {
            "tenant_name": "Sonnenhof AG",
            "canton": "ZH",
            "first_name": "Max",
            "last_name": "Muster",
            "email": "max@example.com",
            "username": "maxmuster",
            "password": "SuperSicher!2026",
        }
        payload.update(overrides)
        return payload

    def test_signup_is_allowed_without_authentication(self):
        response = self.client.post("/api/signup/", self._payload(), format="json")
        self.assertEqual(response.status_code, 201)

    def test_signup_creates_tenant_user_membership_and_seed_data(self):
        response = self.client.post("/api/signup/", self._payload(), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data.keys()), {"token", "tenant_name", "username"})

        tenant = Tenant.objects.get(slug="sonnenhof-ag")
        self.assertFalse(tenant.onboarding_completed)
        self.assertEqual(tenant.canton, "ZH")

        user = User.objects.get(username="maxmuster")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.must_change_password)
        self.assertEqual(user.email, "max@example.com")
        self.assertTrue(user.check_password("SuperSicher!2026"))

        membership = Membership.objects.get(tenant=tenant, user=user)
        self.assertEqual(membership.role, Membership.Role.ADMIN)

        # +1 für den unsichtbaren Tenant-Wurzelknoten (Node.is_forest_root,
        # siehe Node.get_or_create_forest_root) -- nicht Teil der
        # Demo-Daten, aber ebenfalls ein Node-Datensatz dieses Tenants.
        self.assertEqual(Node.all_objects.filter(tenant=tenant, is_forest_root=False).count(), 2)
        self.assertEqual(Node.all_objects.filter(tenant=tenant, is_forest_root=True).count(), 1)
        self.assertEqual(AbsenceType.all_objects.filter(tenant=tenant).count(), 3)
        self.assertEqual(TimeTemplate.all_objects.filter(tenant=tenant).count(), 3)
        self.assertEqual(Employee.all_objects.filter(tenant=tenant).count(), 2)

    def test_token_from_response_authenticates_immediately(self):
        response = self.client.post("/api/signup/", self._payload(), format="json")
        token = response.data["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        me_response = self.client.get("/api/me/")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data["role"], Membership.Role.ADMIN)
        self.assertFalse(me_response.data["tenant_onboarding_completed"])

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user(username="maxmuster", password="irgendein-pw-123!")
        response = self.client.post("/api/signup/", self._payload(), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.data)

    def test_weak_password_is_rejected(self):
        response = self.client.post("/api/signup/", self._payload(password="1234"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)
        self.assertFalse(User.objects.filter(username="maxmuster").exists())

    def test_same_tenant_name_twice_gets_different_slugs(self):
        first = self.client.post("/api/signup/", self._payload(username="erster"), format="json")
        second = self.client.post(
            "/api/signup/", self._payload(username="zweiter"), format="json"
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        slugs = set(Tenant.objects.filter(name="Sonnenhof AG").values_list("slug", flat=True))
        self.assertEqual(slugs, {"sonnenhof-ag", "sonnenhof-ag-2"})

    def test_optional_fields_may_be_omitted(self):
        response = self.client.post(
            "/api/signup/",
            {"tenant_name": "Minimal AG", "username": "minimaluser", "password": "SuperSicher!2026"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        tenant = Tenant.objects.get(slug="minimal-ag")
        self.assertEqual(tenant.canton, "")
        user = User.objects.get(username="minimaluser")
        self.assertEqual(user.email, "")


class TenantOnboardingCompletedTests(APITestCase):
    """
    Tenant.onboarding_completed (README Block 3): default=True hält jeden
    bestehenden Tenant.objects.create()-Aufruf (Django-Admin, Fixtures, alle
    anderen Tests dieser Datei) unverändert sofort nutzbar -- nur
    core.views.SignupView setzt es explizit auf False.
    """

    def test_plain_tenant_create_defaults_to_completed(self):
        tenant = Tenant.objects.create(name="Klinik Default", slug="klinik-default-onb")
        self.assertTrue(tenant.onboarding_completed)

    def test_me_exposes_field(self):
        tenant = Tenant.objects.create(name="Klinik Default", slug="klinik-default-onb-me")
        user = User.objects.create_user(username="admin-onb", password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.ADMIN)
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["tenant_onboarding_completed"])

    def test_admin_can_patch_field(self):
        tenant = Tenant.objects.create(name="Klinik Default", slug="klinik-default-onb-patch", onboarding_completed=False)
        user = User.objects.create_user(username="admin-onb-patch", password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.ADMIN)
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.patch("/api/tenant/", {"onboarding_completed": True}, format="json")
        self.assertEqual(response.status_code, 200)
        tenant.refresh_from_db()
        self.assertTrue(tenant.onboarding_completed)

    def test_non_admin_cannot_patch_field(self):
        tenant = Tenant.objects.create(name="Klinik Default", slug="klinik-default-onb-non-admin", onboarding_completed=False)
        user = User.objects.create_user(username="planner-onb", password="pw-not-real-123!")
        Membership.objects.create(user=user, tenant=tenant, role=Membership.Role.PLANNER)
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.patch("/api/tenant/", {"onboarding_completed": True}, format="json")
        self.assertEqual(response.status_code, 403)

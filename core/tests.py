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
from core.tenancy import resolve_tenant_for_user
from scheduling.models import Employee, Node, Skill

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
        self.node_a = Node.add_root(name="Station A", tenant=self.tenant_a)
        self.node_b = Node.add_root(name="Station B", tenant=self.tenant_b)
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
        self.node = Node.add_root(name="Station A", tenant=self.tenant)
        other_tenant = Tenant.objects.create(name="Klinik B", slug="klinik-b-membership")
        self.foreign_node = Node.add_root(name="Fremde Station", tenant=other_tenant)

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

    def test_cannot_create_or_delete_membership_via_endpoint(self):
        self.auth_as(self.admin_user)
        create_response = self.client.post(
            "/api/memberships/", {"role": "planner", "scoped_nodes": []}, format="json"
        )
        self.assertEqual(create_response.status_code, 405)
        delete_response = self.client.delete(f"/api/memberships/{self.planner_membership.id}/")
        self.assertEqual(delete_response.status_code, 405)

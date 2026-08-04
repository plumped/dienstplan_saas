from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core.context import get_current_tenant, set_current_tenant
from core.middleware import TenantContextCleanupMiddleware
from core.models import Membership, Tenant
from core.tenancy import resolve_tenant_for_user
from scheduling.models import Employee, Skill

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
        self.assertEqual(response.data, {"role": None, "tenant_name": None, "employee": None})

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

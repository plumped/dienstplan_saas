from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core.context import get_current_tenant, set_current_tenant
from core.middleware import TenantContextCleanupMiddleware
from core.models import Membership, Tenant
from core.tenancy import resolve_tenant_for_user
from scheduling.models import Skill

User = get_user_model()


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

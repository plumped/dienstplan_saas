"""
Abrechnung (README Block 6, 2026-08): Tests für core/billing.py,
core/billing_views.py, das Trial-Mitarbeiterlimit und das 402-Zugriffsgate.

Alle Stripe-Aufrufe sind gemockt (unittest.mock.patch("core.billing.stripe"))
-- diese Sandbox hat keinen Netzwerkzugriff auf api.stripe.com (siehe
core/billing.py-Docstring), ein echter Smoke-Test gegen Stripe Test-Mode
sollte vor dem produktiven Umstieg trotzdem einmal von einer Maschine mit
Internetzugriff aus gemacht werden.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from core import billing
from core.models import Membership, Tenant
from scheduling.tests import make_station

User = get_user_model()


class TenantAccessModelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def test_active_tenant_has_access(self):
        self.assertTrue(self.tenant.has_active_access())

    def test_trialing_without_end_date_has_access(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.TRIALING
        self.tenant.trial_ends_at = None
        self.assertTrue(self.tenant.has_active_access())

    def test_trialing_before_end_date_has_access(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.TRIALING
        self.tenant.trial_ends_at = timezone.now() + timedelta(days=1)
        self.assertTrue(self.tenant.has_active_access())

    def test_trialing_after_end_date_has_no_access(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.TRIALING
        self.tenant.trial_ends_at = timezone.now() - timedelta(days=1)
        self.assertFalse(self.tenant.has_active_access())

    def test_past_due_canceled_incomplete_have_no_access(self):
        for status in (
            Tenant.SubscriptionStatus.PAST_DUE,
            Tenant.SubscriptionStatus.CANCELED,
            Tenant.SubscriptionStatus.INCOMPLETE,
        ):
            self.tenant.subscription_status = status
            self.assertFalse(self.tenant.has_active_access(), status)

    def test_active_employee_count(self):
        node = make_station(self.tenant, "Pflege")
        from scheduling.models import Employee

        Employee.objects.create(tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100)
        inactive = Employee.objects.create(
            tenant=self.tenant, first_name="Ben", last_name="B", employment_pct=100
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        self.assertEqual(self.tenant.active_employee_count(), 1)
        node.delete()


@override_settings(STRIPE_SECRET_KEY="", STRIPE_PRICE_ID="")
class BillingNotConfiguredTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def test_is_configured_false_without_keys(self):
        self.assertFalse(billing.is_configured())

    def test_create_checkout_session_raises_without_config(self):
        with self.assertRaises(billing.BillingNotConfigured):
            billing.create_checkout_session(self.tenant, success_url="https://x/ok", cancel_url="https://x/no")

    def test_sync_subscription_quantity_is_noop_without_config(self):
        self.tenant.stripe_subscription_id = "sub_123"
        billing.sync_subscription_quantity(self.tenant)  # darf nicht werfen


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PRICE_ID="price_dummy")
class BillingStripeCallsTests(TestCase):
    """Mockt stripe.* komplett -- prüft nur, dass core.billing die Stripe-API korrekt aufruft."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    @patch("core.billing.stripe")
    def test_get_or_create_stripe_customer_creates_once(self, mock_stripe):
        mock_stripe.Customer.create.return_value = MagicMock(id="cus_123")
        customer_id = billing.get_or_create_stripe_customer(self.tenant, email="a@b.ch")
        self.assertEqual(customer_id, "cus_123")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.stripe_customer_id, "cus_123")
        mock_stripe.Customer.create.assert_called_once()

        # Zweiter Aufruf: bereits gesetzte stripe_customer_id wird wiederverwendet, kein neuer Call.
        mock_stripe.Customer.create.reset_mock()
        customer_id_2 = billing.get_or_create_stripe_customer(self.tenant)
        self.assertEqual(customer_id_2, "cus_123")
        mock_stripe.Customer.create.assert_not_called()

    @patch("core.billing.stripe")
    def test_create_checkout_session_uses_active_employee_count_as_quantity(self, mock_stripe):
        make_station(self.tenant, "Pflege")
        from scheduling.models import Employee

        Employee.objects.create(tenant=self.tenant, first_name="Anna", last_name="A", employment_pct=100)
        Employee.objects.create(tenant=self.tenant, first_name="Ben", last_name="B", employment_pct=100)

        mock_stripe.Customer.create.return_value = MagicMock(id="cus_123")
        mock_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/xyz")

        url = billing.create_checkout_session(
            self.tenant, success_url="https://x/ok", cancel_url="https://x/no"
        )
        self.assertEqual(url, "https://checkout.stripe.com/xyz")
        _, kwargs = mock_stripe.checkout.Session.create.call_args
        self.assertEqual(kwargs["line_items"][0]["quantity"], 2)

    @patch("core.billing.stripe")
    def test_create_checkout_session_quantity_never_below_one(self, mock_stripe):
        mock_stripe.Customer.create.return_value = MagicMock(id="cus_123")
        mock_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/xyz")
        billing.create_checkout_session(self.tenant, success_url="https://x/ok", cancel_url="https://x/no")
        _, kwargs = mock_stripe.checkout.Session.create.call_args
        self.assertEqual(kwargs["line_items"][0]["quantity"], 1)

    def test_create_billing_portal_session_requires_existing_customer(self):
        with self.assertRaises(billing.BillingNotConfigured):
            billing.create_billing_portal_session(self.tenant, return_url="https://x/back")

    @patch("core.billing.stripe")
    def test_create_billing_portal_session(self, mock_stripe):
        self.tenant.stripe_customer_id = "cus_123"
        self.tenant.save(update_fields=["stripe_customer_id"])
        mock_stripe.billing_portal.Session.create.return_value = MagicMock(url="https://billing.stripe.com/xyz")
        url = billing.create_billing_portal_session(self.tenant, return_url="https://x/back")
        self.assertEqual(url, "https://billing.stripe.com/xyz")

    @patch("core.billing.stripe")
    def test_sync_subscription_quantity_updates_when_changed(self, mock_stripe):
        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        mock_stripe.Subscription.retrieve.return_value = {
            "items": {"data": [{"id": "si_1", "quantity": 3}]}
        }
        billing.sync_subscription_quantity(self.tenant)
        mock_stripe.SubscriptionItem.modify.assert_called_once_with("si_1", quantity=1)

    @patch("core.billing.stripe")
    def test_sync_subscription_quantity_noop_when_unchanged(self, mock_stripe):
        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        mock_stripe.Subscription.retrieve.return_value = {
            "items": {"data": [{"id": "si_1", "quantity": 1}]}
        }
        billing.sync_subscription_quantity(self.tenant)
        mock_stripe.SubscriptionItem.modify.assert_not_called()

    @patch("core.billing.stripe")
    def test_sync_subscription_quantity_swallows_stripe_errors(self, mock_stripe):
        import stripe as real_stripe

        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        mock_stripe.error = real_stripe.error
        mock_stripe.Subscription.retrieve.side_effect = real_stripe.error.StripeError("boom")
        billing.sync_subscription_quantity(self.tenant)  # darf nicht werfen


class GetSubscriptionDetailsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    @override_settings(STRIPE_SECRET_KEY="", STRIPE_PRICE_ID="")
    def test_returns_none_without_config(self):
        self.tenant.stripe_subscription_id = "sub_123"
        self.assertIsNone(billing.get_subscription_details(self.tenant))

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PRICE_ID="price_dummy")
    def test_returns_none_without_subscription_id(self):
        self.assertIsNone(billing.get_subscription_details(self.tenant))

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PRICE_ID="price_dummy")
    @patch("core.billing.stripe")
    def test_returns_none_on_stripe_error(self, mock_stripe):
        import stripe as real_stripe

        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        mock_stripe.error = real_stripe.error
        mock_stripe.Subscription.retrieve.side_effect = real_stripe.error.StripeError("boom")
        self.assertIsNone(billing.get_subscription_details(self.tenant))

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PRICE_ID="price_dummy")
    @patch("core.billing.stripe")
    def test_full_details_happy_path(self, mock_stripe):
        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        mock_stripe.Subscription.retrieve.return_value = {
            "items": {
                "data": [
                    {
                        "quantity": 20,
                        "price": {
                            "unit_amount": 900,
                            "currency": "chf",
                            "recurring": {"interval": "month"},
                        },
                    }
                ]
            },
            "current_period_end": 1893456000,  # 2030-01-01T00:00:00Z
            "cancel_at_period_end": False,
            "default_payment_method": {"type": "card", "card": {"brand": "visa", "last4": "4242"}},
            "latest_invoice": {"status": "paid", "amount_due": 0},
        }
        details = billing.get_subscription_details(self.tenant)
        self.assertEqual(details["price_amount"], 900)
        self.assertEqual(details["price_currency"], "chf")
        self.assertEqual(details["price_interval"], "month")
        self.assertEqual(details["quantity"], 20)
        self.assertFalse(details["cancel_at_period_end"])
        self.assertEqual(details["payment_method"], {"brand": "visa", "last4": "4242"})
        self.assertEqual(details["latest_invoice_status"], "paid")
        self.assertEqual(details["latest_invoice_amount_due"], 0)
        self.assertEqual(details["current_period_end"].year, 2030)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PRICE_ID="price_dummy")
    @patch("core.billing.stripe")
    def test_missing_payment_method_and_invoice_are_none(self, mock_stripe):
        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        mock_stripe.Subscription.retrieve.return_value = {
            "items": {
                "data": [
                    {
                        "quantity": 1,
                        "price": {"unit_amount": 900, "currency": "chf", "recurring": {"interval": "month"}},
                    }
                ]
            },
            "current_period_end": None,
            "cancel_at_period_end": True,
            "default_payment_method": None,
            "latest_invoice": None,
        }
        details = billing.get_subscription_details(self.tenant)
        self.assertIsNone(details["payment_method"])
        self.assertIsNone(details["latest_invoice_status"])
        self.assertIsNone(details["current_period_end"])
        self.assertTrue(details["cancel_at_period_end"])


class WebhookEventHandlingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Klinik A", slug="klinik-a", subscription_status=Tenant.SubscriptionStatus.TRIALING
        )

    def test_checkout_session_completed_activates_tenant(self):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"tenant_id": str(self.tenant.id)},
                    "customer": "cus_123",
                    "subscription": "sub_123",
                }
            },
        }
        billing.handle_webhook_event(event)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_status, Tenant.SubscriptionStatus.ACTIVE)
        self.assertEqual(self.tenant.stripe_customer_id, "cus_123")
        self.assertEqual(self.tenant.stripe_subscription_id, "sub_123")

    def test_checkout_session_completed_unknown_tenant_is_ignored(self):
        import uuid

        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"tenant_id": str(uuid.uuid4())}, "customer": "cus_x"}},
        }
        billing.handle_webhook_event(event)  # darf nicht werfen

    def test_subscription_updated_maps_status(self):
        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.save(update_fields=["stripe_subscription_id"])
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_123", "status": "past_due"}},
        }
        billing.handle_webhook_event(event)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_status, Tenant.SubscriptionStatus.PAST_DUE)

    def test_subscription_deleted_cancels_tenant(self):
        self.tenant.stripe_subscription_id = "sub_123"
        self.tenant.subscription_status = Tenant.SubscriptionStatus.ACTIVE
        self.tenant.save(update_fields=["stripe_subscription_id", "subscription_status"])
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_123", "status": "canceled"}},
        }
        billing.handle_webhook_event(event)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_status, Tenant.SubscriptionStatus.CANCELED)

    def test_subscription_event_unknown_tenant_is_ignored(self):
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_unknown", "status": "active"}},
        }
        billing.handle_webhook_event(event)  # darf nicht werfen

    def test_invoice_payment_failed_is_logged_only(self):
        event = {"type": "invoice.payment_failed", "data": {"object": {"subscription": "sub_123"}}}
        billing.handle_webhook_event(event)  # darf nicht werfen, ändert nichts

    def test_unhandled_event_type_is_ignored(self):
        event = {"type": "some.other.event", "data": {"object": {}}}
        billing.handle_webhook_event(event)  # darf nicht werfen


class EnforceBillingAccessTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik A", slug="klinik-a")

    def _request(self, method, tenant):
        req = MagicMock()
        req.method = method
        req.tenant = tenant
        return req

    def test_no_tenant_is_allowed(self):
        billing.enforce_billing_access(self._request("POST", None))  # darf nicht werfen

    def test_get_is_always_allowed(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.CANCELED
        billing.enforce_billing_access(self._request("GET", self.tenant))  # darf nicht werfen

    def test_post_with_active_access_is_allowed(self):
        billing.enforce_billing_access(self._request("POST", self.tenant))  # darf nicht werfen

    def test_post_without_active_access_raises_402(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.CANCELED
        with self.assertRaises(billing.PaymentRequired) as ctx:
            billing.enforce_billing_access(self._request("POST", self.tenant))
        self.assertEqual(ctx.exception.status_code, 402)


class BillingAccessGateAPITests(APITestCase):
    """End-to-End: 402 auf einem echten TenantScopedViewSet-Endpoint (Nodes)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Klinik A", slug="klinik-a", subscription_status=Tenant.SubscriptionStatus.CANCELED
        )
        self.user = User.objects.create_user(username="admin", password="pw-not-real-123!")
        Membership.objects.create(user=self.user, tenant=self.tenant, role=Membership.Role.ADMIN)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_get_still_works_when_blocked(self):
        response = self.client.get("/api/nodes/")
        self.assertEqual(response.status_code, 200)

    def test_post_is_blocked_with_402(self):
        response = self.client.post("/api/nodes/", {"name": "Neue Station"}, format="json")
        self.assertEqual(response.status_code, 402)

    def test_post_works_again_once_active(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.ACTIVE
        self.tenant.save(update_fields=["subscription_status"])
        response = self.client.post("/api/nodes/", {"name": "Neue Station"}, format="json")
        self.assertEqual(response.status_code, 201)


class TrialEmployeeLimitTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Klinik A",
            slug="klinik-a",
            subscription_status=Tenant.SubscriptionStatus.TRIALING,
            trial_employee_limit=2,
        )
        self.user = User.objects.create_user(username="admin", password="pw-not-real-123!")
        Membership.objects.create(user=self.user, tenant=self.tenant, role=Membership.Role.ADMIN)
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def _create_employee(self, first_name):
        return self.client.post(
            "/api/employees/",
            {"first_name": first_name, "last_name": "Test", "employment_pct": 100},
            format="json",
        )

    def test_creation_within_limit_succeeds(self):
        self.assertEqual(self._create_employee("Anna").status_code, 201)
        self.assertEqual(self._create_employee("Ben").status_code, 201)

    def test_creation_beyond_limit_is_rejected(self):
        self.assertEqual(self._create_employee("Anna").status_code, 201)
        self.assertEqual(self._create_employee("Ben").status_code, 201)
        response = self._create_employee("Chris")
        self.assertEqual(response.status_code, 400)

    def test_limit_does_not_apply_when_active(self):
        self.tenant.subscription_status = Tenant.SubscriptionStatus.ACTIVE
        self.tenant.save(update_fields=["subscription_status"])
        self.assertEqual(self._create_employee("Anna").status_code, 201)
        self.assertEqual(self._create_employee("Ben").status_code, 201)
        self.assertEqual(self._create_employee("Chris").status_code, 201)

    def test_update_of_existing_employee_is_not_limited(self):
        self._create_employee("Anna")
        self._create_employee("Ben")
        from scheduling.models import Employee

        employee = Employee.objects.get(first_name="Anna")
        response = self.client.patch(f"/api/employees/{employee.id}/", {"employment_pct": 80}, format="json")
        self.assertEqual(response.status_code, 200)


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PRICE_ID="price_dummy")
class BillingStatusViewTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Klinik A",
            slug="klinik-a",
            subscription_status=Tenant.SubscriptionStatus.TRIALING,
            trial_ends_at=timezone.now() + timedelta(days=5),
        )
        self.admin = User.objects.create_user(username="admin", password="pw-not-real-123!")
        Membership.objects.create(user=self.admin, tenant=self.tenant, role=Membership.Role.ADMIN)
        self.planner = User.objects.create_user(username="planner", password="pw-not-real-123!")
        Membership.objects.create(user=self.planner, tenant=self.tenant, role=Membership.Role.PLANNER)

    def auth_as(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_admin_can_read_status(self):
        self.auth_as(self.admin)
        response = self.client.get("/api/billing/status/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["subscription_status"], "trialing")
        self.assertTrue(response.data["has_active_access"])
        self.assertTrue(response.data["billing_configured"])
        self.assertIsNone(response.data["subscription"])  # kein stripe_subscription_id in diesem Test

    def test_non_admin_cannot_read_status(self):
        self.auth_as(self.planner)
        response = self.client.get("/api/billing/status/")
        self.assertEqual(response.status_code, 403)

    def test_status_endpoint_works_even_when_blocked(self):
        # Wichtig: Status/Checkout/Portal dürfen NIE selbst durch
        # enforce_billing_access blockiert werden, sonst gäbe es keinen
        # Ausweg aus dem 402-Zustand.
        self.tenant.subscription_status = Tenant.SubscriptionStatus.CANCELED
        self.tenant.save(update_fields=["subscription_status"])
        self.auth_as(self.admin)
        response = self.client.get("/api/billing/status/")
        self.assertEqual(response.status_code, 200)

    @patch("core.billing_views.billing.create_checkout_session")
    def test_checkout_session_endpoint_even_when_blocked(self, mock_create):
        mock_create.return_value = "https://checkout.stripe.com/xyz"
        self.tenant.subscription_status = Tenant.SubscriptionStatus.CANCELED
        self.tenant.save(update_fields=["subscription_status"])
        self.auth_as(self.admin)
        response = self.client.post(
            "/api/billing/checkout/",
            {"success_url": "https://x/ok", "cancel_url": "https://x/no"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["checkout_url"], "https://checkout.stripe.com/xyz")

    def test_checkout_session_requires_urls(self):
        self.auth_as(self.admin)
        response = self.client.post("/api/billing/checkout/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    @patch("core.billing_views.billing.create_billing_portal_session")
    def test_portal_session_endpoint(self, mock_create):
        mock_create.return_value = "https://billing.stripe.com/xyz"
        self.auth_as(self.admin)
        response = self.client.post("/api/billing/portal/", {"return_url": "https://x/back"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["portal_url"], "https://billing.stripe.com/xyz")


class StripeWebhookViewTests(APITestCase):
    """
    Signaturprüfung selbst gegen echtes stripe.Webhook.construct_event
    getestet (kein Mock) -- die Bibliothek ist deterministisch, ein
    frei erfundenes Secret liefert zuverlässig eine ungültige Signatur.
    """

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_returns_503_when_not_configured(self):
        response = self.client.post(
            "/api/billing/webhook/", data="{}", content_type="application/json", HTTP_STRIPE_SIGNATURE="x"
        )
        self.assertEqual(response.status_code, 503)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test_secret")
    def test_rejects_invalid_signature(self):
        response = self.client.post(
            "/api/billing/webhook/",
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=invalid",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test_secret")
    def test_accepts_valid_signature_and_processes_event(self):
        import json
        import time

        import stripe

        tenant = Tenant.objects.create(
            name="Klinik A", slug="klinik-a", subscription_status=Tenant.SubscriptionStatus.TRIALING
        )
        payload = json.dumps(
            {
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "metadata": {"tenant_id": str(tenant.id)},
                        "customer": "cus_123",
                        "subscription": "sub_123",
                    }
                },
            }
        )
        timestamp = int(time.time())
        signed_payload = f"{timestamp}.{payload}"
        signature = stripe.WebhookSignature._compute_signature(signed_payload, "whsec_test_secret")
        sig_header = f"t={timestamp},v1={signature}"

        response = self.client.post(
            "/api/billing/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=sig_header,
        )
        self.assertEqual(response.status_code, 200)
        tenant.refresh_from_db()
        self.assertEqual(tenant.subscription_status, Tenant.SubscriptionStatus.ACTIVE)

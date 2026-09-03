from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.billing import stripe_status_to_subscription_status
from core.models import Tenant


class Command(BaseCommand):
    """
    Nachtrag für Tenants, deren stripe_customer_id gesetzt ist (Checkout wurde
    begonnen, siehe core.billing.get_or_create_stripe_customer -- das passiert
    synchron im Backend, BEVOR zu Stripe weitergeleitet wird), deren
    stripe_subscription_id aber leer geblieben ist -- das bedeutet, das
    checkout.session.completed-Webhook-Event ist nie angekommen (häufigste
    Ursache: lokale Entwicklung ohne öffentlich erreichbaren Webhook-Endpoint,
    Stripe kann http://localhost:8000/api/billing/webhook/ nicht erreichen,
    siehe README Block 6 "Setup").

    Fragt für jeden betroffenen Tenant Stripe direkt nach aktiven/trialing
    Subscriptions des jeweiligen Customers und trägt die neueste gefundene
    nach -- Einmal-Ausführung zum Reparieren bestehender Lücken, kein
    Ersatz für einen funktionierenden Webhook (künftige Checkouts brauchen
    weiterhin einen erreichbaren Endpoint, sonst tritt dieselbe Lücke wieder
    auf). Für lokale Entwicklung: `stripe listen --forward-to
    localhost:8000/api/billing/webhook/` liefert ein lokal gültiges
    Webhook-Secret für .env.

    WICHTIG: braucht ausgehenden Netzwerkzugriff auf api.stripe.com, siehe
    setup_stripe_billing-Docstring für den Hintergrund.

    Aufruf:
        python manage.py sync_stripe_subscriptions
    """

    help = "Trägt fehlende stripe_subscription_id nach, wenn das Checkout-Webhook nie ankam."

    def handle(self, *args, **options):
        if not settings.STRIPE_SECRET_KEY:
            raise CommandError("STRIPE_SECRET_KEY ist nicht gesetzt (.env prüfen).")

        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY

        tenants = Tenant.objects.exclude(stripe_customer_id="").filter(stripe_subscription_id="")
        if not tenants:
            self.stdout.write("Keine Tenants mit Customer ohne Subscription gefunden.")
            return

        for tenant in tenants:
            subscriptions = stripe.Subscription.list(customer=tenant.stripe_customer_id, limit=1)
            data = subscriptions["data"]
            if not data:
                self.stdout.write(
                    f"{tenant.name}: kein Abo bei Stripe für Customer {tenant.stripe_customer_id} "
                    "gefunden -- Checkout wurde vermutlich nie abgeschlossen."
                )
                continue

            subscription = data[0]
            tenant.stripe_subscription_id = subscription["id"]
            tenant.subscription_status = stripe_status_to_subscription_status(
                subscription["status"], fallback=tenant.subscription_status
            )
            tenant.save(update_fields=["stripe_subscription_id", "subscription_status"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"{tenant.name}: {subscription['id']} nachgetragen (Status: {subscription['status']})."
                )
            )

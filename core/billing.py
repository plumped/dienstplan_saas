"""
Abrechnung (README Block 6, 2026-08): Stripe-Zahlungsanbindung für ein Abo
pro aktivem Mitarbeitenden.

Netzwerkhinweis: diese Datei wurde in einer Sandbox-Umgebung geschrieben,
deren Egress-Policy api.stripe.com/checkout.stripe.com sperrt -- ein echter
End-to-End-Test (Checkout-Redirect, Webhook-Zustellung) war von dort aus
nicht möglich. Die Stripe-Aufrufe folgen der offiziellen API-Dokumentation
(stripe-python 15.x) und sind über core/tests_billing.py mit gemockten
Stripe-Calls abgedeckt -- ein echter Smoke-Test gegen Stripe Test-Mode
(Checkout durchklicken, `stripe trigger checkout.session.completed` o. Ä.)
sollte vor dem produktiven Umstieg trotzdem einmal von einer Maschine mit
Internetzugriff aus gemacht werden.

Alle Funktionen prüfen `is_configured()` selbst und werfen bei fehlender
Konfiguration `BillingNotConfigured` statt eines rohen Stripe-Fehlers --
das hält z. B. die Test-Suite (die STRIPE_SECRET_KEY bewusst nicht setzt)
unabhängig von Stripe lauffähig.
"""

import logging
from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class BillingNotConfigured(Exception):
    """Stripe-Keys fehlen (settings.STRIPE_SECRET_KEY/STRIPE_PRICE_ID leer)."""


class PaymentRequired(APIException):
    """
    402 Payment Required -- DRF kennt diesen Code nicht eingebaut (nur 401/
    403). Wird von enforce_billing_access() geworfen, wenn ein Tenant nach
    Ablauf der Trial-Phase ohne aktives Abo schreibend auf die API zugreift.
    """

    status_code = 402
    default_detail = "Die Testphase ist abgelaufen oder das Abo ist nicht aktiv."
    default_code = "payment_required"


def is_configured():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PRICE_ID)


def _require_configured():
    if not is_configured():
        raise BillingNotConfigured(
            "Stripe ist nicht konfiguriert (STRIPE_SECRET_KEY/STRIPE_PRICE_ID fehlen) -- siehe "
            ".env und README Block 6."
        )
    stripe.api_key = settings.STRIPE_SECRET_KEY


def get_or_create_stripe_customer(tenant, email=""):
    """
    Liefert die Stripe-Customer-ID des Tenants, legt bei Bedarf einen neuen
    Stripe-Customer an. `email` ist nur ein Anzeigehinweis für Stripe
    (Rechnungen/Dashboard) -- die eigentliche Zuordnung läuft über
    tenant.stripe_customer_id, nicht über die E-Mail.
    """
    _require_configured()
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id

    customer = stripe.Customer.create(
        name=tenant.name,
        email=email or None,
        metadata={"tenant_id": str(tenant.id), "tenant_slug": tenant.slug},
    )
    tenant.stripe_customer_id = customer.id
    tenant.save(update_fields=["stripe_customer_id"])
    return customer.id


def create_checkout_session(tenant, success_url, cancel_url, email=""):
    """
    Stripe Checkout im Subscription-Modus -- die eigentliche Zahlungsseite
    ist komplett Stripe-gehostet (kein Kartendaten-Handling im eigenen
    Frontend/Backend, kein PCI-Scope hier). Menge = aktuelle aktive
    Mitarbeitende (mind. 1, Stripe erlaubt keine Menge 0) -- wird danach bei
    jeder relevanten Mitarbeitenden-Änderung nachgeführt, siehe
    sync_subscription_quantity().
    """
    _require_configured()
    customer_id = get_or_create_stripe_customer(tenant, email=email)
    quantity = max(1, tenant.active_employee_count())
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": quantity}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"tenant_id": str(tenant.id)},
        subscription_data={"metadata": {"tenant_id": str(tenant.id)}},
    )
    return session.url


def create_billing_portal_session(tenant, return_url):
    """
    Stripe-gehostetes Kundenportal: Zahlungsmittel ändern, Rechnungen
    einsehen, Abo kündigen -- ohne dass wir das selbst nachbauen müssen.
    Setzt einen bestehenden Stripe-Customer voraus (erst nach mindestens
    einem Checkout-Durchlauf vorhanden).
    """
    _require_configured()
    if not tenant.stripe_customer_id:
        raise BillingNotConfigured(
            "Noch kein Stripe-Kunde für diesen Tenant -- zuerst ein Abo über create_checkout_session "
            "abschliessen."
        )
    session = stripe.billing_portal.Session.create(
        customer=tenant.stripe_customer_id,
        return_url=return_url,
    )
    return session.url


def sync_subscription_quantity(tenant):
    """
    Meldet die aktuelle Anzahl aktiver Mitarbeitender an Stripe (Menge des
    einzigen Subscription-Items), damit die nächste Rechnung stimmt --
    "best effort": ein Stripe-Fehler hier darf niemals eine ganz normale
    Mitarbeitenden-Aktion (anlegen/deaktivieren/reaktivieren) blockieren,
    siehe Aufrufstellen in scheduling/views.py::EmployeeViewSet. Ohne
    aktives Abo (kein stripe_subscription_id) ein No-Op -- betrifft Tenants
    in der Trial-Phase oder ohne Stripe-Konfiguration.
    """
    if not is_configured() or not tenant.stripe_subscription_id:
        return
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        subscription = stripe.Subscription.retrieve(tenant.stripe_subscription_id)
        item = subscription["items"]["data"][0]
        quantity = max(1, tenant.active_employee_count())
        if item["quantity"] != quantity:
            stripe.SubscriptionItem.modify(item["id"], quantity=quantity)
    except stripe.error.StripeError:
        logger.exception("Stripe-Mengen-Sync fehlgeschlagen für Tenant %s", tenant.id)


def get_subscription_details(tenant):
    """
    Liefert Detailinfos zur laufenden Stripe-Subscription (nächstes
    Rechnungsdatum, Preis/Menge, Zahlungsmittel, letzte Rechnung) fürs
    Abrechnungs-Dashboard (BillingStatusView) -- Nutzer-Feedback (2026-08):
    "Das ist viel zu wenig Info, ich will möglichst viel Informationen zur
    subscription sehen", die reinen Tenant-Felder (subscription_status,
    active_employee_count) allein reichten nicht.

    None, wenn (noch) keine Subscription existiert, Stripe nicht
    konfiguriert ist, oder der Stripe-Call fehlschlägt/die Antwort nicht wie
    erwartet aussieht -- "best effort" analog sync_subscription_quantity:
    ein Stripe-Fehler hier darf die übrige Status-Anzeige nicht blockieren,
    GET-Requests laufen ausserdem nie durch enforce_billing_access.

    _safe() statt rohem Bracket-Zugriff: anders als zunächst angenommen
    liefert stripe.StripeObject.__getitem__ bei einem wirklich fehlenden
    Key einen KeyError (kein stilles None) -- betraf hier konkret
    current_period_end, das Stripe in neueren API-Versionen vom
    Subscription-Objekt auf die einzelnen Subscription-Items verschoben hat
    (siehe Fallback unten). .get() ist keine Alternative, stripe.StripeObject
    unterstützt das in stripe-python 15.x nicht (siehe
    handle_webhook_event-Kommentar). Der ganze Parse-Block läuft ausserdem
    in einem eigenen try/except -- eine weitere, heute noch unbekannte
    API-Versions-Abweichung soll höchstens diesen Detailblock leer lassen,
    nicht die ganze Abrechnungsseite mit einem 500er blockieren.
    """
    if not is_configured() or not tenant.stripe_subscription_id:
        return None
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        subscription = stripe.Subscription.retrieve(
            tenant.stripe_subscription_id,
            expand=["default_payment_method", "latest_invoice"],
        )
    except stripe.error.StripeError:
        logger.exception("Stripe-Subscription-Details fehlgeschlagen für Tenant %s", tenant.id)
        return None

    def _safe(obj, key, default=None):
        try:
            return obj[key]
        except (KeyError, TypeError, IndexError):
            return default

    try:
        item = _safe(subscription, "items")["data"][0]
        price = _safe(item, "price") or {}
        recurring = _safe(price, "recurring")

        payment_method = _safe(subscription, "default_payment_method")
        payment_method_info = None
        if payment_method and _safe(payment_method, "type") == "card":
            card = _safe(payment_method, "card") or {}
            payment_method_info = {"brand": _safe(card, "brand"), "last4": _safe(card, "last4")}

        latest_invoice = _safe(subscription, "latest_invoice")

        # current_period_end sass früher direkt auf der Subscription, liegt
        # in neueren Stripe-API-Versionen stattdessen auf dem Item -- beide
        # Stellen versuchen, damit es unabhängig von der Account-API-Version
        # funktioniert.
        current_period_end = _safe(subscription, "current_period_end") or _safe(item, "current_period_end")

        return {
            "current_period_end": (
                datetime.fromtimestamp(current_period_end, tz=dt_timezone.utc) if current_period_end else None
            ),
            "cancel_at_period_end": bool(_safe(subscription, "cancel_at_period_end")),
            "price_amount": _safe(price, "unit_amount"),
            "price_currency": _safe(price, "currency"),
            "price_interval": _safe(recurring, "interval") if recurring else None,
            "quantity": _safe(item, "quantity"),
            "payment_method": payment_method_info,
            "latest_invoice_status": _safe(latest_invoice, "status") if latest_invoice else None,
            "latest_invoice_amount_due": _safe(latest_invoice, "amount_due") if latest_invoice else None,
        }
    except Exception:
        logger.exception("Stripe-Subscription-Antwort konnte nicht geparst werden für Tenant %s", tenant.id)
        return None


def handle_webhook_event(event):
    """
    Reagiert auf die vier Events, die core.views.StripeWebhookView (Stripe
    Dashboard -> Developers -> Webhooks) abonniert:

    - checkout.session.completed: Abo gerade abgeschlossen -- Subscription-
      ID speichern, Status auf ACTIVE setzen (überschreibt die Trial-Phase).
    - customer.subscription.updated: Statuswechsel (z. B. active -> past_due
      nach fehlgeschlagener Zahlung, oder umgekehrt nach Nachzahlung).
    - customer.subscription.deleted: Kündigung wirksam geworden.
    - invoice.payment_failed: informativ geloggt, der eigentliche Statuswechsel
      kommt über customer.subscription.updated (Stripe setzt die Subscription
      dabei i. d. R. selbst auf past_due).

    tenant_id kommt aus den beim Anlegen gesetzten metadata (siehe
    create_checkout_session) -- ein unbekannter/fehlender Tenant wird
    geloggt und ignoriert statt einen 500er zu werfen, damit ein einzelnes
    kaputtes Event nicht die Stripe-Retry-Queue blockiert.
    """
    from core.models import Tenant

    event_type = event["type"]
    # .to_dict(): stripe.StripeObject unterstuetzt zwar Index-Zugriff
    # (event["data"]["object"]), aber KEIN .get() (kein dict-Subtyp mehr in
    # stripe-python 15.x) -- ein plain dict macht den Rest dieser Funktion
    # unabhaengig von diesem SDK-Detail und bleibt mit den gemockten
    # dict-Payloads in core/tests_billing.py kompatibel.
    data = event["data"]["object"]
    if hasattr(data, "to_dict"):
        data = data.to_dict()

    if event_type == "checkout.session.completed":
        tenant_id = (data.get("metadata") or {}).get("tenant_id")
        tenant = Tenant.objects.filter(pk=tenant_id).first() if tenant_id else None
        if not tenant:
            logger.warning("checkout.session.completed ohne bekannten Tenant: %s", tenant_id)
            return
        tenant.stripe_customer_id = data.get("customer") or tenant.stripe_customer_id
        tenant.stripe_subscription_id = data.get("subscription") or tenant.stripe_subscription_id
        tenant.subscription_status = Tenant.SubscriptionStatus.ACTIVE
        tenant.save(update_fields=["stripe_customer_id", "stripe_subscription_id", "subscription_status"])
        return

    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        subscription_id = data.get("id")
        tenant = Tenant.objects.filter(stripe_subscription_id=subscription_id).first()
        if not tenant:
            logger.warning("Subscription-Event ohne bekannten Tenant: %s", subscription_id)
            return
        if event_type == "customer.subscription.deleted":
            tenant.subscription_status = Tenant.SubscriptionStatus.CANCELED
        else:
            status_map = {
                "active": Tenant.SubscriptionStatus.ACTIVE,
                "trialing": Tenant.SubscriptionStatus.TRIALING,
                "past_due": Tenant.SubscriptionStatus.PAST_DUE,
                "canceled": Tenant.SubscriptionStatus.CANCELED,
                "unpaid": Tenant.SubscriptionStatus.PAST_DUE,
                "incomplete": Tenant.SubscriptionStatus.INCOMPLETE,
                "incomplete_expired": Tenant.SubscriptionStatus.CANCELED,
            }
            tenant.subscription_status = status_map.get(
                data.get("status"), tenant.subscription_status
            )
        tenant.save(update_fields=["subscription_status"])
        return

    if event_type == "invoice.payment_failed":
        logger.info("Zahlung fehlgeschlagen für Stripe-Subscription %s", data.get("subscription"))
        return

    logger.debug("Unbehandelter Stripe-Webhook-Typ: %s", event_type)


def enforce_billing_access(request):
    """
    Zentrale Zugriffssperre, aufgerufen aus TenantScopedViewSet.initial()
    (scheduling/views.py) und TenantScopedAPIMixin.initial() (core/views.py)
    -- schreibende Requests (POST/PUT/PATCH/DELETE) eines Tenants ohne
    aktiven Zugriff (Tenant.has_active_access()) werden mit 402 abgelehnt,
    GET bleibt immer erlaubt (Kündigungs-/Trial-Ablauf darf nicht den
    Datenzugriff selbst sperren, nur weitere Mutationen). Reine
    Billing-Views (Checkout/Portal erstellen) rufen diese Funktion bewusst
    NICHT auf -- sonst könnte ein gesperrter Tenant sich nicht mehr selbst
    freischalten.
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not tenant.has_active_access():
        raise PaymentRequired()

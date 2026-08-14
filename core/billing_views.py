"""
Abrechnung (README Block 6, 2026-08): API-Endpoints rund um Stripe.

Bewusst ein EIGENES initial() statt core.views.TenantScopedAPIMixin -- dessen
initial() ruft billing.enforce_billing_access(request) auf, das genau diese
Views sperren würde. Ein Tenant, dessen Trial abgelaufen ist, muss sich über
CreateCheckoutSessionView/CreateBillingPortalSessionView trotzdem noch selbst
freischalten können, sonst gäbe es keinen Ausweg mehr aus dem 402-Zustand.
StripeWebhookView braucht überhaupt keinen eingeloggten Tenant (Stripe ruft
diesen Endpoint unauthentifiziert auf, Absicherung läuft über die
Signaturprüfung, nicht über Login).
"""

import logging

import stripe
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import billing
from core.models import Membership
from core.tenancy import resolve_membership_for_user

logger = logging.getLogger(__name__)


class _IsTenantAdminStrict(BasePermission):
    """
    Anders als core.permissions.IsTenantAdmin (das GET für alle vier Rollen
    offen lässt, siehe dessen Docstring) ist Zahlungsstatus/Checkout/Portal
    HIER auch beim Lesen Admin-only -- Planer/HR/Mitarbeitende sollen weder
    den Abrechnungsstatus noch eine Checkout-/Portal-Session des Tenants
    einsehen können.
    """

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        return bool(membership and membership.role == Membership.Role.ADMIN)


class _TenantScopedNoBillingGateMixin:
    """Wie core.views.TenantScopedAPIMixin, aber ohne enforce_billing_access()."""

    def initial(self, request, *args, **kwargs):
        self.format_kwarg = self.get_format_suffix(**kwargs)
        neg = self.perform_content_negotiation(request)
        request.accepted_renderer, request.accepted_media_type = neg
        version, scheme = self.determine_version(request, *args, **kwargs)
        request.version, request.versioning_scheme = version, scheme

        self.perform_authentication(request)
        membership = resolve_membership_for_user(request.user)
        request.membership = membership
        request.tenant = membership.tenant if membership else None

        self.check_permissions(request)
        self.check_throttles(request)


class BillingStatusView(_TenantScopedNoBillingGateMixin, APIView):
    """
    GET: aktueller Abrechnungsstatus des eigenen Tenants -- Grundlage für die
    Trial-Restzeit-Anzeige und die Blockier-Banner im Frontend
    (BillingSettings.jsx). Admin-only (_IsTenantAdminStrict), da
    Zahlungsstatus keine Tagesgeschäft-Information für Planer/Mitarbeitende
    ist.
    """

    permission_classes = [IsAuthenticated, _IsTenantAdminStrict]

    def get(self, request):
        tenant = request.tenant
        if not tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        return Response(
            {
                "subscription_status": tenant.subscription_status,
                "trial_ends_at": tenant.trial_ends_at,
                "trial_employee_limit": tenant.trial_employee_limit,
                "has_active_access": tenant.has_active_access(),
                "active_employee_count": tenant.active_employee_count(),
                "billing_configured": billing.is_configured(),
                "subscription": billing.get_subscription_details(tenant),
            }
        )


class CreateCheckoutSessionView(_TenantScopedNoBillingGateMixin, APIView):
    """
    POST: erzeugt eine Stripe-Checkout-Session (Subscription-Modus) für den
    eigenen Tenant und liefert die Redirect-URL zurück -- das Frontend leitet
    den Browser dorthin weiter (window.location.href = checkout_url), die
    eigentliche Zahlungsseite ist komplett Stripe-gehostet. success_url/
    cancel_url kommen vom Frontend (kennt seine eigene Basis-URL besser als
    das Backend), werden aber nicht weiter validiert -- Admin-only
    (_IsTenantAdminStrict), ein offener Redirect ist hier kein
    Sicherheitsproblem, weil nur der Betreiber selbst (Admin) diese URL
    bestimmt.
    """

    permission_classes = [IsAuthenticated, _IsTenantAdminStrict]

    def post(self, request):
        tenant = request.tenant
        if not tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        success_url = request.data.get("success_url")
        cancel_url = request.data.get("cancel_url")
        if not success_url or not cancel_url:
            raise ValidationError({"detail": "success_url und cancel_url sind Pflichtfelder."})
        try:
            checkout_url = billing.create_checkout_session(
                tenant, success_url=success_url, cancel_url=cancel_url, email=request.user.email
            )
        except billing.BillingNotConfigured as exc:
            raise ValidationError({"detail": str(exc)})
        return Response({"checkout_url": checkout_url})


class CreateBillingPortalSessionView(_TenantScopedNoBillingGateMixin, APIView):
    """
    POST: erzeugt eine Stripe-Billing-Portal-Session (Zahlungsmittel ändern,
    Rechnungen einsehen, Abo kündigen) -- Gegenstück zu
    CreateCheckoutSessionView für einen Tenant, der bereits Kunde ist.
    """

    permission_classes = [IsAuthenticated, _IsTenantAdminStrict]

    def post(self, request):
        tenant = request.tenant
        if not tenant:
            raise NotFound("Kein Tenant zugeordnet.")
        return_url = request.data.get("return_url")
        if not return_url:
            raise ValidationError({"detail": "return_url ist Pflichtfeld."})
        try:
            portal_url = billing.create_billing_portal_session(tenant, return_url=return_url)
        except billing.BillingNotConfigured as exc:
            raise ValidationError({"detail": str(exc)})
        return Response({"portal_url": portal_url})


@csrf_exempt
def stripe_webhook(request):
    """
    POST /api/billing/webhook/ -- reiner Django-View (kein DRF APIView),
    damit wir an den ROHEN, unveränderten Request-Body herankommen. DRFs
    JSONParser würde den Body bereits geparst haben, bevor
    stripe.Webhook.construct_event() ihn für die Signaturprüfung braucht
    (die Signatur wird über die exakten Bytes berechnet, ein
    Re-Serialisieren würde sie ungültig machen). csrf_exempt: Stripe sendet
    keinen CSRF-Token, Absicherung läuft ausschliesslich über die
    HMAC-Signaturprüfung (STRIPE_WEBHOOK_SECRET) -- ohne gültige Signatur
    wird die Anfrage mit 400 abgelehnt, bevor überhaupt Tenant-Daten
    angefasst werden.
    """
    if request.method != "POST":
        return _json_response({"detail": "Method not allowed"}, status=405)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("Stripe-Webhook aufgerufen, aber STRIPE_WEBHOOK_SECRET ist nicht konfiguriert.")
        return _json_response({"detail": "Webhook nicht konfiguriert."}, status=503)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        logger.warning("Ungültiges Stripe-Webhook-Event abgelehnt (Signatur/Payload).")
        return _json_response({"detail": "Ungültige Signatur."}, status=400)

    billing.handle_webhook_event(event)
    return _json_response({"received": True})


def _json_response(data, status=200):
    from django.http import JsonResponse

    return JsonResponse(data, status=status)

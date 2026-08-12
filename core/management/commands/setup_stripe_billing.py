from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Default-Betrag pro aktivem Mitarbeitenden/Monat -- NICHT vom Nutzer
# bestätigt (README Block 6, 2026-08: nur das Preismodell "ein Plan, Preis
# pro aktivem Mitarbeitenden" wurde festgelegt, kein konkreter Betrag).
# Bewusst als eigene Konstante statt in settings.py, damit --amount die
# Kommandozeile als offensichtlichen Override-Weg zeigt, statt still einen
# falschen Betrag zu übernehmen.
DEFAULT_AMOUNT_RAPPEN = 900  # CHF 9.00 -- Platzhalter, vor dem Live-Umstieg mit dem Nutzer klären.


class Command(BaseCommand):
    """
    Legt (einmalig, idempotent) das Stripe-Product + die Stripe-Price für das
    Abo "pro aktivem Mitarbeitenden/Monat" an und gibt die resultierende
    Price-ID aus -- die anschliessend als STRIPE_PRICE_ID in .env eingetragen
    werden muss (siehe core/billing.py, config/settings.py).

    WICHTIG: dieser Befehl braucht ausgehenden Netzwerkzugriff auf
    api.stripe.com. Die Sandbox, in der dieses Feature entwickelt wurde, hat
    genau diesen Host per Egress-Policy blockiert (siehe /root/.ccr/README.md)
    -- der Befehl konnte von dort aus nicht selbst ausgeführt/getestet werden.
    Bitte auf einer Maschine mit echtem Internetzugriff laufen lassen, mit
    STRIPE_SECRET_KEY in .env bereits gesetzt (siehe .env.example bzw. die
    bereits eingetragenen Test-Keys).

    Der Betrag ist ein Platzhalter (siehe DEFAULT_AMOUNT_RAPPEN oben) -- vor
    dem Produktivumstieg unbedingt mit --amount auf den echten Preis pro
    Mitarbeitendem setzen, oder die Konstante hier anpassen.

    Aufruf:
        python manage.py setup_stripe_billing
        python manage.py setup_stripe_billing --amount 1200  # CHF 12.00
    """

    help = "Legt das Stripe-Product/-Price für das Abrechnungsmodell an (README Block 6)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--amount",
            type=int,
            default=DEFAULT_AMOUNT_RAPPEN,
            help="Preis pro aktivem Mitarbeitenden/Monat in Rappen (Standard: %(default)s = CHF 9.00).",
        )

    def handle(self, *args, **options):
        if not settings.STRIPE_SECRET_KEY:
            raise CommandError(
                "STRIPE_SECRET_KEY ist nicht gesetzt (.env prüfen) -- ohne Secret Key kann kein "
                "Stripe-Product/-Price angelegt werden."
            )

        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        amount = options["amount"]

        product = stripe.Product.create(
            name="Dienstplan Abo",
            description="Abo pro aktivem Mitarbeitenden/Monat.",
        )
        price = stripe.Price.create(
            product=product.id,
            currency="chf",
            unit_amount=amount,
            recurring={"interval": "month", "usage_type": "licensed"},
        )

        self.stdout.write(self.style.SUCCESS(f"Product angelegt: {product.id}"))
        self.stdout.write(self.style.SUCCESS(f"Price angelegt: {price.id} (CHF {amount / 100:.2f}/Monat)"))
        self.stdout.write("")
        self.stdout.write("Jetzt in .env eintragen:")
        self.stdout.write(f"STRIPE_PRICE_ID={price.id}")

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from core.context import get_current_tenant


class User(AbstractUser):
    """
    Eigenes User-Model statt django.contrib.auth.User, von Anfang an, damit ein
    späterer Wechsel (schmerzhaft, sobald einmal Produktivdaten existieren)
    nicht mehr nötig ist. Vorerst keine zusätzlichen Felder gegenüber
    AbstractUser -- die Erweiterung (z. B. bevorzugte Sprache, Telefon für
    Diensttausch-Benachrichtigungen) kann später ohne Datenmigration der
    Auth-Tabellen ergänzt werden.
    """


class Tenant(models.Model):
    """Eine Gesundheitseinrichtung (Kunde) auf der Plattform."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Pro Tenant/Branche konfigurierbare Grenzwerte für die Regel-Engine
    # (scheduling.models.ShiftAssignment.clean). Defaults entsprechen dem
    # Schweizer Arbeitsgesetz (ArG) für Gesundheits-/Büropersonal -- andere
    # Branchen oder ein strengerer GAV (Gesamtarbeitsvertrag) können das pro
    # Tenant überschreiben. Diese Felder bilden nur die numerischen
    # Grenzwerte ab; die eigentlichen gesetzlichen Regeln (Pausenpflicht,
    # wöchentlicher freier Tag, Nacht-/Sonntagsarbeit) sind in der
    # Regel-Engine selbst fest verdrahtet, siehe deren Docstrings.
    minimum_rest_hours = models.PositiveSmallIntegerField(
        default=11,
        help_text="Mindestruhezeit zwischen zwei Schichten in Stunden (Art. 15a ArG: 11h Minimum).",
    )
    maximum_weekly_hours = models.PositiveSmallIntegerField(
        default=45,
        help_text="Wöchentliche Höchstarbeitszeit in Stunden (Art. 9 ArG: 45h für Gesundheits- und "
        "Büropersonal, 50h für übrige Betriebe -- je nach Branche/GAV anpassen).",
    )
    maximum_daily_span_hours = models.PositiveSmallIntegerField(
        default=14,
        help_text="Maximale Tagesspanne von Arbeitsbeginn bis Arbeitsende inkl. Pausen, in Stunden "
        "(Art. 10 Abs. 3 ArG).",
    )
    time_record_deviation_tolerance_minutes = models.PositiveSmallIntegerField(
        default=15,
        help_text="Ab dieser Abweichung (in Minuten) zwischen geplanter und tatsächlicher "
        "Arbeitszeit verlangt die Ist-Zeiterfassung eine Begründung (scheduling.models.TimeRecord).",
    )

    # Überzeitarbeit (Art. 13 ArG, MVP-Fahrplan Block 1.11): bewusst getrennt von
    # maximum_weekly_hours -- das ist die gesetzliche/GAV-Höchstgrenze, ab der eine
    # Zuweisung abgelehnt wird (ShiftAssignment.clean), während standard_weekly_hours
    # die Normalarbeitszeit eines 100%-Pensums ist, ab der Mehrarbeit als Überzeit mit
    # Zuschlag gilt (reine Auswertungs-/Lohnfrage, siehe scheduling.models.Employee.
    # weekly_hours_summary -- keine Ablehnung, nur Berechnung).
    standard_weekly_hours = models.PositiveSmallIntegerField(
        default=42,
        help_text="Normalarbeitszeit eines 100%-Pensums pro Woche, in Stunden (z. B. 42h GAV-Vorgabe "
        "in vielen Spitälern) -- die Basis für den Soll/Ist-Vergleich bei der Überzeitberechnung "
        "(Art. 13 ArG). Nicht zu verwechseln mit maximum_weekly_hours, der gesetzlichen Höchstgrenze.",
    )
    overtime_surcharge_pct = models.PositiveSmallIntegerField(
        default=25,
        help_text="Zuschlag auf Überzeitstunden in Prozent (Art. 13 Abs. 1 ArG: i. d. R. 25%, "
        "GAV-abhängig anpassbar).",
    )

    # Feriensaldo (MVP-Fahrplan Block 2.7): Default pro Tenant, einzelne
    # Mitarbeitende können das über Employee.vacation_days_per_year
    # überschreiben (gleiches Override-Muster wie bei den Wochenstunden,
    # siehe Block 1.14).
    default_vacation_days_per_year = models.PositiveSmallIntegerField(
        default=20,
        help_text="Gesetzlicher Mindestanspruch: 20 Arbeitstage (4 Wochen, Art. 329a Abs. 1 OR) für "
        "Erwachsene, 25 Tage (5 Wochen) für unter 20-Jährige (Art. 329a Abs. 3 OR) -- als "
        "Tenant-Default hinterlegt, pro Mitarbeiter überschreibbar für abweichende Verträge.",
    )

    # Nacht-/Sonntagsarbeit (MVP-Fahrplan Block 1.5/1.6): night_hours/is_sunday
    # auf ShiftAssignment erkennen die Sachverhalte bereits informativ (siehe
    # dort); diese Felder liefern die Zuschlags-/Schwellenwerte dafür, siehe
    # scheduling.models.Employee.night_work_summary/weekly_hours_summary.
    night_work_surcharge_pct = models.PositiveSmallIntegerField(
        default=10,
        help_text="Zeitgutschrift auf Nachtstunden bei regelmässiger Nachtarbeit, in Prozent "
        "(Art. 17b Abs. 1 ArG: i. d. R. 10%, sofern nicht durch einen gleichwertigen "
        "Lohnzuschlag abgegolten).",
    )
    night_work_regular_threshold_nights = models.PositiveSmallIntegerField(
        default=25,
        help_text="Ab dieser Anzahl Nächte mit Nachtarbeit pro Kalenderjahr gilt Nachtarbeit als "
        "'regelmässig' (ArGV 1 Art. 31) -- Voraussetzung für Zeitgutschrift, Bewilligungspflicht "
        "und arbeitsmedizinische Untersuchungspflicht (Art. 17c ArG).",
    )
    night_work_permit_confirmed = models.BooleanField(
        default=False,
        help_text="Von Admin bestätigt: die nötige behördliche Bewilligung für regelmässige "
        "Nachtarbeit (Art. 17 ArG) liegt vor, bzw. der Betrieb ist davon ausgenommen. Ohne "
        "Bestätigung erscheint ein Warnhinweis, sobald ein Mitarbeiter regelmässige Nachtarbeit "
        "leistet (siehe Employee.night_work_summary).",
    )
    sunday_work_surcharge_pct = models.PositiveSmallIntegerField(
        default=50,
        help_text="Lohnzuschlag auf Sonntagsstunden in Prozent (Art. 19 Abs. 3 ArG i. V. m. Art. 46 "
        "ArGV 1: i. d. R. 50%; für Dauerbetriebe wie viele Gesundheitseinrichtungen können "
        "Ausnahmen gelten -- auf 0 setzen, falls nicht zutreffend).",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Membership(models.Model):
    """Verknüpft einen User mit einem Tenant und einer Rolle (siehe Abschnitt 10 im Funktionsumfang)."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        PLANNER = "planner", "Planer"
        EMPLOYEE = "employee", "Mitarbeiter"
        HR = "hr", "HR / Leitung (nur Reporting)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "tenant")

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"


class TenantScopedManager(models.Manager):
    """
    Filtert automatisch nach dem aktuellen Tenant (aus der ContextVar), wenn
    einer gesetzt ist. Praktisch für Code ausserhalb von Views (z. B. Shell,
    Management-Commands, Celery-Tasks mit gesetztem Kontext).

    In den DRF-Views wird zusätzlich explizit über `all_objects` gefiltert
    (siehe scheduling.views) statt sich allein auf die ContextVar zu
    verlassen – doppelte Absicherung gegen Datenlecks zwischen Mandanten.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return qs


class TenantScopedModel(models.Model):
    """Abstract Base: jedes fachliche Modell hängt an genau einem Tenant."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

    objects = TenantScopedManager()
    all_objects = models.Manager()  # ungefiltert – bewusst für Admin/Views/Skripte

    class Meta:
        abstract = True

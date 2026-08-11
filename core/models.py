import uuid

import holidays
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from core.context import get_current_tenant

# Kantone der Schweiz, wie von der "holidays"-Bibliothek unterstützt
# (vacanza/holidays, siehe README Architektur-Entscheidungen zum
# Feiertagskalender) -- bewusst ohne den Sonderfall "Stadt Zurich" aus
# holidays.Switzerland.subdivisions, der eine Gemeinde statt eines Kantons
# ist und hier zu Verwirrung führen würde.
SWISS_CANTON_CHOICES = [
    ("AG", "Aargau"),
    ("AI", "Appenzell Innerrhoden"),
    ("AR", "Appenzell Ausserrhoden"),
    ("BE", "Bern"),
    ("BL", "Basel-Landschaft"),
    ("BS", "Basel-Stadt"),
    ("FR", "Freiburg"),
    ("GE", "Genf"),
    ("GL", "Glarus"),
    ("GR", "Graubünden"),
    ("JU", "Jura"),
    ("LU", "Luzern"),
    ("NE", "Neuenburg"),
    ("NW", "Nidwalden"),
    ("OW", "Obwalden"),
    ("SG", "St. Gallen"),
    ("SH", "Schaffhausen"),
    ("SO", "Solothurn"),
    ("SZ", "Schwyz"),
    ("TG", "Thurgau"),
    ("TI", "Tessin"),
    ("UR", "Uri"),
    ("VD", "Waadt"),
    ("VS", "Wallis"),
    ("ZG", "Zug"),
    ("ZH", "Zürich"),
]


class User(AbstractUser):
    """
    Eigenes User-Model statt django.contrib.auth.User, von Anfang an, damit ein
    späterer Wechsel (schmerzhaft, sobald einmal Produktivdaten existieren)
    nicht mehr nötig ist. Vorerst keine zusätzlichen Felder gegenüber
    AbstractUser -- die Erweiterung (z. B. bevorzugte Sprache, Telefon für
    Diensttausch-Benachrichtigungen) kann später ohne Datenmigration der
    Auth-Tabellen ergänzt werden.
    """

    def save(self, *args, **kwargs):
        """
        Erzwingt strukturell, dass ein Account nie gleichzeitig Django-Admin-
        Zugriff (is_staff/is_superuser) UND eine Tenant-Mitgliedschaft
        (Membership) hat -- siehe README, Architektur-Abschnitt "Django Admin
        ist bewusst kein Kundenzugriff": die ModelAdmins sind nicht
        tenant-gescoped, ein solcher Account würde alle Tenants sehen. Nur
        für bereits gespeicherte User relevant (self.pk gesetzt) -- ein noch
        nicht gespeicherter User kann per Definition noch keine Memberships
        haben, die Prüfung wäre dort ohnehin nicht auswertbar.
        """
        if self.pk and (self.is_staff or self.is_superuser) and self.memberships.exists():
            raise ValidationError(
                "Dieser Account hat eine Tenant-Mitgliedschaft (Membership) -- is_staff/"
                "is_superuser (Django-Admin-Zugriff) darf damit nicht kombiniert werden."
            )
        super().save(*args, **kwargs)


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
    flextime_corridor_hours = models.PositiveSmallIntegerField(
        default=20,
        help_text="Nutzer-Feedback (2026-08): 'bei uns gilt Gleitzeit, nur angeordnete Überstunden "
        "werden effektiv abgerechnet'. Gleitzeit-Bandbreite in Stunden: der laufende Gleitzeitsaldo "
        "(scheduling.models.Employee.time_account_summary) darf sich innerhalb dieses Korridors frei "
        "bewegen -- weder Zuschlag noch Auszahlung, ohne dass Planer pro Schicht etwas markieren "
        "müssen. Erst der Anteil, der über den Korridor hinausgeht, wird in der Monatsauswertung "
        "als abrechnungsrelevant vorgeschlagen und muss dort einmalig pro Monat bestätigt werden "
        "(scheduling.models.OvertimeSettlement), bevor er in overtime_surcharge_hours einfliesst.",
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
    occasional_night_work_surcharge_pct = models.PositiveSmallIntegerField(
        default=25,
        help_text="Lohnzuschlag auf Nachtstunden bei GELEGENTLICHER Nachtarbeit (unterhalb der "
        "Regelmässigkeits-Schwelle oben), in Prozent (Art. 17b Abs. 2 ArG: i. d. R. 25%). Anders "
        "als night_work_surcharge_pct (Zeitgutschrift, Art. 17b Abs. 1) ist das Geld statt Zeit -- "
        "die App kennt keinen Stundenlohn und rechnet daher keinen CHF-Betrag aus, nur die "
        "anzuwendende Stundenzahl + diesen Prozentsatz (siehe Employee.night_work_summary).",
    )
    sunday_work_surcharge_pct = models.PositiveSmallIntegerField(
        default=50,
        help_text="Lohnzuschlag auf Sonntagsstunden in Prozent (Art. 19 Abs. 3 ArG i. V. m. Art. 46 "
        "ArGV 1: i. d. R. 50%; für Dauerbetriebe wie viele Gesundheitseinrichtungen können "
        "Ausnahmen gelten -- auf 0 setzen, falls nicht zutreffend).",
    )

    # Feiertagskalender (Arbeitszeitmodell Block 2.7 Punkt 7): Grundlage für
    # die Jahressoll-/Saldo-Berechnung in scheduling.models.Employee
    # (annual_target_hours/time_account_summary) -- Feiertage reduzieren das
    # Soll wie ein arbeitsfreier Tag. Bewusst über die gepflegte Bibliothek
    # "holidays" (vacanza/holidays) statt eigenem Kalender: die Schweiz hat
    # nicht nur pro Kanton, sondern in GR/LU/SZ/SO teils sogar pro Gemeinde
    # unterschiedliche Feiertage (Patrozinien) inkl. beweglicher Feste
    # (Ostern-Formel) -- das selbst zu pflegen wäre erheblicher Aufwand und
    # fehleranfällig. Für die verbleibenden lokalen Sonderfälle siehe
    # TenantHolidayOverride.
    canton = models.CharField(
        max_length=2,
        choices=SWISS_CANTON_CHOICES,
        blank=True,
        help_text="Kanton für den kantonalen Feiertagskalender (holidays-Bibliothek). Leer = kein "
        "automatischer Feiertagsabzug im Arbeitszeitmodell.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def public_holidays_with_names(self, year):
        """
        Feiertagsdaten mit Namen (dict[date, str]) für ein Kalenderjahr:
        kantonaler Kalender (falls `canton` gesetzt) kombiniert mit den
        manuell gepflegten TenantHolidayOverride-Einträgen (ADD/REMOVE) für
        lokale Sonderfälle (z. B. eine Gemeinde-Patrozinie, die die
        Bibliothek nicht kennt, oder ein kantonaler Feiertag, der für diesen
        Betrieb nicht gilt). Ohne gesetzten Kanton nur die Overrides selbst.
        Basis für public_holidays() (nur die Daten, für die Saldo-Berechnung)
        und core.views.TenantHolidaysView (mit Namen, fürs Planblatt/
        Jahresplan).
        """
        base = dict(holidays.Switzerland(subdiv=self.canton, years=year)) if self.canton else {}
        # Explizit über all_objects statt der reverse-Accessor-Default-Manager
        # (TenantScopedManager, ContextVar-gefiltert) -- self ist hier schon
        # eine konkrete Tenant-Instanz, das explizite tenant=self-Filter ist
        # unabhängig vom (evtl. nicht gesetzten) Kontext korrekt, siehe
        # README-Abschnitt zur "explizite Filterung ist die echte Grenze"-
        # Philosophie.
        overrides = TenantHolidayOverride.all_objects.filter(tenant=self, date__year=year)
        for override in overrides:
            if override.kind == TenantHolidayOverride.Kind.ADD:
                base[override.date] = override.name or "Feiertag"
            else:
                base.pop(override.date, None)
        return base

    def public_holidays(self, year):
        """Menge der Feiertagsdaten (set[date]) -- siehe public_holidays_with_names()."""
        return set(self.public_holidays_with_names(year))


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
    # Nutzer-Feedback (2026-08): "Natürlich gibt es in einer Klinik Planer mit
    # unterschiedlichen Zuständigkeiten! Nur Admin darf immer alles sehen."
    # Nur für PLANNER/HR ausgewertet (siehe scheduling.views.
    # _employee_scoped_node_ids) -- ADMIN bleibt davon unabhängig immer
    # uneingeschränkt, EMPLOYEE nutzt weiterhin Employee.nodes/Employment.
    # Leer = keine Einschränkung (bisheriges Verhalten, migrationssicher für
    # alle heute schon bestehenden Planer/HR-Mitgliedschaften) -- erst eine
    # explizite Zuweisung schränkt ein. String-Referenz "scheduling.Node"
    # statt Import, damit core (die "unterste" App) kein Modul-Level-
    # Abhängigkeit zu scheduling bekommt (siehe core.views.MeView-Docstring
    # für dasselbe Prinzip).
    scoped_nodes = models.ManyToManyField(
        "scheduling.Node",
        related_name="scoped_memberships",
        blank=True,
        help_text="Nur für Planer/HR: schränkt die Sichtbarkeit auf diese Stationen (inkl. Unterstationen) "
        "ein. Leer = keine Einschränkung.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "tenant")

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"

    def save(self, *args, **kwargs):
        """
        Kehrseite der Prüfung in User.save(): verhindert auch den umgekehrten
        Weg, einem bestehenden Django-Admin-Account (is_staff/is_superuser)
        nachträglich eine Tenant-Mitgliedschaft zu geben. Bewusst hier in
        save() statt nur in clean(): Memberships werden im ganzen Code
        (Tests, künftige Einladungs-/Onboarding-Flows) über
        `Membership.objects.create(...)` angelegt, was clean() NICHT
        automatisch aufruft -- nur save() wird garantiert immer durchlaufen.
        """
        if self.user.is_staff or self.user.is_superuser:
            raise ValidationError(
                "Kann keine Tenant-Mitgliedschaft für einen Account mit Django-Admin-Zugriff "
                "(is_staff/is_superuser) anlegen -- siehe README, Architektur-Abschnitt."
            )
        super().save(*args, **kwargs)


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


class TenantHolidayOverride(TenantScopedModel):
    """
    Manuelle Ausnahme zum kantonalen Feiertagskalender (Tenant.canton, siehe
    Tenant.public_holidays) für lokale Sonderfälle, die die "holidays"-
    Bibliothek nicht kennt -- v. a. Gemeinde-Patrozinien in GR/LU/SZ/SO. Ein
    Spital hat i. d. R. höchstens 1-2 solche Fälle für seinen festen
    Standort, deshalb bewusst als dünne Ausnahme-Tabelle statt als
    eigenständiges Feiertagsmodell.
    """

    class Kind(models.TextChoices):
        ADD = "add", "Zusätzlicher Feiertag"
        REMOVE = "remove", "Kein Feiertag (Ausnahme vom Kantonskalender)"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="holiday_overrides")
    date = models.DateField()
    name = models.CharField(max_length=100, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.ADD)

    class Meta:
        unique_together = ("tenant", "date")
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} ({self.get_kind_display()})"

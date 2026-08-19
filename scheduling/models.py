import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone
from simple_history.models import HistoricalRecords
from treebeard.mp_tree import MP_Node

from core.models import Tenant, TenantScopedModel

# Nachtarbeitszeitraum nach Art. 10 Abs. 1 / Art. 16 ArG (Grundregel; einzelne
# Branchenverordnungen können abweichen, hier bewusst nicht tenant-konfigurierbar
# gehalten, da es sich um eine gesetzliche Definition und nicht um einen
# betrieblichen Spielraum handelt).
NIGHT_WORK_START = time(23, 0)
NIGHT_WORK_END = time(6, 0)

# Mutterschutz (Art. 35a Abs. 4 ArG): eigene, WEITER gefasste Nachtdefinition
# als die allgemeine (20:00-06:00 statt 23:00-06:00) -- gilt nur für die
# Nachtarbeitsverbot-Prüfung bei Schwangeren, siehe
# ShiftAssignment._maternity_night_hours()/_check_maternity_protection().
MATERNITY_NIGHT_BAN_START = time(20, 0)

# Pausenmindestdauer nach Art. 15 ArG, gestaffelt nach Netto-Arbeitszeit
# (Arbeitszeit *ohne* Pause) an einem Tag. Absteigend sortiert, damit der
# erste zutreffende Schwellenwert in _required_break_minutes() gewinnt.
BREAK_RULES_ART_15 = [
    (9 * 60, 60),  # > 9h Arbeitszeit -> 1h Pause
    (7 * 60, 30),  # > 7h Arbeitszeit -> 30min Pause
    (5.5 * 60, 15),  # > 5.5h Arbeitszeit -> 15min Pause
]

# Jugendschutz (ArGV 5, Verordnung 5 zum Arbeitsgesetz) für unter 18-Jährige:
# erhöhte Mindestruhezeit statt der tenant-konfigurierbaren Erwachsenen-Regel.
# Bewusst nicht tenant-konfigurierbar, da gesetzliche Mindestvorgabe.
YOUTH_MINIMUM_REST_HOURS = 12


def _segment_datetimes(reference_date, segments):
    """
    Wandelt eine geordnete Liste von (start_time, end_time)-Paaren (Segmente
    eines TimeTemplate oder TimeRecord, Block 1.12) in tatsächliche datetimes
    um. Nur ein einzelnes Segment darf über Mitternacht hinausgehen
    (Nachtschicht als ein Block, bisheriges Verhalten) -- ein Tagesüberlauf
    *zwischen* zwei Blöcken ist für die MVP-Version nicht vorgesehen und wird
    von den Aufrufern (TimeTemplateSerializer, TimeRecord.clean) bereits
    zurückgewiesen, bevor diese Funktion mehrsegmentig aufgerufen wird.
    """
    result = []
    cursor = None
    for start_time, end_time in segments:
        start_dt = datetime.combine(reference_date, start_time)
        if cursor is not None and start_dt < cursor:
            start_dt += timedelta(days=1)
        end_dt = datetime.combine(start_dt.date(), end_time)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        result.append((start_dt, end_dt))
        cursor = end_dt
    return result


def _count_workdays(start_date, end_date):
    """
    Anzahl Mo-Fr-Tage zwischen start_date und end_date (inklusive). Für den
    Feriensaldo (vacation_balance(), Block 2.7) -- bewusst weiterhin ohne
    Feiertagsabzug (bekannte, unveränderte Vereinfachung dieser älteren
    Kennzahl). Das neuere Arbeitszeitmodell (annual_target_hours()/
    time_account_summary()) nutzt diese Funktion ebenfalls als reinen
    Wochentag-Zähler, zieht Feiertage/Absenzen aber dort selbst zusätzlich ab
    (siehe Tenant.public_holidays).
    """
    total_days = (end_date - start_date).days + 1
    full_weeks, remainder = divmod(total_days, 7)
    count = full_weeks * 5
    for i in range(remainder):
        if (start_date + timedelta(days=full_weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


def _default_employment_start_date():
    """Default für Employee.employment_start_date: 1. Januar des laufenden Jahres."""
    return date(timezone.localdate().year, 1, 1)


# Gerichtliche Skalen zur Lohnfortzahlungsdauer bei Krankheit (Art. 324a OR,
# Employee.sick_pay_summary()). Das Gesetz selbst nennt nur "eine beschränkte
# Zeit" -- diese drei Skalen sind gängige, in der Praxis verbreitete
# Konkretisierungen der Gerichte, aber NICHT im Gesetz kodifiziert und nicht
# schweizweit einheitlich. Die hier hinterlegten Wochenwerte sind gängige
# Näherungen -- vor Produktivnutzung mit einer Rechts-/Treuhandstelle
# verifizieren (siehe auch Tenant.sick_pay_scale help_text).
def _basel_scale_weeks(service_years):
    """Basler Skala: 3 Wochen im 1. Dienstjahr, danach gestaffelt nach Dienstjahren."""
    table = {1: 3, 2: 9, 3: 9, 4: 13, 5: 13}
    if service_years in table:
        return table[service_years]
    if service_years < 1:
        return 0
    return 13 + (service_years - 5) * 4


def _bern_scale_weeks(service_years):
    """Berner Skala: 3 Wochen im 1. Dienstjahr, danach 4 Wochen je weiterem Dienstjahr."""
    if service_years < 1:
        return 0
    if service_years == 1:
        return 3
    return service_years * 4


def _zurich_scale_weeks(service_years):
    """Zürcher Skala: 3 Wochen im 1. Dienstjahr, danach gestaffelt nach Dienstjahren."""
    table = {1: 3, 2: 8, 3: 9, 4: 10, 5: 11, 6: 12, 7: 13, 8: 14, 9: 15, 10: 16}
    if service_years in table:
        return table[service_years]
    if service_years < 1:
        return 0
    return 16 + (service_years - 10)


_SICK_PAY_SCALE_FUNCTIONS = {
    "basel": _basel_scale_weeks,
    "bern": _bern_scale_weeks,
    "zuerich": _zurich_scale_weeks,
}


class Node(MP_Node, TenantScopedModel):
    """
    Organisationsknoten (Standort, Abteilung, Station, ...), beliebig
    verschachtelbar. Nutzt django-treebeard (Materialized Path) für
    effiziente Baumabfragen statt eines selbstgebauten parent-Felds.

    Achtung Mandantentrennung: treebeard verwaltet EINEN einzigen, global
    geteilten Namensraum für alle Wurzelknoten (path-Feld, DB-seitig
    unique=True über die gesamte Tabelle, nicht pro Tenant) -- ohne
    Gegenmassnahme wären alle Tenants buchstäblich Geschwister im selben
    Baum. Deshalb bekommt jeder Tenant genau einen unsichtbaren
    Wurzelknoten (is_forest_root=True, siehe get_or_create_forest_root()),
    und JEDE echte Station wird als dessen Kind angelegt (add_child()),
    NIE mehr direkt als treebeard-Wurzel (add_root()) -- Pfad-Vergabe
    findet dadurch nur noch unter einem tenant-exklusiven Elternknoten
    statt, nie mehr auf einer mit anderen Tenants geteilten Ebene.
    """

    name = models.CharField(max_length=200)
    cost_center = models.CharField(
        max_length=50,
        blank=True,
        help_text="Kostenstelle für den Lohn-Export (Block 2 Punkt 30/31). Leer lassen, um von der "
        "übergeordneten Station zu erben (siehe effective_cost_center()) -- ein Team ohne eigene "
        "Kostenstelle übernimmt so automatisch die seiner Station.",
    )
    is_forest_root = models.BooleanField(
        default=False,
        help_text="Interner, für Endnutzer unsichtbarer Wurzelknoten -- genau einer pro Tenant "
        "(siehe get_or_create_forest_root()). Echte Stationen werden nie mehr direkt als "
        "treebeard-Wurzel angelegt, sondern immer als Kind dieses Knotens -- verhindert, dass "
        "alle Tenants sich einen einzigen, global geteilten Namensraum auf Wurzelebene teilen.",
    )

    node_order_by = ["name"]

    class Meta:
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_forest_root=True),
                name="unique_forest_root_per_tenant",
            ),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def get_or_create_forest_root(cls, tenant):
        """
        Holt den unsichtbaren Wurzelknoten des Tenants oder legt ihn beim
        ersten Zugriff an. Die EINMALIGE add_root()-Erzeugung liegt noch auf
        der geteilten treebeard-Wurzelebene (siehe Klassen-Docstring) und
        kann deshalb theoretisch mit dem Wurzelknoten eines fremden Tenants
        kollidieren (IntegrityError durch node_order_by-Sortierung) -- aber
        nur EINMAL pro Tenant-Lebenszeit, nicht mehr bei jeder Stations-/
        Team-Anlage wie zuvor. Ein erneutes Nachschlagen nach IntegrityError
        deckt sowohl diesen seltenen Kollisionsfall als auch eine echte Race
        Condition zwischen zwei gleichzeitigen Requests desselben Tenants ab
        -- die UniqueConstraint oben verhindert in letzterem Fall
        zuverlässig einen zweiten Wurzelknoten, exakt das gleiche
        Retry-Muster wie core.onboarding.unique_tenant_slug.
        """
        existing = cls.all_objects.filter(tenant=tenant, is_forest_root=True).first()
        if existing is not None:
            return existing
        for attempt in range(2):
            try:
                with transaction.atomic():
                    return cls.add_root(name="Wurzel", tenant=tenant, is_forest_root=True)
            except IntegrityError:
                existing = cls.all_objects.filter(tenant=tenant, is_forest_root=True).first()
                if existing is not None:
                    return existing
                if attempt == 1:
                    raise

    def effective_cost_center(self):
        """
        Eigene Kostenstelle, sonst von der nächstgelegenen Vorfahren-Station
        geerbt (z. B. übernimmt ein Team ohne eigene Kostenstelle die seiner
        Station) -- None, wenn nirgends in der Kette konfiguriert.
        get_ancestors() liefert Wurzel zuerst, `reversed()` dreht das um,
        damit der NÄCHSTE Vorfahre zuerst geprüft wird.
        """
        if self.cost_center:
            return self.cost_center
        for ancestor in reversed(self.get_ancestors()):
            if ancestor.cost_center:
                return ancestor.cost_center
        return None


class Skill(TenantScopedModel):
    """Qualifikation/Kompetenz, z. B. 'dipl. Pflegefachperson', 'Nachtdienst-berechtigt'."""

    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ("tenant", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Employee(TenantScopedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        null=True,
        blank=True,
        help_text="Verknüpfung zum Login-Account, falls der Mitarbeiter Self-Service nutzt.",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    birth_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional. Nötig für den Jugendschutz (ArGV 5) bei Lernenden/Auszubildenden "
        "unter 18 Jahren -- ohne Angabe wird der Mitarbeiter als volljährig behandelt.",
    )
    employment_pct = models.PositiveSmallIntegerField(help_text="Pensum in %, z. B. 80")
    # Nutzer-Feedback (2026-08, Automatisierte Planung mit Auffülldienst):
    # employment_pct allein sagt nur "wie viel", nicht "an welchen
    # Wochentagen" -- für die Automatik (siehe scheduling.planning) reicht
    # das nicht, um z. B. eine 60%-Kraft nur an ihren tatsächlich
    # vereinbarten Tagen einzuplanen statt einfach irgendwelche Tage bis zum
    # Wochenlimit aufzufüllen. Dasselbe Feld deckt auch ein Team ohne
    # Wochenend-Betrieb ab (dort tragen dann auch 100%-Kräfte Sa+So ein) --
    # bewusst kein separates "Betriebstage"-Konzept auf Node/TimeTemplate,
    # um das Modell nicht zu verdoppeln. Wirkt NUR in der automatisierten
    # Planung (harter Ausschluss, siehe generate_draft_plan) -- manuelles
    # Stempeln im Planblatt bleibt an diesen Tagen weiterhin möglich, falls
    # im Einzelfall doch nötig.
    fixed_weekdays_off = models.JSONField(
        default=list,
        blank=True,
        help_text="Wochentage, die fest arbeitsfrei sind (0=Montag ... 6=Sonntag, wie "
        "date.weekday()). Für Teilzeit das individuelle Wochenmuster, für ein Team ohne "
        "Wochenend-Betrieb z. B. [5, 6] auch bei Vollzeitkräften. Leer = keine festen freien Tage "
        "(übliches Vollzeit-Muster, nur der gesetzliche Wochenruhetag gilt).",
    )
    employment_start_date = models.DateField(
        default=_default_employment_start_date,
        help_text="Eintrittsdatum -- Startpunkt für das Jahressoll im Arbeitszeitmodell "
        "(annual_target_hours()/time_account_summary()): Wochen vor diesem Datum zählen weder "
        "als Soll noch als Ist, auch wenn sie im laufenden Kalenderjahr liegen (z. B. bei "
        "unterjährigem Eintritt).",
    )
    nodes = models.ManyToManyField(Node, related_name="employees", blank=True)
    skills = models.ManyToManyField(Skill, related_name="employees", blank=True)
    is_active = models.BooleanField(default=True)
    termination_date = models.DateField(
        null=True,
        blank=True,
        help_text="Austrittsdatum. Sobald dieses Datum erreicht ist, deaktiviert der "
        "management command deactivate_expired_employees automatisch is_active und -- falls "
        "vorhanden -- den Login-Zugang (user.is_active), siehe EmployeeViewSet.deactivate für die "
        "sofortige, manuelle Variante desselben Vorgangs.",
    )

    # Personalkategorie-Override (MVP-Fahrplan Block 1.14): innerhalb eines
    # Spitals gelten je nach Funktion oft unterschiedliche Wochenstunden-Werte
    # (z. B. Ärzteschaft 50h, Büropersonal 42h) -- ein einzelner Tenant-Wert
    # reicht dafür nicht. Beide Felder sind optional und überschreiben den
    # jeweiligen Tenant-Default nur für diesen Mitarbeiter, wenn gesetzt (None
    # = Tenant-Wert gilt), siehe ShiftAssignment._check_maximum_weekly_hours
    # und weekly_hours_summary weiter unten.
    maximum_weekly_hours = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Überschreibt Tenant.maximum_weekly_hours (gesetzliche/GAV-Höchstgrenze, Art. 9 "
        "ArG) für diesen Mitarbeiter. Leer lassen, um den Tenant-Wert zu übernehmen.",
    )
    standard_weekly_hours = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Überschreibt Tenant.standard_weekly_hours (Normalarbeitszeit/Soll für die "
        "Überzeitberechnung, Art. 13 ArG) für diesen Mitarbeiter. Leer lassen, um den Tenant-Wert "
        "zu übernehmen.",
    )

    # Saldo-Übersicht (MVP-Fahrplan Block 2.7), siehe overtime_balance()/
    # vacation_balance() weiter unten.
    overtime_balance_carryover_hours = models.FloatField(
        default=0,
        help_text="Überstunden-Saldo beim Systemstart (z. B. aus der vorherigen Zeiterfassung "
        "übernommen). Wird zum seit der ersten erfassten Schicht berechneten Saldo addiert.",
    )
    vacation_days_per_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Überschreibt Tenant.default_vacation_days_per_year (Ferienanspruch in "
        "Arbeitstagen) für diesen Mitarbeiter. Leer lassen, um den Tenant-Wert zu übernehmen.",
    )

    # Nachtarbeit (MVP-Fahrplan Block 1.5, Art. 17c ArG): Pflicht zur
    # arbeitsmedizinischen Untersuchung bei regelmässiger Nachtarbeit, siehe
    # night_work_medical_exam_due() weiter unten.
    last_night_work_medical_exam_date = models.DateField(
        null=True,
        blank=True,
        help_text="Datum der letzten arbeitsmedizinischen Untersuchung (Art. 17c ArG) -- Pflicht "
        "bei regelmässiger Nachtarbeit, alle 2 Jahre bzw. jährlich ab 45 Jahren (Art. 45 ArGV 1). "
        "Nur relevant, wenn night_work_summary() regelmässige Nachtarbeit erkennt.",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def clean(self):
        if self.fixed_weekdays_off:
            if not isinstance(self.fixed_weekdays_off, list) or any(
                not isinstance(d, int) or not (0 <= d <= 6) for d in self.fixed_weekdays_off
            ):
                raise ValidationError(
                    "fixed_weekdays_off muss eine Liste von Wochentagen 0 (Montag) bis 6 (Sonntag) sein."
                )
            if len(set(self.fixed_weekdays_off)) == 7:
                raise ValidationError("fixed_weekdays_off darf nicht alle sieben Wochentage umfassen.")

    def has_fixed_day_off(self, day):
        """True, wenn `day` laut Wochenmuster fest arbeitsfrei ist (siehe fixed_weekdays_off)."""
        return day.weekday() in self.fixed_weekdays_off

    def _age_on(self, reference_date):
        """Alter in vollen Jahren am reference_date, oder None ohne bekanntes Geburtsdatum."""
        if not self.birth_date:
            return None
        return reference_date.year - self.birth_date.year - (
            (reference_date.month, reference_date.day) < (self.birth_date.month, self.birth_date.day)
        )

    def is_minor_on(self, reference_date):
        """True, wenn der Mitarbeiter am reference_date unter 18 Jahre alt ist."""
        age = self._age_on(reference_date)
        return age is not None and age < 18

    def _service_years_on(self, reference_date):
        """
        Vollendete Dienstjahre am reference_date, analog zu _age_on() aber
        bezogen auf employment_start_date statt birth_date (für die
        Skala-Einstufung in sick_pay_summary()). Kein None-Guard nötig --
        anders als birth_date hat employment_start_date immer einen Default
        (_default_employment_start_date).
        """
        start = self.employment_start_date
        return reference_date.year - start.year - (
            (reference_date.month, reference_date.day) < (start.month, start.day)
        )

    def _current_service_year_window(self, reference_date):
        """
        (window_start, window_end, service_year_number) des laufenden
        Dienstjahres (12-Monats-Zyklus ab dem Jahrestag von
        employment_start_date, nicht Kalenderjahr) für reference_date --
        Grundlage für den Anspruchs-/Verbrauchszeitraum in
        sick_pay_summary(). service_year_number ist 1-basiert (erstes
        Dienstjahr = 1, wie in den Gerichtsskalen üblich).
        """
        start = self.employment_start_date
        anniversary_year = reference_date.year
        if (reference_date.month, reference_date.day) < (start.month, start.day):
            anniversary_year -= 1
        try:
            window_start = start.replace(year=anniversary_year)
        except ValueError:
            # 29. Februar ohne Schaltjahr im Ziel-Jahr.
            window_start = start.replace(year=anniversary_year, day=28)
        try:
            window_end = start.replace(year=anniversary_year + 1) - timedelta(days=1)
        except ValueError:
            window_end = start.replace(year=anniversary_year + 1, day=28) - timedelta(days=1)
        service_year_number = anniversary_year - start.year + 1
        return window_start, window_end, service_year_number

    def is_maternity_protected_on(self, reference_date):
        """
        Mutterschutz-Status (None/"night_ban"/"full_ban"/"consent_required")
        an reference_date, über alle erfassten Pregnancy-Fälle hinweg (analog
        is_minor_on(), aber mit differenziertem Rückgabewert statt nur
        True/False, weil die drei Fristen aus Art. 35a ArG unterschiedlich
        hart durchgesetzt werden -- siehe Pregnancy.protection_status_on()
        und ShiftAssignment._check_maternity_protection()).
        """
        for pregnancy in self.pregnancies.all():
            status = pregnancy.protection_status_on(reference_date)
            if status:
                return status
        return None

    def weekly_hours_summary(self, reference_date):
        """
        Soll/Ist-Vergleich für die Kalenderwoche (Montag-Sonntag) von
        reference_date -- Basis der Überzeitberechnung (Art. 13 ArG,
        MVP-Fahrplan Block 1.11). Bewusst nicht Teil der Regel-Engine
        (ShiftAssignment.clean): eine Auswertungs-/Lohnfrage, keine Ablehnung
        einer Zuweisung. Ist-Stunden kommen pro Schicht aus TimeRecord,
        sobald erfasst, sonst aus der Planung (ShiftAssignment._shift_hours)
        als bester verfügbarer Schätzwert.
        """
        week_start = reference_date - timedelta(days=reference_date.weekday())
        week_end = week_start + timedelta(days=6)

        # Spezialitäten (TimeTemplate.category == "special", z. B.
        # Pikettdienst) sind rein informativ und zählen nicht zu den
        # Stunden -- siehe skip_for_specialties-Decorator.
        assignments = (
            ShiftAssignment.all_objects.filter(employee=self, date__range=[week_start, week_end])
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template", "time_record")
        )

        ist_hours = 0.0
        sunday_hours = 0.0
        is_provisional = False
        for assignment in assignments:
            time_record = getattr(assignment, "time_record", None)
            if time_record is not None:
                ist_hours += time_record.actual_hours
                if time_record.status != TimeRecord.Status.CONFIRMED:
                    is_provisional = True
            else:
                ist_hours += ShiftAssignment._shift_hours(assignment.date, assignment.template)
                is_provisional = True
            if assignment.is_sunday:
                # Sonntagszuschlag (Art. 19 Abs. 3 ArG, Block 1.6): bewusst auf
                # den geplanten Stunden berechnet, nicht auf der Ist-Zeit --
                # analog zu night_hours/is_sunday selbst, die ebenfalls den
                # Plan auswerten, nicht die Zeiterfassung.
                sunday_hours += ShiftAssignment._shift_hours(assignment.date, assignment.template)

        standard_weekly_hours = self.standard_weekly_hours or self.tenant.standard_weekly_hours
        soll_hours = round(self.employment_pct / 100 * standard_weekly_hours, 2)
        ist_hours = round(ist_hours, 2)
        overtime_hours = round(max(0.0, ist_hours - soll_hours), 2)
        surcharge_hours = round(overtime_hours * self.tenant.overtime_surcharge_pct / 100, 2)
        sunday_hours = round(sunday_hours, 2)
        sunday_surcharge_hours = round(sunday_hours * self.tenant.sunday_work_surcharge_pct / 100, 2)

        return {
            "week_start": week_start,
            "week_end": week_end,
            "soll_hours": soll_hours,
            "ist_hours": ist_hours,
            "overtime_hours": overtime_hours,
            "surcharge_hours": surcharge_hours,
            "sunday_hours": sunday_hours,
            "sunday_surcharge_hours": sunday_surcharge_hours,
            # True, sobald mindestens eine Schicht der Woche nicht auf einer
            # geprüften (CONFIRMED) Zeiterfassung beruht -- entweder, weil noch
            # keine Ist-Zeit erfasst wurde (Schätzung aus der Planung), oder
            # weil die Erfassung erst SUBMITTED ist. Rechnerisch zählt sie
            # trotzdem schon mit, siehe overtime_summary().
            "is_provisional": is_provisional,
        }

    def sunday_replacement_rest_missing(self, sunday_date):
        """
        Vereinfachte Kontrolle des Ersatzruhetags (Art. 20 ArG) für eine am
        sunday_date geleistete Sonntagsschicht (Block 1.6): prüft, ob im
        14-Tage-Fenster ab sunday_date (Rest der Sonntagswoche + gesamte
        Folgewoche) mindestens zwei Tage ohne jede Zuweisung liegen -- einer
        für den ohnehin vorgeschriebenen wöchentlichen freien Tag (Art. 21
        ArG, siehe ShiftAssignment._check_weekly_rest_day), einer als
        zusätzlicher Ersatzruhetag für die Sonntagsarbeit. Prüft bewusst
        *nicht* die genaue gesetzliche Anforderung, dass der Ersatzruhetag
        unmittelbar an eine Tagesruhezeit anschliessen und mit ihr zusammen
        mindestens 35 zusammenhängende Stunden ergeben muss (Art. 20 Abs. 2
        ArG) -- das wäre eine deutlich aufwändigere Prüfung. Rein informativ,
        blockiert nichts (siehe ShiftAssignment.sunday_replacement_rest_missing).
        """
        window_end = sunday_date + timedelta(days=13)
        # Spezialitäten zählen nicht als Arbeitstag, konsistent mit
        # ShiftAssignment._check_weekly_rest_day.
        worked_dates = set(
            ShiftAssignment.all_objects.filter(employee=self, date__range=[sunday_date, window_end])
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .values_list("date", flat=True)
        )
        free_days = sum(
            1 for offset in range(14) if (sunday_date + timedelta(days=offset)) not in worked_dates
        )
        return free_days < 2

    def night_work_summary(self, year=None):
        """
        Nachtarbeit-Auswertung für ein Kalenderjahr (MVP-Fahrplan Block 1.5):
        Anzahl Nächte mit Nachtarbeit, ob das als "regelmässig" gilt (ArGV 1
        Art. 31, Tenant.night_work_regular_threshold_nights), die daraus
        resultierende Zeitgutschrift (Art. 17b Abs. 1 ArG) sowie zwei
        Hinweise für Admin/Planer: fehlende Bewilligungsbestätigung und
        fällige arbeitsmedizinische Untersuchung (Art. 17c ArG). Rein
        informativ wie night_hours selbst -- blockiert keine Zuweisung, ist
        kein Rechtsrat.

        Block 1.17 (Compliance-Audit 2026-08): wer die Regelmässigkeits-
        Schwelle NICHT erreicht, hat trotzdem Anspruch auf einen 25 %
        Lohnzuschlag (Art. 17b Abs. 2 ArG) -- anders als die Zeitgutschrift
        oben ist das Geld statt Zeit, die App kennt keinen Stundenlohn und
        kann daher keinen CHF-Betrag ausrechnen. `occasional_night_hours`/
        `occasional_night_surcharge_pct` liefern deshalb nur die
        anzuwendende Stundenzahl + den Prozentsatz als Rohinput für den
        Lohn-Export (Block 2 Punkt 30/31), nicht das Produkt daraus.
        Regelmässig und gelegentlich schliessen sich gegenseitig aus:
        `occasional_night_hours` ist nur bei `not is_regular` > 0.
        """
        year = year or timezone.localdate().year
        assignments = ShiftAssignment.all_objects.filter(employee=self, date__year=year).select_related(
            "template"
        )

        total_night_hours = 0.0
        night_dates = set()
        for assignment in assignments:
            hours = assignment.night_hours
            if hours > 0:
                total_night_hours += hours
                night_dates.add(assignment.date)

        nights_count = len(night_dates)
        is_regular = nights_count >= self.tenant.night_work_regular_threshold_nights
        surcharge_hours = (
            round(total_night_hours * self.tenant.night_work_surcharge_pct / 100, 2) if is_regular else 0.0
        )
        rounded_night_hours = round(total_night_hours, 2)

        return {
            "year": year,
            "nights_count": nights_count,
            "night_hours": rounded_night_hours,
            "is_regular": is_regular,
            "surcharge_hours": surcharge_hours,
            "occasional_night_hours": 0.0 if is_regular else rounded_night_hours,
            "occasional_night_surcharge_pct": self.tenant.occasional_night_work_surcharge_pct,
            "permit_warning": is_regular and not self.tenant.night_work_permit_confirmed,
            "medical_exam_due": self.night_work_medical_exam_due(is_regular=is_regular),
        }

    def night_work_medical_exam_due(self, as_of_date=None, is_regular=None):
        """
        Arbeitsmedizinische Untersuchungspflicht (Art. 17c ArG, Art. 45 ArGV
        1): alle 2 Jahre, ab 45 Jahren jährlich. Nur relevant bei
        regelmässiger Nachtarbeit; ohne bisherige Untersuchung
        (last_night_work_medical_exam_date leer) sofort fällig.
        """
        as_of_date = as_of_date or timezone.localdate()
        if is_regular is None:
            is_regular = self.night_work_summary(as_of_date.year)["is_regular"]
        if not is_regular:
            return False
        if not self.last_night_work_medical_exam_date:
            return True
        age = self._age_on(as_of_date)
        interval_years = 1 if (age is not None and age >= 45) else 2
        next_due = self.last_night_work_medical_exam_date + timedelta(days=interval_years * 365)
        return as_of_date >= next_due

    def _fairness_points(self, window_start, window_end):
        """
        Rohbausteine für fairness_summary() -- ausgelagert, damit sie sowohl
        für `self` als auch für jede Kollegin/jeden Kollegen einzeln
        aufgerufen werden können, ohne dass dabei rekursiv wieder deren
        eigener Team-Durchschnitt mitberechnet wird (das würde
        fairness_summary() selbst tun).

        Nutzer-Feedback (2026-08, "wie würdest du das Bonussystem am
        intuitivsten aufbauen?"): eine Zuweisung, für die am selben Tag ein
        `ShiftPreference` vom Typ `wunschdienst` existiert, zählt NICHT als
        Belastung -- wer sich die Schicht gewünscht hat, wurde dadurch nicht
        unfair behandelt.
        """
        assignments = ShiftAssignment.all_objects.filter(
            employee=self, date__gte=window_start, date__lte=window_end
        ).select_related("template")
        wished_dates = set(
            ShiftPreference.all_objects.filter(
                employee=self,
                type=ShiftPreference.Type.SHIFT,
                date__gte=window_start,
                date__lte=window_end,
            ).values_list("date", flat=True)
        )
        sunday_hours = 0.0
        night_hours = 0.0
        for assignment in assignments:
            if assignment.date in wished_dates:
                continue
            if assignment.is_sunday:
                sunday_hours += ShiftAssignment._shift_hours(assignment.date, assignment.template)
            night_hours += assignment.night_hours
        sunday_hours = round(sunday_hours, 2)
        night_hours = round(night_hours, 2)
        sunday_points = round(sunday_hours * self.tenant.sunday_shift_bonus_points_per_hour, 2)
        night_points = round(night_hours * self.tenant.night_shift_bonus_points_per_hour, 2)
        return sunday_hours, night_hours, sunday_points, night_points

    def fairness_summary(self, reference_date=None):
        """
        Fairness-Punkte für unpopuläre Schichten (Sonntag, Nacht) über ein
        gleitendes 365-Tage-Fenster (MVP-Fahrplan Block 2, Punkt 20).
        Bewusst NICHT das Kalenderjahr-Muster von night_work_summary()
        (harter Reset am 1. Januar würde die über den Jahreswechsel hinweg
        spürbare Belastung verschleiern) und NICHT das Dienstjahr-Muster der
        Lohnfortzahlung (an employment_start_date gekoppelt -- für Fairness
        sachlich nicht begründbar). Stattdessen täglich gleitend: die
        letzten 365 Tage ab `reference_date` (Default heute), kein fixer
        Reset-Zeitpunkt.

        "Team" = alle aktiven Mitarbeitenden, die mindestens eine Station
        mit dieser Person teilen (Employee.nodes-Schnittmenge, dieselbe
        Abgrenzung wie bei effective_cost_center(), Punkt 31) -- inkl. der
        Person selbst. `team_average_points` ist None, falls die Person
        keiner Station zugeordnet ist.

        Nutzer-Feedback (2026-08, "wird das Pensum berücksichtigt?"): ein
        roher Punkte-Vergleich benachteiligt Teilzeit-Mitarbeitende
        systematisch -- wer 40% arbeitet, hat schlicht weniger Gelegenheit,
        Sonntags-/Nachtschichten zu übernehmen, unabhängig davon, ob die
        Verteilung untereinander fair ist. Der Team-Durchschnitt wird
        deshalb auf Vollzeit-Basis (100%) gebildet und danach auf das
        eigene Pensum zurückgerechnet -- dadurch bleibt er direkt mit
        `points` vergleichbar (gleiche Einheit), ohne dass die Badge eine
        zusätzliche, erklärungsbedürftige Kennzahl anzeigen muss.
        Mitarbeitende mit employment_pct=0 (Dateninkonsistenz, Feld erlaubt
        es technisch) werden von der Normalisierung ausgeschlossen statt
        eine Division durch 0 zu riskieren.

        Rein informativ wie night_work_summary() selbst -- kein Bestandteil
        der Regel-Engine, kein Lohnbestandteil.
        """
        reference_date = reference_date or timezone.localdate()
        window_start = reference_date - timedelta(days=365)
        sunday_hours, night_hours, sunday_points, night_points = self._fairness_points(
            window_start, reference_date
        )
        points = round(sunday_points + night_points, 2)

        colleagues = list(
            Employee.objects.filter(tenant=self.tenant, is_active=True, nodes__in=self.nodes.all()).distinct()
        )
        colleague_points = Employee._bulk_fairness_points(colleagues, window_start, reference_date)
        colleague_points_per_fte = [
            colleague_points[c.id] / (c.employment_pct / 100) for c in colleagues if c.employment_pct > 0
        ]
        team_average_points = (
            round(
                (sum(colleague_points_per_fte) / len(colleague_points_per_fte)) * (self.employment_pct / 100), 2
            )
            if colleague_points_per_fte and self.employment_pct > 0
            else None
        )

        return {
            "window_start": window_start,
            "window_end": reference_date,
            "sunday_hours": sunday_hours,
            "sunday_points": sunday_points,
            "night_hours": night_hours,
            "night_points": night_points,
            "points": points,
            "team_average_points": team_average_points,
        }

    @staticmethod
    def _bulk_fairness_points(employees, window_start, window_end):
        """
        Wie _fairness_points(), aber für mehrere Mitarbeitende auf einmal --
        Performance-Fix für fairness_summary(): die vorherige Variante hat
        pro Team-Mitglied zwei eigene Queries ausgelöst (O(Teamgrösse)
        Queries bei JEDEM einzelnen fairness_summary()-Aufruf, multipliziert
        mit der Anzahl Badges auf der Mitarbeitendenliste -- spürbar
        langsam ab ca. 15-20 Mitarbeitenden). Stattdessen je eine Query für
        alle Zuweisungen/Wunschdienste der ganzen Gruppe, danach in Python
        pro Mitarbeiter aggregiert.
        """
        if not employees:
            return {}
        employee_ids = [e.id for e in employees]
        tenant = employees[0].tenant
        sunday_rate = tenant.sunday_shift_bonus_points_per_hour
        night_rate = tenant.night_shift_bonus_points_per_hour

        wished_by_employee = defaultdict(set)
        for employee_id, wished_date in ShiftPreference.all_objects.filter(
            employee_id__in=employee_ids,
            type=ShiftPreference.Type.SHIFT,
            date__gte=window_start,
            date__lte=window_end,
        ).values_list("employee_id", "date"):
            wished_by_employee[employee_id].add(wished_date)

        sunday_hours_by_employee = defaultdict(float)
        night_hours_by_employee = defaultdict(float)
        assignments = ShiftAssignment.all_objects.filter(
            employee_id__in=employee_ids, date__gte=window_start, date__lte=window_end
        ).select_related("template")
        for assignment in assignments:
            if assignment.date in wished_by_employee[assignment.employee_id]:
                continue
            if assignment.is_sunday:
                sunday_hours_by_employee[assignment.employee_id] += ShiftAssignment._shift_hours(
                    assignment.date, assignment.template
                )
            night_hours_by_employee[assignment.employee_id] += assignment.night_hours

        return {
            e.id: round(
                sunday_hours_by_employee[e.id] * sunday_rate + night_hours_by_employee[e.id] * night_rate, 2
            )
            for e in employees
        }

    def _effective_weekly_hours(self):
        """Wochensoll bei 100% Pensum: Employee-Override oder Tenant-Default."""
        return self.standard_weekly_hours or self.tenant.standard_weekly_hours

    def _effective_vacation_days(self):
        """Ferienanspruch in Arbeitstagen/Jahr: Employee-Override oder Tenant-Default."""
        return self.vacation_days_per_year or self.tenant.default_vacation_days_per_year

    def _daily_target_hours(self):
        """Tagessoll: Wochensoll * Pensum% / 5 Arbeitstage (Mo-Fr-Konvention, wie _count_workdays)."""
        weekly = self._effective_weekly_hours() * self.employment_pct / 100
        return weekly / 5

    def _approved_absence_day_weights(self, start_date, end_date):
        """
        Datum -> Anteil des Tages [0.5, 1.0], der durch mindestens eine
        genehmigte Absenz (Ferien/Krankheit/Sonstiges, jeder Typ) als
        arbeitsfrei gilt -- 1.0 für eine ganztägige, 0.5 für eine
        Halbtags-Absenz (day_portion morning/afternoon). Basis für die
        Soll-Neutralität UND den Ist-Ausschluss in time_account_summary()/
        monthly_summary().

        Nutzer-Feedback (2026-08): "wenn ich einen halben Tag Ferien eingebe,
        stimmt die Stunden-Rechnung dann noch?" -- vorher (_approved_absence_
        dates(), reine Datumsmenge ohne day_portion) wurde JEDE Absenz, auch
        eine Halbtags-Absenz, wie ein ganzer freier Tag behandelt: sowohl der
        komplette Tagessoll als auch die komplette (weiterhin bestehende,
        siehe Absence._shift_extends_into_other_half()) Dienst-Zuweisung
        dieses Tages fielen aus der Rechnung -- bei einer Halbtags-Absenz ein
        um einen halben Tag zu grosser Ausschlag (empirisch: -8.4h statt der
        erwarteten -4.2h bei einem 8.4h-Tagessoll). Mit Gewichtung 0.5 wird
        pro Halbtags-Absenz nur noch die Hälfte des Tagessolls excused UND
        nur die Hälfte der (mangels TimeRecord geschätzten) Dienststunden aus
        dem Ist ausgeschlossen -- konsistent mit vacation_balance(), die
        bereits 0.5 statt 1 Ferientag zieht.

        Überschneiden sich zwei Absenzen (sollte laut Absence.clean() nicht
        vorkommen, aber defensiv), wird der Tag höchstens als 1.0 (ganz
        excused) gezählt.
        """
        if start_date > end_date:
            return {}
        absences = Absence.all_objects.filter(
            employee=self,
            status=Absence.Status.APPROVED,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        weights = {}
        for absence in absences:
            day_weight = 1.0 if absence.day_portion == Absence.DayPortion.FULL else 0.5
            cursor = max(absence.start_date, start_date)
            stop = min(absence.end_date, end_date)
            while cursor <= stop:
                weights[cursor] = min(1.0, weights.get(cursor, 0.0) + day_weight)
                cursor += timedelta(days=1)
        return weights

    def _monthly_absence_day_breakdown(self, start_date, end_date):
        """
        AbsenceType-ID -> genehmigte Absenztage im Zeitraum (Kalendertage,
        Halbtags-Absenzen als 0.5 gewichtet) -- für den Lohn-Export
        (Block 2 Punkt 30/31, Employee.payroll_raw_lines()). Anders als
        _approved_absence_day_weights() (soll-neutrale Tagesgewichtung
        über ALLE Typen hinweg, überlappende Absenzen auf max. 1.0 gekappt)
        wird hier PRO Absenztyp gezählt, weil ein Ferientag und ein
        Krankheitstag payroll-technisch unterschiedliche Lohnarten sind --
        eine Überschneidung zwischen zwei VERSCHIEDENEN Typen zählt hier
        also bewusst bei beiden mit, statt gekappt zu werden.

        Kalendertage statt Werktage (wie sick_pay_summary()), nicht Mo-Fr-
        Werktage wie vacation_balance() -- die Lohn-Rohdaten sollen die
        tatsächlich beanspruchten Tage zeigen.
        """
        if start_date > end_date:
            return {}
        absences = Absence.all_objects.filter(
            employee=self,
            status=Absence.Status.APPROVED,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        totals = {}
        for absence in absences:
            weight = 1.0 if absence.day_portion == Absence.DayPortion.FULL else 0.5
            days = (min(absence.end_date, end_date) - max(absence.start_date, start_date)).days + 1
            totals[absence.type_id] = totals.get(absence.type_id, 0.0) + days * weight
        return totals

    def _public_holiday_workdays(self, start_date, end_date):
        """Menge der Feiertage (Tenant.public_holidays) in [start_date, end_date], die auf Mo-Fr fallen."""
        if start_date > end_date:
            return set()
        result = set()
        for year in range(start_date.year, end_date.year + 1):
            for day in self.tenant.public_holidays(year):
                if start_date <= day <= end_date and day.weekday() < 5:
                    result.add(day)
        return result

    def annual_target_hours(self, year=None):
        """
        Jahressoll (Arbeitszeitmodell, README Block 2.7 Punkt 7): fixes
        Jahresziel in Stunden -- Wochensoll * Pensum% über alle Mo-Fr-
        Arbeitstage des Jahres ab employment_start_date, abzüglich Feiertage
        auf Arbeitstage und dem vollen Ferienanspruch in Stunden. Der
        Ferienanspruch wird bewusst NICHT anteilig für unterjährigen Eintritt
        gekürzt (bekannte Vereinfachung) -- unabhängig davon, WANN im Jahr die
        Ferien tatsächlich bezogen werden; time_account_summary() zieht nur
        die bereits VERGANGENEN Ferientage vom laufenden Saldo ab.
        """
        year = year or timezone.localdate().year
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        period_start = max(year_start, self.employment_start_date)
        if period_start > year_end:
            return 0.0
        workdays = _count_workdays(period_start, year_end)
        holiday_days = len(self._public_holiday_workdays(period_start, year_end))
        daily = self._daily_target_hours()
        vacation_hours = self._effective_vacation_days() * daily
        target = (workdays - holiday_days) * daily - vacation_hours
        return round(max(target, 0.0), 2)

    def target_hours_for_period(self, start_date, end_date):
        """
        Soll für einen beliebigen Zeitraum -- Verallgemeinerung des
        soll_kumuliert-Musters aus time_account_summary() (dort fest auf
        "Jahresbeginn/Anstellungsbeginn bis heute" verdrahtet). Neu für die
        automatisierte Planung (README Block 2 Punkt 19): der Solver braucht
        ein Monats-Soll pro Mitarbeiter als weiches Zielmass -- NICHT
        annual_target_hours() (Jahres-Fixgrösse, zieht den vollen
        Ferienanspruch pauschal ab statt tatsächlich genehmigter Absenzen im
        Zeitraum) und NICHT time_account_summary() (bringt Carryover-/
        Plan-Saldo-Semantik mit, die hier nicht gebraucht wird).
        """
        period_start = max(start_date, self.employment_start_date)
        if period_start > end_date:
            return 0.0
        workdays = _count_workdays(period_start, end_date)
        holiday_days = self._public_holiday_workdays(period_start, end_date)
        absence_weights = self._approved_absence_day_weights(period_start, end_date)
        daily = self._daily_target_hours()
        excused_units = len(holiday_days) + sum(
            weight for d, weight in absence_weights.items() if d.weekday() < 5 and d not in holiday_days
        )
        return round(max((workdays - excused_units) * daily, 0.0), 2)

    def time_account_summary(self, as_of_date=None):
        """
        Arbeitszeitmodell (README Block 2.7 Punkt 7, redesignt 2026-08 nach
        Nutzer-Feedback -- siehe unten): drei Kennzahlen statt einer.

        - saldo_hours: laufender Saldo = Ist_kumuliert(t) - Soll_kumuliert(t)
          im LAUFENDEN Kalenderjahr (ab max(1. Januar, employment_start_date))
          bis (inkl.) as_of_date, plus overtime_balance_carryover_hours als
          Startwert. Soll_kumuliert(t) ist das anteilige Soll bis heute
          (Anzahl Mo-Fr-Arbeitstage seit Jahres-/Anstellungsbeginn *
          Tagessoll), abzüglich bereits vergangener Feiertage und genehmigter
          Absenzen (Ferien, Krankheit, Sonstiges) -- diese Tage sind
          Soll-neutral, keine "verpasste Sollzeit". Sowohl Ist_kumuliert(t)
          als auch Soll_kumuliert(t) zählen bewusst nur bis (inkl.)
          as_of_date -- eine künftig eingeplante Schicht wirkt sich hier
          erst aus, sobald ihr Datum erreicht ist (klassisches
          Gleitzeitkonto-Verhalten). Weiterhin korrekt für Lohn-/
          Überzeit-relevante Auswertungen (nur tatsächlich Geleistetes darf
          dort zählen), aber als alleinige Haupt-Anzeige irreführend: bei
          festem Pensum entscheidet der Planer, WANN die Stunden anfallen,
          nicht die Mitarbeitenden -- ein grosser Minus-Wert bedeutet hier
          oft nur "die Tage sind noch nicht eingetreten", nicht "zu wenig
          gearbeitet/geplant". Deshalb nur noch Detail-Kennzahl, siehe
          plan_saldo_hours für die primäre Anzeige.
        - plan_saldo_hours (NEU, primäre Anzeige): wie saldo_hours, aber
          Ist_kumuliert schliesst zusätzlich bereits eingeplante KÜNFTIGE
          Zuweisungen bis Jahresende mit ein (mit den geplanten Stunden aus
          dem Template, da für sie naturgemäss noch keine Zeiterfassung
          existieren kann) und wird gegen das volle Jahressoll verglichen,
          nicht nur das anteilige Soll bis heute. Bei einem für das ganze
          Jahr sauber durchgeplanten Pensum liegt dieser Wert nahe 0 --
          unabhängig davon, ob gerade Januar oder Dezember ist. Das
          beantwortet die eigentlich relevante Frage "wird mein Vertrags-
          soll durch den aktuellen Plan erfüllt", nicht nur "wie viel wurde
          bereits gearbeitet".
        - annual_target_hours / annual_remaining_hours: Jahressoll (fix fürs
          ganze Jahr, siehe annual_target_hours()) und was davon noch NICHT
          verplant ist (weder bereits geleistet noch bereits eingeplant) --
          die Kennzahl für "wie viele Stunden muss der Planer mich für den
          Rest des Jahres noch einteilen".
        - is_provisional: True, sobald mindestens eine der eingerechneten
          Schichten (bis as_of_date UND jede künftig eingeplante, die per
          Definition noch keine geprüfte Zeiterfassung haben kann) nicht auf
          einer geprüften (CONFIRMED) Zeiterfassung beruht -- gilt für beide
          Saldo-Werte gemeinsam.
        """
        as_of_date = as_of_date or timezone.localdate()
        year = as_of_date.year
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        period_start = max(year_start, self.employment_start_date)
        annual_target = self.annual_target_hours(year)

        if period_start > as_of_date:
            # Eintritt liegt erst später in diesem Jahr -- weder Ist noch
            # Soll sind bislang angefallen.
            carryover = round(self.overtime_balance_carryover_hours, 2)
            return {
                "saldo_hours": carryover,
                "plan_saldo_hours": carryover,
                "annual_target_hours": annual_target,
                "annual_remaining_hours": annual_target,
                "is_provisional": False,
            }

        workdays_elapsed = _count_workdays(period_start, as_of_date)
        holiday_days = self._public_holiday_workdays(period_start, as_of_date)
        absence_weights = self._approved_absence_day_weights(period_start, as_of_date)
        daily = self._daily_target_hours()
        # Ein Feiertag zählt voll (1.0), unabhängig von einer ggf. am selben
        # Tag zusätzlich bestehenden Absenz (Doppelzählung ausgeschlossen).
        excused_units = len(holiday_days) + sum(
            weight for d, weight in absence_weights.items() if d.weekday() < 5 and d not in holiday_days
        )
        soll_kumuliert = (workdays_elapsed - excused_units) * daily

        # Zuweisungen an einem GANZTÄGIGEN genehmigten Absenztag zählen nicht
        # als Ist-Zeit (siehe _approved_absence_day_weights-Docstring) -- ein
        # solcher Tag ist per Definition arbeitsfrei, unabhängig davon, ob
        # versehentlich trotzdem eine Zuweisung dafür existiert. Eine
        # Halbtags-Absenz schliesst die (weiterhin bestehende) Zuweisung
        # NICHT aus, sondern gewichtet ihre geschätzten Stunden weiter unten
        # nur zur Hälfte -- eine bereits erfasste TimeRecord (tatsächlich
        # geleistete Zeit) bleibt davon unberührt, die ist schon korrekt.
        # Spezialitäten (TimeTemplate.category == "special", z. B.
        # Pikettdienst) sind rein informativ und zählen nicht zu den
        # Stunden -- siehe skip_for_specialties-Decorator.
        full_absence_dates = {d for d, weight in absence_weights.items() if weight >= 1.0}
        assignments = (
            ShiftAssignment.all_objects.filter(employee=self, date__gte=period_start, date__lte=as_of_date)
            .exclude(date__in=full_absence_dates)
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template", "time_record")
        )
        ist_kumuliert = 0.0
        is_provisional = False
        for assignment in assignments:
            time_record = getattr(assignment, "time_record", None)
            if time_record is not None:
                ist_kumuliert += time_record.actual_hours
                if time_record.status != TimeRecord.Status.CONFIRMED:
                    is_provisional = True
            else:
                hours = ShiftAssignment._shift_hours(assignment.date, assignment.template)
                ist_kumuliert += hours * (1 - absence_weights.get(assignment.date, 0.0))
                is_provisional = True

        saldo = self.overtime_balance_carryover_hours + ist_kumuliert - soll_kumuliert

        # Bereits eingeplante künftige Zuweisungen bis Jahresende (siehe
        # Docstring plan_saldo_hours oben) -- niemals eine geprüfte
        # Zeiterfassung möglich (liegt in der Zukunft), daher direkt die
        # geplanten Template-Stunden statt eines TimeRecord-Lookups (mit
        # derselben Halbtags-Gewichtung wie oben).
        ist_geplant_zukunft = 0.0
        if as_of_date < year_end:
            future_absence_weights = self._approved_absence_day_weights(as_of_date + timedelta(days=1), year_end)
            future_full_absence_dates = {d for d, weight in future_absence_weights.items() if weight >= 1.0}
            future_assignments = (
                ShiftAssignment.all_objects.filter(employee=self, date__gt=as_of_date, date__lte=year_end)
                .exclude(date__in=future_full_absence_dates)
                .exclude(template__category=TimeTemplate.Category.SPECIAL)
                .select_related("template")
            )
            for assignment in future_assignments:
                hours = ShiftAssignment._shift_hours(assignment.date, assignment.template)
                ist_geplant_zukunft += hours * (1 - future_absence_weights.get(assignment.date, 0.0))
                is_provisional = True

        ist_kumuliert_geplant = ist_kumuliert + ist_geplant_zukunft
        plan_saldo = self.overtime_balance_carryover_hours + ist_kumuliert_geplant - annual_target
        remaining = annual_target - ist_kumuliert_geplant

        return {
            "saldo_hours": round(saldo, 2),
            "plan_saldo_hours": round(plan_saldo, 2),
            "annual_target_hours": annual_target,
            "annual_remaining_hours": round(remaining, 2),
            "is_provisional": is_provisional,
        }

    def vacation_balance(self, year=None):
        """
        Feriensaldo für ein Kalenderjahr (MVP-Fahrplan Block 2.7): Anspruch
        (Tenant-Default oder Employee-Override) minus genehmigte
        Ferien-Absenzen, die in dieses Jahr fallen (an den Jahresgrenzen
        gekappt). Verbrauchte Tage werden als Mo-Fr-Werktage gezählt (siehe
        _count_workdays) -- ohne Feiertagskalender und ohne Übertrag
        zwischen Kalenderjahren (beides bewusst noch offen, siehe README).
        """
        year = year or timezone.localdate().year
        entitlement_days = self.vacation_days_per_year or self.tenant.default_vacation_days_per_year
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)

        # Nutzer-Feedback (2026-08): "vacation" war ein hartcodierter Absenz-
        # Typ -- jetzt zieht jeder AbsenceType mit deducts_vacation_days=True
        # Tage vom Ferienanspruch ab, nicht nur ein fest benannter.
        absences = Absence.all_objects.filter(
            employee=self,
            type__deducts_vacation_days=True,
            status=Absence.Status.APPROVED,
            start_date__lte=year_end,
            end_date__gte=year_start,
        )
        # Nutzer-Feedback (2026-08, Halbtags-Absenzen): "nur vormittags"/"nur
        # nachmittags" zieht 0.5 statt 1 Ferientag ab -- day_portion ist per
        # clean() nur bei einem einzelnen Tag erlaubt (start_date ==
        # end_date), _count_workdays liefert für so eine Absenz daher immer
        # 0 (Wochenende) oder 1 (Werktag), multipliziert mit 0.5 also 0/0.5.
        used_days = sum(
            _count_workdays(max(a.start_date, year_start), min(a.end_date, year_end))
            * (0.5 if a.day_portion != Absence.DayPortion.FULL else 1)
            for a in absences
        )

        return {
            "year": year,
            "entitlement_days": entitlement_days,
            "used_days": used_days,
            "remaining_days": entitlement_days - used_days,
        }

    def sick_pay_summary(self, reference_date=None):
        """
        Lohnfortzahlungs-Anspruch bei Krankheit (Art. 324a OR, MVP-Fahrplan
        Block 1 Punkt 16) für das laufende Dienstjahr (12-Monats-Zyklus ab
        dem Jahrestag von employment_start_date, siehe
        _current_service_year_window() -- NICHT das Kalenderjahr wie bei
        vacation_balance()).

        Anders als vacation_balance() wird hier in KALENDERTAGEN gezählt statt
        in Mo-Fr-Werktagen (_count_workdays): einmal krank, zählt auch das
        Wochenende mit -- die Skalen sind auf Kalendertage/-wochen ausgelegt.

        Zwei Modelle (Tenant.sick_pay_model), Rückgabe-Keys sind in BEIDEN
        Fällen vorhanden (auf None/0 gesetzt, wenn nicht zutreffend), damit
        das Frontend nicht je nach Modell unterschiedliche Felder behandeln
        muss:
          - "scale": Anspruch aus der gewählten Gerichtsskala
            (Tenant.sick_pay_scale) nach Dienstjahr, siehe
            _basel_scale_weeks()/_bern_scale_weeks()/_zurich_scale_weeks()
            und deren Disclaimer zu Näherungswerten.
          - "daily_allowance_insurance": keine Skala -- die Police übernimmt
            ab der Wartefrist (Tenant.sick_pay_waiting_days), siehe
            Tenant.sick_pay_model help_text. used_days wird trotzdem
            ausgewiesen (informativ, z. B. um die Wartefrist selbst im Blick
            zu behalten), aber es gibt keinen Tages-Anspruch/-Saldo zu
            berechnen -- die App rechnet bewusst kein Taggeld/keine
            Lohnprozente aus (Grenzziehung Zeitmanagement vs.
            Lohnbuchhaltung, siehe README).
        """
        reference_date = reference_date or timezone.localdate()
        window_start, window_end, service_year_number = self._current_service_year_window(reference_date)
        tenant = self.tenant

        absences = Absence.all_objects.filter(
            employee=self,
            type__counts_as_sick_leave=True,
            status=Absence.Status.APPROVED,
            start_date__lte=window_end,
            end_date__gte=window_start,
        )
        used_days = sum(
            ((min(a.end_date, window_end) - max(a.start_date, window_start)).days + 1)
            * (0.5 if a.day_portion != Absence.DayPortion.FULL else 1)
            for a in absences
        )

        entitlement_weeks = None
        entitlement_days = None
        remaining_days = None
        if tenant.sick_pay_model == Tenant.SickPayModel.SCALE:
            scale_func = _SICK_PAY_SCALE_FUNCTIONS[tenant.sick_pay_scale]
            entitlement_weeks = scale_func(service_year_number)
            entitlement_days = entitlement_weeks * 7
            remaining_days = entitlement_days - used_days

        return {
            "reference_date": reference_date,
            "service_year_number": service_year_number,
            "service_year_start": window_start,
            "service_year_end": window_end,
            "used_days": used_days,
            "model": tenant.sick_pay_model,
            "scale": tenant.sick_pay_scale if tenant.sick_pay_model == Tenant.SickPayModel.SCALE else None,
            "entitlement_weeks": entitlement_weeks,
            "entitlement_days": entitlement_days,
            "remaining_days": remaining_days,
            "waiting_days": (
                tenant.sick_pay_waiting_days
                if tenant.sick_pay_model == Tenant.SickPayModel.DAILY_ALLOWANCE_INSURANCE
                else None
            ),
        }

    def monthly_summary(self, year=None, month=None):
        """
        Monatsauswertung Soll/Ist-Stunden (README Block 2.6, "Basis für den
        Lohnlauf"): Soll/Ist-Vergleich, Überzeit- sowie Nacht-/
        Sonntagszuschlag für einen Kalendermonat. Ist-Stunden kommen pro
        Schicht wie bei weekly_hours_summary()/time_account_summary()
        bevorzugt aus TimeRecord, sobald erfasst, sonst aus der Planung
        (ShiftAssignment._shift_hours) als bester verfügbarer Schätzwert
        (README Punkt 13: "Anschluss der Ist-Arbeitszeiterfassung an Block
        2.6"). Zusätzlich `special_surcharge_breakdown`: Zuschlagsstunden pro
        Spezialität mit TimeTemplate.surcharge_pct > 0 (z. B. Pikett), siehe
        Docstring dort.

        Bewusst getrennt von weekly_hours_summary() (Block 1.11, strikt
        Kalenderwoche für den Art.-13-ArG-Zuschlag): hier wird über den
        ganzen Monat aggregiert, inkl. derselben soll-neutralen Behandlung
        von Feiertagen und genehmigten Absenzen wie in time_account_summary()
        (ein Ferientag zählt weder als Soll- noch als Ist-Zeit). Überzeit
        wird dafür als einfacher Monats-Soll/Ist-Vergleich berechnet statt
        als Summe der einzelnen Wochenwerte -- Kalenderwochen liegen selten
        exakt in einem Monat, ein Aufsummieren würde an den Monatsgrenzen zu
        Doppel-/Unterzählungen führen. Für die rechtlich massgebliche
        wöchentliche Grenze bleibt Block 1.11 die Quelle der Wahrheit, dies
        hier ist die monatliche Lohnlauf-Zusammenfassung.
        """
        year = year or timezone.localdate().year
        month = month or timezone.localdate().month
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        period_start = max(month_start, self.employment_start_date)

        if period_start > month_end:
            # Eintritt liegt erst nach diesem Monat -- weder Ist noch Soll
            # sind in diesem Monat angefallen (analog time_account_summary).
            return {
                "year": year,
                "month": month,
                "month_start": month_start,
                "month_end": month_end,
                "soll_hours": 0.0,
                "ist_hours": 0.0,
                "overtime_hours": 0.0,
                "overtime_surcharge_hours": 0.0,
                "saldo_hours": 0.0,
                "flextime_corridor_hours": self.tenant.flextime_corridor_hours,
                "flextime_corridor_excess_hours": 0.0,
                "is_overtime_settled": False,
                "night_hours": 0.0,
                "night_surcharge_hours": 0.0,
                "occasional_night_hours": 0.0,
                "occasional_night_surcharge_hours": 0.0,
                "sunday_hours": 0.0,
                "sunday_surcharge_hours": 0.0,
                "special_surcharge_hours": 0.0,
                "special_surcharge_breakdown": [],
                "holiday_days": 0,
                "is_provisional": False,
            }

        workdays = _count_workdays(period_start, month_end)
        holiday_days = self._public_holiday_workdays(period_start, month_end)
        absence_weights = self._approved_absence_day_weights(period_start, month_end)
        daily = self._daily_target_hours()
        excused_units = len(holiday_days) + sum(
            weight for d, weight in absence_weights.items() if d.weekday() < 5 and d not in holiday_days
        )
        soll_hours = round((workdays - excused_units) * daily, 2)

        # Zuweisungen an einem GANZTÄGIGEN genehmigten Absenztag zählen nicht
        # als Ist-Zeit, eine Halbtags-Absenz gewichtet stattdessen nur die
        # (mangels TimeRecord geschätzten) Stunden -- siehe
        # _approved_absence_day_weights-Docstring/time_account_summary().
        full_absence_dates = {d for d, weight in absence_weights.items() if weight >= 1.0}
        assignments = (
            ShiftAssignment.all_objects.filter(employee=self, date__gte=period_start, date__lte=month_end)
            .exclude(date__in=full_absence_dates)
            .select_related("template", "time_record")
        )
        ist_hours = 0.0
        night_hours = 0.0
        sunday_hours = 0.0
        is_provisional = False
        # Nutzer-Feedback (2026-08): "Spezialitäten Schichttypen können auch
        # zuschlagspflichtig sein, korrekt? wenn jemand Pikett macht zum
        # Beispiel". Spezialitäten (TimeTemplate.category == SPECIAL) zählen
        # -- wie in weekly_hours_summary()/time_account_summary() -- nicht zu
        # Ist-/Nacht-/Sonntagsstunden (additiv, kein eigener Dienst). Vorher
        # fehlte dieser Ausschluss hier als einzige der drei Summary-Methoden
        # (Bugfix): eine Pikett-Zuweisung mit nichtleerer Zeitspanne wäre
        # sonst fälschlich in die normale Ist-Stundenzahl eingeflossen. Wer
        # Zuschlagsprozent trägt (TimeTemplate.surcharge_pct), wird
        # stattdessen separat pro Schichttyp aufgeschlüsselt gesammelt --
        # nicht zu einer Summe zusammengefasst, da das künftige
        # Lohnart-Mapping (README Punkt 30) pro Spezialität einen eigenen
        # Lohnart-Code braucht.
        special_surcharge_totals = {}
        for assignment in assignments:
            if assignment.template.category == TimeTemplate.Category.SPECIAL:
                if assignment.template.surcharge_pct:
                    hours = ShiftAssignment._shift_hours(assignment.date, assignment.template)
                    entry = special_surcharge_totals.setdefault(
                        assignment.template_id,
                        {
                            "template_id": assignment.template_id,
                            "template_name": assignment.template.name,
                            "surcharge_pct": assignment.template.surcharge_pct,
                            "hours": 0.0,
                            "surcharge_hours": 0.0,
                        },
                    )
                    entry["hours"] += hours
                    entry["surcharge_hours"] += hours * assignment.template.surcharge_pct / 100
                continue
            time_record = getattr(assignment, "time_record", None)
            if time_record is not None:
                ist_hours += time_record.actual_hours
                if time_record.status != TimeRecord.Status.CONFIRMED:
                    is_provisional = True
            else:
                hours = ShiftAssignment._shift_hours(assignment.date, assignment.template)
                ist_hours += hours * (1 - absence_weights.get(assignment.date, 0.0))
                is_provisional = True
            # Nacht-/Sonntagszuschlag (Art. 17b/19 ArG) bewusst wie in
            # weekly_hours_summary()/night_work_summary() auf den GEPLANTEN
            # Stunden berechnet, nicht auf der Ist-Zeit -- die Erfassung
            # bildet nur ab, WANN innerhalb der Schicht gearbeitet wurde,
            # nicht ob diese Stunden in der Nacht/am Sonntag lagen.
            night_hours += assignment.night_hours
            if assignment.is_sunday:
                sunday_hours += ShiftAssignment._shift_hours(assignment.date, assignment.template)

        special_surcharge_breakdown = [
            {
                "template_id": entry["template_id"],
                "template_name": entry["template_name"],
                "surcharge_pct": entry["surcharge_pct"],
                "hours": round(entry["hours"], 2),
                "surcharge_hours": round(entry["surcharge_hours"], 2),
            }
            for entry in sorted(special_surcharge_totals.values(), key=lambda e: e["template_name"])
        ]
        special_surcharge_hours = round(sum(e["surcharge_hours"] for e in special_surcharge_breakdown), 2)

        ist_hours = round(ist_hours, 2)
        # Nutzer-Feedback (2026-08): "bei uns gilt Gleitzeit, nur angeordnete
        # Überstunden werden effektiv abgerechnet" -- overtime_hours bleibt
        # als reine Kennzahl "wie weit war dieser Monat vom Soll entfernt"
        # bestehen, ist aber NICHT mehr automatisch abrechnungsrelevant.
        # Massgeblich für overtime_surcharge_hours ist stattdessen der
        # Gleitzeit-Korridor (siehe _flextime_corridor_status/
        # OvertimeSettlement): erst der Anteil des laufenden Jahressaldos,
        # der über die Tenant-Bandbreite (flextime_corridor_hours) hinausgeht
        # UND von Planer/Admin für diesen Monat bestätigt wurde, zählt.
        overtime_hours = round(max(0.0, ist_hours - soll_hours), 2)
        corridor = self._flextime_corridor_status(year, month)
        overtime_surcharge_hours = corridor["settled_surcharge_hours"]

        night_hours = round(night_hours, 2)
        # Zeitgutschrift (Art. 17b Abs. 1 ArG) nur bei regelmässiger
        # Nachtarbeit -- dieselbe jahresbezogene Schwelle wie
        # night_work_summary(), nicht neu pro Monat ermittelt, da
        # "regelmässig" sich per Definition auf das ganze Kalenderjahr
        # bezieht. Wer die Schwelle NICHT erreicht, hat stattdessen Anspruch
        # auf den 25%-Lohnzuschlag (Art. 17b Abs. 2 ArG, Punkt 17) --
        # occasional_night_hours/occasional_night_surcharge_hours liefern
        # dafür den monatlichen Rohinput für den Lohn-Export (Block 2 Punkt
        # 30/31), analog zu night_work_summary()'s jährlicher Variante.
        # Regelmässig und gelegentlich schliessen sich gegenseitig aus.
        is_regular_night_work = self.night_work_summary(year)["is_regular"]
        night_surcharge_hours = (
            round(night_hours * self.tenant.night_work_surcharge_pct / 100, 2) if is_regular_night_work else 0.0
        )
        occasional_night_hours = 0.0 if is_regular_night_work else night_hours
        occasional_night_surcharge_hours = (
            0.0
            if is_regular_night_work
            else round(night_hours * self.tenant.occasional_night_work_surcharge_pct / 100, 2)
        )

        sunday_hours = round(sunday_hours, 2)
        sunday_surcharge_hours = round(sunday_hours * self.tenant.sunday_work_surcharge_pct / 100, 2)

        return {
            "year": year,
            "month": month,
            "month_start": month_start,
            "month_end": month_end,
            "soll_hours": soll_hours,
            "ist_hours": ist_hours,
            "overtime_hours": overtime_hours,
            "overtime_surcharge_hours": overtime_surcharge_hours,
            "saldo_hours": corridor["saldo_hours"],
            "flextime_corridor_hours": corridor["corridor_hours"],
            "flextime_corridor_excess_hours": corridor["excess_hours"],
            "is_overtime_settled": corridor["is_settled"],
            "night_hours": night_hours,
            "night_surcharge_hours": night_surcharge_hours,
            "occasional_night_hours": occasional_night_hours,
            "occasional_night_surcharge_hours": occasional_night_surcharge_hours,
            "sunday_hours": sunday_hours,
            "sunday_surcharge_hours": sunday_surcharge_hours,
            "special_surcharge_hours": special_surcharge_hours,
            "special_surcharge_breakdown": special_surcharge_breakdown,
            "holiday_days": len(holiday_days),
            "is_provisional": is_provisional,
        }

    def effective_cost_center(self):
        """
        Kostenstelle für den Lohn-Export (Block 2 Punkt 30/31): eindeutig
        nur, wenn alle Stationen dieses Mitarbeitenden (Employee.nodes,
        inkl. Vererbung von Eltern-Knoten, siehe Node.effective_cost_center())
        auf dieselbe Kostenstelle auflösen. Bei mehreren unterschiedlichen
        Kostenstellen oder wenn keine konfiguriert ist: None -- bekannte
        Vereinfachung, eine echte Aufteilung nach Station müsste
        monthly_summary() selbst pro Station aufschlüsseln (die Stunden
        werden dort tenant-/mitarbeiterweit aggregiert, nicht pro Node).
        """
        centers = {n.effective_cost_center() for n in self.nodes.all()}
        centers.discard(None)
        if len(centers) == 1:
            return next(iter(centers))
        return None

    def payroll_raw_lines(self, year=None, month=None):
        """
        Rohdaten für den Lohn-Export (MVP-Fahrplan Block 2, Punkt 30/31) für
        einen Kalendermonat: eine Liste von {category, special_template_id,
        absence_type_id, amount, unit}-Zeilen (genau eines der ersten drei
        Felder gesetzt, analog PayrollCategoryMapping), ungerundet auf
        Lohnart-Codes -- die Übersetzung passiert bewusst NICHT hier,
        sondern in der View über PayrollCategoryMapping, damit Employee
        (scheduling) nicht von einer Konfiguration abhängt, die sich der
        Kunde jederzeit ändern kann. Zeilen mit amount == 0 werden
        ausgelassen (kein Bedarf, in der Kundenlohnsoftware eine Nullzeile
        zu erzeugen).

        Baut auf monthly_summary() (Stunden-Kategorien) und
        _monthly_absence_day_breakdown() (Tage-Kategorien) auf:
        - Normalstunden = Ist-Stunden abzüglich der (nur informativen, siehe
          monthly_summary-Docstring) Überzeit -- entspricht min(Ist, Soll).
        - Überstunden nur der über den Gleitzeit-Korridor bereits
          BESTÄTIGTE Anteil (overtime_surcharge_hours), nicht der rohe
          Ist-Soll-Überschuss (overtime_hours) -- unbestätigte Überzeit ist
          noch nicht abrechnungsreif (README, Gleitzeit-Entscheidung 2026-08).
        - Ferientage: alle AbsenceTypes mit deducts_vacation_days in einer
          Zeile (fixe Kategorie -- welcher Typ im Einzelnen deduziert hat,
          ist für die Lohnbuchhaltung i. d. R. nicht relevant).
        - Krankheitstage: alle AbsenceTypes mit counts_as_sick_leave,
          aufgeteilt nach Lohnfortzahlungs-Anspruch, siehe
          _sick_pay_entitlement_split().
        - Alle übrigen AbsenceTypes (Nutzer-Feedback 2026-08: "sonstige
          Absenztage müssten aufgeschlüsselt werden", z. B. Militärdienst
          braucht einen anderen Lohnart-Code als unbezahlter Urlaub): eine
          eigene Zeile PRO Typ (absence_type_id statt category, analog zu
          den Spezialitäten unten) statt eines gemeinsamen Sammel-Topfs.
        - Eine Zeile pro Spezialität mit Zuschlag (special_template_id
          statt category, siehe PayrollCategoryMapping-Docstring).
        """
        summary = self.monthly_summary(year, month)
        lines = []

        def add(category, amount, unit="hours", special_template_id=None, absence_type_id=None):
            if amount:
                lines.append(
                    {
                        "category": category,
                        "special_template_id": special_template_id,
                        "absence_type_id": absence_type_id,
                        "amount": amount,
                        "unit": unit,
                    }
                )

        regular_hours = round(summary["ist_hours"] - summary["overtime_hours"], 2)
        add(PayrollCategoryMapping.Category.REGULAR_HOURS, regular_hours)
        add(PayrollCategoryMapping.Category.OVERTIME, summary["overtime_surcharge_hours"])
        add(PayrollCategoryMapping.Category.NIGHT_CREDIT, summary["night_surcharge_hours"])
        add(PayrollCategoryMapping.Category.NIGHT_SURCHARGE, summary["occasional_night_surcharge_hours"])
        add(PayrollCategoryMapping.Category.SUNDAY_SURCHARGE, summary["sunday_surcharge_hours"])
        add(PayrollCategoryMapping.Category.HOLIDAYS, summary["holiday_days"], unit="days")

        absence_days = self._monthly_absence_day_breakdown(summary["month_start"], summary["month_end"])
        if absence_days:
            types_by_id = {t.id: t for t in AbsenceType.all_objects.filter(pk__in=absence_days.keys())}
            vacation_days = sick_days = 0.0
            for type_id, days in absence_days.items():
                absence_type = types_by_id.get(type_id)
                if absence_type and absence_type.deducts_vacation_days:
                    vacation_days += days
                elif absence_type and absence_type.counts_as_sick_leave:
                    sick_days += days
                else:
                    add(None, round(days, 2), unit="days", absence_type_id=type_id)
            add(PayrollCategoryMapping.Category.VACATION_DAYS, round(vacation_days, 2), unit="days")
            paid_sick_days, exhausted_sick_days = self._sick_pay_entitlement_split(
                round(sick_days, 2), summary["month_start"], summary["month_end"]
            )
            add(PayrollCategoryMapping.Category.SICK_DAYS, paid_sick_days, unit="days")
            add(PayrollCategoryMapping.Category.SICK_DAYS_EXHAUSTED, exhausted_sick_days, unit="days")

        for entry in summary["special_surcharge_breakdown"]:
            if entry["surcharge_hours"]:
                add(None, entry["surcharge_hours"], special_template_id=entry["template_id"])

        return lines

    def _sick_pay_entitlement_split(self, sick_days_this_month, month_start, month_end):
        """
        Splittet die Krankheitstage eines Monats in "mit Lohnfortzahlung"
        und "Anspruch erschöpft" (Nutzer-Feedback 2026-08: "Sick-Pay-Skala
        einbauen"), nur aussagekräftig für sick_pay_model=SCALE -- beim
        Taggeldversicherungs-Modell rechnet die App bewusst keine
        Wartefrist pro Krankheitsfall aus (siehe sick_pay_summary()-
        Docstring, Grenzziehung Zeitmanagement vs. Lohnbuchhaltung), dort
        bleibt es bei einer einzigen Zeile.

        Bekannte Vereinfachung: der Anspruch wird anhand des Dienstjahr-
        Fensters zum MONATSENDE bestimmt (wie sick_pay_summary()). Fällt
        der Dienstjahr-Wechsel mitten in den Monat, zählen "vor diesem
        Monat verbrauchte Tage" nur die Tage innerhalb des so bestimmten
        Fensters -- ein in der Praxis seltener Randfall.
        """
        tenant = self.tenant
        if tenant.sick_pay_model != Tenant.SickPayModel.SCALE or not sick_days_this_month:
            return sick_days_this_month, 0.0
        window_start, _window_end, service_year_number = self._current_service_year_window(month_end)
        entitlement_days = _SICK_PAY_SCALE_FUNCTIONS[tenant.sick_pay_scale](service_year_number) * 7
        before_month = self._monthly_absence_day_breakdown(window_start, month_start - timedelta(days=1))
        types_by_id = {t.id: t for t in AbsenceType.all_objects.filter(pk__in=before_month.keys())}
        used_before_month = sum(
            days for type_id, days in before_month.items() if types_by_id.get(type_id) and types_by_id[type_id].counts_as_sick_leave
        )
        remaining_before_month = max(0.0, entitlement_days - used_before_month)
        paid_days = round(min(sick_days_this_month, remaining_before_month), 2)
        exhausted_days = round(sick_days_this_month - paid_days, 2)
        return paid_days, exhausted_days

    def _flextime_corridor_status(self, year, month):
        """
        Gleitzeit-Korridor-Status für einen Monat (Nutzer-Feedback 2026-08:
        "bei uns gilt Gleitzeit, nur angeordnete Überstunden werden effektiv
        abgerechnet"). Vergleicht den laufenden Jahressaldo
        (time_account_summary()["saldo_hours"] zum Monatsende, abzüglich
        bereits in früheren Monaten DIESES Jahres bestätigter
        OvertimeSettlement-Beträge) mit der Tenant-Bandbreite
        (flextime_corridor_hours). Nur der POSITIVE Übertritt zählt -- ein
        stark negativer Saldo ist kein Auszahlungsthema, sondern etwas, das
        die Mitarbeitenden selbst über die Zeit wieder ausgleichen.

        Ist für diesen Monat bereits eine Bestätigung vorhanden (idempotent,
        siehe confirm_overtime_settlement), wird deren fixierter Wert
        zurückgegeben statt neu zu rechnen -- der bestätigte Betrag ändert
        sich nicht rückwirkend, auch wenn sich Zuweisungen danach noch
        verschieben.
        """
        month_end = date(year, month, calendar.monthrange(year, month)[1])
        saldo_hours = self.time_account_summary(as_of_date=month_end)["saldo_hours"]

        settlement_this_month = self.overtime_settlements.filter(year=year, month=month).first()
        if settlement_this_month:
            return {
                "corridor_hours": self.tenant.flextime_corridor_hours,
                "saldo_hours": saldo_hours,
                "excess_hours": 0.0,
                "is_settled": True,
                "settled_hours": settlement_this_month.hours,
                "settled_surcharge_hours": settlement_this_month.surcharge_hours,
            }

        settled_prior_this_year = self.overtime_settlements.filter(year=year, month__lt=month).aggregate(
            total=models.Sum("hours")
        )["total"] or 0.0
        unsettled_saldo = saldo_hours - settled_prior_this_year
        excess_hours = round(max(0.0, unsettled_saldo - self.tenant.flextime_corridor_hours), 2)

        return {
            "corridor_hours": self.tenant.flextime_corridor_hours,
            "saldo_hours": saldo_hours,
            "excess_hours": excess_hours,
            "is_settled": False,
            "settled_hours": 0.0,
            "settled_surcharge_hours": 0.0,
        }

    def confirm_overtime_settlement(self, year, month):
        """
        Bestätigt den aktuellen Gleitzeit-Korridor-Überschuss für einen Monat
        als abrechnungsrelevant -- legt einen OvertimeSettlement-Datensatz an,
        der den weiteren Saldo-Verlauf dauerhaft um genau diese Stunden
        reduziert (auditierbar pro Monat statt eines einzelnen mutierbaren
        Werts wie overtime_balance_carryover_hours). Idempotent: ein bereits
        bestätigter Monat liefert den bestehenden Datensatz unverändert
        zurück, ein zweiter Klick zahlt nicht doppelt aus.
        """
        existing = self.overtime_settlements.filter(year=year, month=month).first()
        if existing:
            return existing
        status = self._flextime_corridor_status(year, month)
        if status["excess_hours"] <= 0:
            raise ValueError(
                "Kein Saldo ausserhalb des Gleitzeit-Korridors für diesen Monat -- nichts zu bestätigen."
            )
        return OvertimeSettlement.objects.create(
            tenant=self.tenant,
            employee=self,
            year=year,
            month=month,
            hours=status["excess_hours"],
            surcharge_hours=round(status["excess_hours"] * self.tenant.overtime_surcharge_pct / 100, 2),
        )


class OvertimeSettlement(TenantScopedModel):
    """
    Bestätigte Auszahlung von Gleitzeit-Saldo ausserhalb des Korridors
    (Nutzer-Feedback 2026-08: "bei uns gilt Gleitzeit, nur angeordnete
    Überstunden werden effektiv abgerechnet" -- siehe
    Employee._flextime_corridor_status/confirm_overtime_settlement). Ein
    Datensatz pro Mitarbeiter und Monat, angelegt per Klick in der
    Monatsauswertung statt einer Markierung pro einzelner Schicht -- reduziert
    den weiteren Saldo-Verlauf dauerhaft um `hours`, `surcharge_hours` ist der
    zum Bestätigungszeitpunkt gültige Zuschlag (Tenant.overtime_surcharge_pct)
    und fliesst als einzige Quelle in monthly_summary()["overtime_surcharge_
    hours"] ein -- reine Kalender-/Gleitzeit-Schwankungen innerhalb des
    Korridors werden dadurch nie automatisch "ausbezahlt".
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="overtime_settlements")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    hours = models.FloatField(help_text="Bestätigte Stunden ausserhalb des Gleitzeit-Korridors.")
    surcharge_hours = models.FloatField(help_text="hours * Tenant.overtime_surcharge_pct/100 zum Bestätigungszeitpunkt.")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        unique_together = ("employee", "year", "month")
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.employee} {self.year}-{self.month:02d}: {self.hours}h"


class Pregnancy(TenantScopedModel):
    """
    Mutterschutz (Art. 35, 35a, 35b ArG + Mutterschutzverordnung, MVP-Fahrplan
    Block 1.15). Eigenes Ereignis-Modell statt eines einzelnen Feldes an
    Employee (analog Absence): eine Mitarbeiterin kann über ihre Anstellung
    hinweg mehrmals schwanger sein, jede Schwangerschaft hat ihr eigenes
    Schutzfenster (Diskussion 2026-08). `expected_birth_date` wird bei
    Bekanntgabe erfasst, `actual_birth_date` erst nachträglich -- die ab der
    Niederkunft gerechneten Fristen (Art. 35a Abs. 3 ArG) rechnen ab dem
    EFFEKTIVEN Datum, nicht ab dem Termin, deshalb bleibt der ursprüngliche
    Termin bei einer Korrektur erhalten statt überschrieben zu werden. Siehe
    Employee.is_maternity_protected_on() für die Auswertung.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="pregnancies")
    expected_birth_date = models.DateField(help_text="Voraussichtlicher Geburtstermin, bei Bekanntgabe erfasst.")
    actual_birth_date = models.DateField(
        null=True,
        blank=True,
        help_text="Tatsächliches Geburtsdatum, sobald bekannt -- korrigiert die ab der Niederkunft "
        "gerechneten Schutzfristen (Art. 35a Abs. 3 ArG). Leer lassen, solange die Geburt noch "
        "aussteht; expected_birth_date wird bis dahin als Schätzung verwendet.",
    )
    notes = models.CharField(max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-expected_birth_date"]

    def __str__(self):
        return f"{self.employee} – Schwangerschaft (Termin {self.expected_birth_date})"

    def _anchor_date(self):
        """Effektive Niederkunft, falls bekannt, sonst der Termin als Schätzung."""
        return self.actual_birth_date or self.expected_birth_date

    def protection_status_on(self, reference_date):
        """
        Schutzstatus an reference_date, oder None ausserhalb jedes
        Schutzfensters dieser Schwangerschaft. Rechnet die drei Fristen aus
        Art. 35a ArG relativ zu _anchor_date() (vor der Geburt der Termin,
        danach das effektive Datum):
        - "night_ban" (Abs. 4): 8 Wochen vor der Niederkunft bis zur
          Niederkunft, Verbot von Arbeit zwischen 20:00-06:00 (eigene, weiter
          gefasste Definition als das allgemeine Nachtfenster 23:00-06:00 aus
          Art. 16 ArG, siehe ShiftAssignment._maternity_night_hours()).
        - "full_ban" (Abs. 3, 1. Halbsatz): 8 Wochen ab der Niederkunft,
          generelles Beschäftigungsverbot.
        - "consent_required" (Abs. 3, 2. Halbsatz): 9.-16. Woche nach der
          Niederkunft, nur mit Einverständnis der Mitarbeiterin -- die App
          bildet keine Einverständnis-Erfassung ab und blockiert diese Wochen
          daher vorsorglich hart (analog zur vereinfachten
          Jugendschutz-Prüfung, siehe ShiftAssignment._check_youth_protection()).
        """
        anchor = self._anchor_date()
        if not anchor:
            return None
        night_ban_start = anchor - timedelta(weeks=8)
        full_ban_end = anchor + timedelta(weeks=8)
        consent_end = anchor + timedelta(weeks=16)

        if night_ban_start <= reference_date < anchor:
            return "night_ban"
        if anchor <= reference_date < full_ban_end:
            return "full_ban"
        if full_ban_end <= reference_date < consent_end:
            return "consent_required"
        return None


class Employment(TenantScopedModel):
    """
    Eine einzelne Anstellung: Person + Team-Node + Pensum + Rolle (README
    Punkt 17, "Teams pro Station + Mehrfachanstellungen"). Additive Ebene
    neben Employee.nodes/employment_pct, die für die bestehende
    Saldo-/ArG-Berechnung weiterhin massgeblich bleiben (Employee.
    time_account_summary()/weekly_hours_summary() etc. sind bewusst
    unverändert -- sie aggregieren schon heute personenweit über alle
    ShiftAssignments, unabhängig vom Node) -- Employment trägt nur
    Team-Zugehörigkeit + Anzeige-Metadaten (Pensum pro Team, Rollentitel,
    Teamleitung). Employee.nodes wird serverseitig aus den
    Employment-Zeilen abgeleitet (siehe EmployeeSerializer._sync_nested),
    damit es nur einen Änderungsweg für Team-Mitgliedschaft gibt.

    Bewusst kein zweites Anstellungsverhältnis am selben Node (unique_together)
    -- wer zwei unterschiedliche Rollen im selben Team hat, ist ein Spezialfall,
    der hier nicht abgebildet wird.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="employments")
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="employments")
    pensum_pct = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Pensum in % für diese einzelne Anstellung, z. B. 60.",
    )
    title = models.CharField(
        max_length=100, blank=True, help_text="Rollenbezeichnung dieser Anstellung, z. B. 'Arzt'."
    )
    is_team_lead = models.BooleanField(default=False)

    class Meta:
        unique_together = ("employee", "node")
        ordering = ["node__path"]

    def __str__(self):
        return f"{self.employee} – {self.node.name} ({self.pensum_pct}%)"


class AbsenceType(TenantScopedModel):
    """
    Nutzer-Feedback (2026-08): Absenzarten (bisher hartcodiert Ferien/
    Krankheit/Sonstiges) sollen genauso frei definierbar sein wie
    Schichttypen (TimeTemplate) -- z. B. "Militärdienst" oder
    "Weiterbildung" als eigene Art. `deducts_vacation_days` ersetzt die
    frühere Sonderbehandlung von "vacation" in Employee.vacation_balance():
    nur Absenzen eines Typs mit dieser Flag ziehen Tage vom Ferienanspruch
    ab, alle anderen Typen sind reine Kategorisierung ohne Auswirkung.
    """

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#64748b", help_text="Hex-Farbe für Chips/Badges")
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Kurzes Kürzel/Glyphe fürs Planblatt (z. B. 'F' oder ein Emoji), analog TimeTemplate.icon.",
    )
    deducts_vacation_days = models.BooleanField(
        default=False,
        help_text="Genehmigte Tage dieses Typs zählen als Ferienbezug (Employee.vacation_balance()).",
    )
    # MVP-Fahrplan Block 1, Punkt 16 (Lohnfortzahlung bei Krankheit, Art. 324a
    # OR): analog zu deducts_vacation_days -- nur Absenzen eines Typs mit
    # dieser Flag zählen gegen den Krankheits-Anspruch (Employee.
    # sick_pay_summary()). Bewusst kein Rückgriff auf den Namen ("Krankheit"),
    # weil AbsenceType ein frei benennbarer Katalog ist (siehe Docstring
    # oben) -- ein Tenant könnte den Typ z. B. "Unfall/Krankheit" nennen.
    counts_as_sick_leave = models.BooleanField(
        default=False,
        help_text="Genehmigte Tage dieses Typs zählen gegen den Lohnfortzahlungs-Anspruch bei Krankheit "
        "(Art. 324a OR, Employee.sick_pay_summary()).",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Absence(TenantScopedModel):
    """
    Ferien/Krankheit/Sonstiges (Abschnitt 6). Blockiert Schichtzuweisungen im
    überlappenden Zeitraum -- aber nur, solange sie APPROVED ist (siehe
    _check_no_absence_conflict auf ShiftAssignment). Genehmigungs-Workflow
    (MVP-Fahrplan, Block 2.3): Von Mitarbeitenden erstellte Absenzen starten
    als PENDING und müssen von Admin/Planer freigegeben werden (approve()/
    reject(), aufgerufen von AbsenceViewSet.approve/reject); von Admin/
    Planer selbst erstellte Absenzen sind sofort APPROVED
    (AbsenceViewSet.perform_create), weil die Freigabe in dem Fall bereits
    durch die anlegende Person erfolgt ist.

    Umgekehrte Richtung (Bugfix 2026-08, Arbeitszeitmodell): eine APPROVED
    Absenz darf ebenfalls nicht mit bereits bestehenden Schicht-Zuweisungen
    überlappen (siehe clean()) -- vorher konnten beide nebeneinander
    existieren, weil nur ShiftAssignment.clean() die eine Richtung prüfte.
    Das liess Employee.time_account_summary() Zuweisungen an "Ferientagen"
    weiterhin voll als Ist-Zeit zählen, obwohl der Tag gleichzeitig vom Soll
    ausgenommen wurde -- ein stiller Überstunden-Bonus ohne Gegenwert.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        APPROVED = "approved", "Genehmigt"
        REJECTED = "rejected", "Abgelehnt"

    # Nutzer-Feedback (2026-08): "ich kann auch einen Nachmittag frei nehmen"
    # -- bisher kannte das Modell nur ganze Tage. Ein Tagesanteil ist nur bei
    # einem EINZELNEN Tag sinnvoll (start_date == end_date, siehe clean()) --
    # eine "halbtags"-Absenz über mehrere Tage hätte keine eindeutige
    # Bedeutung (jeden Tag nur vormittags? nur den ersten/letzten Tag?).
    # Die Grenze zwischen Vormittag/Nachmittag ist bewusst fest bei 12:00
    # Mittag (nicht pro Tenant konfigurierbar) -- ein einfacher, universell
    # verständlicher Standard statt einer weiteren Einstellung; siehe
    # _half_day_window().
    class DayPortion(models.TextChoices):
        FULL = "full", "Ganzer Tag"
        MORNING = "morning", "Nur vormittags"
        AFTERNOON = "afternoon", "Nur nachmittags"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="absences")
    start_date = models.DateField()
    end_date = models.DateField()
    day_portion = models.CharField(max_length=20, choices=DayPortion.choices, default=DayPortion.FULL)
    # Nutzer-Feedback (2026-08): war ein hartcodiertes CharField(choices=...)
    # mit genau drei Werten (vacation/sick/other) -- jetzt FK auf den
    # tenant-eigenen AbsenceType-Katalog (analog TimeTemplate). PROTECT statt
    # CASCADE/SET_NULL: eine Absenz braucht immer einen Typ, ein versehentlich
    # gelöschter, noch verwendeter Typ soll nicht historische Absenzen
    # kaputt machen, sondern der Admin bekommt einen klaren Fehler.
    type = models.ForeignKey(AbsenceType, on_delete=models.PROTECT, related_name="absences")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        portion = "" if self.day_portion == Absence.DayPortion.FULL else f", {self.get_day_portion_display()}"
        return f"{self.employee} – {self.type.name} ({self.start_date}–{self.end_date}{portion})"

    @staticmethod
    def _half_day_window(day, portion):
        """
        (start_dt, end_dt) der Zeitspanne, die diese Absenz an `day` belegt --
        fest bei 12:00 Mittag geteilt (siehe DayPortion oben). "Ganzer Tag"
        deckt exklusiv bis Mitternacht des Folgetags ab, damit Nachtschichten
        (Ende < Start, siehe TimeTemplate) korrekt als überlappend erkannt
        werden -- gleiches Prinzip wie ShiftAssignment._shift_datetimes().
        """
        if portion == Absence.DayPortion.MORNING:
            return datetime.combine(day, time(0, 0)), datetime.combine(day, time(12, 0))
        if portion == Absence.DayPortion.AFTERNOON:
            return datetime.combine(day, time(12, 0)), datetime.combine(day + timedelta(days=1), time(0, 0))
        return datetime.combine(day, time(0, 0)), datetime.combine(day + timedelta(days=1), time(0, 0))

    @staticmethod
    def _shift_extends_into_other_half(shift_start, shift_end, day, portion):
        """
        Nutzer-Feedback (2026-08): "ein normaler (durchgehender) Dienst bleibt
        bei einer Halbtags-Absenz unverändert stehen -- Krankheit/Ferien sind
        arbeitszeitrechtlich weiterhin Arbeitszeit (Lohnfortzahlungspflicht,
        Schweizer ArG)". Ein Dienst, dessen Zeitfenster auch die jeweils
        ANDERE (nicht von `portion` beanspruchte) Tageshälfte berührt -- z. B.
        eine durchgehende Frühschicht 07:00-17:00 bei einer
        Nachmittags-Absenz --, blockiert eine Halbtags-Absenz NICHT (und
        umgekehrt): beide dürfen koexistieren. Ein Dienst, der AUSSCHLIESSLICH
        in der von `portion` beanspruchten Hälfte liegt (ein echter,
        eigenständiger Halbtags-Dienst, z. B. "Nachmittag" 13:30-17:00 bei
        einer Nachmittags-Absenz), bleibt weiterhin ein echter Konflikt.
        Gilt nicht für `FULL` -- eine ganztägige Absenz blockiert wie bisher
        jeden Dienst am gleichen Tag ausnahmslos.
        """
        if portion == Absence.DayPortion.FULL:
            return False
        other_portion = (
            Absence.DayPortion.AFTERNOON if portion == Absence.DayPortion.MORNING else Absence.DayPortion.MORNING
        )
        other_start, other_end = Absence._half_day_window(day, other_portion)
        return shift_start < other_end and other_start < shift_end

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("Enddatum darf nicht vor dem Startdatum liegen.")
        if (
            self.day_portion != Absence.DayPortion.FULL
            and self.start_date
            and self.end_date
            and self.start_date != self.end_date
        ):
            raise ValidationError(
                "Vormittags/Nachmittags gilt nur für einen einzelnen Tag -- Von und Bis müssen "
                "identisch sein."
            )
        if self.status == Absence.Status.APPROVED and self.employee_id and self.start_date and self.end_date:
            candidates = ShiftAssignment.all_objects.filter(
                employee_id=self.employee_id, date__range=[self.start_date, self.end_date]
            ).select_related("template")
            if self.day_portion == Absence.DayPortion.FULL:
                conflicts = sorted({a.date for a in candidates})
            else:
                absence_start, absence_end = self._half_day_window(self.start_date, self.day_portion)
                conflicts = sorted(
                    {
                        a.date
                        for a in candidates
                        if (lambda s, e: s < absence_end and absence_start < e)(
                            *ShiftAssignment._shift_datetimes(a.date, a.template)
                        )
                        # Nutzer-Feedback (2026-08): ein durchgehender Dienst
                        # (z. B. Frühschicht 07:00-17:00), der auch die
                        # jeweils andere Tageshälfte abdeckt, blockiert eine
                        # Halbtags-Absenz nicht -- er bleibt unverändert
                        # stehen (siehe _shift_extends_into_other_half()).
                        and not self._shift_extends_into_other_half(
                            *ShiftAssignment._shift_datetimes(a.date, a.template), a.date, self.day_portion
                        )
                    }
                )
            if conflicts:
                raise ValidationError(
                    f"{self.employee} hat im Zeitraum {self.start_date}–{self.end_date} bereits "
                    f"{len(conflicts)} Dienst-Zuweisung(en) (z. B. {conflicts[0]}) -- diese zuerst im "
                    "Planblatt entfernen, bevor eine genehmigte Absenz für diesen Zeitraum angelegt wird."
                )

    def approve(self):
        """
        Admin/Planer-Freigabe. Ruft clean() auf (nicht bloss full_clean()),
        weil erst der APPROVED-Status den Konflikt-Check gegen bestehende
        Schicht-Zuweisungen oben in clean() aktiviert -- eine PENDING-Absenz
        mit Konflikt ist erlaubt, eine APPROVED nicht.
        """
        if self.status != Absence.Status.PENDING:
            raise ValidationError("Nur offene Absenzanträge können genehmigt werden.")
        self.status = Absence.Status.APPROVED
        self.clean()
        self.save(update_fields=["status"])

    def reject(self):
        """Admin/Planer lehnt einen offenen Absenzantrag ab -- kein clean() nötig, REJECTED hat keine Konfliktregel."""
        if self.status != Absence.Status.PENDING:
            raise ValidationError("Nur offene Absenzanträge können abgelehnt werden.")
        self.status = Absence.Status.REJECTED
        self.save(update_fields=["status"])


class TimeTemplate(TenantScopedModel):
    """Vordefinierter Schichttyp (Icon/Farbe/Zeitfenster), z. B. 'Frühdienst'."""

    class Category(models.TextChoices):
        SHIFT = "shift", "Dienst"
        SPECIAL = "special", "Spezialität"

    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="time_templates")
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField(help_text="Bei Nachtschichten über Mitternacht: Endzeit < Startzeit ist erlaubt.")
    break_minutes = models.PositiveSmallIntegerField(default=0)
    icon = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=7, default="#2563eb", help_text="Hex-Farbe für die Planblatt-UI")
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.SHIFT,
        help_text="Nutzer-Feedback (2026-08): reine UI-Gruppierung für die Stempelleisten im "
        "Planblatt/Jahresplan -- eine eigene Zeile für Spezialitäten wie z. B. Pikettdienst, "
        "getrennt von den regulären Diensten. Keine Regel-Engine-Auswirkung.",
    )
    required_skill = models.ForeignKey(
        Skill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional: Qualifikation, die für diese Schicht zwingend vorhanden sein muss.",
    )
    minimum_staffing = models.PositiveSmallIntegerField(
        default=0,
        help_text="Mindestanzahl gleichzeitig eingeteilter Mitarbeitender an diesem Schichttyp; "
        "0 = keine Mindestbesetzung definiert.",
    )
    surcharge_pct = models.PositiveSmallIntegerField(
        default=0,
        help_text="Nutzer-Feedback (2026-08): 'Spezialitäten wie Pikett können auch "
        "zuschlagspflichtig sein'. Lohnzuschlag in % der geplanten Stunden dieses Schichttyps "
        "(0 = kein Zuschlag) -- wirkt nur bei Kategorie 'Spezialität' (siehe "
        "Employee.monthly_summary): reguläre Dienste laufen bereits über den Soll/Ist-Vergleich "
        "(Überzeit) sowie die zeitpunktbasierten Nacht-/Sonntagszuschläge, ein zusätzlicher "
        "Prozentsatz hier würde sich damit überschneiden. Erscheint pro Schichttyp einzeln "
        "aufgeschlüsselt in der Monatsauswertung (Block 2.6) -- bewusst nicht zu einer einzigen "
        "Summe zusammengefasst, da das spätere Lohnart-Mapping (Punkt 30) pro Spezialität einen "
        "eigenen Lohnart-Code braucht (z. B. 'Pikett Wochentag' und 'Pikett Wochenende' können in "
        "der Kundenlohnsoftware unterschiedliche Codes haben).",
    )
    # Nutzer-Feedback (2026-08, Automatisierte Planung): "es gibt Dienste
    # (Fr\u00fch/Sp\u00e4t) mit fixer Personenzahl -- und einen, den alle anderen im
    # Team bekommen, die keinen der beiden haben (z. B. Gleitzeit)".
    # minimum_staffing (fixe Zielzahl, Boden UND Deckel) kann das nicht
    # abbilden -- die Anzahl variiert t\u00e4glich mit Absenzen/Teilzeit-Mustern.
    # fills_remaining_capacity kehrt die Logik um: kein Zahlen-Ziel, sondern
    # "jede an diesem Tag arbeitspflichtige, f\u00fcr dieses Team eingeteilte
    # Person, die keinen anderen regul\u00e4ren Dienst hat, bekommt diesen"
    # (siehe scheduling.planning: von minimum_staffing-Boden/Deckel
    # ausgenommen, stattdessen eigene Pflicht-Anwesenheits-Constraint pro
    # Woche, zusammen mit Employee.fixed_weekdays_off).
    fills_remaining_capacity = models.BooleanField(
        default=False,
        help_text="Auff\u00fclldienst: statt einer festen Mindestbesetzung erhalten alle an diesem Tag "
        "arbeitspflichtigen Mitarbeitenden dieses Teams, die keinen anderen regul\u00e4ren Dienst haben, "
        "automatisch diesen Schichttyp (z. B. 'Gleitzeit'). H\u00f6chstens ein Auff\u00fclldienst pro Team.",
    )

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.name} ({self.start_time}\u2013{self.end_time})"

    def clean(self):
        if self.fills_remaining_capacity:
            if self.minimum_staffing:
                raise ValidationError(
                    "Ein Auff\u00fclldienst kann keine Mindestbesetzung haben -- die Anzahl ergibt sich "
                    "automatisch aus den \u00fcbrigen Zuweisungen des Tages."
                )
            if self.required_skill_id:
                raise ValidationError("Ein Auff\u00fclldienst kann keine Pflicht-Qualifikation verlangen.")
            if self.category == TimeTemplate.Category.SPECIAL:
                raise ValidationError("Ein Auff\u00fclldienst kann keine Spezialit\u00e4t (Pikett o. \u00c4.) sein.")
            if (
                self.node_id
                and TimeTemplate.all_objects.filter(node_id=self.node_id, fills_remaining_capacity=True)
                .exclude(pk=self.pk)
                .exists()
            ):
                raise ValidationError("F\u00fcr diesen Knoten existiert bereits ein Auff\u00fclldienst.")

    def effective_segments(self):
        """
        Liste von (start_time, end_time)-Paaren, geordnet: aus den expliziten
        TimeTemplateSegment-Kindzeilen, falls vorhanden (Block 1.12 -- der
        Planer definiert die Blockstruktur, z. B. Vormittag/Nachmittag mit
        einer fixen Mittagspause dazwischen), sonst als einzelnes Segment aus
        start_time/end_time (bisheriges Verhalten, pauschale Pause \u00fcber
        break_minutes).
        """
        segments = list(self.segments.order_by("order").values_list("start_time", "end_time"))
        return segments or [(self.start_time, self.end_time)]


class TimeTemplateSegment(TenantScopedModel):
    """
    Einzelner Arbeitsblock innerhalb eines TimeTemplate (Block 1.12). Ein
    Template ohne Segmente verh\u00e4lt sich weiterhin wie bisher (ein
    durchgehendes Zeitfenster + `break_minutes` pauschal) -- Segmente sind
    pro Template opt-in und werden vom Planer gepflegt (aktuell im
    Django-Admin, eine eigene Frontend-Oberfl\u00e4che folgt in Block 2.9), nicht
    von der einzelnen Schicht oder der Ist-Erfassung \u00fcberschrieben: die
    Anzahl/Reihenfolge der Bl\u00f6cke ist am Template fix vorgegeben, die
    Ist-Erfassung (TimeRecordSegment) darf pro Block nur die Uhrzeiten
    anpassen. Die Pause ergibt sich automatisch aus der L\u00fccke zwischen zwei
    aufeinanderfolgenden Segmenten.
    """

    template = models.ForeignKey(TimeTemplate, on_delete=models.CASCADE, related_name="segments")
    order = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField(
        help_text="Segmente, die \u00fcber Mitternacht gehen, werden aktuell nicht unterst\u00fctzt "
        "(nur ein einzelnes Segment darf \u00fcber Mitternacht hinausgehen)."
    )

    class Meta:
        ordering = ["order"]
        unique_together = ("template", "order")

    def __str__(self):
        return f"{self.template.name} #{self.order} ({self.start_time}\u2013{self.end_time})"


class PayrollCategoryMapping(TenantScopedModel):
    """
    MVP-Fahrplan Block 2, Punkt 30: konfigurierbare Zuordnung unserer intern
    berechneten Zuschlagskategorien (Employee.payroll_raw_lines()) zu den
    frei vergebenen Lohnart-Codes des jeweiligen Kunden-Lohnsystems. Ohne
    diese Zuordnung ist der Export (Punkt 31) f\u00fcr den n\u00e4chsten Kunden
    nutzlos, da z. B. "Nachtzulage" bei jedem Lohnsystem eine andere
    Lohnart-Nummer hat -- kein einziger verpflichtender CH-Standard daf\u00fcr
    (anders als ELM f\u00fcr die Beh\u00f6rden-Meldung), siehe README.

    Genau eine Zeile pro fester `category` ODER pro `special_template`
    (Spezialit\u00e4t mit `TimeTemplate.surcharge_pct > 0`, z. B. "Pikett
    Wochentag"/"Pikett Wochenende" k\u00f6nnen unterschiedliche Lohnart-Codes
    brauchen -- die feste category-Auswahl reicht daf\u00fcr nicht) ODER pro
    `absence_type` (Nutzer-Feedback 2026-08: "sonstige Absenztage m\u00fcssten
    aufgeschl\u00fcsselt werden" -- z. B. Milit\u00e4rdienst braucht einen anderen
    Lohnart-Code als unbezahlter Urlaub, beide fielen vorher unter dieselbe
    feste OTHER_ABSENCE_DAYS-Kategorie). Alle drei in einem Modell statt
    getrennten, damit der Export (payroll_raw_lines + View) nur EINE
    Lookup-Struktur braucht.
    """

    class Category(models.TextChoices):
        REGULAR_HOURS = "regular_hours", "Normalstunden"
        OVERTIME = "overtime", "\u00dcberstunden"
        NIGHT_CREDIT = "night_credit", "Nacht-Zeitgutschrift (regelm\u00e4ssig)"
        NIGHT_SURCHARGE = "night_surcharge", "Nacht-Lohnzuschlag (gelegentlich)"
        SUNDAY_SURCHARGE = "sunday_surcharge", "Sonntagszuschlag"
        VACATION_DAYS = "vacation_days", "Ferientage"
        SICK_DAYS = "sick_days", "Krankheitstage"
        SICK_DAYS_EXHAUSTED = "sick_days_exhausted", "Krankheitstage (Anspruch ersch\u00f6pft)"
        HOLIDAYS = "holidays", "Feiertage"

    category = models.CharField(max_length=30, choices=Category.choices, null=True, blank=True)
    special_template = models.ForeignKey(
        TimeTemplate,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payroll_mappings",
        help_text="Nur gesetzt f\u00fcr eine Spezialit\u00e4t mit Zuschlag statt einer festen Kategorie oben "
        "-- genau eines von category/special_template/absence_type muss gesetzt sein.",
    )
    absence_type = models.ForeignKey(
        AbsenceType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payroll_mappings",
        help_text="Nur gesetzt f\u00fcr eine Absenzart, die weder Ferien- noch Krankheits-Anspruch "
        "abzieht (z. B. Milit\u00e4rdienst, unbezahlter Urlaub) -- genau eines von "
        "category/special_template/absence_type muss gesetzt sein.",
    )
    payroll_code = models.CharField(max_length=50, help_text="Vom Kunden vergebener Lohnart-Code.")
    payroll_label = models.CharField(max_length=200, blank=True, help_text="Freitext, nur zur Anzeige.")
    is_active = models.BooleanField(
        default=True,
        help_text="Inaktive Zeilen werden beim Export ausgelassen (z. B. falls ein Kunde eine "
        "Kategorie bereits anders l\u00f6st).",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(category__isnull=False, special_template__isnull=True, absence_type__isnull=True)
                    | Q(category__isnull=True, special_template__isnull=False, absence_type__isnull=True)
                    | Q(category__isnull=True, special_template__isnull=True, absence_type__isnull=False)
                ),
                name="payrollcategorymapping_exactly_one_of_category_or_template_or_absence_type",
            ),
            models.UniqueConstraint(
                fields=["tenant", "category"],
                condition=Q(special_template__isnull=True, absence_type__isnull=True),
                name="unique_payroll_category_per_tenant",
            ),
            models.UniqueConstraint(
                fields=["tenant", "special_template"],
                condition=Q(category__isnull=True, absence_type__isnull=True),
                name="unique_payroll_special_template_per_tenant",
            ),
            models.UniqueConstraint(
                fields=["tenant", "absence_type"],
                condition=Q(category__isnull=True, special_template__isnull=True),
                name="unique_payroll_absence_type_per_tenant",
            ),
        ]

    def __str__(self):
        if self.category:
            label = self.category
        elif self.special_template_id:
            label = self.special_template.name
        elif self.absence_type_id:
            label = self.absence_type.name
        else:
            label = "?"
        return f"{label} -> {self.payroll_code}"

    def clean(self):
        set_count = sum(bool(v) for v in (self.category, self.special_template_id, self.absence_type_id))
        if set_count != 1:
            raise ValidationError(
                "Genau eines von Kategorie, Spezialit\u00e4t oder Absenzart muss gesetzt sein."
            )


def skip_for_specialties(check_method):
    """
    Decorator für ShiftAssignment._check_*-Methoden, die für Spezialitäten
    (TimeTemplate.category == SPECIAL, z. B. Pikettdienst) nicht gelten --
    eine Spezialität ist Zusatz zu einem regulären Dienst, kein Ersatz: sie
    soll weder selbst eine Ruhezeit einhalten müssen, noch als "Schicht" die
    Ruhezeit/Höchstarbeitszeit/Pausen/Tagesspanne/wöchentlichen freien Tag
    eines echten Dienstes beeinflussen. Gilt bewusst NICHT für Qualifikation/
    Jugendschutz/Absenz-Konflikt, die weiterhin auch für Spezialitäten
    gelten (siehe jeweilige _check_*-Methode) -- deshalb kein pauschaler
    Guard in clean(), sondern gezielt an den zeit-/stundenbezogenen Checks.
    Vorher an jeder betroffenen Methode als identische 2-Zeilen-Bedingung
    wiederholt.
    """

    @wraps(check_method)
    def wrapper(self, *args, **kwargs):
        if self.template.category == TimeTemplate.Category.SPECIAL:
            return None
        return check_method(self, *args, **kwargs)

    return wrapper


class ShiftAssignment(TenantScopedModel):
    """Die einzelne Zuweisung im Planblatt: ein Mitarbeiter, ein Tag, ein Time Template."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="assignments")
    date = models.DateField()
    template = models.ForeignKey(TimeTemplate, on_delete=models.PROTECT, related_name="assignments")
    note = models.CharField(max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        # README Punkt 18 (2026-08): gelockert von ("employee", "date") auf
        # ("employee", "date", "template") -- geteilte Dienste (Split-Shifts,
        # z. B. Frühdienst 07-12 + Spätdienst 13-17:30 derselben Person am
        # selben Tag) sind jetzt strukturell möglich; exakt dasselbe Template
        # zweimal am selben Tag bleibt weiterhin sinnlos und blockiert.
        # Echte Übe­rschneidungen verhindert stattdessen _check_no_overlap()
        # in clean() -- eine reine DB-Constraint kann Zeit-Overlap nicht
        # ausdrücken.
        unique_together = ("employee", "date", "template")
        ordering = ["date"]

    def __str__(self):
        return f"{self.employee} \u2013 {self.date} \u2013 {self.template.name}"

    @staticmethod
    def _shift_datetimes(date, template):
        """Gesamtspanne der Schicht (erster Segmentbeginn bis letztes Segmentende)."""
        segments = _segment_datetimes(date, template.effective_segments())
        return segments[0][0], segments[-1][1]

    @classmethod
    def _shift_hours(cls, date, template):
        """
        Netto-Arbeitszeit: bei einem Template ohne explizite Segmente wie
        bisher Gesamtspanne minus `break_minutes`; bei mehreren Segmenten die
        Summe der einzelnen Blockdauern (die Lücken dazwischen sind dann
        implizit die Pause, siehe _check_break_minutes).
        """
        segments = _segment_datetimes(date, template.effective_segments())
        worked = sum((end - start for start, end in segments), timedelta())
        if len(segments) == 1:
            worked -= timedelta(minutes=template.break_minutes)
        return worked.total_seconds() / 3600

    @staticmethod
    def _required_break_minutes(net_work_minutes):
        """Pausenmindestdauer nach Art. 15 ArG, gestaffelt nach Netto-Arbeitszeit."""
        for threshold_minutes, required in BREAK_RULES_ART_15:
            if net_work_minutes > threshold_minutes:
                return required
        return 0

    @classmethod
    def _night_hours(cls, date, template):
        """
        Überlappung der Schicht mit dem Nachtarbeitszeitraum (23:00-06:00,
        Art. 10/16 ArG). Prüft sowohl das Nachtfenster der Vornacht (falls die
        Schicht z. B. um 05:00 beginnt) als auch das der aktuellen Nacht
        (falls sie über Mitternacht hinausgeht).
        """
        start, end = cls._shift_datetimes(date, template)
        total = 0.0
        for offset in (-1, 0):
            night_start = datetime.combine(date + timedelta(days=offset), NIGHT_WORK_START)
            night_end = night_start + timedelta(hours=7)  # 23:00 -> 06:00
            overlap_start = max(start, night_start)
            overlap_end = min(end, night_end)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds() / 3600
        return total

    @classmethod
    def _maternity_night_hours(cls, date, template):
        """
        Wie _night_hours(), aber mit der weiter gefassten Nachtdefinition
        20:00-06:00 aus Art. 35a Abs. 4 ArG (Mutterschutz) statt 23:00-06:00
        (Art. 16 ArG, allgemeine Nachtarbeit) -- siehe
        _check_maternity_protection().
        """
        start, end = cls._shift_datetimes(date, template)
        total = 0.0
        for offset in (-1, 0):
            night_start = datetime.combine(date + timedelta(days=offset), MATERNITY_NIGHT_BAN_START)
            night_end = night_start + timedelta(hours=10)  # 20:00 -> 06:00
            overlap_start = max(start, night_start)
            overlap_end = min(end, night_end)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds() / 3600
        return total

    @property
    def night_hours(self):
        """Informativ (Art. 17b ArG: Zeitgutschrift bei regelmässiger Nachtarbeit) -- blockiert nichts."""
        if not (self.date and self.template_id):
            return 0.0
        return round(self._night_hours(self.date, self.template), 2)

    @property
    def is_sunday(self):
        """Informativ (Art. 19/20 ArG: Sonntagszuschlag/Ersatzruhetag) -- blockiert nichts."""
        return self.date.weekday() == 6 if self.date else None

    @property
    def sunday_replacement_rest_missing(self):
        """
        Nur aussagekräftig, wenn is_sunday True ist (sonst False). Informativ
        (Art. 20 ArG: Ersatzruhetag) -- siehe
        Employee.sunday_replacement_rest_missing für Definition/
        Einschränkungen der (vereinfachten) Prüfung.
        """
        if not self.is_sunday or not self.employee_id:
            return False
        return self.employee.sunday_replacement_rest_missing(self.date)

    def clean(self):
        """
        Regel-Engine für Abschnitt 4 des Funktionsumfangs, orientiert am
        Schweizer Arbeitsgesetz (ArG). Bewusst als Warnung/Exception statt
        stiller Ablehnung, damit der Planer die Übersteuerung mit Begründung
        im UI vornehmen kann. Geprüft werden die *harten* Grenzen (Ruhezeit,
        Höchstarbeitszeit, Pausen, Tagesspanne, wöchentlicher freier Tag,
        Jugendschutz, Qualifikation, Absenzen). Nacht- und Sonntagsarbeit
        werden für Erwachsene nur *erkannt* (`night_hours`/`is_sunday`) statt
        blockiert, weil Zuschläge und Ersatzruhetage eine Lohn-/
        Planungsentscheidung sind, keine Ablehnung der Zuweisung;
        Überzeit-Zuschläge und die automatische Kontrolle des Ersatzruhetags
        sind bewusst nicht Teil dieser Engine, sondern der geplanten
        Monatsauswertung (siehe README, MVP-Fahrplan). Für minderjährige
        Mitarbeitende (`Employee.birth_date`) gelten dagegen die strengeren,
        hart durchgesetzten Regeln aus `_check_youth_protection()` -- ebenso
        hart durchgesetzt ist der Mutterschutz (`_check_maternity_protection()`)
        für Mitarbeiterinnen mit erfassten `Pregnancy`-Fällen.
        """
        if not (self.employee_id and self.template_id and self.date):
            return

        self._check_required_skill()
        self._check_rest_period()
        self._check_maximum_weekly_hours()
        self._check_break_minutes()
        self._check_daily_span()
        self._check_weekly_rest_day()
        self._check_youth_protection()
        self._check_maternity_protection()
        self._check_no_absence_conflict()
        self._check_node_has_no_children()
        self._check_no_overlap()

    def _check_node_has_no_children(self):
        """
        README Punkt 17 (Nutzer-Entscheidung): sobald eine Station Teams
        (Kind-Knoten) hat, muss jede Schicht einem Team zugewiesen werden,
        nicht direkt der Station -- sonst wäre die Zuweisung in keinem
        Team-Block des Planblatts sichtbar. Für jede heutige, teamlose
        Station ist get_children() leer, der Check ist dort ein No-Op.
        """
        if self.node.get_children().exists():
            raise ValidationError(
                f"„{self.node.name}“ hat Teams -- eine Schicht muss einem Team zugewiesen werden, "
                "nicht direkt der Station."
            )

    def _check_required_skill(self):
        required_skill_id = self.template.required_skill_id
        if not required_skill_id:
            return
        if not self.employee.skills.filter(pk=required_skill_id).exists():
            raise ValidationError(
                f"{self.employee} hat nicht die für '{self.template.name}' erforderliche "
                f"Qualifikation '{self.template.required_skill.name}'."
            )

    @skip_for_specialties
    def _check_rest_period(self):
        this_start, this_end = self._shift_datetimes(self.date, self.template)

        neighbours = (
            ShiftAssignment.all_objects.filter(
                employee=self.employee,
                date__in=[self.date - timedelta(days=1), self.date + timedelta(days=1)],
            )
            .exclude(pk=self.pk)
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template")
        )

        for other in neighbours:
            other_start, other_end = self._shift_datetimes(other.date, other.template)
            if other.date < self.date:
                gap_hours = (this_start - other_end).total_seconds() / 3600
            else:
                gap_hours = (other_start - this_end).total_seconds() / 3600

            minimum_rest_hours = self.tenant.minimum_rest_hours
            law_reference = "Art. 15a ArG"
            if self.employee.is_minor_on(self.date):
                minimum_rest_hours = max(minimum_rest_hours, YOUTH_MINIMUM_REST_HOURS)
                law_reference = "Art. 15a ArG / ArGV 5 (Jugendschutz)"
            if gap_hours < minimum_rest_hours:
                raise ValidationError(
                    f"Ruhezeit zu {other.date} ({other.template.name}) beträgt nur "
                    f"{gap_hours:.1f}h, mindestens {minimum_rest_hours}h erforderlich ({law_reference})."
                )

    @skip_for_specialties
    def _check_maximum_weekly_hours(self):
        week_start = self.date - timedelta(days=self.date.weekday())  # Montag
        week_end = week_start + timedelta(days=6)  # Sonntag

        week_assignments = (
            ShiftAssignment.all_objects.filter(
                employee=self.employee,
                date__range=[week_start, week_end],
            )
            .exclude(pk=self.pk)
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template")
        )

        total_hours = self._shift_hours(self.date, self.template)
        total_hours += sum(self._shift_hours(a.date, a.template) for a in week_assignments)

        maximum_weekly_hours = self.employee.maximum_weekly_hours or self.tenant.maximum_weekly_hours
        if total_hours > maximum_weekly_hours:
            raise ValidationError(
                f"Wochenarbeitszeit von {self.employee} wäre {total_hours:.1f}h "
                f"(Woche ab {week_start}), maximal {maximum_weekly_hours}h erlaubt (Art. 9 ArG)."
            )

    @skip_for_specialties
    def _check_break_minutes(self):
        segments = _segment_datetimes(self.date, self.template.effective_segments())
        net_work_minutes = sum((end - start).total_seconds() / 60 for start, end in segments)
        required = self._required_break_minutes(net_work_minutes)

        if len(segments) > 1:
            actual_break = sum(
                (segments[i + 1][0] - segments[i][1]).total_seconds() / 60
                for i in range(len(segments) - 1)
            )
            if actual_break < required:
                raise ValidationError(
                    f"'{self.template.name}' hat zwischen den Blöcken nur {actual_break:.0f} Min. Pause, "
                    f"bei {net_work_minutes / 60:.1f}h Arbeitszeit sind mindestens {required} Min. "
                    f"vorgeschrieben (Art. 15 ArG)."
                )
        elif self.template.break_minutes < required:
            raise ValidationError(
                f"'{self.template.name}' hat nur {self.template.break_minutes} Min. Pause hinterlegt, "
                f"bei {net_work_minutes / 60:.1f}h Arbeitszeit sind mindestens {required} Min. "
                f"vorgeschrieben (Art. 15 ArG)."
            )

    def _same_day_exclude_pks(self):
        """
        Welche Zuweisungen bei "was ist sonst noch an diesem Tag" (Tagesspanne,
        Überschneidung) ignoriert werden -- normalerweise nur sich selbst.
        swap()/ShiftTradeRequest.approve() setzen das transiente Attribut
        _overlap_exclude_pks zusätzlich auf den jeweils anderen
        Tauschpartner, dessen Datenbank-Zeile während der Transaktion noch
        den Vor-Tausch-Stand zeigt und sonst fälschlich mitgezählt würde --
        exakt dasselbe Muster wie validate_unique=False in denselben
        Methoden. Gemeinsam genutzt von _check_daily_span() und
        _check_no_overlap().
        """
        exclude_pks = getattr(self, "_overlap_exclude_pks", None)
        if exclude_pks is None:
            exclude_pks = [self.pk] if self.pk else []
        return exclude_pks

    @skip_for_specialties
    def _check_daily_span(self):
        """
        Art. 10 Abs. 3 ArG: Arbeitsbeginn bis Arbeitsende INKLUSIVE Pausen --
        bei geteilten Diensten (README Punkt 18, Split-Shifts) ist das die
        Spanne über ALLE Zuweisungen desselben Tages hinweg (erster
        Arbeitsbeginn bis letztes Arbeitsende), nicht nur die Spanne dieser
        einen Zuweisung -- sonst liesse sich die gesetzliche Tagesgrenze
        durch Aufteilen in mehrere kurze Templates am selben Tag umgehen.
        Ohne weitere Zuweisungen an diesem Tag (der bisherige Normalfall)
        ist das Ergebnis identisch zur vorherigen, Template-einzelnen
        Berechnung.
        """
        same_day = (
            ShiftAssignment.all_objects.filter(employee=self.employee, date=self.date)
            .exclude(pk__in=self._same_day_exclude_pks())
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template")
        )
        spans = [self._shift_datetimes(self.date, self.template)]
        spans += [self._shift_datetimes(self.date, a.template) for a in same_day]
        span_hours = (max(e for _, e in spans) - min(s for s, _ in spans)).total_seconds() / 3600
        maximum_daily_span_hours = self.tenant.maximum_daily_span_hours
        if span_hours > maximum_daily_span_hours:
            raise ValidationError(
                f"Tagesspanne von {self.employee} am {self.date} beträgt {span_hours:.1f}h "
                f"(erster Arbeitsbeginn bis letztes Arbeitsende), maximal {maximum_daily_span_hours}h "
                "erlaubt (Art. 10 ArG)."
            )

    @staticmethod
    def _shifts_overlap(date, template_a, template_b):
        """Zeit-Überlappung zweier Templates am selben Tag (README Punkt 18)."""
        a_start, a_end = ShiftAssignment._shift_datetimes(date, template_a)
        b_start, b_end = ShiftAssignment._shift_datetimes(date, template_b)
        return a_start < b_end and b_start < a_end

    @classmethod
    def _overlapping_conflict(cls, employee_id, date, template, exclude_pks):
        """
        Sucht unter den bestehenden Zuweisungen derselben Person am selben
        Tag (ausser exclude_pks) eine, die sich zeitlich mit `template`
        überschneidet. Gemeinsame Basis für _check_no_overlap() (Regel-Engine)
        sowie die manuellen Konfliktchecks in swap()/ShiftTradeRequest.
        approve() -- die wegen validate_unique=False (siehe dort) nicht auf
        den eingebauten Unique-Check zurückgreifen können und vor Punkt 18
        stattdessen naiv "irgendeine andere Zuweisung an diesem Tag" als
        Konflikt werteten. Mit Split-Shifts ist das jetzt zu grob -- zwei
        einander nicht überschneidende Zuweisungen am selben Tag sind gültig.

        Nutzer-Feedback (2026-08): eine Spezialität (category == "special",
        z. B. Pikettdienst) ist additiv zu einem regulären Dienst, kein
        Slot-Konkurrent -- sie kann daher nie in Konflikt geraten, weder als
        die neue Zuweisung (früher Return) noch als vorhandener Kandidat
        (aus der Kandidatenliste ausgeschlossen). Gilt automatisch auch für
        swap()/ShiftTradeRequest.approve(), die diese Methode mitverwenden.
        """
        if template.category == TimeTemplate.Category.SPECIAL:
            return None
        candidates = (
            cls.all_objects.filter(employee_id=employee_id, date=date)
            .exclude(pk__in=exclude_pks)
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .select_related("template")
        )
        for other in candidates:
            if cls._shifts_overlap(date, template, other.template):
                return other
        return None

    def _check_no_overlap(self):
        """
        README Punkt 18 (Split-Shifts): mehrere Zuweisungen derselben Person
        am selben Tag sind seit der Lockerung von unique_together erlaubt,
        dürfen sich aber nicht zeitlich überschneiden (sonst wäre dieselbe
        Person an zwei Orten gleichzeitig eingeplant). exclude_pks siehe
        _same_day_exclude_pks().
        """
        exclude_pks = self._same_day_exclude_pks()
        conflict = self._overlapping_conflict(
            self.employee_id, self.date, self.template, exclude_pks=exclude_pks
        )
        if conflict:
            raise ValidationError(
                f"{self.employee} hat am {self.date} bereits '{conflict.template.name}' "
                f"({conflict.template.start_time.strftime('%H:%M')}–"
                f"{conflict.template.end_time.strftime('%H:%M')}), das sich zeitlich mit "
                f"'{self.template.name}' überschneidet."
            )

    @skip_for_specialties
    def _check_weekly_rest_day(self):
        # Ein Tag mit nur einem Pikettdienst bleibt ein freier Tag, siehe
        # skip_for_specialties-Docstring.
        week_start = self.date - timedelta(days=self.date.weekday())  # Montag
        week_dates = {week_start + timedelta(days=i) for i in range(7)}

        occupied_dates = set(
            ShiftAssignment.all_objects.filter(
                employee=self.employee,
                date__range=[week_start, week_start + timedelta(days=6)],
            )
            .exclude(pk=self.pk)
            .exclude(template__category=TimeTemplate.Category.SPECIAL)
            .values_list("date", flat=True)
        )
        occupied_dates.add(self.date)

        if week_dates.issubset(occupied_dates):
            raise ValidationError(
                f"{self.employee} hätte in der Woche ab {week_start} keinen freien Tag mehr "
                "(Art. 21 ArG: mindestens ein ganzer freier Tag pro Woche)."
            )

    def _check_youth_protection(self):
        """
        Vereinfachter Jugendschutz-Check (ArGV 5, Verordnung 5 zum
        Arbeitsgesetz) für unter 18-jährige Mitarbeitende: kein Nachtarbeit,
        keine Sonntagsarbeit. Die erhöhte Mindestruhezeit (12h) wird bereits
        in _check_rest_period() berücksichtigt. Bildet nicht alle
        gesetzlichen Ausnahmen ab (z. B. Berufsbildung mit Nachtarbeit in
        bestimmten Branchen, bewilligte Sonntagsarbeit in Gesundheitsberufen
        unter Auflagen) -- bei Lernenden/Auszubildenden im Betrieb empfiehlt
        sich eine arbeitsrechtliche Prüfung der konkreten Ausnahmetatbestände.
        """
        if not self.employee.is_minor_on(self.date):
            return

        if self.night_hours > 0:
            raise ValidationError(
                f"{self.employee} ist minderjährig: Nachtarbeit (23:00–06:00) ist für unter "
                "18-Jährige grundsätzlich untersagt (ArGV 5)."
            )
        if self.is_sunday:
            raise ValidationError(
                f"{self.employee} ist minderjährig: Sonntagsarbeit ist für unter 18-Jährige "
                "grundsätzlich untersagt (ArGV 5)."
            )

    def _check_maternity_protection(self):
        """
        Mutterschutz (Art. 35a ArG, MVP-Fahrplan Block 1.15), hart durchgesetzt
        analog _check_youth_protection() -- Grundlage ist Employee.
        is_maternity_protected_on(), das über alle erfassten Pregnancy-Fälle
        iteriert. Spezialitäten (category=SPECIAL) gelten anders als bei den
        stunden-/zeitbezogenen Checks oben bewusst NICHT als Ausnahme (siehe
        _check_rest_period()) -- ein Beschäftigungsverbot betrifft auch
        Pikettdienst o. Ä., nicht nur reguläre Dienste.
        """
        status = self.employee.is_maternity_protected_on(self.date)
        if not status:
            return
        if status == "full_ban":
            raise ValidationError(
                f"{self.employee} befindet sich im Mutterschutz: generelles Beschäftigungsverbot "
                "in den ersten 8 Wochen nach der Niederkunft (Art. 35a Abs. 3 ArG)."
            )
        if status == "consent_required":
            raise ValidationError(
                f"{self.employee} befindet sich im Mutterschutz: Beschäftigung in der 9.–16. "
                "Woche nach der Niederkunft ist nur mit Einverständnis der Mitarbeiterin zulässig "
                "(Art. 35a Abs. 3 ArG) -- diese App erfasst kein Einverständnis und blockiert die "
                "Zuweisung deshalb vorsorglich."
            )
        if status == "night_ban" and self._maternity_night_hours(self.date, self.template) > 0:
            raise ValidationError(
                f"{self.employee} ist schwanger (ab der 8. Woche vor dem Termin): Beschäftigung "
                "zwischen 20:00 und 06:00 ist untersagt (Art. 35a Abs. 4 ArG)."
            )

    def _check_no_absence_conflict(self):
        # Nur genehmigte Absenzen blockieren -- ein offener (PENDING) Antrag
        # soll die Planung nicht schon vor der Freigabe einschränken (Block 2.3).
        # Nutzer-Feedback (2026-08, Halbtags-Absenzen): eine "nur vormittags"/
        # "nur nachmittags"-Absenz blockiert nur Dienste, die zeitlich in
        # diese Tageshälfte fallen -- ein Nachmittagsdienst bleibt an einem
        # Vormittag-frei-Tag also weiterhin planbar. Ganztags-Absenzen (der
        # Normalfall) verhalten sich unverändert wie zuvor.
        shift_start, shift_end = self._shift_datetimes(self.date, self.template)
        candidates = Absence.all_objects.filter(
            employee=self.employee,
            status=Absence.Status.APPROVED,
            start_date__lte=self.date,
            end_date__gte=self.date,
        ).select_related("type")
        for conflict in candidates:
            absence_start, absence_end = conflict._half_day_window(self.date, conflict.day_portion)
            if shift_start < absence_end and absence_start < shift_end:
                # Nutzer-Feedback (2026-08): ein Dienst, der auch die jeweils
                # andere (von der Halbtags-Absenz nicht beanspruchte)
                # Tageshälfte abdeckt -- ein durchgehender Dienst über Mittag
                # hinweg --, wird von einer Halbtags-Absenz nicht blockiert
                # (Krankheit/Ferien sind weiterhin Arbeitszeit im Sinne der
                # Lohnfortzahlungspflicht, der Dienst bleibt unverändert
                # stehen). Siehe Absence._shift_extends_into_other_half().
                if Absence._shift_extends_into_other_half(
                    shift_start, shift_end, self.date, conflict.day_portion
                ):
                    continue
                portion = (
                    ""
                    if conflict.day_portion == Absence.DayPortion.FULL
                    else f", {conflict.get_day_portion_display()}"
                )
                raise ValidationError(
                    f"{self.employee} hat am {self.date} eine genehmigte Abwesenheit "
                    f"({conflict.type.name}, {conflict.start_date}–{conflict.end_date}{portion})."
                )

    @classmethod
    def swap(cls, first_id, second_id):
        """
        Echter Swap zweier Zuweisungen im Drag & Drop (README Block 2.8):
        tauscht employee_id, date UND node_id zwischen den beiden Zeilen als
        Einheit -- template/note/history bleiben bei ihrer bisherigen
        Zeile. Damit landet exakt das, was vorher in der jeweils ANDEREN
        Zelle stand, in der eigenen Zelle -- unabhängig davon, ob Quelle und
        Ziel denselben Tag/dieselbe Team-Zeile betreffen. Anders als
        ShiftTradeRequest.approve() (das bewusst nur employee_id tauscht und
        date unangetastet lässt -- dort korrekt, weil ein Diensttausch "mein
        Montag gegen deinen Dienstag" per Definition beide Daten unverändert
        lässt) kann ein Grid-Drag jede beliebige Quell-/Zielzelle
        kombinieren. node_id ergibt sich automatisch korrekt aus der
        jeweils anderen Zeile, weil eine gezogene Zuweisung immer node ===
        rowNodeId ihrer eigenen Zeile hat (Team-Gruppierung, Punkt 17) --
        kein zusätzlicher Parameter nötig.

        Seit README Punkt 18 (Split-Shifts) kann ein Tag mehr als eine
        Zuweisung derselben Person haben -- ein "Konflikt" ist daher nicht
        mehr "irgendeine andere Zuweisung an diesem Tag", sondern nur noch
        eine, die sich zeitlich mit der (neuen) eigenen überschneidet (siehe
        _overlapping_conflict()). Der manuelle Drittkonflikt-Check unten
        bleibt als Absicherung drin, obwohl _check_no_overlap() (via
        full_clean() unten, mit demselben Ausschluss beider Tauschpartner
        über _overlap_exclude_pks) ihn strukturell bereits mit abdeckt --
        günstige Absicherung gegen künftige Änderungen an dieser Methode.
        """
        first_id, second_id = int(first_id), int(second_id)
        if first_id == second_id:
            raise ValidationError("Kann nicht mit sich selbst getauscht werden.")
        with transaction.atomic():
            # Deterministische Sperrreihenfolge unabhängig von der
            # Aufrufreihenfolge, sonst Deadlock-Risiko bei gegenläufig
            # geordneten Swap-Requests.
            lo_id, hi_id = sorted([first_id, second_id])
            lo = cls.all_objects.select_for_update().get(pk=lo_id)
            hi = cls.all_objects.select_for_update().get(pk=hi_id)
            first = lo if lo.pk == first_id else hi
            second = hi if hi.pk == second_id else lo

            first_orig = (first.employee_id, first.date, first.node_id)
            second_orig = (second.employee_id, second.date, second.node_id)
            first.employee_id, first.date, first.node_id = second_orig
            second.employee_id, second.date, second.node_id = first_orig

            # validate_unique=False + _overlap_exclude_pks: der eingebaute
            # Unique-Check sowie _check_no_overlap() sähen während dieser
            # Transaktion noch den unveränderten DB-Stand der jeweils
            # anderen Zeile und würden beim Tauschen fälschlich einen
            # Konflikt mit sich selbst melden. Konflikte mit echten Dritten
            # werden unten separat geprüft (siehe Docstring oben).
            first._overlap_exclude_pks = [first.pk, second.pk]
            second._overlap_exclude_pks = [first.pk, second.pk]
            first.full_clean(validate_unique=False)
            second.full_clean(validate_unique=False)
            for assignment, other_pk in ((first, second.pk), (second, first.pk)):
                conflict = cls._overlapping_conflict(
                    assignment.employee_id, assignment.date, assignment.template,
                    exclude_pks=[assignment.pk, other_pk],
                )
                if conflict:
                    raise ValidationError(
                        f"{assignment.employee} hat am {assignment.date} bereits '{conflict.template.name}', "
                        f"das sich zeitlich mit '{assignment.template.name}' überschneidet."
                    )

            # DB-Constraint (employee, date, template) verlangt einen
            # Zwischenschritt beim Tauschen zweier Zeilen -- sonst kollidiert
            # das erste save() mit dem noch nicht aktualisierten zweiten
            # Datensatz.
            cls.all_objects.filter(pk=first.pk).update(date=date.max)
            second.save()
            first.save()
        return first, second


class ShiftPreference(TenantScopedModel):
    """
    Wunschfrei/Wunschdienst (MVP-Fahrplan Block 2.13): ein Hinweis der
    Mitarbeitenden an den Planer -- standardmässig (PENDING) KEIN Anspruch
    und KEINE Sperre, anders als Absence. Höchstpersönlich: nur die
    betroffene Person selbst darf ihre eigenen Wünsche anlegen (siehe
    ShiftPreferencePermission) -- anders als bei Absence, wo Admin/Planer
    für andere anlegen dürfen. Ändern/Löschen bleibt der Mitarbeiter-Rolle
    nur erlaubt, solange der Wunsch noch nicht entschieden ist (status ==
    PENDING) -- analog zu Absence, siehe OwnEmployeeRecordPermission.

    Nutzer-Feedback (2026-08, Automatisierte Planung mit Auffülldienst):
    "auch hier braucht es einen Genehmigungsprozess" -- ein von Admin/Planer
    freigegebener Wunsch soll für die Automatik hart gelten (kein
    überschreibbarer Kompromiss mehr wie bei PENDING), ein abgelehnter wie
    nicht vorhanden. approve()/reject() (Block 2.3-Muster, siehe Absence)
    ergänzen deshalb jetzt einen echten Status statt der bisherigen reinen
    Selbstauskunft ohne Entscheidungsschritt. Die Auswirkung auf den Solver
    lebt in scheduling.planning (APPROVED hart, PENDING weiter weich wie
    bisher, REJECTED ignoriert), nicht hier im Model.

    Ein Eintrag pro Mitarbeiter und Tag (unique_together), damit sich
    Wunschfrei und Wunschdienst am selben Tag nicht widersprechen können.
    """

    class Type(models.TextChoices):
        FREE = "wunschfrei", "Wunschfrei"
        SHIFT = "wunschdienst", "Wunschdienst"

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        APPROVED = "approved", "Freigegeben"
        REJECTED = "rejected", "Abgelehnt"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="shift_preferences")
    date = models.DateField()
    type = models.CharField(max_length=20, choices=Type.choices)
    template = models.ForeignKey(
        TimeTemplate,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        help_text="Nur bei Wunschdienst gesetzt -- der gewünschte Schichttyp.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("employee", "date")
        ordering = ["date"]

    def __str__(self):
        return f"{self.employee} – {self.get_type_display()} ({self.date})"

    def clean(self):
        if self.type == self.Type.SHIFT and not self.template_id:
            raise ValidationError("Wunschdienst braucht einen gewünschten Schichttyp.")
        if self.type == self.Type.FREE and self.template_id:
            raise ValidationError("Wunschfrei darf keinen Schichttyp haben.")

    def approve(self):
        """Admin/Planer-Freigabe -- macht den Wunsch für die Automatik hart (siehe scheduling.planning)."""
        if self.status != ShiftPreference.Status.PENDING:
            raise ValidationError("Nur offene Wünsche können freigegeben werden.")
        self.status = ShiftPreference.Status.APPROVED
        self.save(update_fields=["status"])

    def reject(self):
        """Admin/Planer lehnt einen offenen Wunsch ab -- für die Automatik dann wie nicht vorhanden."""
        if self.status != ShiftPreference.Status.PENDING:
            raise ValidationError("Nur offene Wünsche können abgelehnt werden.")
        self.status = ShiftPreference.Status.REJECTED
        self.save(update_fields=["status"])


class ShiftTradeRequest(TenantScopedModel):
    """
    Diensttausch (Abschnitt 7): ein Mitarbeiter bietet eine eigene Schicht an,
    entweder zur einfachen Übernahme durch target_employee (target_assignment
    leer) oder als echten Tausch gegen eine konkrete Schicht von
    target_employee (target_assignment gesetzt).

    Zweistufiger Genehmigungs-Workflow (MVP-Fahrplan, Block 2.3):
    1. Die Zielperson stimmt über accept() zu -> Status EMPLOYEE_ACCEPTED.
       Das vollzieht den Tausch NOCH NICHT.
    2. Admin/Planer geben über approve() frei -> erst hier wird der Tausch
       tatsächlich vollzogen, inkl. voller Regel-Engine-Prüfung (Ruhezeit,
       Höchstarbeitszeit, Qualifikation, Absenzen) für die resultierende(n)
       Zuweisung(en). approve() kann auch direkt aus PENDING aufgerufen
       werden, falls Admin/Planer die Zustimmung z. B. telefonisch eingeholt
       haben und nicht auf den Klick der Zielperson warten wollen.
    reject() lehnt eine offene oder bereits von der Zielperson angenommene
    Anfrage als Admin/Planer ab (z. B. wegen eines Konflikts, den nur der
    Planer sieht) -- zu unterscheiden von decline() (Zielperson lehnt selbst
    ab) und cancel() (anbietende Person zieht zurück).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        EMPLOYEE_ACCEPTED = "employee_accepted", "Von Mitarbeiter angenommen, wartet auf Freigabe"
        ACCEPTED = "accepted", "Angenommen"
        DECLINED = "declined", "Abgelehnt"
        CANCELLED = "cancelled", "Zurückgezogen"
        REJECTED = "rejected", "Von Planer abgelehnt"

    requester_assignment = models.ForeignKey(
        ShiftAssignment, on_delete=models.CASCADE, related_name="trade_requests_as_source"
    )
    target_employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="trade_requests_received"
    )
    target_assignment = models.ForeignKey(
        ShiftAssignment,
        on_delete=models.CASCADE,
        related_name="trade_requests_as_target",
        null=True,
        blank=True,
        help_text="Optional: konkrete Gegenschicht für einen echten Tausch. Leer lassen, "
        "wenn target_employee die Schicht einfach übernehmen soll.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.requester_assignment} → {self.target_employee} ({self.get_status_display()})"

    def clean(self):
        if not (self.requester_assignment_id and self.target_employee_id):
            return
        if self.requester_assignment.employee_id == self.target_employee_id:
            raise ValidationError("Zielmitarbeiter ist identisch mit dem anbietenden Mitarbeiter.")
        if self.target_assignment_id and self.target_assignment.employee_id != self.target_employee_id:
            raise ValidationError({"target_assignment": "Zielschicht gehört nicht zum Zielmitarbeiter."})

    def accept(self):
        """Zielperson stimmt zu. Vollzieht den Tausch noch NICHT -- das passiert erst in approve()."""
        if self.status != self.Status.PENDING:
            raise ValidationError("Nur offene Tauschanfragen können angenommen werden.")
        self.status = self.Status.EMPLOYEE_ACCEPTED
        self.save(update_fields=["status"])

    def approve(self):
        """
        Admin/Planer-Freigabe: vollzieht den eigentlichen Tausch. Wirft
        ValidationError (z. B. bei Ruhezeit- oder Qualifikationskonflikt
        durch den Tausch), ohne etwas zu speichern -- die Anfrage bleibt
        dann im bisherigen Status und der Planer sieht den Grund im UI.
        """
        if self.status not in (self.Status.PENDING, self.Status.EMPLOYEE_ACCEPTED):
            raise ValidationError(
                "Nur offene oder von der Zielperson angenommene Anfragen können freigegeben werden."
            )

        with transaction.atomic():
            requester_assignment = ShiftAssignment.all_objects.select_for_update().get(
                pk=self.requester_assignment_id
            )
            original_employee_id = requester_assignment.employee_id

            if self.target_assignment_id:
                target_assignment = ShiftAssignment.all_objects.select_for_update().get(
                    pk=self.target_assignment_id
                )
                requester_assignment.employee_id = self.target_employee_id
                target_assignment.employee_id = original_employee_id
                # validate_unique=False + _overlap_exclude_pks + manueller
                # Konfliktcheck + Zwischenschritt: liegen beide Zuweisungen
                # auf demselben Datum (der häufigste Tausch-Fall), sähen der
                # eingebaute Unique-Check sowie _check_no_overlap() beim
                # Prüfen der ersten Zuweisung noch den unveränderten DB-Stand
                # der zweiten und würden fälschlich einen Konflikt mit sich
                # selbst melden -- siehe ShiftAssignment.swap() für dasselbe
                # Muster inkl. Begründung. Seit README Punkt 18 (Split-
                # Shifts) ist "eine andere Zuweisung am selben Tag" für sich
                # kein Konflikt mehr, nur eine zeitlich überschneidende.
                requester_assignment._overlap_exclude_pks = [requester_assignment.pk, target_assignment.pk]
                target_assignment._overlap_exclude_pks = [requester_assignment.pk, target_assignment.pk]
                requester_assignment.full_clean(validate_unique=False)
                target_assignment.full_clean(validate_unique=False)
                for assignment, other_pk in (
                    (requester_assignment, target_assignment.pk),
                    (target_assignment, requester_assignment.pk),
                ):
                    conflict = ShiftAssignment._overlapping_conflict(
                        assignment.employee_id, assignment.date, assignment.template,
                        exclude_pks=[assignment.pk, other_pk],
                    )
                    if conflict:
                        raise ValidationError(
                            f"{assignment.employee} hat am {assignment.date} bereits '{conflict.template.name}', "
                            f"das sich zeitlich mit '{assignment.template.name}' überschneidet."
                        )
                ShiftAssignment.all_objects.filter(pk=requester_assignment.pk).update(date=date.max)
                target_assignment.save()
                requester_assignment.save()
            else:
                requester_assignment.employee_id = self.target_employee_id
                requester_assignment.full_clean()
                requester_assignment.save()

            self.status = self.Status.ACCEPTED
            self.resolved_at = timezone.now()
            self.save(update_fields=["status", "resolved_at"])

    def reject(self):
        """Admin/Planer lehnt eine offene oder von der Zielperson angenommene Anfrage ab."""
        if self.status not in (self.Status.PENDING, self.Status.EMPLOYEE_ACCEPTED):
            raise ValidationError(
                "Nur offene oder von der Zielperson angenommene Anfragen können abgelehnt werden."
            )
        self.status = self.Status.REJECTED
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_at"])

    def decline(self):
        """Zielperson lehnt eine offene Anfrage selbst ab -- zu unterscheiden von reject() (Planer) und cancel() (anbietende Person)."""
        if self.status != self.Status.PENDING:
            raise ValidationError("Nur offene Tauschanfragen können abgelehnt werden.")
        self.status = self.Status.DECLINED
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_at"])

    def cancel(self):
        """Anbietende Person zieht eine offene oder bereits von der Zielperson angenommene Anfrage zurück."""
        if self.status not in (self.Status.PENDING, self.Status.EMPLOYEE_ACCEPTED):
            raise ValidationError("Nur offene oder angenommene Tauschanfragen können zurückgezogen werden.")
        self.status = self.Status.CANCELLED
        self.resolved_at = timezone.now()
        self.save(update_fields=["status", "resolved_at"])


class TimeRecord(TenantScopedModel):
    """
    Ist-Arbeitszeiterfassung zu einer geplanten Schicht (Art. 73 ArGV 1:
    Pflicht zur Aufzeichnung von Beginn, Ende und Pausen der tatsächlich
    geleisteten Arbeitszeit). Ein Eintrag pro ShiftAssignment (OneToOne),
    damit Ist immer eindeutig einer Soll-Schicht zugeordnet ist.

    Zweistufig wie Absence/ShiftTradeRequest: Mitarbeitende erfassen selbst
    (Status SUBMITTED) und können ihren Eintrag bearbeiten/löschen, solange
    er noch nicht geprüft ist; Admin/Planer bestätigen über confirm()
    (Status CONFIRMED) -- reine Selbstauskunft ohne jede Kontrolle wäre
    gegenüber Behörden/Revision wenig belastbar. Admin/Planer dürfen auch
    nach der Bestätigung noch direkt korrigieren (TimeRecordPermission).
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Erfasst"
        CONFIRMED = "confirmed", "Geprüft"

    assignment = models.OneToOneField(
        ShiftAssignment, on_delete=models.CASCADE, related_name="time_record"
    )
    actual_start = models.TimeField(
        null=True,
        blank=True,
        help_text="Nur relevant, wenn das Template keine Segmente hat (Block 1.12) -- sonst siehe "
        "TimeRecordSegment. Bei Nachtschichten über Mitternacht: Endzeit < Startzeit ist erlaubt.",
    )
    actual_end = models.TimeField(null=True, blank=True)
    actual_break_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="Nur relevant ohne Segmente -- bei Segmenten ergibt sich die Pause aus den Lücken."
    )
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-assignment__date"]

    def __str__(self):
        return f"Ist-Zeit {self.assignment}"

    def effective_segments(self):
        """
        Liste von (actual_start, actual_end)-Paaren (Block 1.12): aus den
        gespeicherten TimeRecordSegment-Kindzeilen, aus noch nicht
        gespeicherten Segmenten während der Serializer-Validierung
        (`_pending_segments`, siehe TimeRecordSerializer -- die Kindzeilen
        existieren zu dem Zeitpunkt noch nicht, weil sie erst nach dem
        Speichern des Elternobjekts angelegt werden können), oder sonst als
        einzelnes Segment aus den klassischen actual_start/actual_end-Feldern
        (bisheriges Verhalten für Templates ohne Segmente).
        """
        pending = getattr(self, "_pending_segments", None)
        if pending is not None:
            ordered = sorted(pending, key=lambda s: s.get("order", 0))
            return [(s["actual_start"], s["actual_end"]) for s in ordered]
        if self.pk:
            stored = list(self.segments.order_by("order").values_list("actual_start", "actual_end"))
            if stored:
                return stored
        return [(self.actual_start, self.actual_end)]

    def _actual_datetimes_list(self):
        return _segment_datetimes(self.assignment.date, self.effective_segments())

    @property
    def actual_hours(self):
        """Netto-Arbeitszeit (Ist). Bei Segmenten die Summe der Blockdauern, sonst Spanne minus Pausenfeld."""
        segments = self._actual_datetimes_list()
        worked = sum((end - start for start, end in segments), timedelta())
        if len(segments) == 1:
            worked -= timedelta(minutes=self.actual_break_minutes)
        return round(worked.total_seconds() / 3600, 2)

    @property
    def break_minutes_total(self):
        """Gesamtpause: bei Segmenten die Summe der Lücken dazwischen, sonst das erfasste Pausenfeld."""
        segments = self._actual_datetimes_list()
        if len(segments) > 1:
            return round(
                sum(
                    (segments[i + 1][0] - segments[i][1]).total_seconds() / 60
                    for i in range(len(segments) - 1)
                )
            )
        return self.actual_break_minutes

    @property
    def deviation_minutes(self):
        """Abweichung des tatsächlichen vom geplanten Arbeitsbeginn (erstes Segment), in Minuten (positiv = später)."""
        segments = self._actual_datetimes_list()
        planned = _segment_datetimes(self.assignment.date, self.assignment.template.effective_segments())
        return round((segments[0][0] - planned[0][0]).total_seconds() / 60)

    @property
    def hours_deviation(self):
        """
        Nutzer-Feedback (2026-08): "Sichtbarkeit der IST-Zeit... grün für +,
        rot für -" -- anders als deviation_minutes (nur Start-Zeitpunkt) hier
        die Netto-Stunden-Differenz Ist ggü. Soll, damit ein Planer beim
        Bestätigen auf einen Blick sieht, ob insgesamt mehr oder weniger als
        geplant gearbeitet wurde. Wiederverwendet ShiftAssignment._shift_hours
        (dieselbe Soll-Berechnung wie im Saldo) statt einer eigenen.
        """
        return round(self.actual_hours - ShiftAssignment._shift_hours(self.assignment.date, self.assignment.template), 2)

    @property
    def end_deviation_minutes(self):
        """Abweichung des tatsächlichen vom geplanten Arbeitsende (letztes Segment), in Minuten (positiv = später)."""
        segments = self._actual_datetimes_list()
        planned = _segment_datetimes(self.assignment.date, self.assignment.template.effective_segments())
        return round((segments[-1][1] - planned[-1][1]).total_seconds() / 60)

    @property
    def break_below_minimum(self):
        """Informativ (Art. 15 ArG): true, wenn die tatsächliche Gesamtpause unter der Mindestvorgabe liegt."""
        segments = self._actual_datetimes_list()
        if len(segments) > 1:
            net_minutes = sum((end - start).total_seconds() / 60 for start, end in segments)
        else:
            gross_minutes = (segments[0][1] - segments[0][0]).total_seconds() / 60
            net_minutes = gross_minutes - self.actual_break_minutes
        required = ShiftAssignment._required_break_minutes(net_minutes)
        return self.break_minutes_total < required

    def clean(self):
        if not self.assignment_id:
            return

        actual_segments = self.effective_segments()
        if not actual_segments or any(s[0] is None or s[1] is None for s in actual_segments):
            return

        if self.assignment.date > timezone.localdate():
            raise ValidationError("Ist-Zeiten können erst nach der Schicht erfasst werden.")

        template = self.assignment.template
        planned_segments = template.effective_segments()

        if len(actual_segments) != len(planned_segments):
            raise ValidationError(
                f"'{template.name}' hat {len(planned_segments)} vorgegebene Zeitblöcke -- "
                "bitte für jeden Block eine Ist-Zeit erfassen."
            )

        if len(actual_segments) > 1:
            for i in range(len(actual_segments) - 1):
                if actual_segments[i][1] > actual_segments[i + 1][0]:
                    raise ValidationError(
                        "Die Zeitblöcke müssen chronologisch geordnet sein und dürfen sich nicht überlappen."
                    )

        planned_dt = _segment_datetimes(self.assignment.date, planned_segments)
        actual_dt = _segment_datetimes(self.assignment.date, actual_segments)

        start_deviation = abs((actual_dt[0][0] - planned_dt[0][0]).total_seconds() / 60)
        end_deviation = abs((actual_dt[-1][1] - planned_dt[-1][1]).total_seconds() / 60)

        tolerance = self.tenant.time_record_deviation_tolerance_minutes
        if (start_deviation > tolerance or end_deviation > tolerance) and not self.note:
            raise ValidationError(
                f"Abweichung vom geplanten Zeitfenster beträgt mehr als {tolerance} Minuten -- "
                "bitte eine Begründung eintragen."
            )

    def confirm(self):
        if self.status != self.Status.SUBMITTED:
            raise ValidationError("Nur erfasste (noch nicht geprüfte) Einträge können bestätigt werden.")
        self.status = self.Status.CONFIRMED
        self.save(update_fields=["status"])


class TimeRecordSegment(TenantScopedModel):
    """
    Ist-Zeit für einen einzelnen Block einer Schicht (Block 1.12). Nur
    relevant, wenn assignment.template Segmente hat -- Anzahl und Reihenfolge
    sind dadurch vorgegeben (siehe TimeRecord.clean), der Mitarbeiter
    verschiebt hier ausschliesslich die Uhrzeiten je Block, nicht deren
    Anzahl. Wird zusammen mit dem TimeRecord über TimeRecordSerializer
    geschrieben (bei jeder Änderung vollständig ersetzt statt einzeln
    aktualisiert -- die Blockliste ist kurz genug, dass das keine Rolle
    spielt).
    """

    time_record = models.ForeignKey(TimeRecord, on_delete=models.CASCADE, related_name="segments")
    order = models.PositiveSmallIntegerField()
    actual_start = models.TimeField()
    actual_end = models.TimeField()

    class Meta:
        ordering = ["order"]
        unique_together = ("time_record", "order")

    def __str__(self):
        return f"{self.time_record} #{self.order} ({self.actual_start}–{self.actual_end})"

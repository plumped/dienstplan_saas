from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from simple_history.models import HistoricalRecords
from treebeard.mp_tree import MP_Node

from core.models import TenantScopedModel

# Nachtarbeitszeitraum nach Art. 10 Abs. 1 / Art. 16 ArG (Grundregel; einzelne
# Branchenverordnungen können abweichen, hier bewusst nicht tenant-konfigurierbar
# gehalten, da es sich um eine gesetzliche Definition und nicht um einen
# betrieblichen Spielraum handelt).
NIGHT_WORK_START = time(23, 0)
NIGHT_WORK_END = time(6, 0)

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
    Feriensaldo (Block 2.7) -- Feiertage werden bewusst nicht berücksichtigt
    (kein kantonaler Feiertagskalender hinterlegt), das ist eine bekannte
    Vereinfachung.
    """
    total_days = (end_date - start_date).days + 1
    full_weeks, remainder = divmod(total_days, 7)
    count = full_weeks * 5
    for i in range(remainder):
        if (start_date + timedelta(days=full_weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


class Node(MP_Node, TenantScopedModel):
    """
    Organisationsknoten (Standort, Abteilung, Station, ...), beliebig
    verschachtelbar. Nutzt django-treebeard (Materialized Path) für
    effiziente Baumabfragen statt eines selbstgebauten parent-Felds.
    """

    name = models.CharField(max_length=200)

    node_order_by = ["name"]

    class Meta:
        ordering = ["path"]

    def __str__(self):
        return self.name


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
    nodes = models.ManyToManyField(Node, related_name="employees", blank=True)
    skills = models.ManyToManyField(Skill, related_name="employees", blank=True)
    is_active = models.BooleanField(default=True)

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

        assignments = ShiftAssignment.all_objects.filter(
            employee=self, date__range=[week_start, week_end]
        ).select_related("template", "time_record")

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
        worked_dates = set(
            ShiftAssignment.all_objects.filter(
                employee=self, date__range=[sunday_date, window_end]
            ).values_list("date", flat=True)
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
        resultierende Zeitgutschrift (Art. 17b ArG) sowie zwei Hinweise für
        Admin/Planer: fehlende Bewilligungsbestätigung und fällige
        arbeitsmedizinische Untersuchung (Art. 17c ArG). Rein informativ wie
        night_hours selbst -- blockiert keine Zuweisung, ist kein
        Rechtsrat.
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

        return {
            "year": year,
            "nights_count": nights_count,
            "night_hours": round(total_night_hours, 2),
            "is_regular": is_regular,
            "surcharge_hours": surcharge_hours,
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

    def overtime_balance(self, as_of_date=None):
        """
        Kumulierter Überstunden-Saldo als Zahl -- Kurzform von
        overtime_summary()["balance_hours"] für Aufrufer, die das
        is_provisional-Flag nicht brauchen (siehe dort für Details/Tests).
        """
        return self.overtime_summary(as_of_date)["balance_hours"]

    def overtime_summary(self, as_of_date=None):
        """
        Kumulierter Überstunden-Saldo (MVP-Fahrplan Block 2.7) -- im
        Gegensatz zu weekly_hours_summary()["overtime_hours"] (auf 0 nach
        unten begrenzt, Basis für den Zuschlag) hier die **vorzeichenbehaftete**
        Differenz Ist-Soll je Woche, aufsummiert über alle Wochen, in denen
        der Mitarbeiter mindestens eine Zuweisung hatte (plus
        overtime_balance_carryover_hours als Startwert). Wochen ohne jede
        Zuweisung tragen bewusst nichts bei -- sie würden sonst so behandelt,
        als hätte der Mitarbeiter in einer Woche vor Anstellungsbeginn oder
        in einer Lücke die volle Sollzeit verpasst.

        as_of_date=None (Normalfall, z.B. Topbar-Badge/Settings-Liste) bezieht
        *alle* Wochen mit Zuweisung ein, auch künftig geplante -- Zuweisungen
        zählen laut is_provisional/weekly_hours_summary bewusst sofort, nicht
        erst ab ihrem Datum. Wird as_of_date dagegen explizit übergeben (z.B.
        eine Saldo-Momentaufnahme "Stand <Datum>"), begrenzt das die
        einbezogenen Wochen auf date__lte=as_of_date, wie von Aufrufern
        erwartet, die bewusst einen historischen/zukünftigen Stichtag angeben.

        `is_provisional` (Block 2.7 UX-Nachbesserung): True, wenn der Saldo
        mindestens eine ungeprüfte Schicht enthält (siehe
        weekly_hours_summary). Der Saldo selbst ist trotzdem schon jetzt
        korrekt und aktuell -- er wartet nicht auf die Prüfung --, das Flag
        dient nur dazu, das im Frontend transparent zu machen, statt den
        falschen Eindruck zu erwecken, ungeprüfte Erfassungen zählten noch
        nicht mit.
        """
        assignment_qs = ShiftAssignment.all_objects.filter(employee=self)
        if as_of_date is not None:
            assignment_qs = assignment_qs.filter(date__lte=as_of_date)
        assignment_dates = assignment_qs.values_list("date", flat=True)
        week_starts = {d - timedelta(days=d.weekday()) for d in assignment_dates}

        balance = self.overtime_balance_carryover_hours
        is_provisional = False
        for week_start in week_starts:
            summary = self.weekly_hours_summary(week_start)
            balance += summary["ist_hours"] - summary["soll_hours"]
            is_provisional = is_provisional or summary["is_provisional"]
        return {"balance_hours": round(balance, 2), "is_provisional": is_provisional}

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

        absences = Absence.all_objects.filter(
            employee=self,
            type=Absence.Type.VACATION,
            status=Absence.Status.APPROVED,
            start_date__lte=year_end,
            end_date__gte=year_start,
        )
        used_days = sum(
            _count_workdays(max(a.start_date, year_start), min(a.end_date, year_end)) for a in absences
        )

        return {
            "year": year,
            "entitlement_days": entitlement_days,
            "used_days": used_days,
            "remaining_days": entitlement_days - used_days,
        }

class Absence(TenantScopedModel):
    """
    Ferien/Krankheit/Sonstiges (Abschnitt 6). Blockiert Schichtzuweisungen im
    überlappenden Zeitraum -- aber nur, solange sie APPROVED ist (siehe
    _check_no_absence_conflict auf ShiftAssignment). Genehmigungs-Workflow
    (MVP-Fahrplan, Block 2.3): Von Mitarbeitenden erstellte Absenzen starten
    als PENDING und müssen von Admin/Planer freigegeben werden
    (AbsenceViewSet.approve/reject); von Admin/Planer selbst erstellte
    Absenzen sind sofort APPROVED (AbsenceViewSet.perform_create), weil die
    Freigabe in dem Fall bereits durch die anlegende Person erfolgt ist.
    """

    class Type(models.TextChoices):
        VACATION = "vacation", "Ferien"
        SICK = "sick", "Krankheit"
        OTHER = "other", "Sonstiges"

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        APPROVED = "approved", "Genehmigt"
        REJECTED = "rejected", "Abgelehnt"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="absences")
    start_date = models.DateField()
    end_date = models.DateField()
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.VACATION)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.employee} – {self.get_type_display()} ({self.start_date}–{self.end_date})"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("Enddatum darf nicht vor dem Startdatum liegen.")


class TimeTemplate(TenantScopedModel):
    """Vordefinierter Schichttyp (Icon/Farbe/Zeitfenster), z. B. 'Frühdienst'."""

    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="time_templates")
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField(help_text="Bei Nachtschichten über Mitternacht: Endzeit < Startzeit ist erlaubt.")
    break_minutes = models.PositiveSmallIntegerField(default=0)
    icon = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=7, default="#2563eb", help_text="Hex-Farbe für die Planblatt-UI")
    required_skill = models.ForeignKey(
        Skill,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional: Qualifikation, die für diese Schicht zwingend vorhanden sein muss.",
    )

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.name} ({self.start_time}\u2013{self.end_time})"

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


class ShiftAssignment(TenantScopedModel):
    """Die einzelne Zuweisung im Planblatt: ein Mitarbeiter, ein Tag, ein Time Template."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="assignments")
    date = models.DateField()
    template = models.ForeignKey(TimeTemplate, on_delete=models.PROTECT, related_name="assignments")
    note = models.CharField(max_length=200, blank=True)

    history = HistoricalRecords()

    class Meta:
        # MVP-Annahme: ein Einsatz pro Mitarbeiter und Tag. Für Split-Shifts
        # (mehrere Templates am selben Tag) müsste das gelockert werden.
        unique_together = ("employee", "date")
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
        hart durchgesetzten Regeln aus `_check_youth_protection()`.
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
        self._check_no_absence_conflict()

    def _check_required_skill(self):
        required_skill_id = self.template.required_skill_id
        if not required_skill_id:
            return
        if not self.employee.skills.filter(pk=required_skill_id).exists():
            raise ValidationError(
                f"{self.employee} hat nicht die für '{self.template.name}' erforderliche "
                f"Qualifikation '{self.template.required_skill.name}'."
            )

    def _check_rest_period(self):
        this_start, this_end = self._shift_datetimes(self.date, self.template)

        neighbours = (
            ShiftAssignment.all_objects.filter(
                employee=self.employee,
                date__in=[self.date - timedelta(days=1), self.date + timedelta(days=1)],
            )
            .exclude(pk=self.pk)
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

    def _check_maximum_weekly_hours(self):
        week_start = self.date - timedelta(days=self.date.weekday())  # Montag
        week_end = week_start + timedelta(days=6)  # Sonntag

        week_assignments = (
            ShiftAssignment.all_objects.filter(
                employee=self.employee,
                date__range=[week_start, week_end],
            )
            .exclude(pk=self.pk)
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

    def _check_daily_span(self):
        start, end = self._shift_datetimes(self.date, self.template)
        span_hours = (end - start).total_seconds() / 3600
        maximum_daily_span_hours = self.tenant.maximum_daily_span_hours
        if span_hours > maximum_daily_span_hours:
            raise ValidationError(
                f"Tagesspanne von '{self.template.name}' beträgt {span_hours:.1f}h, "
                f"maximal {maximum_daily_span_hours}h erlaubt (Art. 10 ArG)."
            )

    def _check_weekly_rest_day(self):
        week_start = self.date - timedelta(days=self.date.weekday())  # Montag
        week_dates = {week_start + timedelta(days=i) for i in range(7)}

        occupied_dates = set(
            ShiftAssignment.all_objects.filter(
                employee=self.employee,
                date__range=[week_start, week_start + timedelta(days=6)],
            )
            .exclude(pk=self.pk)
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

    def _check_no_absence_conflict(self):
        # Nur genehmigte Absenzen blockieren -- ein offener (PENDING) Antrag
        # soll die Planung nicht schon vor der Freigabe einschränken (Block 2.3).
        conflict = Absence.all_objects.filter(
            employee=self.employee,
            status=Absence.Status.APPROVED,
            start_date__lte=self.date,
            end_date__gte=self.date,
        ).first()
        if conflict:
            raise ValidationError(
                f"{self.employee} hat am {self.date} eine genehmigte Abwesenheit "
                f"({conflict.get_type_display()}, {conflict.start_date}–{conflict.end_date})."
            )


class ShiftPreference(TenantScopedModel):
    """
    Wunschfrei/Wunschdienst (MVP-Fahrplan Block 2.13): ein Hinweis der
    Mitarbeitenden an den Planer, KEIN Anspruch und KEINE Sperre -- anders
    als Absence blockiert das hier nichts in der Regel-Engine
    (ShiftAssignment.clean() prüft ShiftPreference bewusst nicht) und
    braucht keinen Genehmigungs-Workflow. Höchstpersönlich: nur die
    betroffene Person selbst darf ihre eigenen Wünsche anlegen/ändern/
    löschen, nicht einmal Admin/Planer dürfen das stellvertretend tun
    (siehe ShiftPreferencePermission) -- anders als bei Absence, wo
    Admin/Planer für andere anlegen dürfen.

    Ein Eintrag pro Mitarbeiter und Tag (unique_together), damit sich
    Wunschfrei und Wunschdienst am selben Tag nicht widersprechen können.
    """

    class Type(models.TextChoices):
        FREE = "wunschfrei", "Wunschfrei"
        SHIFT = "wunschdienst", "Wunschdienst"

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
                requester_assignment.full_clean()
                target_assignment.full_clean()
                requester_assignment.save()
                target_assignment.save()
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

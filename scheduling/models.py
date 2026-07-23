from datetime import datetime, time, timedelta

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

    history = HistoricalRecords()

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def is_minor_on(self, reference_date):
        """True, wenn der Mitarbeiter am reference_date unter 18 Jahre alt ist."""
        if not self.birth_date:
            return False
        age = reference_date.year - self.birth_date.year - (
            (reference_date.month, reference_date.day) < (self.birth_date.month, self.birth_date.day)
        )
        return age < 18


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
        start = datetime.combine(date, template.start_time)
        end = datetime.combine(date, template.end_time)
        if template.end_time <= template.start_time:
            end += timedelta(days=1)  # Nachtschicht über Mitternacht
        return start, end

    @classmethod
    def _shift_hours(cls, date, template):
        start, end = cls._shift_datetimes(date, template)
        worked = (end - start) - timedelta(minutes=template.break_minutes)
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

        maximum_weekly_hours = self.tenant.maximum_weekly_hours
        if total_hours > maximum_weekly_hours:
            raise ValidationError(
                f"Wochenarbeitszeit von {self.employee} wäre {total_hours:.1f}h "
                f"(Woche ab {week_start}), maximal {maximum_weekly_hours}h erlaubt (Art. 9 ArG)."
            )

    def _check_break_minutes(self):
        net_work_minutes = self._shift_hours(self.date, self.template) * 60
        required = self._required_break_minutes(net_work_minutes)
        if self.template.break_minutes < required:
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

from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from simple_history.models import HistoricalRecords
from treebeard.mp_tree import MP_Node

from core.models import TenantScopedModel

# Gesetzliches Mindest-Ruhezeit-Minimum (Platzhalter, siehe Docstring von
# ShiftAssignment.clean). In einer echten Regel-Engine würde das pro Tenant
# konfigurierbar sein (Abschnitt 4 im Funktionsumfang).
MINIMUM_REST_HOURS = 11

# Wöchentliche Höchstarbeitszeit (Platzhalter, wie MINIMUM_REST_HOURS -- in
# einer echten Regel-Engine pro Tenant/Branche konfigurierbar, z. B.
# unterschiedliche ArG-Grenzwerte für Gesundheitspersonal).
MAXIMUM_WEEKLY_HOURS = 50


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
    employment_pct = models.PositiveSmallIntegerField(help_text="Pensum in %, z. B. 80")
    nodes = models.ManyToManyField(Node, related_name="employees", blank=True)
    skills = models.ManyToManyField(Skill, related_name="employees", blank=True)
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Absence(TenantScopedModel):
    """Ferien/Krankheit/Sonstiges (Abschnitt 6). Blockiert Schichtzuweisungen im überlappenden Zeitraum."""

    class Type(models.TextChoices):
        VACATION = "vacation", "Ferien"
        SICK = "sick", "Krankheit"
        OTHER = "other", "Sonstiges"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="absences")
    start_date = models.DateField()
    end_date = models.DateField()
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.VACATION)
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

    def clean(self):
        """
        Regel-Engine-Platzhalter für Abschnitt 4 des Funktionsumfangs:
        Ruhezeit, Wochenhöchstarbeitszeit und Pflicht-Qualifikation. Bewusst
        als Warnung/Exception statt stiller Ablehnung, damit der Planer die
        Übersteuerung mit Begründung im UI vornehmen kann. Eine vollständige
        Regel-Engine würde diese Grenzwerte pro Tenant/Branche konfigurierbar
        machen und weitere Regeln (z. B. Mindestbesetzung) ergänzen.
        """
        if not (self.employee_id and self.template_id and self.date):
            return

        self._check_required_skill()
        self._check_rest_period()
        self._check_maximum_weekly_hours()
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

            if gap_hours < MINIMUM_REST_HOURS:
                raise ValidationError(
                    f"Ruhezeit zu {other.date} ({other.template.name}) beträgt nur "
                    f"{gap_hours:.1f}h, mindestens {MINIMUM_REST_HOURS}h erforderlich."
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

        if total_hours > MAXIMUM_WEEKLY_HOURS:
            raise ValidationError(
                f"Wochenarbeitszeit von {self.employee} wäre {total_hours:.1f}h "
                f"(Woche ab {week_start}), maximal {MAXIMUM_WEEKLY_HOURS}h erlaubt."
            )

    def _check_no_absence_conflict(self):
        conflict = Absence.all_objects.filter(
            employee=self.employee,
            start_date__lte=self.date,
            end_date__gte=self.date,
        ).first()
        if conflict:
            raise ValidationError(
                f"{self.employee} hat am {self.date} eine Abwesenheit "
                f"({conflict.get_type_display()}, {conflict.start_date}–{conflict.end_date})."
            )


class ShiftTradeRequest(TenantScopedModel):
    """
    Diensttausch (Abschnitt 7): ein Mitarbeiter bietet eine eigene Schicht an,
    entweder zur einfachen Übernahme durch target_employee (target_assignment
    leer) oder als echten Tausch gegen eine konkrete Schicht von
    target_employee (target_assignment gesetzt). Bewusst ohne Genehmigungs-
    Workflow durch Vorgesetzte (Abschnitt 7 erwähnt das als optionale
    Erweiterung) -- accept() prüft aber die volle Regel-Engine (Ruhezeit,
    Höchstarbeitszeit, Qualifikation, Absenzen) für die resultierende(n)
    Zuweisung(en), bevor der Tausch tatsächlich vollzogen wird.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        ACCEPTED = "accepted", "Angenommen"
        DECLINED = "declined", "Abgelehnt"
        CANCELLED = "cancelled", "Zurückgezogen"

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
        """
        Vollzieht den Tausch. Wirft ValidationError (z. B. bei Ruhezeit- oder
        Qualifikationskonflikt durch den Tausch), ohne etwas zu speichern --
        die Anfrage bleibt dann 'pending' und der Planer sieht den Grund im UI.
        """
        if self.status != self.Status.PENDING:
            raise ValidationError("Nur offene Tauschanfragen können angenommen werden.")

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

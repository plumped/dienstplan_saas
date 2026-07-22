from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords
from treebeard.mp_tree import MP_Node

from core.models import TenantScopedModel

# Gesetzliches Mindest-Ruhezeit-Minimum (Platzhalter, siehe Docstring von
# ShiftAssignment.clean). In einer echten Regel-Engine würde das pro Tenant
# konfigurierbar sein (Abschnitt 4 im Funktionsumfang).
MINIMUM_REST_HOURS = 11


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

    def clean(self):
        """
        Rudimentärer Ruhezeit-Check als Platzhalter für die volle Regel-Engine
        aus Abschnitt 4 des Funktionsumfangs (dort sollen später auch
        Höchstarbeitszeit und Qualifikationscheck rein). Bewusst als
        Warnung/Exception statt stiller Ablehnung, damit der Planer die
        Übersteuerung mit Begründung im UI vornehmen kann.
        """
        if not (self.employee_id and self.template_id and self.date):
            return

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
                    f"Ruhezeit zu {other.date} ({other.template.name}) betr\u00e4gt nur "
                    f"{gap_hours:.1f}h, mindestens {MINIMUM_REST_HOURS}h erforderlich."
                )

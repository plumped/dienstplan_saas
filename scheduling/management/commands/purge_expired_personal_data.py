from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from scheduling.models import Absence, Employee, Pregnancy


class Command(BaseCommand):
    """
    README Block 5 (Datenschutz & Rechtliches, revDSG): Löschkonzept, gesetzlich begründete
    Fristen statt einer pauschalen Löschung nach X Jahren -- siehe docs/legal/
    datenschutzerklaerung.md Ziffer 7 für die Herleitung:

    - Mitarbeitendendaten (Employee) werden ANONYMISIERT (nicht hart gelöscht), sobald
      termination_date mindestens 10 Jahre zurückliegt (Art. 958f OR, Aufbewahrungspflicht für
      lohnrelevante Geschäftsunterlagen) -- ein Hard-Delete würde die FK-Integrität zu
      ShiftAssignment/TimeRecord/Absence verletzen, die als Buchungsbeleg genauso lange stehen
      bleiben müssen. Name/Geburtsdatum werden geleert, der verknüpfte Login-Account (falls
      vorhanden) wird hart gelöscht (analog core.views.MembershipViewSet.perform_destroy).
    - Freitextnotizen zu besonderen Personendaten (Art. 5 lit. c revDSG: Absence.note bei
      krankheitsbezogenen AbsenceTypes, Pregnancy.notes) werden bereits nach 2 Jahren geleert --
      unabhängig vom Employee-Status, da hierfür (anders als bei Lohnunterlagen) keine
      eigenständige gesetzliche Aufbewahrungspflicht besteht (Verhältnismässigkeitsgrundsatz,
      Art. 6 Abs. 2 revDSG).

    In BEIDEN Fällen werden zusätzlich die zugehörigen django-simple-history-Zeilen bereinigt
    (Employee.history/Absence.history/Pregnancy.history) -- ohne das wäre die Anonymisierung nur
    Fassade, weil der Audit-Trail unabhängig vom Live-Datensatz weiterlebt (siehe Recherche zu
    Block 5: HistoricalRecords() überlebt jede Änderung/Löschung des Live-Objekts). .update() auf
    dem history-Manager schreibt direkt in die Archiv-Zeilen, ohne (wie beim normalen
    .save()-Pfad) einen neuen History-Eintrag zu erzeugen -- genau das ist hier gewollt, es wird
    Altdaten bereinigt, kein neuer "Änderungs"-Event soll entstehen.

    Nutzt Employee.all_objects/Absence.all_objects/Pregnancy.all_objects (nicht das
    tenant-scope-gefilterte .objects) -- ein Management-Command läuft ausserhalb eines Requests,
    es gibt also keinen "aktuellen Tenant" in der ContextVar (core.context), über alle Mandanten
    hinweg muss es trotzdem laufen (gleiches Muster wie deactivate_expired_employees.py).

    --dry-run zeigt nur, was betroffen wäre, ohne etwas zu ändern -- empfohlen vor jedem
    produktiven Einsatz. Für den Regelbetrieb als monatlicher Cronjob gedacht (z. B.
    `0 5 1 * *  cd <projekt> && venv/bin/python manage.py purge_expired_personal_data`).
    """

    help = "Anonymisiert/bereinigt personenbezogene Daten gemäss den gesetzlichen Aufbewahrungsfristen."

    EMPLOYEE_RETENTION_YEARS = 10  # Art. 958f OR
    SENSITIVE_NOTE_RETENTION_YEARS = 2  # Verhältnismässigkeit, Art. 6 Abs. 2 revDSG

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur anzeigen, was betroffen wäre, ohne Daten zu ändern.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        today = timezone.localdate()

        employee_cutoff = today - relativedelta(years=self.EMPLOYEE_RETENTION_YEARS)
        note_cutoff = today - relativedelta(years=self.SENSITIVE_NOTE_RETENTION_YEARS)

        anonymized_employees = self._anonymize_employees(employee_cutoff, dry_run)
        cleared_absence_notes = self._clear_absence_notes(note_cutoff, dry_run)
        cleared_pregnancy_notes = self._clear_pregnancy_notes(note_cutoff, dry_run)

        prefix = "[--dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{anonymized_employees} Mitarbeitende anonymisiert, "
                f"{cleared_absence_notes} Absenz-Notizen geleert, "
                f"{cleared_pregnancy_notes} Schwangerschafts-Notizen geleert."
            )
        )

    def _anonymize_employees(self, cutoff, dry_run):
        qs = Employee.all_objects.filter(
            termination_date__isnull=False, termination_date__lte=cutoff
        ).exclude(first_name="Gelöscht").select_related("user")
        count = 0
        for employee in qs:
            count += 1
            if dry_run:
                continue
            employee_id = employee.pk
            employee.first_name = "Gelöscht"
            employee.last_name = f"[{employee_id}]"
            employee.birth_date = None
            employee.save(update_fields=["first_name", "last_name", "birth_date"])
            Employee.history.filter(id=employee_id).update(
                first_name="Gelöscht", last_name=f"[{employee_id}]", birth_date=None
            )
            if employee.user_id:
                employee.user.delete()
        return count

    def _clear_absence_notes(self, cutoff, dry_run):
        qs = Absence.all_objects.filter(
            type__counts_as_sick_leave=True, end_date__lte=cutoff
        ).exclude(note="")
        count = 0
        for absence in qs:
            count += 1
            if dry_run:
                continue
            absence_id = absence.pk
            absence.note = ""
            absence.save(update_fields=["note"])
            Absence.history.filter(id=absence_id).update(note="")
        return count

    def _clear_pregnancy_notes(self, cutoff, dry_run):
        qs = Pregnancy.all_objects.exclude(notes="")
        count = 0
        for pregnancy in qs:
            anchor = pregnancy.actual_birth_date or pregnancy.expected_birth_date
            if anchor > cutoff:
                continue
            count += 1
            if dry_run:
                continue
            pregnancy_id = pregnancy.pk
            pregnancy.notes = ""
            pregnancy.save(update_fields=["notes"])
            Pregnancy.history.filter(id=pregnancy_id).update(notes="")
        return count

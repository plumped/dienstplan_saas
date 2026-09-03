from django.core.management.base import BaseCommand
from django.utils import timezone

from scheduling.models import Employee


class Command(BaseCommand):
    """
    Nutzer-Feedback (2026-08): "ein Mitarbeiter braucht auch ein
    Austrittsdatum. Wird dieses Erreicht wird automatisch inaktiviert und
    login gesperrt" -- für den täglichen Lauf gedacht (z. B. Cronjob um
    00:05: `0 5 0 * * *  cd <projekt> && venv/bin/python manage.py
    deactivate_expired_employees`), setzt für jede noch aktive Employee mit
    erreichtem/verstrichenem termination_date sowohl Employee.is_active als
    auch (falls ein Login existiert) user.is_active auf False -- dieselbe
    Wirkung wie EmployeeViewSet.deactivate, nur automatisch statt manuell
    ausgelöst. Idempotent: bereits deaktivierte Mitarbeitende (is_active=False)
    werden nicht erneut angefasst, ein erneuter Lauf am selben Tag ändert
    nichts mehr.

    Nutzt Employee.all_objects (statt des tenant-scope-gefilterten
    Employee.objects) -- ein Management-Command läuft ausserhalb eines
    Requests, es gibt also keinen "aktuellen Tenant" in der ContextVar
    (core.context), über alle Mandanten hinweg muss es trotzdem laufen.
    """

    help = "Deaktiviert Mitarbeitende (inkl. Login), deren Austrittsdatum erreicht ist."

    def handle(self, *args, **options):
        today = timezone.localdate()
        qs = Employee.all_objects.filter(
            termination_date__isnull=False, termination_date__lte=today, is_active=True
        ).select_related("user")
        count = 0
        for employee in qs:
            employee.is_active = False
            employee.save(update_fields=["is_active"])
            if employee.user_id:
                employee.user.is_active = False
                employee.user.save(update_fields=["is_active"])
            count += 1
        self.stdout.write(self.style.SUCCESS(f"{count} Mitarbeitende deaktiviert (Austrittsdatum erreicht)."))

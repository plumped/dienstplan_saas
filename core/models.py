import uuid

from django.conf import settings
from django.db import models

from core.context import get_current_tenant


class Tenant(models.Model):
    """Eine Gesundheitseinrichtung (Kunde) auf der Plattform."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

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

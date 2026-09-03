import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_tenant_canton_tenantholidayoverride"),
        ("scheduling", "0016_timetemplate_category"),
    ]

    operations = [
        migrations.CreateModel(
            name="AbsenceType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                (
                    "color",
                    models.CharField(
                        default="#64748b",
                        help_text="Hex-Farbe für Chips/Badges",
                        max_length=7,
                    ),
                ),
                (
                    "deducts_vacation_days",
                    models.BooleanField(
                        default=False,
                        help_text="Genehmigte Tage dieses Typs zählen als Ferienbezug (Employee.vacation_balance()).",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="core.tenant"
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
    ]

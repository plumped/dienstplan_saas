import django.db.models.deletion
from django.db import migrations, models


# Nutzer-Feedback (2026-08): Absenzarten (bisher hartcodiert vacation/sick/
# other) sollen ein tenant-eigener Katalog werden (analog TimeTemplate),
# siehe scheduling.models.AbsenceType. Migration in drei Schritten (0017 =
# CreateModel AbsenceType, 0018 = dieser Schritt, 0019 = Aufräumen), damit
# bestehende Absence-Zeilen dabei nicht verloren gehen:
# 1. Absence.type_new (nullable FK) zusätzlich zum alten CharField anlegen.
# 2. Für jeden Tenant mit bestehenden Absence-Zeilen die drei bisherigen
#    Typen als AbsenceType seeden (Farben aus den bisherigen
#    .type-badge--vacation/--sick/--other-CSS-Regeln übernommen) und jede
#    Absence.type_new anhand des alten String-Werts auf den passenden neuen
#    AbsenceType mappen.
# Migration 0019 entfernt danach das alte CharField und benennt type_new zu
# type um. Tenants ohne bestehende Absence-Zeilen bekommen bewusst keine
# automatisch geseedeten Typen -- konsistent mit TimeTemplate, das ebenfalls
# nie automatisch vorbelegt wird; sie legen ihren Katalog wie bei
# Schichttypen selbst in den Einstellungen an.
SEED_TYPES = [
    ("Ferien", "vacation", "#2b6e68", True),
    ("Krankheit", "sick", "#c2542c", False),
    ("Sonstiges", "other", "#64748b", False),
]


def seed_and_backfill(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Absence = apps.get_model("scheduling", "Absence")
    HistoricalAbsence = apps.get_model("scheduling", "HistoricalAbsence")
    AbsenceType = apps.get_model("scheduling", "AbsenceType")

    tenant_ids_with_absences = set(Absence.objects.values_list("tenant_id", flat=True).distinct()) | set(
        HistoricalAbsence.objects.values_list("tenant_id", flat=True).distinct()
    )
    for tenant in Tenant.objects.filter(id__in=tenant_ids_with_absences):
        types_by_old_value = {}
        for name, old_value, color, deducts_vacation_days in SEED_TYPES:
            absence_type, _ = AbsenceType.objects.get_or_create(
                tenant=tenant,
                name=name,
                defaults={"color": color, "deducts_vacation_days": deducts_vacation_days},
            )
            types_by_old_value[old_value] = absence_type

        for old_value, absence_type in types_by_old_value.items():
            Absence.objects.filter(tenant=tenant, type=old_value).update(type_new=absence_type)
            # Auch die simple_history-Audit-Snapshots mit dem passenden
            # AbsenceType verknüpfen, statt sie beim Umbenennen in 0019
            # kommentarlos auf NULL zu setzen.
            HistoricalAbsence.objects.filter(tenant=tenant, type=old_value).update(type_new=absence_type)


def noop_reverse(apps, schema_editor):
    # Absichtlich kein Rückbau der geseedeten AbsenceType-Zeilen -- 0019
    # entfernt ohnehin das alte CharField, ein Zurückmigrieren dieser
    # Zwischenstufe ist praktisch nie sinnvoll.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0017_absencetype"),
    ]

    operations = [
        migrations.AddField(
            model_name="absence",
            name="type_new",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="absences",
                to="scheduling.absencetype",
            ),
        ),
        migrations.AddField(
            model_name="historicalabsence",
            name="type_new",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="scheduling.absencetype",
            ),
        ),
        migrations.RunPython(seed_and_backfill, noop_reverse),
    ]

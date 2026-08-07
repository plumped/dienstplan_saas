import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0018_absence_type_fk_seed_and_backfill"),
    ]

    operations = [
        migrations.RemoveField(model_name="absence", name="type"),
        migrations.RemoveField(model_name="historicalabsence", name="type"),
        migrations.RenameField(model_name="absence", old_name="type_new", new_name="type"),
        migrations.RenameField(model_name="historicalabsence", old_name="type_new", new_name="type"),
        migrations.AlterField(
            model_name="absence",
            name="type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="absences",
                to="scheduling.absencetype",
            ),
        ),
        migrations.AlterField(
            model_name="historicalabsence",
            name="type",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="scheduling.absencetype",
            ),
        ),
    ]

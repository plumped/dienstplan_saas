from django.db import migrations


# Mandanten-Isolation für den Node-Baum (Nutzer-Feedback: "Es muss ALLES tenant
# unabhängig sein schon rein datenschutz technisch. Es darf nicht sein das
# alle tenants einen baum teilen! Das ist fahrlässig"): django-treebeard
# verwaltet einen einzigen, global geteilten Pfad-Namensraum für alle
# Wurzelknoten -- bislang lag jede Station jedes Tenants direkt auf dieser
# geteilten Ebene (Node.add_root()). 0031 hat das neue Feld
# Node.is_forest_root + eine DB-seitige UniqueConstraint ergänzt (max. ein
# Wurzelknoten pro Tenant). Diese Migration legt für jeden bestehenden
# Tenant mit mindestens einer Station den unsichtbaren Wurzelknoten an und
# hängt dessen bisherige Top-Level-Stationen als Kinder darunter --
# Tenants ohne jede Station bekommen (noch) keinen, konsistent mit dem
# "lazy, kein Signal"-Prinzip aus core/onboarding.py.
#
# Bewusst Import der ECHTEN Model-Klassen (scheduling.models.Node,
# core.models.Tenant) statt der eingefrorenen apps.get_model()-Historie --
# eine begründete Ausnahme vom Standardmuster: treebeards add_root()/move()
# existieren nicht auf dem gefrorenen Migrations-Snapshot-Modell (siehe der
# bereits vorhandene Kommentar zu genau diesem Problem bei
# scheduling/tests.py, EmploymentMigrationTests), diese Datenmigration
# braucht aber echte treebeard-Baumoperationen, kein reines Feld-Update.
def backfill_forest_roots(apps, schema_editor):
    from scheduling.models import Node
    from core.models import Tenant

    for tenant in Tenant.objects.all():
        existing_roots = list(Node.all_objects.filter(tenant=tenant, depth=1).order_by("path"))
        if not existing_roots:
            continue
        forest_root = Node.get_or_create_forest_root(tenant)
        for old_root in existing_roots:
            old_root.move(forest_root, pos="sorted-child")


def noop_reverse(apps, schema_editor):
    # Absichtlich kein Rückbau -- die Wurzelknoten wieder zu entfernen und
    # ihre Kinder zurück auf die geteilte Ebene zu heben würde exakt den
    # Fehler wiederherstellen, den diese Migration behebt.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0031_node_is_forest_root"),
    ]

    operations = [
        migrations.RunPython(backfill_forest_roots, noop_reverse),
    ]

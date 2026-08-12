"""
Self-Signup (README Block 3): Hilfsfunktionen für core.views.SignupView.

Bewusst KEINE Signal-Kopplung (post_save o. Ä.) und KEIN Tenant.save()-Override --
Tenant-Erstellung ist im gesamten übrigen Code (Django-Admin, Fixtures, und
insbesondere die ~800 bestehenden Tests in core/tests.py/scheduling/tests.py, die
alle direkt `Tenant.objects.create(...)` aufrufen) bewusst ein reiner Datensatz ohne
Nebenwirkungen -- ein Signal würde jeden dieser Aufrufe stillschweigend mit
zusätzlichen Node/AbsenceType/TimeTemplate/Employee-Zeilen verschmutzen. Diese
Funktionen werden deshalb ausschliesslich explizit aus SignupView aufgerufen, analog
zur Begründung in core/tenancy.py, warum die Tenant-Auflösung nicht in einer
Middleware passiert.
"""

from django.utils.text import slugify

from core.models import Tenant


def unique_tenant_slug(name):
    """
    Leitet einen eindeutigen Slug aus dem Firmennamen ab (z. B. "Sonnenhof AG" ->
    "sonnenhof-ag"), mit numerischem Suffix bei Kollision ("sonnenhof-ag-2", ...).
    Zwei Tenants dürfen denselben `name` tragen (keine Eindeutigkeitsprüfung dafür),
    nur der URL-taugliche Slug muss eindeutig sein.

    Kleines Race-Window zwischen dieser Prüfung und dem eigentlichen
    Tenant.objects.create() unter Nebenläufigkeit -- akzeptabel für MVP, da
    Tenant.slug zusätzlich DB-seitig `unique=True` ist; SignupView fängt einen
    dadurch ausgelösten IntegrityError ab und wiederholt einmal mit dem nächsten
    Suffix, statt hier zusätzliche Locking-Mechanik einzubauen.
    """
    base = slugify(name) or "tenant"
    slug = base
    n = 2
    while Tenant.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def seed_demo_tenant(tenant):
    """
    Nutzer-Feedback (2026-08, README Block 3): "Ein vorbefüllter Tenant" -- Self-Signup
    landet nicht in einem leeren Tenant, sondern in einem mit Beispieldaten zum
    Anfassen (Stationen, Schichttypen, Mitarbeitende), bevor die echten Daten
    eingegeben werden. Der OnboardingWizard bietet später an, diese Beispieldaten zu
    löschen/ersetzen.

    Markierung von Demo-Daten über ein literales "(Beispiel)"-Suffix im Namen
    (Node/TimeTemplate) bzw. Nachnamen (Employee) statt eines eigenen Feldes/Modells
    -- keine zusätzlichen Migrationen nötig, trivial abfragbar
    (`name__endswith=" (Beispiel)"`), und selbsterklärend in der UI auch ohne
    Wizard-Interaktion. AbsenceType-Zeilen sind bewusst NICHT markiert -- Ferien/
    Krankheit/Sonstiges sind echte Dauerkonfiguration, die jeder Tenant dauerhaft
    braucht, kein zu ersetzendes Demo-Beispiel (der Wizard hat dafür keinen eigenen
    Schritt).

    Bewusst KEINE ShiftAssignment-Seeds: müsste die volle Regel-Engine (Ruhezeiten,
    Wochenmax, ArG-Konflikte) gegen die konfigurierbaren Tenant-Grenzwerte zur
    Seed-Zeit durchlaufen, ist fragil und bringt wenig gegenüber zwei
    Beispiel-Mitarbeitenden mit fertigen Schichttypen zum sofortigen Anfassen im
    Planblatt.

    Lokale Imports von scheduling.models (statt Modul-Level) -- core bleibt die
    "unterste" App, auf die scheduling aufbaut, nicht umgekehrt (gleiches Muster wie
    core.views.MeView.get()).
    """
    from scheduling.models import AbsenceType, Employee, Employment, Node, TimeTemplate

    pflege_tag = Node.add_root(name="Pflege Tag (Beispiel)", tenant=tenant)
    pflege_nacht = Node.add_root(name="Pflege Nacht (Beispiel)", tenant=tenant)

    AbsenceType.objects.create(
        tenant=tenant, name="Ferien", color="#2b6e68", icon="F", deducts_vacation_days=True
    )
    AbsenceType.objects.create(
        tenant=tenant, name="Krankheit", color="#c2542c", icon="K", counts_as_sick_leave=True
    )
    AbsenceType.objects.create(tenant=tenant, name="Sonstiges", color="#64748b", icon="S")

    TimeTemplate.objects.create(
        tenant=tenant,
        node=pflege_tag,
        name="Frühdienst (Beispiel)",
        start_time="07:00",
        end_time="15:00",
        break_minutes=30,
    )
    TimeTemplate.objects.create(
        tenant=tenant,
        node=pflege_tag,
        name="Spätdienst (Beispiel)",
        start_time="13:00",
        end_time="21:00",
        break_minutes=30,
    )
    TimeTemplate.objects.create(
        tenant=tenant,
        node=pflege_nacht,
        name="Nachtdienst (Beispiel)",
        start_time="21:00",
        end_time="07:00",
        break_minutes=0,
    )

    anna = Employee.objects.create(
        tenant=tenant, first_name="Anna", last_name="(Beispiel)", employment_pct=100
    )
    Employment.objects.create(tenant=tenant, employee=anna, node=pflege_tag, pensum_pct=100)
    anna.nodes.set([pflege_tag.id])

    peter = Employee.objects.create(
        tenant=tenant, first_name="Peter", last_name="(Beispiel)", employment_pct=80
    )
    Employment.objects.create(tenant=tenant, employee=peter, node=pflege_nacht, pensum_pct=80)
    peter.nodes.set([pflege_nacht.id])

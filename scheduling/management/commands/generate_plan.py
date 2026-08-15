from django.core.management.base import BaseCommand, CommandError

from scheduling.models import Node
from scheduling.planning import commit_draft_assignments, generate_draft_plan


class Command(BaseCommand):
    """
    README Block 2 Punkt 19 (Automatisierte Planung): CLI-Zugang zum CP-SAT-
    Solver in scheduling/planning.py -- nützlich, um einen Lauf gegen echte
    Daten zu prüfen, bevor/ohne dass die API-Endpunkte (GeneratePlanView/
    CommitPlanView) benutzt werden.

    Anders als purge_expired_personal_data.py (dort ist --dry-run die
    Opt-in-Sicherheitsbremse, der Default mutiert) ist hier der DEFAULT
    reine Vorschau ohne Schreibzugriff, --commit muss explizit gesetzt
    werden. Bewusste Umkehrung der sonstigen Konvention: das Kernversprechen
    dieses Features ist "nie blind speichern" (README Block 2 Punkt 19,
    "Vorschau statt Blindautomatik") -- ein CLI-Aufruf, der das
    standardmässig doch tut, würde diesem Versprechen widersprechen.

    Nutzt Node.all_objects (nicht das tenant-scope-gefilterte .objects) --
    ein Management-Command läuft ausserhalb eines Requests, es gibt also
    keinen "aktuellen Tenant" in der ContextVar (core.context), siehe
    generate_draft_plan()/commit_draft_assignments() in scheduling/planning.py.
    """

    help = "Generiert (und optional übernimmt) einen automatisierten Dienstplan-Entwurf für eine Station/einen Monat."

    def add_arguments(self, parser):
        parser.add_argument("--node", type=int, required=True, help="ID der Ziel-Station.")
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--month", type=int, required=True)
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Entwurf tatsächlich speichern (Default: nur Vorschau, keine Schreibzugriffe).",
        )

    def handle(self, *args, **options):
        node = Node.all_objects.filter(pk=options["node"]).first()
        if node is None:
            raise CommandError(f"Station mit ID {options['node']} nicht gefunden.")
        tenant = node.tenant
        scope_node_ids = [node.id] + [c.id for c in node.get_children()]

        result = generate_draft_plan(tenant, scope_node_ids, options["year"], options["month"])

        self.stdout.write(f"Status: {result.status} ({result.solver_status})")
        self.stdout.write(f"Vorgeschlagene Zuweisungen: {len(result.assignments)}")
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(warning))

        if not options["commit"]:
            self.stdout.write(self.style.SUCCESS("[Vorschau] Nichts gespeichert -- mit --commit übernehmen."))
            return

        specs = [
            {
                "employee_id": a.employee_id,
                "node_id": a.node_id,
                "date": a.date,
                "template_id": a.template_id,
            }
            for a in result.assignments
        ]
        created, skipped = commit_draft_assignments(tenant, specs)
        self.stdout.write(self.style.SUCCESS(f"{len(created)} Zuweisungen gespeichert, {len(skipped)} übersprungen."))
        for entry in skipped:
            self.stdout.write(self.style.WARNING(f"  übersprungen: {entry}"))

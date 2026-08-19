"""
Automatisierte Planung (One-Click Planning), README Block 2 Punkt 19.

Zentrale Solver-Logik für den automatischen Dienstplan-Entwurf, analog zu
core/billing.py/core/onboarding.py (Geschäftslogik zentral statt in Views
verstreut). Wird sowohl von scheduling.views.GeneratePlanView/CommitPlanView
als auch vom generate_plan-Management-Command aufgerufen.

Bildet dieselben Regeln wie ShiftAssignment.clean() ab (siehe dortige
_check_*-Methoden), einschliesslich der skip_for_specialties-Ausnahmen für
category=SPECIAL (Pikettdienst) -- keine Teilmenge davon. Bestehende, bereits
gespeicherte Zuweisungen im Zielmonat werden als Konstanten behandelt und nie
verändert, nur ergänzt (auch um Split-Shifts, README Punkt 18). Ergebnis ist
immer ein Entwurf, nie automatisch gespeichert -- siehe
commit_draft_assignments() für den separaten, expliziten Übernahme-Schritt.

Bewusst NUR Templates mit `minimum_staffing > 0` als Kandidaten: dafür gibt
es kein Signal, wie viele Personen ein Template ohne definierte
Mindestbesetzung erhalten sollte (dieselbe Bedingung wie
scheduling.views.UnderstaffedShiftsView für die Unterbesetzt-Anzeige).

Scope pro Lauf: eine Station (inkl. direkter Team-Kinder) + ein Kalendermonat,
synchron innerhalb eines Solver-Zeitlimits (siehe SOLVER_TIME_LIMIT_SECONDS)
-- keine Task-Queue im Projekt, siehe requirements.txt.
"""
import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from ortools.sat.python import cp_model

from .models import (
    YOUTH_MINIMUM_REST_HOURS,
    Absence,
    Employee,
    Node,
    ShiftAssignment,
    ShiftPreference,
    TimeTemplate,
    _segment_datetimes,
)

SOLVER_TIME_LIMIT_SECONDS = 20

# Gewichtete Zielfunktion -- grosse Abstände zwischen den Stufen sorgen dafür,
# dass eine höhere Priorität eine niedrigere immer dominiert (Mindestbesetzung
# schlägt jeden Fairness-/Wunsch-Kompromiss, siehe Plan-Dokument).
SHORTFALL_WEIGHT = 100_000
WISH_FREE_VIOLATION_WEIGHT = 800
WISH_SHIFT_MATCH_REWARD = 500
SPECIAL_BALANCE_WEIGHT = 50
TARGET_DEVIATION_WEIGHT = 1
FAIRNESS_TIE_BREAK_WEIGHT = 1


@dataclass
class DraftAssignment:
    employee_id: int
    date: date
    template_id: int
    node_id: int


@dataclass
class PlanGenerationResult:
    status: str  # "ok" | "no_candidates" | "infeasible"
    solver_status: str = ""
    assignments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    shortfalls: list = field(default_factory=list)


def _week_start(day):
    """Montag der Kalenderwoche von `day` (wie ShiftAssignment._check_weekly_rest_day())."""
    return day - timedelta(days=day.weekday())


def _template_break_rule_ok(template):
    """
    Pausenregel (Art. 15 ArG) als Eigenschaft des Templates allein --
    unabhängig davon, wem es zugewiesen würde. Identische Logik wie
    ShiftAssignment._check_break_minutes(), aber ohne Instanz: ein Template,
    das die Regel nicht erfüllt, kann sie für NIEMANDEN erfüllen.
    """
    segments = _segment_datetimes(date(2000, 1, 3), template.effective_segments())
    net_minutes = sum((end - start).total_seconds() / 60 for start, end in segments)
    required = ShiftAssignment._required_break_minutes(net_minutes)
    if len(segments) > 1:
        actual = sum(
            (segments[i + 1][0] - segments[i][1]).total_seconds() / 60 for i in range(len(segments) - 1)
        )
        return actual >= required
    return template.break_minutes >= required


def generate_draft_plan(tenant, scope_node_ids, year, month):
    """
    Baut und löst das CP-SAT-Modell für eine Station (scope_node_ids) und
    einen Monat. Liest ausschliesslich, schreibt nichts.
    """
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    month_dates = [month_start + timedelta(days=i) for i in range((month_end - month_start).days + 1)]
    context_start = month_start - timedelta(days=6)
    context_end = month_end + timedelta(days=6)

    all_templates = list(
        TimeTemplate.all_objects.filter(
            tenant=tenant, node_id__in=scope_node_ids, minimum_staffing__gt=0
        ).select_related("required_skill")
    )
    if not all_templates:
        return PlanGenerationResult(
            status="no_candidates",
            warnings=["Keine Schichttypen mit Mindestbesetzung > 0 für diese Station hinterlegt."],
        )

    valid_templates = []
    warnings = []
    for template in all_templates:
        if _template_break_rule_ok(template):
            valid_templates.append(template)
        else:
            warnings.append(
                f"Schichttyp '{template.name}' erfüllt die Pausenregelung nicht (Art. 15 ArG) "
                "und wurde von der automatischen Planung ausgeschlossen."
            )
    if not valid_templates:
        return PlanGenerationResult(status="no_candidates", warnings=warnings)
    template_by_id = {t.id: t for t in valid_templates}

    employees = list(
        Employee.all_objects.filter(tenant=tenant, is_active=True, nodes__in=scope_node_ids)
        .distinct()
        .prefetch_related("skills", "nodes")
    )
    if not employees:
        return PlanGenerationResult(
            status="no_candidates", warnings=warnings + ["Keine aktiven Mitarbeitenden für diese Station."]
        )
    employee_ids = [e.id for e in employees]
    employee_by_id = {e.id: e for e in employees}
    employee_skill_ids = {e.id: {s.id for s in e.skills.all()} for e in employees}
    employee_node_ids = {e.id: {n.id for n in e.nodes.all()} for e in employees}

    # README Punkt 17: eine Station mit Team-Kindern zeigt Templates, die
    # DIREKT auf der Station selbst liegen, als geteilten Katalog für ALLE
    # ihre Teams (siehe PlanGrid.jsx: stampTemplates -- "ein Schichttyp
    # direkt auf der Station gilt als geteilter Katalog für jede markierte
    # Team-Zeile"). Mitarbeitende sind aber nie der Station selbst
    # zugeordnet, sondern jeweils ihrem Team -- ein simples
    # `template.node_id in emp_node_ids` (wie unten) würde daher für JEDES
    # stationsweite Template null Kandidaten finden. station_node ist der
    # Knoten mit der geringsten Tiefe im Scope (die Station selbst, siehe
    # _resolve_plan_scope: scope_ids = [Station] + [ihre Team-Kinder]);
    # team_node_ids sind alle übrigen Scope-Knoten.
    scope_nodes = list(Node.all_objects.filter(tenant=tenant, pk__in=scope_node_ids))
    station_node = min(scope_nodes, key=lambda n: n.depth)
    team_node_ids = {n.id for n in scope_nodes if n.id != station_node.id}

    def resolve_assignment_node_id(template, emp_node_ids):
        """
        Liefert die Team-/Stations-Id, auf die eine neue ShiftAssignment für
        dieses Template/diese Mitarbeiterin gebucht würde -- None, wenn die
        Mitarbeiterin für dieses Template nicht in Frage kommt. Ein
        teamspezifisches Template (node_id != Station) verlangt exakte
        Team-Zugehörigkeit; ein stationsweites Template (node_id ==
        Station) verlangt nur, dass die Mitarbeiterin IRGENDEINEM Team
        dieser Station angehört -- die neue Zuweisung landet dann auf genau
        diesem Team (nie auf der Station selbst, die bei vorhandenen Teams
        ohnehin keine direkten Zuweisungen tragen darf, siehe
        ShiftAssignment._check_node_has_no_children).
        """
        if template.node_id in emp_node_ids:
            return template.node_id
        if template.node_id == station_node.id:
            matching_teams = sorted(emp_node_ids & team_node_ids)
            if matching_teams:
                return matching_teams[0]
        return None

    context_assignments = list(
        ShiftAssignment.all_objects.filter(
            tenant=tenant, employee_id__in=employee_ids, date__range=[context_start, context_end]
        ).select_related("template")
    )
    fixed_by_employee_date = defaultdict(list)
    for a in context_assignments:
        fixed_by_employee_date[(a.employee_id, a.date)].append(a)

    absences_by_employee_date = defaultdict(list)
    for absence in Absence.all_objects.filter(
        tenant=tenant,
        employee_id__in=employee_ids,
        status=Absence.Status.APPROVED,
        start_date__lte=month_end,
        end_date__gte=month_start,
    ):
        cursor = max(absence.start_date, month_start)
        stop = min(absence.end_date, month_end)
        while cursor <= stop:
            absences_by_employee_date[(absence.employee_id, cursor)].append(absence)
            cursor += timedelta(days=1)

    # Genehmigungsprozess (2026-08, Automatisierte Planung mit Auffülldienst):
    # ein noch offener (PENDING) Wunsch bleibt wie bisher ein weicher
    # Zielfunktions-Term (wish_free/wish_shift, siehe Objective weiter
    # unten); ein von Admin/Planer freigegebener (APPROVED) gilt für die
    # Automatik hart (approved_wish_free/approved_wish_shift, siehe
    # Kandidaten-Schleife unten -- analog zu Absence, aber ohne eigenes
    # Absence-Objekt). Ein abgelehnter (REJECTED) wird hier komplett
    # ignoriert, als gäbe es ihn nicht.
    wish_free = set()
    wish_shift = {}
    approved_wish_free = set()
    approved_wish_shift = {}
    for pref in ShiftPreference.all_objects.filter(
        tenant=tenant, employee_id__in=employee_ids, date__range=[month_start, month_end]
    ).exclude(status=ShiftPreference.Status.REJECTED):
        if pref.status == ShiftPreference.Status.APPROVED:
            if pref.type == ShiftPreference.Type.FREE:
                approved_wish_free.add((pref.employee_id, pref.date))
            else:
                approved_wish_shift[(pref.employee_id, pref.date)] = pref.template_id
        elif pref.type == ShiftPreference.Type.FREE:
            wish_free.add((pref.employee_id, pref.date))
        else:
            wish_shift[(pref.employee_id, pref.date)] = pref.template_id

    fairness_points = Employee._bulk_fairness_points(
        employees, window_start=month_start - timedelta(days=365), window_end=month_start - timedelta(days=1)
    )
    points_per_fte = {}
    for e in employees:
        pct = e.employment_pct or 0
        points_per_fte[e.id] = (fairness_points.get(e.id, 0.0) / (pct / 100)) if pct else 0.0

    target_minutes = {e.id: round(e.target_hours_for_period(month_start, month_end) * 60) for e in employees}

    minor_cache = {}
    maternity_cache = {}

    def is_minor(employee, day):
        key = (employee.id, day)
        if key not in minor_cache:
            minor_cache[key] = employee.is_minor_on(day)
        return minor_cache[key]

    def maternity_status(employee, day):
        key = (employee.id, day)
        if key not in maternity_cache:
            maternity_cache[key] = employee.is_maternity_protected_on(day)
        return maternity_cache[key]

    epoch = datetime.combine(context_start, time(0, 0))

    def to_minutes(dt):
        return int((dt - epoch).total_seconds() // 60)

    model = cp_model.CpModel()

    # candidates[(employee_id, date, template_id)] = BoolVar
    candidates = {}
    # (employee_id, date) -> [(template_id, is_special, start_min, end_min, hours, is_unpopular)]
    day_candidate_info = defaultdict(list)

    # (employee_id, template_id) -> Knoten, auf den eine neue Zuweisung
    # gebucht würde (siehe resolve_assignment_node_id oben) -- unabhängig
    # vom Datum, daher einmal pro Mitarbeiter/Template statt pro Kandidat
    # berechnet.
    assignment_node_by_employee_template = {}

    for employee in employees:
        emp_node_ids = employee_node_ids.get(employee.id, set())
        for d in month_dates:
            # Wochenmuster (README, Automatisierte Planung mit Auffülldienst):
            # ein fest arbeitsfreier Wochentag ist kein Fehlbedarf und keine
            # Absenz -- er blockt hier hart, bevor überhaupt ein Kandidat für
            # irgendein Template entsteht, genau wie ein Absenz-Volltag unten.
            if employee.has_fixed_day_off(d):
                continue
            day_absences = absences_by_employee_date.get((employee.id, d), [])
            if any(a.day_portion == Absence.DayPortion.FULL for a in day_absences):
                continue
            # Genehmigter Wunschfrei (siehe oben): identisch hart wie ein
            # Absenz-Volltag -- der ganze Tag bleibt frei von Kandidaten.
            if (employee.id, d) in approved_wish_free:
                continue
            minor = is_minor(employee, d)
            m_status = maternity_status(employee, d)
            if m_status in ("full_ban", "consent_required"):
                continue
            # Genehmigter Wunschdienst (siehe oben): verdrängt an diesem Tag
            # jedes andere Template -- nur das gewünschte kommt überhaupt als
            # Kandidat in Frage (unten hart auf 1 gesetzt, siehe nach der
            # Kandidaten-Schleife; existiert der Kandidat gar nicht, weil ein
            # anderer harter Regel-Check ihn ausschliesst, entsteht dort eine
            # Konflikt-Warnung statt eines stillen Widerspruchs).
            approved_shift_template_id = approved_wish_shift.get((employee.id, d))

            for template in valid_templates:
                # Nur reguläre Templates verdrängen -- ein Wunschdienst
                # ("ich will genau DIESEN Dienst") sagt nichts über Pikett/
                # Spezialitäten (category=SPECIAL) aus, die oft zusätzlich
                # zu einem regulären Dienst laufen (z. B. Pikett am Abend
                # nach einem Tagdienst). Bleibt deshalb normal kandidierbar,
                # anders als bei Wunschfrei/Absenz (dort ganzer Tag blockt).
                if (
                    approved_shift_template_id is not None
                    and template.category != TimeTemplate.Category.SPECIAL
                    and template.id != approved_shift_template_id
                ):
                    continue
                assignment_node_id = resolve_assignment_node_id(template, emp_node_ids)
                if assignment_node_id is None:
                    continue
                assignment_node_by_employee_template[(employee.id, template.id)] = assignment_node_id
                if template.required_skill_id and template.required_skill_id not in employee_skill_ids[employee.id]:
                    continue
                is_special = template.category == TimeTemplate.Category.SPECIAL
                shift_start, shift_end = ShiftAssignment._shift_datetimes(d, template)

                blocked_by_absence = False
                for absence in day_absences:
                    a_start, a_end = Absence._half_day_window(d, absence.day_portion)
                    if shift_start < a_end and a_start < shift_end:
                        blocked_by_absence = True
                        break
                if blocked_by_absence:
                    continue

                if minor:
                    if ShiftAssignment._night_hours(d, template) > 0 or d.weekday() == 6:
                        continue
                if m_status == "night_ban" and ShiftAssignment._maternity_night_hours(d, template) > 0:
                    continue

                if not is_special:
                    rest_violation = False
                    for offset in (-1, 1):
                        neighbour_date = d + timedelta(days=offset)
                        neighbour_minor = minor or is_minor(employee, neighbour_date)
                        min_rest = tenant.minimum_rest_hours
                        if neighbour_minor:
                            min_rest = max(min_rest, YOUTH_MINIMUM_REST_HOURS)
                        for fixed in fixed_by_employee_date.get((employee.id, neighbour_date), []):
                            if fixed.template.category == TimeTemplate.Category.SPECIAL:
                                continue
                            other_start, other_end = ShiftAssignment._shift_datetimes(fixed.date, fixed.template)
                            if offset == -1:
                                gap_hours = (shift_start - other_end).total_seconds() / 3600
                            else:
                                gap_hours = (other_start - shift_end).total_seconds() / 3600
                            if gap_hours < min_rest:
                                rest_violation = True
                                break
                        if rest_violation:
                            break
                    if rest_violation:
                        continue

                    overlaps_fixed = False
                    for fixed in fixed_by_employee_date.get((employee.id, d), []):
                        if fixed.template.category == TimeTemplate.Category.SPECIAL:
                            continue
                        if ShiftAssignment._shifts_overlap(d, template, fixed.template):
                            overlaps_fixed = True
                            break
                    if overlaps_fixed:
                        continue

                var = model.NewBoolVar(f"x_{employee.id}_{d.isoformat()}_{template.id}")
                candidates[(employee.id, d, template.id)] = var
                is_unpopular = (not is_special) and (d.weekday() == 6 or ShiftAssignment._night_hours(d, template) > 0)
                hours = ShiftAssignment._shift_hours(d, template)
                day_candidate_info[(employee.id, d)].append(
                    (template.id, is_special, to_minutes(shift_start), to_minutes(shift_end), hours, is_unpopular)
                )

    # Genehmigter Wunschdienst hart erzwingen (siehe approved_wish_shift
    # oben): existiert der Kandidat (das gewünschte Template hat die
    # Kandidaten-Schleife überlebt), wird er auf 1 fixiert. Existiert er
    # NICHT, stand die Freigabe im Widerspruch zu einer anderen harten Regel
    # (z. B. Ruhezeit zu einer bereits fixen Nachbarschicht, fehlender
    # Skill) -- dann bleibt es bei einer für Menschen lesbaren Warnung statt
    # eines stillen, unbemerkten Verstosses.
    for (employee_id, d), template_id in approved_wish_shift.items():
        var = candidates.get((employee_id, d, template_id))
        if var is not None:
            model.Add(var == 1)
        else:
            employee = employee_by_id.get(employee_id)
            template = template_by_id.get(template_id)
            if employee and template:
                warnings.append(
                    f"Genehmigter Wunschdienst von {employee} am {d:%d.%m.%Y} ({template.name}) steht im "
                    "Konflikt mit einer anderen harten Regel und konnte nicht automatisch übernommen werden."
                )

    weeks = sorted({_week_start(d) for d in month_dates})
    horizon_minutes = to_minutes(datetime.combine(context_end + timedelta(days=2), time(0, 0)))

    # 1. Überlappung zwischen zwei nicht-SPECIAL Kandidaten desselben Tages
    #    (Split-Shifts sind erlaubt, solange sie sich zeitlich nicht
    #    überschneiden -- mirrors ShiftAssignment._check_no_overlap()).
    for (employee_id, d), infos in day_candidate_info.items():
        non_special = [(tid, s, e) for tid, is_sp, s, e, h, u in infos if not is_sp]
        for i in range(len(non_special)):
            t1, s1, e1 = non_special[i]
            for j in range(i + 1, len(non_special)):
                t2, s2, e2 = non_special[j]
                if s1 < e2 and s2 < e1:
                    model.Add(candidates[(employee_id, d, t1)] + candidates[(employee_id, d, t2)] <= 1)

    # 2. Ruhezeit zwischen Kandidaten an aufeinanderfolgenden Tagen (mirrors
    #    ShiftAssignment._check_rest_period()).
    for employee in employees:
        for d in month_dates:
            infos_today = [i for i in day_candidate_info.get((employee.id, d), []) if not i[1]]
            next_day = d + timedelta(days=1)
            infos_tomorrow = [i for i in day_candidate_info.get((employee.id, next_day), []) if not i[1]]
            if not infos_today or not infos_tomorrow:
                continue
            min_rest = tenant.minimum_rest_hours
            if is_minor(employee, d) or is_minor(employee, next_day):
                min_rest = max(min_rest, YOUTH_MINIMUM_REST_HOURS)
            for t1, _, s1, e1, h1, u1 in infos_today:
                for t2, _, s2, e2, h2, u2 in infos_tomorrow:
                    gap_hours = (s2 - e1) / 60.0
                    if gap_hours < min_rest:
                        model.Add(
                            candidates[(employee.id, d, t1)] + candidates[(employee.id, next_day, t2)] <= 1
                        )

    # 3. Wochenhöchstarbeitszeit (mirrors _check_maximum_weekly_hours()).
    for employee in employees:
        max_weekly_minutes = round((employee.maximum_weekly_hours or tenant.maximum_weekly_hours) * 60)
        for week_start in weeks:
            week_dates = [week_start + timedelta(days=i) for i in range(7)]
            fixed_minutes = 0
            for wd in week_dates:
                for fixed in fixed_by_employee_date.get((employee.id, wd), []):
                    if fixed.template.category != TimeTemplate.Category.SPECIAL:
                        fixed_minutes += round(ShiftAssignment._shift_hours(fixed.date, fixed.template) * 60)
            terms = []
            for wd in week_dates:
                if wd < month_start or wd > month_end:
                    continue
                for tid, is_sp, s, e, h, u in day_candidate_info.get((employee.id, wd), []):
                    if not is_sp:
                        terms.append(round(h * 60) * candidates[(employee.id, wd, tid)])
            if terms or fixed_minutes:
                model.Add(fixed_minutes + sum(terms) <= max_weekly_minutes)

    # 4. Tagesspanne inkl. Split-Shifts (mirrors _check_daily_span()) über
    #    reifizierte Hilfsvariablen day_min_start/day_max_end.
    max_daily_span_minutes = round(tenant.maximum_daily_span_hours * 60)
    for employee in employees:
        for d in month_dates:
            infos = [(tid, s, e) for tid, is_sp, s, e, h, u in day_candidate_info.get((employee.id, d), []) if not is_sp]
            fixed_list = [
                f for f in fixed_by_employee_date.get((employee.id, d), [])
                if f.template.category != TimeTemplate.Category.SPECIAL
            ]
            if not infos and not fixed_list:
                continue
            day_min_start = model.NewIntVar(0, horizon_minutes, f"dmin_{employee.id}_{d.isoformat()}")
            day_max_end = model.NewIntVar(0, horizon_minutes, f"dmax_{employee.id}_{d.isoformat()}")
            for fixed in fixed_list:
                fs, fe = ShiftAssignment._shift_datetimes(fixed.date, fixed.template)
                model.Add(day_min_start <= to_minutes(fs))
                model.Add(day_max_end >= to_minutes(fe))
            for tid, s, e in infos:
                var = candidates[(employee.id, d, tid)]
                model.Add(day_min_start <= s).OnlyEnforceIf(var)
                model.Add(day_max_end >= e).OnlyEnforceIf(var)
            model.Add(day_max_end - day_min_start <= max_daily_span_minutes)

    # 5. Wöchentlicher freier Tag (mirrors _check_weekly_rest_day()).
    for employee in employees:
        for week_start in weeks:
            week_dates = [week_start + timedelta(days=i) for i in range(7)]
            fixed_occupied = 0
            occupied_vars = []
            for wd in week_dates:
                already_fixed = any(
                    f.template.category != TimeTemplate.Category.SPECIAL
                    for f in fixed_by_employee_date.get((employee.id, wd), [])
                )
                if already_fixed:
                    fixed_occupied += 1
                    continue
                if wd < month_start or wd > month_end:
                    continue
                day_vars = [
                    candidates[(employee.id, wd, tid)]
                    for tid, is_sp, s, e, h, u in day_candidate_info.get((employee.id, wd), [])
                    if not is_sp
                ]
                if not day_vars:
                    continue
                occ = model.NewBoolVar(f"occ_{employee.id}_{wd.isoformat()}")
                model.AddMaxEquality(occ, day_vars)
                occupied_vars.append(occ)
            if occupied_vars or fixed_occupied:
                model.Add(sum(occupied_vars) + fixed_occupied <= 6)

    # 6. Mindestbesetzung mit Fehlbedarfs-Schlupfvariable -- der einzige
    #    wirklich neue harte Ziel-Constraint (bislang nirgends durchgesetzt,
    #    siehe scheduling.views.UnderstaffedShiftsView).
    fixed_count_by_template_date = defaultdict(int)
    for a in context_assignments:
        if month_start <= a.date <= month_end:
            fixed_count_by_template_date[(a.template_id, a.date)] += 1

    shortfall_vars = {}
    for template in valid_templates:
        for d in month_dates:
            eligible_vars = [
                candidates[(e.id, d, template.id)] for e in employees if (e.id, d, template.id) in candidates
            ]
            fixed_count = fixed_count_by_template_date.get((template.id, d), 0)
            shortfall = model.NewIntVar(0, template.minimum_staffing, f"short_{template.id}_{d.isoformat()}")
            model.Add(fixed_count + sum(eligible_vars) + shortfall >= template.minimum_staffing)
            # Obergrenze: minimum_staffing ist für die Automatisierung das
            # Ziel, nicht nur ein Boden -- ohne Deckel würde der Solver
            # beliebig viele zusätzliche, unnötige Zuweisungen erzeugen, nur
            # um Mitarbeitende näher an ihr Monats-Soll zu bringen (leeres
            # "mehr Leute einteilen" hat sonst keine Kosten). Bereits fix
            # bestehende Zuweisungen können diesen Deckel überschreiten
            # (nie verändert, siehe Modul-Docstring) -- dann bleibt nur noch
            # 0 Spielraum für neue Kandidaten an diesem Tag.
            if eligible_vars:
                model.Add(sum(eligible_vars) <= max(0, template.minimum_staffing - fixed_count))
            shortfall_vars[(template.id, d)] = shortfall

    # Zielfunktion
    objective_terms = [SHORTFALL_WEIGHT * sum(shortfall_vars.values())] if shortfall_vars else []

    for (employee_id, d, template_id), var in candidates.items():
        if (employee_id, d) in wish_free:
            objective_terms.append(WISH_FREE_VIOLATION_WEIGHT * var)
        if wish_shift.get((employee_id, d)) == template_id:
            objective_terms.append(-WISH_SHIFT_MATCH_REWARD * var)

    for (employee_id, d), infos in day_candidate_info.items():
        for tid, is_sp, s, e, h, is_unpopular in infos:
            if is_unpopular:
                bias = round(points_per_fte.get(employee_id, 0.0))
                if bias:
                    objective_terms.append(FAIRNESS_TIE_BREAK_WEIGHT * bias * candidates[(employee_id, d, tid)])

    # Pikett-Ausgleich: Anzahl SPECIAL-Zuweisungen gleichmässig über
    # berechtigte Mitarbeitende verteilen (eigenständige, einfachere
    # Heuristik -- kein bestehendes Fairness-Signal für Pikett vorhanden).
    fixed_special_count = defaultdict(int)
    for a in context_assignments:
        if month_start <= a.date <= month_end and a.template.category == TimeTemplate.Category.SPECIAL:
            fixed_special_count[a.employee_id] += 1
    special_candidates_by_employee = defaultdict(list)
    for (employee_id, d), infos in day_candidate_info.items():
        for tid, is_sp, s, e, h, u in infos:
            if is_sp:
                special_candidates_by_employee[employee_id].append(candidates[(employee_id, d, tid)])

    eligible_for_special = [
        e for e in employees if special_candidates_by_employee.get(e.id) or fixed_special_count.get(e.id)
    ]
    if len(eligible_for_special) > 1:
        special_counts = []
        max_possible_special = len(month_dates) * max(1, len(valid_templates))
        for e in eligible_for_special:
            count_var = model.NewIntVar(0, max_possible_special, f"special_count_{e.id}")
            model.Add(
                count_var == fixed_special_count.get(e.id, 0) + sum(special_candidates_by_employee.get(e.id, []))
            )
            special_counts.append(count_var)
        n = len(eligible_for_special)
        avg_num = sum(special_counts)  # = n * wahrer Durchschnitt
        for idx, count_var in enumerate(special_counts):
            dev = model.NewIntVar(0, max_possible_special * n, f"special_dev_{idx}")
            model.Add(dev >= count_var * n - avg_num)
            model.Add(dev >= avg_num - count_var * n)
            objective_terms.append(SPECIAL_BALANCE_WEIGHT * dev)

    # Monats-Soll-Abweichung je Mitarbeiter.
    max_possible_minutes = len(month_dates) * 24 * 60
    for employee in employees:
        fixed_minutes_month = 0
        for a in context_assignments:
            if (
                month_start <= a.date <= month_end
                and a.employee_id == employee.id
                and a.template.category != TimeTemplate.Category.SPECIAL
            ):
                fixed_minutes_month += round(ShiftAssignment._shift_hours(a.date, a.template) * 60)
        terms = [
            round(h * 60) * candidates[(employee.id, d, tid)]
            for (eid, d), infos in day_candidate_info.items()
            if eid == employee.id
            for tid, is_sp, s, e, h, u in infos
            if not is_sp
        ]
        deviation = model.NewIntVar(0, max_possible_minutes, f"target_dev_{employee.id}")
        total_expr = fixed_minutes_month + sum(terms)
        target = target_minutes.get(employee.id, 0)
        model.Add(deviation >= total_expr - target)
        model.Add(deviation >= target - total_expr)
        objective_terms.append(TARGET_DEVIATION_WEIGHT * deviation)

    if objective_terms:
        model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT_SECONDS
    solver.parameters.random_seed = 42
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    solver_status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return PlanGenerationResult(
            status="infeasible",
            solver_status=solver_status_name,
            warnings=warnings + ["Der Solver konnte keine gültige Lösung finden."],
        )

    assignments = []
    for (employee_id, d, template_id), var in candidates.items():
        if solver.Value(var):
            assignments.append(
                DraftAssignment(
                    employee_id=employee_id,
                    date=d,
                    template_id=template_id,
                    # Nie template.node_id direkt (das wäre bei einem
                    # stationsweiten Template die Station selbst -- eine
                    # ShiftAssignment darf dort bei vorhandenen Teams nicht
                    # landen, siehe resolve_assignment_node_id oben).
                    node_id=assignment_node_by_employee_template[(employee_id, template_id)],
                )
            )

    shortfalls = []
    for (template_id, d), shortfall_var in shortfall_vars.items():
        shortfall_amount = solver.Value(shortfall_var)
        if shortfall_amount > 0:
            template = template_by_id[template_id]
            skill_clause = f" mit Skill '{template.required_skill.name}'" if template.required_skill_id else ""
            filled = template.minimum_staffing - shortfall_amount
            warnings.append(
                f"{template.name} am {d:%d.%m.%Y}: nur {filled} von {template.minimum_staffing} "
                f"Person(en){skill_clause} verfügbar."
            )
            shortfalls.append(
                {
                    "template_id": template_id,
                    "template_name": template.name,
                    "date": d.isoformat(),
                    "required": template.minimum_staffing,
                    "filled": filled,
                }
            )

    wish_free_violations = sum(
        1
        for (employee_id, d, template_id), var in candidates.items()
        if (employee_id, d) in wish_free and solver.Value(var)
    )
    if wish_free_violations:
        warnings.append(
            f"{wish_free_violations} Wunschfrei-Wunsch(e) konnten wegen Mindestbesetzung nicht eingehalten werden."
        )

    return PlanGenerationResult(
        status="ok", solver_status=solver_status_name, assignments=assignments, warnings=warnings, shortfalls=shortfalls
    )


def commit_draft_assignments(tenant, assignment_specs):
    """
    Persistiert eine (ggf. vom Planer reduzierte) Liste von Entwurfs-
    Zuweisungen. Pro Zeile ein eigener Savepoint statt eines grossen
    Alles-oder-Nichts, damit eine einzelne zwischenzeitlich kollidierende
    Zeile (z. B. parallel manuell gestempelt) nicht den ganzen Übernahme-
    Vorgang zunichtemacht -- ShiftAssignment.full_clean() ist dasselbe
    Sicherheitsnetz, auf das sich auch die manuelle Stempelung verlässt.

    Erwartet bereits typisierte Werte (date-Objekte, int-IDs) -- die
    Übersetzung aus dem JSON-Request-Body ist Sache der aufrufenden View.
    """
    created = []
    skipped = []
    for spec in assignment_specs:
        try:
            with transaction.atomic():
                # Sicherheitsnetz gegen fremde IDs im Request-Body: anders als
                # beim normalen ShiftAssignmentViewSet (dessen
                # PrimaryKeyRelatedFields implizit über Employee.objects/
                # Node.objects/TimeTemplate.objects -- die ContextVar-gefilterte
                # Standard-Manager, siehe TenantScopedModel -- nur Datensätze
                # DIESES Tenants als gültig akzeptieren) baut diese Funktion die
                # ShiftAssignment-Instanz direkt aus rohen IDs, ohne
                # PrimaryKeyRelatedField-Validierung dazwischen. full_clean()
                # allein reicht hier NICHT: es prüft nur referenzielle
                # Integrität (existiert die PK überhaupt), nicht
                # Tenant-Zugehörigkeit -- ShiftAssignment.clean() selbst hat
                # keinen expliziten employee.tenant/node.tenant-Check. Ohne
                # diese drei Existenzprüfungen liesse sich sonst eine
                # ShiftAssignment mit tenant=A, aber employee_id einer fremden
                # Mitarbeiterin aus Tenant B anlegen.
                if not Employee.all_objects.filter(tenant=tenant, pk=spec["employee_id"]).exists():
                    raise ObjectDoesNotExist("employee_id gehört nicht zu diesem Tenant.")
                if not Node.all_objects.filter(tenant=tenant, pk=spec["node_id"]).exists():
                    raise ObjectDoesNotExist("node_id gehört nicht zu diesem Tenant.")
                if not TimeTemplate.all_objects.filter(tenant=tenant, pk=spec["template_id"]).exists():
                    raise ObjectDoesNotExist("template_id gehört nicht zu diesem Tenant.")
                obj = ShiftAssignment(
                    tenant=tenant,
                    employee_id=spec["employee_id"],
                    node_id=spec["node_id"],
                    date=spec["date"],
                    template_id=spec["template_id"],
                )
                obj.full_clean()
                obj.save()
                created.append(obj)
        except (DjangoValidationError, IntegrityError, KeyError, TypeError, ValueError, ObjectDoesNotExist) as exc:
            # ObjectDoesNotExist: sowohl von den expliziten Tenant-Checks oben
            # als auch von full_clean() selbst (das innerhalb von clean() u. a.
            # self.employee dereferenziert, z. B. _check_rest_period) -- bei
            # einer nicht existierenden employee_id/template_id/node_id (z. B.
            # veraltete/manipulierte IDs im Request-Body) wirft das kein
            # ValidationError, sondern <Model>.DoesNotExist.
            skipped.append({**spec, "error": str(exc)})
    return created, skipped

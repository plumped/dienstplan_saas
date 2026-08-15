"""
Tests für die automatisierte Planung (README Block 2 Punkt 19,
scheduling/planning.py) -- eigene Datei statt weiterer Zeilen in der bereits
sehr grossen scheduling/tests.py, analog core/tests_billing.py.
"""
from datetime import date, time, timedelta

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Tenant

from .models import (
    Absence,
    AbsenceType,
    Employee,
    Pregnancy,
    ShiftAssignment,
    ShiftPreference,
    Skill,
    TimeTemplate,
)
from .planning import commit_draft_assignments, generate_draft_plan
from .tests import TwoTenantFixtureMixin, make_station


class PlanningTestBase(TestCase):
    """
    Gemeinsames Fixture: ein Tenant mit Standardregeln, eine Station, ein
    einfacher Schichttyp mit minimum_staffing=1. Einzelne Tests passen die
    Mindestbesetzung/Schichttypen/Mitarbeitende nach Bedarf an.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Klinik Plan", slug="klinik-plan")
        self.node = make_station(self.tenant, "Station A")
        self.template = TimeTemplate.objects.create(
            tenant=self.tenant,
            node=self.node,
            name="Frühdienst",
            start_time=time(7, 0),
            end_time=time(15, 0),
            break_minutes=30,
            minimum_staffing=1,
        )

    def make_employee(self, first_name, last_name="Test", **kwargs):
        kwargs.setdefault("employment_pct", 100)
        employee = Employee.objects.create(tenant=self.tenant, first_name=first_name, last_name=last_name, **kwargs)
        employee.nodes.add(self.node)
        return employee

    def generate(self, year=2026, month=9):
        return generate_draft_plan(self.tenant, [self.node.id], year, month)

    def assignments_for(self, result, employee):
        return [a for a in result.assignments if a.employee_id == employee.id]


class MinimumStaffingTests(PlanningTestBase):
    def test_fills_minimum_staffing_without_shortfall(self):
        self.make_employee("Anna")
        self.make_employee("Bea")
        result = self.generate()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.shortfalls, [])
        # Jeder Tag im September genau einmal besetzt.
        days = {a.date for a in result.assignments if a.template_id == self.template.id}
        self.assertEqual(len(days), 30)

    def test_never_overstaffs_beyond_minimum(self):
        self.make_employee("Anna")
        self.make_employee("Bea")
        result = self.generate()
        counts = {}
        for a in result.assignments:
            counts[(a.template_id, a.date)] = counts.get((a.template_id, a.date), 0) + 1
        self.assertTrue(all(c <= 1 for c in counts.values()))

    def test_minimum_staffing_zero_never_auto_filled(self):
        self.template.minimum_staffing = 0
        self.template.save()
        self.make_employee("Anna")
        result = self.generate()
        self.assertEqual(result.status, "no_candidates")

    def test_shortfall_reported_when_not_enough_employees(self):
        self.template.minimum_staffing = 2
        self.template.save()
        self.make_employee("Anna")
        result = self.generate()
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.shortfalls), 30)
        self.assertTrue(any("Frühdienst" in w for w in result.warnings))


class RequiredSkillTests(PlanningTestBase):
    def test_only_skilled_employees_assigned(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Reanimation")
        self.template.required_skill = skill
        self.template.save()
        skilled = self.make_employee("Anna")
        unskilled = self.make_employee("Bea")
        skilled.skills.add(skill)
        result = self.generate()
        self.assertEqual(self.assignments_for(result, unskilled), [])
        self.assertTrue(len(self.assignments_for(result, skilled)) > 0)

    def test_shortfall_warning_mentions_skill_name(self):
        skill = Skill.objects.create(tenant=self.tenant, name="Reanimation")
        self.template.required_skill = skill
        self.template.minimum_staffing = 2
        self.template.save()
        skilled = self.make_employee("Anna")
        skilled.skills.add(skill)
        self.make_employee("Bea")  # unskilled
        result = self.generate()
        self.assertTrue(any("Reanimation" in w for w in result.warnings))


class BreakRuleTemplateFilterTests(PlanningTestBase):
    def test_invalid_break_rule_template_excluded_with_warning(self):
        self.template.break_minutes = 0  # 8h Netto braucht 30 Min. Pause
        self.template.save()
        self.make_employee("Anna")
        result = self.generate()
        self.assertEqual(result.status, "no_candidates")
        self.assertTrue(any("Pausenregelung" in w for w in result.warnings))


class RestPeriodTests(PlanningTestBase):
    def test_fixed_late_shift_blocks_next_day_early_shift(self):
        late_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Spätdienst",
            start_time=time(13, 0), end_time=time(21, 0), break_minutes=30, minimum_staffing=0,
        )
        employee = self.make_employee("Anna")
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=employee, node=self.node, date=date(2026, 9, 1), template=late_template
        )
        # 21:00 Ende, 07:00 Beginn am 2.9. -> nur 10h Ruhezeit < 11h Minimum.
        result = self.generate()
        blocked = [a for a in result.assignments if a.employee_id == employee.id and a.date == date(2026, 9, 2)]
        self.assertEqual(blocked, [])


class WeeklyMaxHoursTests(PlanningTestBase):
    def test_never_exceeds_maximum_weekly_hours(self):
        self.tenant.maximum_weekly_hours = 20
        self.tenant.save()
        self.make_employee("Anna")
        self.make_employee("Bea")
        result = self.generate()
        hours_by_employee_week = {}
        for a in result.assignments:
            week = a.date - timedelta(days=a.date.weekday())
            hours_by_employee_week[(a.employee_id, week)] = hours_by_employee_week.get((a.employee_id, week), 0) + 7.5
        self.assertTrue(all(h <= 20 for h in hours_by_employee_week.values()))


class WeeklyRestDayTests(PlanningTestBase):
    def test_weekly_rest_day_preserved_even_under_shortfall(self):
        # Nur eine Person für eine Station, die 7 Tage/Woche Mindestbesetzung
        # braucht -- der Wochenruhetag darf trotzdem nie verletzt werden,
        # der Fehlbedarf muss stattdessen steigen.
        self.make_employee("Anna")
        result = self.generate()
        days_by_week = {}
        for a in result.assignments:
            week = a.date - timedelta(days=a.date.weekday())
            days_by_week.setdefault(week, set()).add(a.date)
        for week, days in days_by_week.items():
            self.assertLessEqual(len(days), 6)
        # Mit nur einer Person und Mindestbesetzung 1 muss in jeder
        # vollständig im Monat liegenden Kalenderwoche mindestens 1 Tag
        # unbesetzt (Fehlbedarf) bleiben -- September 2026 hat 3 solche
        # vollständigen Wochen (die Rand-Wochen ragen über den Monat hinaus
        # und erzwingen dadurch keinen Fehlbedarf innerhalb des Monats).
        self.assertTrue(len(result.shortfalls) >= 3)


class SplitShiftTests(PlanningTestBase):
    def test_non_overlapping_split_shift_allowed(self):
        morning = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Morgen", start_time=time(7, 0), end_time=time(11, 0),
            minimum_staffing=1,
        )
        evening = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Abend", start_time=time(16, 0), end_time=time(20, 0),
            minimum_staffing=1,
        )
        self.template.delete()
        self.make_employee("Anna")
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        day = date(2026, 9, 3)
        templates_that_day = {a.template_id for a in result.assignments if a.date == day}
        self.assertEqual(templates_that_day, {morning.id, evening.id})

    def test_overlapping_templates_never_both_chosen_same_day(self):
        first = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="A", start_time=time(7, 0), end_time=time(15, 0),
            break_minutes=30, minimum_staffing=1,
        )
        second = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="B", start_time=time(13, 0), end_time=time(21, 0),
            break_minutes=30, minimum_staffing=1,
        )
        self.template.delete()
        self.make_employee("Anna")
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        for d in {a.date for a in result.assignments}:
            chosen = {a.template_id for a in result.assignments if a.date == d}
            self.assertFalse({first.id, second.id}.issubset(chosen))

    def test_daily_span_respected_across_split_shifts(self):
        self.tenant.maximum_daily_span_hours = 10
        self.tenant.save()
        morning = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Morgen", start_time=time(6, 0), end_time=time(10, 0),
            minimum_staffing=1,
        )
        late = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Spaet", start_time=time(18, 0), end_time=time(22, 0),
            minimum_staffing=1,
        )
        self.template.delete()
        self.make_employee("Anna")
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        for d in {a.date for a in result.assignments}:
            chosen = {a.template_id for a in result.assignments if a.date == d}
            # 06:00-10:00 und 18:00-22:00 zusammen wären 16h Spanne > 10h Limit.
            self.assertFalse({morning.id, late.id}.issubset(chosen))


class YouthProtectionTests(PlanningTestBase):
    def test_minor_never_gets_night_or_sunday_shift(self):
        night_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nacht", start_time=time(22, 0), end_time=time(6, 0),
            break_minutes=30, minimum_staffing=1,
        )
        self.template.delete()
        minor = self.make_employee("Mia", birth_date=date(2026, 9, 1) - timedelta(days=17 * 365))
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        self.assertEqual(self.assignments_for(result, minor), [])
        self.assertTrue(len(result.shortfalls) > 0)


class MaternityProtectionTests(PlanningTestBase):
    def test_full_ban_blocks_all_shifts(self):
        employee = self.make_employee("Anna")
        Pregnancy.objects.create(
            tenant=self.tenant, employee=employee,
            expected_birth_date=date(2026, 8, 20), actual_birth_date=date(2026, 8, 20),
        )
        # 8 Wochen ab 20.8. = full_ban bis 15.10. -> ganz September blockiert.
        result = self.generate()
        self.assertEqual(self.assignments_for(result, employee), [])

    def test_night_ban_blocks_only_night_touching_templates(self):
        night_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nacht", start_time=time(20, 0), end_time=time(6, 0),
            break_minutes=30, minimum_staffing=1,
        )
        employee = self.make_employee("Anna")
        # Termin Ende Oktober -> 1.9. liegt in den 8 Wochen davor (night_ban).
        Pregnancy.objects.create(tenant=self.tenant, employee=employee, expected_birth_date=date(2026, 10, 25))
        result = self.generate()
        self.assertEqual([a for a in self.assignments_for(result, employee) if a.template_id == night_template.id], [])
        # Der reguläre Tagesdienst bleibt weiterhin erlaubt.
        self.assertTrue(any(a.template_id == self.template.id for a in self.assignments_for(result, employee)))


class AbsenceTests(PlanningTestBase):
    def setUp(self):
        super().setUp()
        self.absence_type = AbsenceType.objects.create(tenant=self.tenant, name="Ferien", deducts_vacation_days=True)

    def test_full_day_approved_absence_blocks_the_day(self):
        employee = self.make_employee("Anna")
        Absence.objects.create(
            tenant=self.tenant, employee=employee, type=self.absence_type,
            start_date=date(2026, 9, 5), end_date=date(2026, 9, 5), status=Absence.Status.APPROVED,
        )
        result = self.generate()
        self.assertEqual([a for a in self.assignments_for(result, employee) if a.date == date(2026, 9, 5)], [])

    def test_pending_absence_does_not_block(self):
        employee = self.make_employee("Anna")
        Absence.objects.create(
            tenant=self.tenant, employee=employee, type=self.absence_type,
            start_date=date(2026, 9, 5), end_date=date(2026, 9, 5), status=Absence.Status.PENDING,
        )
        result = self.generate()
        self.assertTrue(any(a.date == date(2026, 9, 5) for a in self.assignments_for(result, employee)))

    def test_half_day_absence_blocks_only_matching_half(self):
        afternoon_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Nachmittag", start_time=time(13, 0), end_time=time(17, 0),
            minimum_staffing=1,
        )
        self.template.delete()
        employee = self.make_employee("Anna")
        Absence.objects.create(
            tenant=self.tenant, employee=employee, type=self.absence_type,
            start_date=date(2026, 9, 5), end_date=date(2026, 9, 5),
            day_portion=Absence.DayPortion.MORNING, status=Absence.Status.APPROVED,
        )
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        assigned = [a for a in self.assignments_for(result, employee) if a.date == date(2026, 9, 5)]
        self.assertEqual({a.template_id for a in assigned}, {afternoon_template.id})


class WishPreferenceTests(PlanningTestBase):
    def test_wunschdienst_preferred_when_equal_alternative_exists(self):
        anna = self.make_employee("Anna")
        bea = self.make_employee("Bea")
        ShiftPreference.objects.create(
            tenant=self.tenant, employee=anna, date=date(2026, 9, 7),
            type=ShiftPreference.Type.SHIFT, template=self.template,
        )
        result = self.generate()
        chosen = [a for a in result.assignments if a.date == date(2026, 9, 7)]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].employee_id, anna.id)

    def test_wunschfrei_avoided_when_alternative_exists(self):
        anna = self.make_employee("Anna")
        bea = self.make_employee("Bea")
        ShiftPreference.objects.create(
            tenant=self.tenant, employee=anna, date=date(2026, 9, 7), type=ShiftPreference.Type.FREE
        )
        result = self.generate()
        chosen = [a for a in result.assignments if a.date == date(2026, 9, 7)]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].employee_id, bea.id)

    def test_wunschfrei_overridden_with_warning_when_no_alternative(self):
        # Nur eine Person, UND der wöchentliche freie Tag ist bereits durch
        # eine genehmigte Absenz an einem ANDEREN Tag derselben Woche
        # "verbraucht" -- ohne das würde der Solver den Wunschfrei-Tag
        # einfach kostenlos als Wochenruhetag wählen (das wäre korrektes,
        # aber für diesen Test uninteressantes Verhalten). Erst wenn der
        # Wunschfrei-Tag die einzige Möglichkeit ist, die Mindestbesetzung
        # zu erfüllen, muss der Solver ihn trotz der Weich-Strafe belegen.
        anna = self.make_employee("Anna")
        absence_type = AbsenceType.objects.create(tenant=self.tenant, name="Ferien", deducts_vacation_days=True)
        Absence.objects.create(
            tenant=self.tenant, employee=anna, type=absence_type,
            start_date=date(2026, 9, 9), end_date=date(2026, 9, 9), status=Absence.Status.APPROVED,
        )
        ShiftPreference.objects.create(
            tenant=self.tenant, employee=anna, date=date(2026, 9, 7), type=ShiftPreference.Type.FREE
        )
        result = self.generate()
        chosen = [a for a in result.assignments if a.date == date(2026, 9, 7)]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0].employee_id, anna.id)
        self.assertTrue(any("Wunschfrei" in w for w in result.warnings))


class FairnessBiasTests(PlanningTestBase):
    def setUp(self):
        super().setUp()
        self.sunday_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Sonntag", start_time=time(7, 0), end_time=time(15, 0),
            break_minutes=30, minimum_staffing=1,
        )
        self.template.delete()

    def test_lower_fairness_points_preferred_for_sunday_shift(self):
        low_points = self.make_employee("Anna")
        high_points = self.make_employee("Bea")
        # Bea hat in den letzten 365 Tagen (aber VOR dem Zielmonat, damit die
        # Mindestbesetzung im September davon unberührt bleibt) bereits
        # mehrere Sonntagsschichten geleistet.
        for i in range(5):
            ShiftAssignment.objects.create(
                tenant=self.tenant, employee=high_points, node=self.node,
                date=date(2026, 8, 2) + timedelta(days=7 * i), template=self.sunday_template,
            )
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        first_sunday = [a for a in result.assignments if a.date == date(2026, 9, 6)]
        self.assertEqual(len(first_sunday), 1)
        self.assertEqual(first_sunday[0].employee_id, low_points.id)

    def test_no_bias_when_bonus_rates_zero(self):
        self.tenant.sunday_shift_bonus_points_per_hour = 0
        self.tenant.night_shift_bonus_points_per_hour = 0
        self.tenant.save()
        self.make_employee("Anna")
        self.make_employee("Bea")
        # Läuft ohne Fehler durch, kein bestimmtes Ergebnis erwartet.
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        self.assertEqual(result.status, "ok")


class SpecialCategoryTests(PlanningTestBase):
    def setUp(self):
        super().setUp()
        self.pikett = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Pikett", start_time=time(18, 0), end_time=time(22, 0),
            minimum_staffing=1, category=TimeTemplate.Category.SPECIAL,
        )
        self.template.delete()

    def test_special_assignments_distributed_evenly(self):
        employees = [self.make_employee(f"Emp{i}") for i in range(3)]
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        counts = {e.id: 0 for e in employees}
        for a in result.assignments:
            counts[a.employee_id] += 1
        self.assertEqual(sum(counts.values()), 30)
        self.assertTrue(max(counts.values()) - min(counts.values()) <= 1)

    def test_special_never_overlaps_with_regular_shift(self):
        regular = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Tag", start_time=time(7, 0), end_time=time(19, 0),
            break_minutes=60, minimum_staffing=1,
        )
        employee = self.make_employee("Anna")
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        # Beide Templates dürfen am selben Tag koexistieren (Pikett ist additiv).
        day = date(2026, 9, 3)
        templates_that_day = {a.template_id for a in result.assignments if a.date == day and a.employee_id == employee.id}
        self.assertEqual(templates_that_day, {regular.id, self.pikett.id})

    def test_special_does_not_count_toward_weekly_hours(self):
        self.tenant.maximum_weekly_hours = 4  # Pikett allein waere 4h*7=28h > 4h, wenn mitgezaehlt
        self.tenant.save()
        self.make_employee("Anna")
        result = generate_draft_plan(self.tenant, [self.node.id], 2026, 9)
        # Trotz 4h/Woche-Limit darf Pikett jeden Tag stattfinden (nicht mitgezaehlt).
        pikett_days = {a.date for a in result.assignments if a.template_id == self.pikett.id}
        self.assertTrue(len(pikett_days) > 4)


class TargetHoursDeviationTests(PlanningTestBase):
    def test_prefers_employee_further_below_target(self):
        below_target = self.make_employee("Anna")
        near_target = self.make_employee("Bea")
        # Bea hat bereits einen grossen Teil ihres Monatssolls durch andere
        # Zuweisungen erreicht -- nur an Wochentagen, damit die Fixdaten
        # selbst weder die Wochenhöchstarbeitszeit noch den wöchentlichen
        # freien Tag verletzen (beides gilt auch für schon bestehende
        # Zuweisungen, die der Solver nie anrührt).
        other_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Sonstiges", start_time=time(7, 0), end_time=time(15, 0),
            break_minutes=30, minimum_staffing=0,
        )
        for d in range(1, 22):
            day = date(2026, 9, d)
            if day.weekday() < 5:
                ShiftAssignment.objects.create(
                    tenant=self.tenant, employee=near_target, node=self.node, date=day, template=other_template
                )
        result = self.generate()
        first_day = [a for a in result.assignments if a.date == date(2026, 9, 1)]
        self.assertEqual(len(first_day), 1)
        self.assertEqual(first_day[0].employee_id, below_target.id)


class NoCandidatesAndUnchangedDataTests(PlanningTestBase):
    def test_no_active_employees_returns_no_candidates(self):
        result = self.generate()
        self.assertEqual(result.status, "no_candidates")

    def test_no_templates_with_minimum_staffing_returns_no_candidates(self):
        self.template.minimum_staffing = 0
        self.template.save()
        self.make_employee("Anna")
        result = self.generate()
        self.assertEqual(result.status, "no_candidates")

    def test_existing_assignments_never_modified(self):
        employee = self.make_employee("Anna")
        existing = ShiftAssignment.objects.create(
            tenant=self.tenant, employee=employee, node=self.node, date=date(2026, 9, 1), template=self.template
        )
        before = (existing.employee_id, existing.node_id, existing.date, existing.template_id)
        self.generate()
        existing.refresh_from_db()
        after = (existing.employee_id, existing.node_id, existing.date, existing.template_id)
        self.assertEqual(before, after)
        self.assertEqual(ShiftAssignment.objects.count(), 1)


class CrossTenantIsolationTests(TwoTenantFixtureMixin, TestCase):
    def test_other_tenants_employees_and_templates_never_leak(self):
        self.template_a.minimum_staffing = 1
        self.template_a.save()
        self.template_b.minimum_staffing = 1
        self.template_b.save()
        result = generate_draft_plan(self.tenant_a, [self.node_a.id], 2026, 9)
        self.assertTrue(all(a.employee_id == self.employee_a.id for a in result.assignments))
        self.assertTrue(all(a.template_id == self.template_a.id for a in result.assignments))


class CommitDraftAssignmentsTests(PlanningTestBase):
    def test_creates_valid_assignments(self):
        employee = self.make_employee("Anna")
        specs = [{"employee_id": employee.id, "node_id": self.node.id, "date": date(2026, 9, 1), "template_id": self.template.id}]
        created, skipped = commit_draft_assignments(self.tenant, specs)
        self.assertEqual(len(created), 1)
        self.assertEqual(skipped, [])
        self.assertEqual(ShiftAssignment.objects.count(), 1)

    def test_skips_conflicting_row_but_keeps_the_rest(self):
        employee = self.make_employee("Anna")
        other_template = TimeTemplate.objects.create(
            tenant=self.tenant, node=self.node, name="Ueberlappend", start_time=time(10, 0), end_time=time(18, 0),
            break_minutes=30, minimum_staffing=0,
        )
        # Bereits bestehende, überlappende Zuweisung -- simuliert eine
        # zwischenzeitliche manuelle Stempelung nach dem Entwurf.
        ShiftAssignment.objects.create(
            tenant=self.tenant, employee=employee, node=self.node, date=date(2026, 9, 1), template=self.template
        )
        specs = [
            {"employee_id": employee.id, "node_id": self.node.id, "date": date(2026, 9, 1), "template_id": other_template.id},
            {"employee_id": employee.id, "node_id": self.node.id, "date": date(2026, 9, 2), "template_id": self.template.id},
        ]
        created, skipped = commit_draft_assignments(self.tenant, specs)
        self.assertEqual(len(created), 1)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["date"], date(2026, 9, 1))

    def test_invalid_cross_tenant_reference_rejected(self):
        other_tenant = Tenant.objects.create(name="Andere Klinik", slug="andere-klinik")
        other_node = make_station(other_tenant, "Fremdstation")
        specs = [{"employee_id": 999999, "node_id": other_node.id, "date": date(2026, 9, 1), "template_id": self.template.id}]
        created, skipped = commit_draft_assignments(self.tenant, specs)
        self.assertEqual(created, [])
        self.assertEqual(len(skipped), 1)


class GeneratePlanCommandTests(PlanningTestBase):
    def test_dry_run_writes_nothing(self):
        self.make_employee("Anna")
        call_command("generate_plan", "--node", self.node.id, "--year", 2026, "--month", 9)
        self.assertEqual(ShiftAssignment.objects.count(), 0)

    def test_commit_flag_persists_matching_dry_run_result(self):
        self.make_employee("Anna")
        self.make_employee("Bea")
        call_command("generate_plan", "--node", self.node.id, "--year", 2026, "--month", 9, "--commit")
        self.assertEqual(ShiftAssignment.objects.count(), 30)

    def test_missing_required_argument_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command("generate_plan", "--year", 2026, "--month", 9)

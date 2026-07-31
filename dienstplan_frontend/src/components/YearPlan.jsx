import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";

const WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTH_NAMES = [
  "Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember",
];

const ABSENCE_TYPES = [
  { value: "vacation", label: "Ferien" },
  { value: "sick", label: "Krankheit" },
  { value: "other", label: "Sonstiges" },
];
const ABSENCE_TYPE_LABELS = Object.fromEntries(ABSENCE_TYPES.map((t) => [t.value, t.label]));
const STATUS_LABELS = { pending: "offen", approved: "genehmigt", rejected: "abgelehnt" };

function pad(n) {
  return String(n).padStart(2, "0");
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function isoDate(year, month, day) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

// 0 = Montag ... 6 = Sonntag, damit Wochen wie im übrigen Frontend Mo-So laufen.
function weekdayIndex(year, month, day) {
  const jsDay = new Date(year, month - 1, day).getDay();
  return jsDay === 0 ? 6 : jsDay - 1;
}

function addDays(iso, delta) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  date.setDate(date.getDate() + delta);
  return isoDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

// Fasst eine Menge von ISO-Tagen zu möglichst wenigen zusammenhängenden
// [start, end]-Bereichen zusammen -- damit z. B. zwei markierte Ferienwochen
// als zwei Absence-Einträge entstehen statt vierzehn Einzeltagen.
function groupConsecutiveDates(dates) {
  const sorted = [...dates].sort();
  const ranges = [];
  let start = null;
  let prev = null;
  for (const d of sorted) {
    if (start === null) {
      start = d;
    } else if (addDays(prev, 1) !== d) {
      ranges.push([start, prev]);
      start = d;
    }
    prev = d;
  }
  if (start !== null) ranges.push([start, prev]);
  return ranges;
}

export default function YearPlan({ nodeId, employees, me, onError }) {
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;

  const [year, setYear] = useState(() => new Date().getFullYear());
  const [employeeId, setEmployeeId] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [absences, setAbsences] = useState([]);
  const [loading, setLoading] = useState(true);
  const [markedDates, setMarkedDates] = useState(() => new Set());

  // Mitarbeitende sehen nur die eigene Person (analog zur Sperrung im
  // Abwesenheiten-Formular); Admin/Planer wählen frei, bleiben aber bei
  // einer gültigen Auswahl, falls sich die Stations-gefilterte Liste ändert.
  useEffect(() => {
    if (!canManage) {
      setEmployeeId(ownEmployeeId);
      return;
    }
    setEmployeeId((current) =>
      employees.some((e) => e.id === current) ? current : employees[0]?.id ?? null
    );
  }, [employees, canManage, ownEmployeeId]);

  useEffect(() => {
    if (!employeeId || !nodeId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setMarkedDates(new Set());
    const dateFrom = `${year}-01-01`;
    const dateTo = `${year}-12-31`;
    Promise.all([
      api.getTimeTemplates(),
      api.getShiftAssignments(nodeId, dateFrom, dateTo),
      api.getAbsences(employeeId),
    ])
      .then(([templatesRes, assignmentsRes, absencesRes]) => {
        if (cancelled) return;
        setTemplates((templatesRes.results ?? templatesRes).filter((t) => t.node === nodeId));
        const allAssignments = assignmentsRes.results ?? assignmentsRes;
        setAssignments(allAssignments.filter((a) => a.employee === employeeId));
        setAbsences(absencesRes.results ?? absencesRes);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, employeeId, year]);

  const assignmentByDate = useMemo(() => {
    const map = new Map();
    for (const a of assignments) map.set(a.date, a);
    return map;
  }, [assignments]);

  // Absenzen sind Zeiträume (start_date/end_date) -- für die Tages-Zellen auf
  // eine Datum->Absenz-Map auflösen, damit jede Zelle in O(1) weiss, ob sie
  // dazugehört.
  const absenceByDate = useMemo(() => {
    const map = new Map();
    for (const a of absences) {
      let d = a.start_date;
      while (d <= a.end_date) {
        map.set(d, a);
        d = addDays(d, 1);
      }
    }
    return map;
  }, [absences]);

  function toggleMark(date) {
    setMarkedDates((prev) => {
      const next = new Set(prev);
      if (next.has(date)) next.delete(date);
      else next.add(date);
      return next;
    });
  }

  async function handleStampShift(templateId) {
    const dates = Array.from(markedDates);
    const upserted = [];
    let skipped = 0;
    for (const date of dates) {
      const existing = assignmentByDate.get(date);
      try {
        if (existing) {
          upserted.push(await api.updateShiftAssignment(existing.id, { template: templateId }));
        } else {
          upserted.push(
            await api.createShiftAssignment({ employee: employeeId, node: nodeId, date, template: templateId })
          );
        }
      } catch {
        // z. B. Ruhezeit-, Höchstarbeitszeit- oder Absenz-Konflikt (siehe
        // ShiftAssignment.clean()) -- Tag überspringen, Rest weiterstempeln.
        skipped += 1;
      }
    }
    if (upserted.length) {
      setAssignments((prev) => {
        const byId = new Map(prev.map((a) => [a.id, a]));
        for (const a of upserted) byId.set(a.id, a);
        return Array.from(byId.values());
      });
    }
    setMarkedDates(new Set());
    if (skipped > 0) {
      onError(
        `${dates.length - skipped} von ${dates.length} Tagen zugewiesen, ${skipped} wegen Regel-Konflikten ` +
          "(z. B. Ruhezeit) übersprungen."
      );
    }
  }

  async function handleClearShifts() {
    const toDelete = Array.from(markedDates)
      .map((d) => assignmentByDate.get(d))
      .filter(Boolean);
    const deletedIds = [];
    let failed = 0;
    for (const a of toDelete) {
      try {
        await api.deleteShiftAssignment(a.id);
        deletedIds.push(a.id);
      } catch {
        failed += 1;
      }
    }
    if (deletedIds.length) {
      setAssignments((prev) => prev.filter((a) => !deletedIds.includes(a.id)));
    }
    setMarkedDates(new Set());
    if (failed > 0) onError(`${failed} Schicht(en) konnten nicht entfernt werden.`);
  }

  async function handleStampAbsence(type) {
    // Tage mit bestehender Absenz überspringen statt zu überlappen (analog
    // zum Wochenmuster-Kopieren: bereits belegte Zieltage nicht überschreiben).
    const dates = Array.from(markedDates).filter((d) => !absenceByDate.has(d));
    const skippedExisting = markedDates.size - dates.length;
    const ranges = groupConsecutiveDates(dates);
    const created = [];
    let skippedConflict = 0;
    for (const [start, end] of ranges) {
      try {
        created.push(await api.createAbsence({ employee: employeeId, start_date: start, end_date: end, type }));
      } catch {
        skippedConflict += 1;
      }
    }
    if (created.length) setAbsences((prev) => [...prev, ...created]);
    setMarkedDates(new Set());
    const notes = [];
    if (skippedExisting > 0) {
      notes.push(`${skippedExisting} Tag(e) übersprungen, dort besteht bereits eine Absenz.`);
    }
    if (skippedConflict > 0) {
      notes.push(`${skippedConflict} Zeitraum(e) konnten nicht angelegt werden.`);
    }
    if (notes.length) onError(notes.join(" "));
  }

  async function handleRemoveAbsences() {
    // Löscht die GANZE Absenz, nicht nur die markierten Tage daraus, falls
    // nur ein Teil eines mehrtägigen Zeitraums markiert war (siehe README) --
    // ein Absenz-Datensatz lässt sich nicht teilweise löschen, ohne ihn in
    // zwei neue Zeiträume aufzuspalten, was hier bewusst nicht automatisiert
    // wird.
    const toDelete = new Map();
    for (const d of markedDates) {
      const absence = absenceByDate.get(d);
      if (absence) toDelete.set(absence.id, absence);
    }
    const deletedIds = [];
    let failed = 0;
    for (const absence of toDelete.values()) {
      try {
        await api.deleteAbsence(absence.id);
        deletedIds.push(absence.id);
      } catch {
        // z. B. bereits genehmigte Absenz einer Mitarbeiter-Rolle (nur
        // Admin/Planer dürfen die noch löschen, siehe OwnEmployeeRecordPermission).
        failed += 1;
      }
    }
    if (deletedIds.length) {
      setAbsences((prev) => prev.filter((a) => !deletedIds.includes(a.id)));
    }
    setMarkedDates(new Set());
    if (failed > 0) {
      onError(
        `${failed} Absenz(en) konnten nicht entfernt werden -- bereits genehmigte Absenzen dürfen ` +
          "Mitarbeitende nicht mehr selbst löschen."
      );
    }
  }

  if (!employees.length) {
    return (
      <p className="empty-state">
        Keine Mitarbeiter dieser Station zugeordnet. Im Admin unter „Employees“ ergänzen.
      </p>
    );
  }

  return (
    <div className="year-plan">
      <div className="year-plan-toolbar">
        {canManage ? (
          <label className="year-plan-employee-select">
            Mitarbeiter
            <select value={employeeId ?? ""} onChange={(e) => setEmployeeId(Number(e.target.value))}>
              {employees.map((emp) => (
                <option key={emp.id} value={emp.id}>
                  {emp.first_name} {emp.last_name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="year-plan-employee-fixed">
            {employees.find((e) => e.id === employeeId)?.first_name}{" "}
            {employees.find((e) => e.id === employeeId)?.last_name}
          </span>
        )}
        <span className="year-nav">
          <button type="button" className="btn-ghost" onClick={() => setYear((y) => y - 1)} aria-label="Vorheriges Jahr">
            ‹
          </button>
          <strong>{year}</strong>
          <button type="button" className="btn-ghost" onClick={() => setYear((y) => y + 1)} aria-label="Nächstes Jahr">
            ›
          </button>
        </span>
        {markedDates.size === 0 ? (
          <span className="multi-select-hint">Tage anklicken, um sie zu markieren.</span>
        ) : (
          <span className="stamp-palette">
            <span className="multi-select-hint">{markedDates.size} markiert:</span>
            {canManage &&
              templates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="stamp-chip"
                  style={{ "--chip-color": t.color }}
                  title={`${t.name} (${t.start_time.slice(0, 5)}–${t.end_time.slice(0, 5)}) auf alle markierten Tage anwenden`}
                  onClick={() => handleStampShift(t.id)}
                >
                  {t.name.slice(0, 3)}
                </button>
              ))}
            {canManage && (
              <button type="button" className="stamp-chip stamp-chip--empty" title="Schicht(en) entfernen" onClick={handleClearShifts}>
                Schicht leeren
              </button>
            )}
            {ABSENCE_TYPES.map((t) => (
              <button
                key={t.value}
                type="button"
                className="stamp-chip stamp-chip--absence"
                title={`${t.label} für alle markierten Tage eintragen`}
                onClick={() => handleStampAbsence(t.value)}
              >
                {t.label}
              </button>
            ))}
            <button
              type="button"
              className="stamp-chip stamp-chip--empty"
              title="Absenz(en) der markierten Tage entfernen -- löscht den ganzen Zeitraum, nicht nur die markierten Tage daraus"
              onClick={handleRemoveAbsences}
            >
              Absenz entfernen
            </button>
            <button type="button" className="btn-ghost" onClick={() => setMarkedDates(new Set())}>
              Auswahl aufheben
            </button>
          </span>
        )}
      </div>

      {loading ? (
        <p className="loading-state">Jahresplan wird geladen …</p>
      ) : (
        <div className="year-grid">
          {MONTH_NAMES.map((monthName, monthIndex) => {
            const month = monthIndex + 1;
            const numDays = daysInMonth(year, month);
            const leadingBlanks = weekdayIndex(year, month, 1);
            const cells = [
              ...Array.from({ length: leadingBlanks }, () => null),
              ...Array.from({ length: numDays }, (_, i) => i + 1),
            ];
            return (
              <div key={month} className="year-month-card">
                <div className="year-month-title">{monthName}</div>
                <div className="year-month-weekdays">
                  {WEEKDAYS_SHORT.map((w) => (
                    <span key={w}>{w}</span>
                  ))}
                </div>
                <div className="year-month-days">
                  {cells.map((day, i) => {
                    if (day === null) return <span key={`blank-${i}`} className="year-day-cell is-empty" />;
                    const date = isoDate(year, month, day);
                    const assignment = assignmentByDate.get(date);
                    const template = assignment ? templates.find((t) => t.id === assignment.template) : null;
                    const absence = absenceByDate.get(date);
                    const marked = markedDates.has(date);
                    const kind = absence ? "absence" : assignment ? "shift" : "empty";
                    const color = absence ? "var(--ink-muted)" : template?.color;
                    const title = absence
                      ? `${date}: ${ABSENCE_TYPE_LABELS[absence.type] ?? absence.type} (${STATUS_LABELS[absence.status] ?? absence.status})`
                      : assignment && template
                        ? `${date}: ${template.name} (${template.start_time.slice(0, 5)}–${template.end_time.slice(0, 5)})`
                        : `${date}: frei`;
                    return (
                      <button
                        key={date}
                        type="button"
                        className={`year-day-cell${marked ? " is-marked" : ""}`}
                        title={title}
                        aria-pressed={marked}
                        onClick={() => toggleMark(date)}
                      >
                        <span className={`year-day-fill year-day-fill--${kind}`} style={color ? { "--chip-color": color } : undefined}>
                          {day}
                        </span>
                        {marked && (
                          <span className="select-check" aria-hidden="true">
                            ✓
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

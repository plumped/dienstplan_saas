import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import ShiftCell from "./ShiftCell.jsx";

const WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

function pad(n) {
  return String(n).padStart(2, "0");
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function isoDate(year, month, day) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

function weekdayLabel(year, month, day) {
  const jsDay = new Date(year, month - 1, day).getDay(); // 0 = Sonntag
  return WEEKDAYS_SHORT[jsDay === 0 ? 6 : jsDay - 1];
}

export default function PlanGrid({ nodeId, year, month, employees, onError }) {
  const [templates, setTemplates] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [absences, setAbsences] = useState([]);
  const [loading, setLoading] = useState(true);

  const days = useMemo(
    () => Array.from({ length: daysInMonth(year, month) }, (_, i) => i + 1),
    [year, month]
  );
  const dateFrom = isoDate(year, month, 1);
  const dateTo = isoDate(year, month, days.length);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.getTimeTemplates(),
      api.getShiftAssignments(nodeId, dateFrom, dateTo),
      api.getAbsences(),
    ])
      .then(([templatesRes, assignmentsRes, absencesRes]) => {
        if (cancelled) return;
        setTemplates((templatesRes.results ?? templatesRes).filter((t) => t.node === nodeId));
        setAssignments(assignmentsRes.results ?? assignmentsRes);
        setAbsences(absencesRes.results ?? absencesRes);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, dateFrom, dateTo]);

  const assignmentMap = useMemo(() => {
    const map = new Map();
    for (const a of assignments) map.set(`${a.employee}:${a.date}`, a);
    return map;
  }, [assignments]);

  function findAbsence(employeeId, date) {
    // ISO-Datumsstrings (YYYY-MM-DD) lassen sich direkt lexikographisch vergleichen.
    return absences.find(
      (a) => a.employee === employeeId && a.start_date <= date && date <= a.end_date
    );
  }

  async function handleAssign(employeeId, date, templateId) {
    const key = `${employeeId}:${date}`;
    const existing = assignmentMap.get(key);
    try {
      if (templateId === null) {
        if (!existing) return;
        await api.deleteShiftAssignment(existing.id);
        setAssignments((prev) => prev.filter((a) => a.id !== existing.id));
        return;
      }
      if (existing) {
        const updated = await api.updateShiftAssignment(existing.id, { template: templateId });
        setAssignments((prev) => prev.map((a) => (a.id === existing.id ? updated : a)));
      } else {
        const created = await api.createShiftAssignment({
          employee: employeeId,
          node: nodeId,
          date,
          template: templateId,
        });
        setAssignments((prev) => [...prev, created]);
      }
    } catch (e) {
      // Greift z. B. bei einer Ruhezeit-Verletzung (siehe ShiftAssignment.clean() im Backend)
      onError(e.message);
    }
  }

  async function handleMove(fromEmployeeId, fromDate, toEmployeeId, toDate) {
    if (fromEmployeeId === toEmployeeId && fromDate === toDate) return;
    const source = assignmentMap.get(`${fromEmployeeId}:${fromDate}`);
    if (!source) return;
    if (assignmentMap.has(`${toEmployeeId}:${toDate}`)) {
      onError("Zielfeld ist bereits belegt. Bitte zuerst leeren, bevor eine Schicht dorthin verschoben wird.");
      return;
    }
    try {
      const updated = await api.updateShiftAssignment(source.id, {
        employee: toEmployeeId,
        date: toDate,
      });
      setAssignments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
    } catch (e) {
      // Greift z. B. bei einer Ruhezeit-Verletzung am Zieltag (siehe ShiftAssignment.clean())
      onError(e.message);
    }
  }

  async function handleCopyWeekPattern(employeeId) {
    const sourceDays = days.filter((d) => d <= 7);
    const hasSourceShift = sourceDays.some((d) =>
      assignmentMap.has(`${employeeId}:${isoDate(year, month, d)}`)
    );
    if (!hasSourceShift) {
      onError("Die erste Woche hat für diesen Mitarbeiter noch keine Schichten zum Kopieren.");
      return;
    }

    const created = [];
    let skippedByConflict = 0;
    for (let targetDay = 8; targetDay <= days.length; targetDay += 1) {
      const sourceDay = ((targetDay - 1) % 7) + 1;
      const source = assignmentMap.get(`${employeeId}:${isoDate(year, month, sourceDay)}`);
      if (!source) continue;
      const targetDate = isoDate(year, month, targetDay);
      if (assignmentMap.has(`${employeeId}:${targetDate}`)) continue; // bestehende Einträge nicht überschreiben

      try {
        const createdAssignment = await api.createShiftAssignment({
          employee: employeeId,
          node: nodeId,
          date: targetDate,
          template: source.template,
        });
        created.push(createdAssignment);
      } catch {
        // z. B. Ruhezeit- oder Höchstarbeitszeit-Konflikt an diesem Tag -- Zelle
        // überspringen, restliche Wochen trotzdem weiterkopieren.
        skippedByConflict += 1;
      }
    }

    if (created.length) {
      setAssignments((prev) => [...prev, ...created]);
    }
    if (skippedByConflict > 0) {
      onError(
        `Wochenmuster kopiert, ${skippedByConflict} Tag(e) wegen Regel-Konflikten (z. B. Ruhezeit) übersprungen.`
      );
    }
  }

  async function handleOfferTrade(employeeId, date, targetEmployeeId) {
    const assignment = assignmentMap.get(`${employeeId}:${date}`);
    if (!assignment) return;
    try {
      await api.createShiftTradeRequest({
        requester_assignment: assignment.id,
        target_employee: targetEmployeeId,
      });
      const targetName = employees.find((e) => e.id === targetEmployeeId);
      onError(
        `Tausch angeboten an ${targetName ? `${targetName.first_name} ${targetName.last_name}` : "Kolleg:in"} ` +
          "-- siehe Tab „Diensttausch“."
      );
    } catch (e) {
      onError(e.message);
    }
  }

  if (loading) return <p className="loading-state">Planblatt wird geladen …</p>;
  if (!employees.length) {
    return (
      <p className="empty-state">
        Keine Mitarbeiter dieser Station zugeordnet. Im Admin unter „Employees“ ergänzen.
      </p>
    );
  }

  return (
    <div className="grid-scroll">
      <table className="plan-grid">
        <thead>
          <tr>
            <th className="col-employee">Mitarbeiter</th>
            {days.map((d) => {
              const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
              return (
                <th key={d} className={weekend ? "is-weekend" : ""}>
                  <span className="day-num">{d}</span>
                  <span className="day-weekday">{weekdayLabel(year, month, d)}</span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {employees.map((emp) => (
            <tr key={emp.id}>
              <th scope="row" className="col-employee">
                <span className="employee-name">
                  {emp.first_name} {emp.last_name}
                </span>
                <span className="pct">{emp.employment_pct}%</span>
                <button
                  type="button"
                  className="btn-copy-week"
                  title="Muster der ersten Woche auf die restlichen Wochen dieses Monats kopieren (belegte Tage bleiben unverändert)"
                  onClick={() => handleCopyWeekPattern(emp.id)}
                >
                  ⧉<span className="visually-hidden"> Wochenmuster kopieren für {emp.first_name} {emp.last_name}</span>
                </button>
              </th>
              {days.map((d) => {
                const date = isoDate(year, month, d);
                const assignment = assignmentMap.get(`${emp.id}:${date}`);
                const template = templates.find((t) => t.id === assignment?.template);
                const absence = findAbsence(emp.id, date);
                const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                return (
                  <td key={d} className={weekend ? "is-weekend" : ""}>
                    <ShiftCell
                      templates={templates}
                      selectedTemplateId={assignment?.template ?? null}
                      templateInfo={template}
                      employeeId={emp.id}
                      date={date}
                      absence={absence}
                      colleagues={employees.filter((e) => e.id !== emp.id)}
                      onChange={(templateId) => handleAssign(emp.id, date, templateId)}
                      onMove={handleMove}
                      onOfferTrade={(targetEmployeeId) =>
                        handleOfferTrade(emp.id, date, targetEmployeeId)
                      }
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

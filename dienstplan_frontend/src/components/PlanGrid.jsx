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

export default function PlanGrid({ nodeId, year, month, onError }) {
  const [employees, setEmployees] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [assignments, setAssignments] = useState([]);
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
      api.getEmployees(),
      api.getTimeTemplates(),
      api.getShiftAssignments(nodeId, dateFrom, dateTo),
    ])
      .then(([employeesRes, templatesRes, assignmentsRes]) => {
        if (cancelled) return;
        const employeeList = (employeesRes.results ?? employeesRes).filter((e) =>
          e.nodes.includes(nodeId)
        );
        setEmployees(employeeList);
        setTemplates((templatesRes.results ?? templatesRes).filter((t) => t.node === nodeId));
        setAssignments(assignmentsRes.results ?? assignmentsRes);
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
                {emp.first_name} {emp.last_name}
                <span className="pct">{emp.employment_pct}%</span>
              </th>
              {days.map((d) => {
                const date = isoDate(year, month, d);
                const assignment = assignmentMap.get(`${emp.id}:${date}`);
                const template = templates.find((t) => t.id === assignment?.template);
                const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                return (
                  <td key={d} className={weekend ? "is-weekend" : ""}>
                    <ShiftCell
                      templates={templates}
                      selectedTemplateId={assignment?.template ?? null}
                      templateInfo={template}
                      onChange={(templateId) => handleAssign(emp.id, date, templateId)}
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

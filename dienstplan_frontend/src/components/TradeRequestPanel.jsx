import { useEffect, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";

const STATUS_LABELS = {
  pending: "Offen",
  accepted: "Angenommen",
  declined: "Abgelehnt",
  cancelled: "Zurückgezogen",
};

export default function TradeRequestPanel({ me, onError }) {
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;
  const [requests, setRequests] = useState([]);
  const [employeesById, setEmployeesById] = useState(new Map());
  const [assignmentsById, setAssignmentsById] = useState(new Map());
  const [templatesById, setTemplatesById] = useState(new Map());
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    async function load() {
      const [employeesRes, templatesRes, requestsRes] = await Promise.all([
        api.getEmployees(),
        api.getTimeTemplates(),
        api.getShiftTradeRequests(),
      ]);
      const employeeList = employeesRes.results ?? employeesRes;
      const templateList = templatesRes.results ?? templatesRes;
      const requestList = requestsRes.results ?? requestsRes;

      const assignmentIds = new Set();
      for (const r of requestList) {
        assignmentIds.add(r.requester_assignment);
        if (r.target_assignment) assignmentIds.add(r.target_assignment);
      }
      const assignments = await Promise.all(
        [...assignmentIds].map((id) => api.getShiftAssignment(id))
      );

      if (cancelled) return;
      setEmployeesById(new Map(employeeList.map((e) => [e.id, e])));
      setTemplatesById(new Map(templateList.map((t) => [t.id, t])));
      setAssignmentsById(new Map(assignments.map((a) => [a.id, a])));
      setRequests(requestList);
    }

    load()
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function employeeName(id) {
    const employee = employeesById.get(id);
    return employee ? `${employee.first_name} ${employee.last_name}` : `#${id}`;
  }

  function describeAssignment(id) {
    const assignment = assignmentsById.get(id);
    if (!assignment) return "…";
    const template = templatesById.get(assignment.template);
    return `${assignment.date}${template ? ` (${template.name})` : ""}`;
  }

  async function runAction(id, action) {
    setBusyId(id);
    try {
      const updated = await action(id);
      setRequests((prev) => prev.map((r) => (r.id === id ? updated : r)));
    } catch (e) {
      onError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <p className="loading-state">Diensttausch-Anfragen werden geladen …</p>;

  return (
    <div className="side-panel">
      <div className="panel-list panel-list--full">
        <h2>Diensttausch-Anfragen</h2>
        <p className="panel-hint">
          Anfragen entstehen im Planblatt über das ⇄-Symbol an einer belegten Schicht.
        </p>
        {requests.length === 0 ? (
          <p className="empty-state">Noch keine Diensttausch-Anfragen.</p>
        ) : (
          <ul className="entry-list">
            {requests.map((r) => {
              const requesterAssignment = assignmentsById.get(r.requester_assignment);
              // Deckt sich mit core.permissions.ShiftTradeRequestPermission im
              // Backend: nur Zielperson darf annehmen/ablehnen, nur die
              // anbietende Person darf zurückziehen (Admin/Planer dürfen immer).
              const isTarget = canManage || (ownEmployeeId !== null && r.target_employee === ownEmployeeId);
              const isRequester =
                canManage ||
                (ownEmployeeId !== null && requesterAssignment?.employee === ownEmployeeId);
              return (
                <li key={r.id} className="entry-list-item entry-list-item--trade">
                  <span className={`status-badge status-badge--${r.status}`}>
                    {STATUS_LABELS[r.status] ?? r.status}
                  </span>
                  <span className="entry-main">
                    <strong>{requesterAssignment ? employeeName(requesterAssignment.employee) : "…"}</strong>{" "}
                    bietet Schicht {describeAssignment(r.requester_assignment)} an{" "}
                    <strong>{employeeName(r.target_employee)}</strong>
                    {r.target_assignment && (
                      <> im Tausch gegen deren Schicht {describeAssignment(r.target_assignment)}</>
                    )}
                    {r.note && <span className="entry-note"> · {r.note}</span>}
                  </span>
                  {r.status === "pending" && (isTarget || isRequester) && (
                    <span className="entry-actions">
                      {isTarget && (
                        <>
                          <button
                            type="button"
                            disabled={busyId === r.id}
                            onClick={() => runAction(r.id, api.acceptShiftTradeRequest)}
                          >
                            Annehmen
                          </button>
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={busyId === r.id}
                            onClick={() => runAction(r.id, api.declineShiftTradeRequest)}
                          >
                            Ablehnen
                          </button>
                        </>
                      )}
                      {isRequester && (
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={busyId === r.id}
                          onClick={() => runAction(r.id, api.cancelShiftTradeRequest)}
                        >
                          Zurückziehen
                        </button>
                      )}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

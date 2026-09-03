import { useEffect, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";

const STATUS_LABELS = {
  pending: "Offen",
  employee_accepted: "Angenommen, wartet auf Freigabe",
  accepted: "Freigegeben",
  declined: "Abgelehnt",
  cancelled: "Zurückgezogen",
  rejected: "Von Planer abgelehnt",
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

  function templateForAssignment(id) {
    const assignment = assignmentsById.get(id);
    return assignment ? templatesById.get(assignment.template) : undefined;
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
              const isOpen = r.status === "pending" || r.status === "employee_accepted";
              // Deckt sich mit core.permissions.ShiftTradeRequestPermission im
              // Backend: Admin/Planer geben frei/lehnen ab (approve/reject),
              // die Zielperson nimmt nur an/lehnt ab (accept/decline), die
              // anbietende Person zieht nur zurück (cancel) -- nie beides.
              const isEmployeeTarget =
                !canManage && ownEmployeeId !== null && r.target_employee === ownEmployeeId;
              const isEmployeeRequester =
                !canManage && ownEmployeeId !== null && requesterAssignment?.employee === ownEmployeeId;
              const requesterTemplate = templateForAssignment(r.requester_assignment);
              const targetAssignment = r.target_assignment ? assignmentsById.get(r.target_assignment) : undefined;
              const targetTemplate = templateForAssignment(r.target_assignment);
              return (
                <li key={r.id} className="entry-list-item entry-list-item--trade trade-entry">
                  <span className="entry-main">
                    <div className="trade-header">
                      <strong>{requesterAssignment ? employeeName(requesterAssignment.employee) : "…"}</strong>
                      <span className="trade-arrow" aria-hidden="true">⇄</span>
                      <strong>{employeeName(r.target_employee)}</strong>
                      <span className={`status-badge status-badge--${r.status}`}>
                        {STATUS_LABELS[r.status] ?? r.status}
                      </span>
                    </div>
                    <div className="trade-shifts">
                      <span className="trade-shift-row">
                        <span className="trade-shift-label">Bietet</span>
                        {requesterTemplate && (
                          <span className="shift-chip" style={{ "--chip-color": requesterTemplate.color }}>
                            {requesterTemplate.name}
                          </span>
                        )}
                        <span className="entry-date">{requesterAssignment?.date ?? "…"}</span>
                      </span>
                      {r.target_assignment && (
                        <span className="trade-shift-row">
                          <span className="trade-shift-label">Gegen</span>
                          {targetTemplate && (
                            <span className="shift-chip" style={{ "--chip-color": targetTemplate.color }}>
                              {targetTemplate.name}
                            </span>
                          )}
                          <span className="entry-date">{targetAssignment?.date ?? "…"}</span>
                        </span>
                      )}
                    </div>
                    {r.note && (
                      <div className="time-record-notes">
                        <span className="entry-note">{r.note}</span>
                      </div>
                    )}
                  </span>
                  {canManage && isOpen && (
                    <span className="entry-actions">
                      <button
                        type="button"
                        disabled={busyId === r.id}
                        onClick={() => runAction(r.id, api.approveShiftTradeRequest)}
                      >
                        Freigeben
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busyId === r.id}
                        onClick={() => runAction(r.id, api.rejectShiftTradeRequest)}
                      >
                        Ablehnen
                      </button>
                    </span>
                  )}
                  {r.status === "pending" && isEmployeeTarget && (
                    <span className="entry-actions">
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
                    </span>
                  )}
                  {isOpen && isEmployeeRequester && (
                    <span className="entry-actions">
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busyId === r.id}
                        onClick={() => runAction(r.id, api.cancelShiftTradeRequest)}
                      >
                        Zurückziehen
                      </button>
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

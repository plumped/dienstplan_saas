import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const ABSENCE_TYPE_LABELS = { vacation: "Ferien", sick: "Krankheit", other: "Sonstiges" };

function isoToday() {
  return new Date().toISOString().slice(0, 10);
}

// README MVP-Fahrplan Block 2, Punkt 21: Übersichtsseite für Admin/Planer.
// Bewusst eine reine Aggregations-, keine Zweitbearbeitungs-Oberfläche --
// alle Listen kommen aus denselben Endpoints/api.js-Funktionen wie
// AbsencePanel.jsx/TradeRequestPanel.jsx (Genehmigen/Ablehnen ruft exakt
// dieselben Mutations-Funktionen auf), damit hier keine zweite, potenziell
// abweichende Genehmigungslogik entsteht. Einzige neue Datenquelle ist
// api.getUnderstaffedShifts() (siehe dort), weil die Mindestbesetzungs-
// Auswertung sonst nur clientseitig pro einzeln gewählter Station existiert
// (PlanGrid.jsx, Block 2.9).
export default function Dashboard({ me, onNavigate, onError }) {
  const [absences, setAbsences] = useState([]);
  const [trades, setTrades] = useState([]);
  const [employeesById, setEmployeesById] = useState(new Map());
  const [assignmentsById, setAssignmentsById] = useState(new Map());
  const [understaffed, setUnderstaffed] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    async function load() {
      const [absencesRes, tradesRes, employeesRes, understaffedRes] = await Promise.all([
        api.getAbsences(),
        api.getShiftTradeRequests(),
        api.getEmployees(),
        api.getUnderstaffedShifts(),
      ]);
      const absenceList = absencesRes.results ?? absencesRes;
      const tradeList = tradesRes.results ?? tradesRes;
      const employeeList = employeesRes.results ?? employeesRes;

      // Nur für die tatsächlich freigabebereiten Anfragen (EMPLOYEE_ACCEPTED)
      // brauchen wir die zugehörige Schicht -- analog zu TradeRequestPanel.jsx.
      const assignmentIds = new Set();
      for (const r of tradeList) {
        if (r.status !== "employee_accepted") continue;
        assignmentIds.add(r.requester_assignment);
      }
      const assignments = await Promise.all([...assignmentIds].map((id) => api.getShiftAssignment(id)));

      if (cancelled) return;
      setAbsences(absenceList);
      setTrades(tradeList);
      setEmployeesById(new Map(employeeList.map((e) => [e.id, e])));
      setAssignmentsById(new Map(assignments.map((a) => [a.id, a])));
      setUnderstaffed(understaffedRes);
    }

    load()
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const today = isoToday();

  const openAbsences = useMemo(() => absences.filter((a) => a.status === "pending"), [absences]);
  const absentToday = useMemo(
    () => absences.filter((a) => a.status === "approved" && a.start_date <= today && today <= a.end_date),
    [absences, today]
  );
  // Dieselben Kriterien wie core.views._task_counts ("trades"): EMPLOYEE_ACCEPTED
  // ist der Stand, an dem eine Anfrage tatsächlich auf Admin/Planer-Freigabe
  // wartet, nicht jede offene PENDING-Anfrage (die meist zuerst auf die
  // Zielperson wartet).
  const openTrades = useMemo(() => trades.filter((t) => t.status === "employee_accepted"), [trades]);

  function employeeName(id) {
    const e = employeesById.get(id);
    return e ? `${e.first_name} ${e.last_name}` : `#${id}`;
  }

  async function handleApproveAbsence(id) {
    try {
      const updated = await api.approveAbsence(id);
      setAbsences((prev) => prev.map((a) => (a.id === id ? updated : a)));
    } catch (e) {
      onError(e.message);
    }
  }

  async function handleRejectAbsence(id) {
    try {
      const updated = await api.rejectAbsence(id);
      setAbsences((prev) => prev.map((a) => (a.id === id ? updated : a)));
    } catch (e) {
      onError(e.message);
    }
  }

  async function handleApproveTrade(id) {
    setBusyId(id);
    try {
      const updated = await api.approveShiftTradeRequest(id);
      setTrades((prev) => prev.map((t) => (t.id === id ? updated : t)));
    } catch (e) {
      onError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleRejectTrade(id) {
    setBusyId(id);
    try {
      const updated = await api.rejectShiftTradeRequest(id);
      setTrades((prev) => prev.map((t) => (t.id === id ? updated : t)));
    } catch (e) {
      onError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  function goToPlanGrid(nodeId, dateIso) {
    const [year, month] = dateIso.split("-").map(Number);
    onNavigate({ tab: "grid", nodeId, year, month });
  }

  if (loading) return <p className="loading-state">Übersicht wird geladen …</p>;

  const nothingOpen =
    openAbsences.length === 0 && openTrades.length === 0 && understaffed.length === 0 && absentToday.length === 0;

  return (
    <div className="side-panel">
      <div className="panel-list panel-list--full">
        <h2>Übersicht</h2>
        {nothingOpen && <p className="empty-state">Nichts offen -- alles erledigt.</p>}

        {openAbsences.length > 0 && (
          <section className="dashboard-section">
            <h3>Offene Absenzanträge ({openAbsences.length})</h3>
            <ul className="entry-list">
              {openAbsences.map((a) => (
                <li key={a.id} className="entry-list-item">
                  <span className={`type-badge type-badge--${a.type}`}>
                    {ABSENCE_TYPE_LABELS[a.type] ?? a.type}
                  </span>
                  <span className="entry-main">
                    <strong>{employeeName(a.employee)}</strong> · {a.start_date} – {a.end_date}
                    {a.note && <span className="entry-note"> · {a.note}</span>}
                  </span>
                  <span className="entry-actions">
                    <button type="button" onClick={() => handleApproveAbsence(a.id)}>
                      Genehmigen
                    </button>
                    <button type="button" className="btn-ghost" onClick={() => handleRejectAbsence(a.id)}>
                      Ablehnen
                    </button>
                    <button type="button" className="btn-ghost" onClick={() => onNavigate({ tab: "absences" })}>
                      Details
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {openTrades.length > 0 && (
          <section className="dashboard-section">
            <h3>Diensttausch wartet auf Freigabe ({openTrades.length})</h3>
            <ul className="entry-list">
              {openTrades.map((t) => {
                const requesterAssignment = assignmentsById.get(t.requester_assignment);
                return (
                  <li key={t.id} className="entry-list-item entry-list-item--trade">
                    <span className="entry-main">
                      <strong>{requesterAssignment ? employeeName(requesterAssignment.employee) : "…"}</strong>
                      <span className="trade-arrow" aria-hidden="true">
                        {" "}
                        ⇄{" "}
                      </span>
                      <strong>{employeeName(t.target_employee)}</strong>
                      {requesterAssignment && <span className="entry-date"> · {requesterAssignment.date}</span>}
                    </span>
                    <span className="entry-actions">
                      <button type="button" disabled={busyId === t.id} onClick={() => handleApproveTrade(t.id)}>
                        Freigeben
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={busyId === t.id}
                        onClick={() => handleRejectTrade(t.id)}
                      >
                        Ablehnen
                      </button>
                      <button type="button" className="btn-ghost" onClick={() => onNavigate({ tab: "trades" })}>
                        Details
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {absentToday.length > 0 && (
          <section className="dashboard-section">
            <h3>Heute abwesend ({absentToday.length})</h3>
            <ul className="entry-list">
              {absentToday.map((a) => (
                <li key={a.id} className="entry-list-item">
                  <span className={`type-badge type-badge--${a.type}`}>
                    {ABSENCE_TYPE_LABELS[a.type] ?? a.type}
                  </span>
                  <span className="entry-main">
                    <strong>{employeeName(a.employee)}</strong> · bis {a.end_date}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {understaffed.length > 0 && (
          <section className="dashboard-section">
            <h3>Unterbesetzte Schichten (nächste 7 Tage)</h3>
            <ul className="entry-list">
              {understaffed.map((u) => (
                <li key={`${u.date}:${u.template_id}`} className="entry-list-item">
                  <span className="entry-main">
                    <span className="shift-chip" style={{ "--chip-color": u.template_color }}>
                      {u.template_name}
                    </span>{" "}
                    <strong>{u.node_name}</strong> · {u.date} · {u.count}/{u.minimum_staffing} besetzt
                  </span>
                  <span className="entry-actions">
                    <button type="button" className="btn-ghost" onClick={() => goToPlanGrid(u.node_id, u.date)}>
                      Im Planblatt öffnen
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

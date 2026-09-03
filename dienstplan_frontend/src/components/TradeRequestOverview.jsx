import { useEffect, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";

const PAGE_SIZE = 50;

const SORT_COLUMNS = [
  { field: "requester_assignment__date", label: "Datum" },
  { field: "requester_assignment__employee__last_name", label: "Anbietende Person" },
  { field: "requester_assignment__node__name", label: "Station" },
];

const STATUS_LABELS = {
  pending: "Offen",
  employee_accepted: "Angenommen, wartet auf Freigabe",
  accepted: "Freigegeben",
  declined: "Abgelehnt",
  cancelled: "Zurückgezogen",
  rejected: "Von Planer abgelehnt",
};

const emptyPage = { count: 0, next: null, previous: null, results: [] };

// Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
// aufgebaut sein wie Zeiterfassung" -- ersetzt für Admin/Planer/HR die
// bisherige, auf eine Station begrenzte TradeRequestPanel.jsx (die
// Mitarbeitende weiterhin unverändert nutzen, siehe App.jsx) durch eine
// stationsübergreifende, durchsuch-/sortierbare Übersicht mit zwei
// Segmenten ("Offen"/"Alle"). Die pro Zeile passende Aktion hängt weiterhin
// von der Beziehung der eingeloggten Person zur Anfrage ab (Admin/Planer:
// Freigeben/Ablehnen; Zielperson: Annehmen/Ablehnen; anbietende Person:
// Zurückziehen) -- anders als bei der Ist-Zeit-Bestätigung gibt es hier
// deshalb bewusst KEINE Massenaktion, die richtige Aktion ist nicht
// einheitlich über die Zeilen hinweg.
export default function TradeRequestOverview({ nodes, me, onError }) {
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;

  const [view, setView] = useState("open");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [ordering, setOrdering] = useState("requester_assignment__date");
  const [nodeFilter, setNodeFilter] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(emptyPage);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, search, ordering, nodeFilter]);

  function loadPage() {
    setLoading(true);
    return api
      .searchShiftTradeRequests({ open: view === "open", search, ordering, node: nodeFilter, page })
      .then(setData)
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .searchShiftTradeRequests({ open: view === "open", search, ordering, node: nodeFilter, page })
      .then((result) => !cancelled && setData(result))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, search, ordering, nodeFilter, page]);

  function toggleSort(field) {
    setOrdering((prev) => (prev === field ? `-${field}` : field));
  }

  async function runAction(id, action) {
    setBusyId(id);
    try {
      await action(id);
      loadPage();
    } catch (e) {
      onError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  const totalPages = Math.max(1, Math.ceil(data.count / PAGE_SIZE));

  return (
    <div className="settings-table-layout">
      <div className="panel-list panel-list--full settings-table-panel">
        <div className="segmented-control">
          <button
            type="button"
            className={`segmented-control-btn${view === "open" ? " is-active" : ""}`}
            onClick={() => setView("open")}
          >
            Offen {view === "open" ? data.count > 0 && `(${data.count})` : ""}
          </button>
          <button
            type="button"
            className={`segmented-control-btn${view === "all" ? " is-active" : ""}`}
            onClick={() => setView("all")}
          >
            Alle
          </button>
        </div>

        <p className="panel-hint">
          Anfragen entstehen im Planblatt über das ⇄-Symbol an einer belegten Schicht -- über alle Stationen, die du
          siehst.
        </p>

        <div className="settings-table-toolbar">
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Person suchen …"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <select value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)}>
            <option value="">Alle Stationen</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
        </div>

        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !data.results.length ? (
          <p className="empty-state">
            {search || nodeFilter ? "Keine Treffer." : view === "open" ? "Nichts offen." : "Noch keine Anfragen."}
          </p>
        ) : (
          <>
            <div className="settings-table-scroll">
              <table className="settings-table">
                <thead>
                  <tr>
                    {SORT_COLUMNS.map((col) => (
                      <th key={col.field}>
                        <button type="button" className="settings-table-sort" onClick={() => toggleSort(col.field)}>
                          {col.label}
                          {ordering === col.field && " ▲"}
                          {ordering === `-${col.field}` && " ▼"}
                        </button>
                      </th>
                    ))}
                    <th>Zielperson</th>
                    <th>Schicht</th>
                    {view === "all" && <th>Status</th>}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((r) => {
                    const isOpen = r.status === "pending" || r.status === "employee_accepted";
                    // Deckt sich mit core.permissions.ShiftTradeRequestPermission
                    // im Backend: Admin/Planer geben frei/lehnen ab, die
                    // Zielperson nimmt nur an/lehnt ab, die anbietende Person
                    // zieht nur zurück -- nie beides.
                    const isEmployeeTarget = !canManage && ownEmployeeId !== null && r.target_employee === ownEmployeeId;
                    const isEmployeeRequester =
                      !canManage && ownEmployeeId !== null && r.requester_employee_id === ownEmployeeId;
                    return (
                      <tr key={r.id} className="settings-table-row">
                        <td>{r.requester_date}</td>
                        <td>{r.requester_employee_name}</td>
                        <td>{r.requester_node_name}</td>
                        <td>{r.target_employee_name}</td>
                        <td>
                          <div className="trade-shifts">
                            <span className="trade-shift-row">
                              <span className="trade-shift-label">Bietet</span>
                              <span className="shift-chip" style={{ "--chip-color": r.requester_template_color }}>
                                {r.requester_template_name}
                              </span>
                            </span>
                            {r.target_assignment && (
                              <span className="trade-shift-row">
                                <span className="trade-shift-label">Gegen</span>
                                <span
                                  className="shift-chip"
                                  style={{ "--chip-color": r.target_assignment_template_color }}
                                >
                                  {r.target_assignment_template_name}
                                </span>
                                <span className="entry-date">{r.target_assignment_date}</span>
                              </span>
                            )}
                            {r.note && <span className="entry-note">{r.note}</span>}
                          </div>
                        </td>
                        {view === "all" && (
                          <td>
                            <span className={`status-badge status-badge--${r.status}`}>
                              {STATUS_LABELS[r.status] ?? r.status}
                            </span>
                          </td>
                        )}
                        <td>
                          {canManage && isOpen && (
                            <span className="settings-table-actions">
                              <button
                                type="button"
                                className="btn-primary"
                                disabled={busyId === r.id}
                                onClick={() => runAction(r.id, api.approveShiftTradeRequest)}
                              >
                                Freigeben
                              </button>
                              <button
                                type="button"
                                className="btn-ghost btn-danger-ghost"
                                disabled={busyId === r.id}
                                onClick={() => runAction(r.id, api.rejectShiftTradeRequest)}
                              >
                                Ablehnen
                              </button>
                            </span>
                          )}
                          {r.status === "pending" && isEmployeeTarget && (
                            <span className="settings-table-actions">
                              <button
                                type="button"
                                className="btn-primary"
                                disabled={busyId === r.id}
                                onClick={() => runAction(r.id, api.acceptShiftTradeRequest)}
                              >
                                Annehmen
                              </button>
                              <button
                                type="button"
                                className="btn-ghost btn-danger-ghost"
                                disabled={busyId === r.id}
                                onClick={() => runAction(r.id, api.declineShiftTradeRequest)}
                              >
                                Ablehnen
                              </button>
                            </span>
                          )}
                          {isOpen && isEmployeeRequester && (
                            <span className="settings-table-actions">
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
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="settings-table-pager">
              <span>
                {data.count} {view === "open" ? "offen" : "Anfragen"}
                {totalPages > 1 ? ` · Seite ${page} von ${totalPages}` : ""}
              </span>
              <div className="entry-actions">
                <button type="button" className="btn-ghost" disabled={!data.previous} onClick={() => setPage((p) => p - 1)}>
                  ← Zurück
                </button>
                <button type="button" className="btn-ghost" disabled={!data.next} onClick={() => setPage((p) => p + 1)}>
                  Weiter →
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

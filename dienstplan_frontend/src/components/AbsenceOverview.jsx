import { useEffect, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import AbsenceForm, { DAY_PORTIONS } from "./AbsenceForm.jsx";

// Muss mit REST_FRAMEWORK.PAGE_SIZE in config/settings.py übereinstimmen --
// nur für die "Seite X von Y"-Anzeige (gleiches Muster wie
// TimeRecordOverview.jsx/EmployeeSettings.jsx).
const PAGE_SIZE = 50;

const SORT_COLUMNS = [
  { field: "start_date", label: "Datum" },
  { field: "employee__last_name", label: "Mitarbeiter" },
];

const STATUS_LABELS = {
  pending: "Offen",
  approved: "Genehmigt",
  rejected: "Abgelehnt",
};

const emptyPage = { count: 0, next: null, previous: null, results: [] };

// Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
// aufgebaut sein wie Zeiterfassung" -- ersetzt für Admin/Planer/HR die
// bisherige, auf eine Station begrenzte AbsencePanel.jsx (die Mitarbeitende
// weiterhin unverändert nutzen, siehe App.jsx) durch eine
// stationsübergreifende, durchsuch-/sortierbare Übersicht mit zwei Segmenten
// (gleiches Muster wie TimeRecordOverview.jsx: "Zu genehmigen"/"Alle").
export default function AbsenceOverview({ nodes, me, onError }) {
  // HR erreicht diese Übersicht über canViewScheduleReports (README/
  // core.permissions.MANAGER_AND_HR_ROLES, "nur Reporting"), darf aber
  // Absenzen nicht genehmigen/ablehnen (core.permissions.
  // OwnEmployeeRecordPermission blockt approve/reject serverseitig) --
  // canManage blendet die entsprechenden Bedienelemente aus, statt HR erst
  // auf einen 403 klicken zu lassen (gleiches Muster wie
  // TradeRequestOverview.jsx).
  const canManage = canManageSchedule(me);
  const [view, setView] = useState("pending");
  const [absenceTypes, setAbsenceTypes] = useState([]);
  // Bewusst ein eigener, kompletter Abruf statt einer stationsgefilterten
  // Prop -- diese Übersicht ist stationsübergreifend, das Formular
  // "+ Absenz erfassen" muss jeden Mitarbeiter tenant-weit anbieten können.
  const [employees, setEmployees] = useState([]);

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [ordering, setOrdering] = useState("start_date");
  const [nodeFilter, setNodeFilter] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(emptyPage);
  const [loading, setLoading] = useState(true);

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkApproving, setBulkApproving] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  useEffect(() => {
    api
      .getAbsenceTypes()
      .then((data) => setAbsenceTypes(data.results ?? data))
      .catch((e) => onError(e.message));
    api
      .getEmployees()
      .then((data) => setEmployees(data.results ?? data))
      .catch((e) => onError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    setSelectedIds(new Set());
    return api
      .searchAbsences({
        status: view === "pending" ? "pending" : undefined,
        search,
        ordering,
        node: nodeFilter,
        page,
      })
      .then(setData)
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSelectedIds(new Set());
    api
      .searchAbsences({
        status: view === "pending" ? "pending" : undefined,
        search,
        ordering,
        node: nodeFilter,
        page,
      })
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

  async function handleApprove(id) {
    try {
      await api.approveAbsence(id);
      loadPage();
    } catch (e) {
      onError(e.message);
    }
  }

  async function handleReject(id) {
    try {
      await api.rejectAbsence(id);
      loadPage();
    } catch (e) {
      onError(e.message);
    }
  }

  function toggleSelect(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Anders als bei der Ist-Zeit-Bestätigung (TimeRecordOverview.jsx) gibt es
  // hier kein Abweichungsmass, das eine Zeile als "auffällig" markieren
  // würde -- "Alle auswählen" nimmt deshalb einfach alle Zeilen der Seite.
  const allSelected = data.results.length > 0 && data.results.every((a) => selectedIds.has(a.id));

  function toggleSelectAll() {
    setSelectedIds((prev) => {
      if (allSelected) return new Set();
      return new Set(data.results.map((a) => a.id));
    });
  }

  async function handleBulkApprove() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBulkApproving(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => api.approveAbsence(id)));
      const failed = results.filter((r) => r.status === "rejected");
      if (failed.length) {
        onError(`${failed.length} von ${ids.length} Absenzen konnten nicht genehmigt werden.`);
      }
      setSelectedIds(new Set());
      loadPage();
    } finally {
      setBulkApproving(false);
    }
  }

  function handleCreated() {
    setFormOpen(false);
    loadPage();
  }

  const totalPages = Math.max(1, Math.ceil(data.count / PAGE_SIZE));

  return (
    <div className="settings-table-layout">
      <div className="panel-list panel-list--full settings-table-panel">
        <div className="segmented-control">
          <button
            type="button"
            className={`segmented-control-btn${view === "pending" ? " is-active" : ""}`}
            onClick={() => setView("pending")}
          >
            Zu genehmigen {view === "pending" ? data.count > 0 && `(${data.count})` : ""}
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
          {view === "pending"
            ? "Noch nicht entschiedene Abwesenheiten -- über alle Stationen, die du siehst."
            : "Alle erfassten Abwesenheiten -- über alle Stationen, die du siehst."}
        </p>

        <div className="settings-table-toolbar">
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Mitarbeiter suchen …"
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
          {canManage && (
            <button type="button" onClick={() => setFormOpen(true)}>
              + Absenz erfassen
            </button>
          )}
          {view === "pending" && canManage && selectedIds.size > 0 && (
            <span className="settings-table-bulk-bar">
              <span>{selectedIds.size} ausgewählt</span>
              <button type="button" onClick={handleBulkApprove} disabled={bulkApproving}>
                {bulkApproving ? "Genehmigt …" : "Ausgewählte genehmigen"}
              </button>
            </span>
          )}
        </div>

        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !data.results.length ? (
          <p className="empty-state">
            {search || nodeFilter
              ? "Keine Treffer."
              : view === "pending"
                ? "Nichts zu genehmigen."
                : "Keine Abwesenheiten erfasst."}
          </p>
        ) : (
          <>
            <div className="settings-table-scroll">
              <table className="settings-table">
                <thead>
                  <tr>
                    {view === "pending" && canManage && (
                      <th className="settings-table-checkbox-col">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          onChange={toggleSelectAll}
                          title="Alle Zeilen dieser Seite auswählen"
                        />
                      </th>
                    )}
                    {SORT_COLUMNS.map((col) => (
                      <th key={col.field}>
                        <button type="button" className="settings-table-sort" onClick={() => toggleSort(col.field)}>
                          {col.label}
                          {ordering === col.field && " ▲"}
                          {ordering === `-${col.field}` && " ▼"}
                        </button>
                      </th>
                    ))}
                    <th>Station(en)</th>
                    <th>Art</th>
                    <th>Tagesanteil</th>
                    <th>Notiz</th>
                    {view === "all" && <th>Status</th>}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((a) => (
                    <tr key={a.id} className="settings-table-row">
                      {view === "pending" && canManage && (
                        <td className="settings-table-checkbox-col" onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" checked={selectedIds.has(a.id)} onChange={() => toggleSelect(a.id)} />
                        </td>
                      )}
                      <td>
                        {a.start_date}
                        {a.start_date !== a.end_date && ` – ${a.end_date}`}
                      </td>
                      <td>{a.employee_name}</td>
                      <td>{a.employee_node_names}</td>
                      <td>
                        <span className="type-badge" style={{ "--chip-color": a.type_color }}>
                          {a.type_name}
                        </span>
                      </td>
                      <td>{DAY_PORTIONS.find((p) => p.value === a.day_portion)?.label ?? a.day_portion}</td>
                      <td>{a.note || "—"}</td>
                      {view === "all" && (
                        <td>
                          <span className={`status-badge status-badge--${a.status}`}>
                            {STATUS_LABELS[a.status] ?? a.status}
                          </span>
                        </td>
                      )}
                      <td>
                        {a.status === "pending" && canManage && (
                          <span className="settings-table-actions">
                            <button type="button" onClick={() => handleApprove(a.id)}>
                              Genehmigen
                            </button>
                            <button type="button" className="btn-ghost" onClick={() => handleReject(a.id)}>
                              Ablehnen
                            </button>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="settings-table-pager">
              <span>
                {data.count} {view === "pending" ? "zu genehmigen" : "Abwesenheiten"}
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

      {formOpen && (
        <div className="modal-overlay" onClick={() => setFormOpen(false)}>
          <div className="panel-form modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Absenz erfassen</h2>
              <button type="button" className="modal-close" onClick={() => setFormOpen(false)} aria-label="Schliessen">
                ×
              </button>
            </div>
            <div className="modal-body">
              <AbsenceForm
                employees={employees}
                canManage
                me={me}
                absenceTypes={absenceTypes}
                onCreated={handleCreated}
                onError={onError}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

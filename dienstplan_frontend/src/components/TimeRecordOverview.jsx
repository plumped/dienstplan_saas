import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { effectiveRecordSegments, effectiveTemplateSegments } from "../timeRecordSegments.js";
import TimeRecordSegmentEditor from "./TimeRecordSegmentEditor.jsx";

// Muss mit REST_FRAMEWORK.PAGE_SIZE in config/settings.py übereinstimmen --
// nur für die "Seite X von Y"-Anzeige (gleiches Muster wie
// TimeTemplateSettings.jsx/EmployeeSettings.jsx).
const PAGE_SIZE = 50;

const CONFIRM_SORT_COLUMNS = [
  { field: "assignment__date", label: "Datum" },
  { field: "assignment__employee__last_name", label: "Mitarbeiter" },
  { field: "assignment__node__name", label: "Station" },
];

const MISSING_SORT_COLUMNS = [
  { field: "date", label: "Datum" },
  { field: "employee__last_name", label: "Mitarbeiter" },
  { field: "node__name", label: "Station" },
];

const emptyPage = { count: 0, next: null, previous: null, results: [] };

// Nutzer-Feedback (2026-08): "Sichtbarkeit der IST-Zeit... grün für +, rot
// für -" -- hours_deviation (Ist minus Soll in Stunden, siehe
// TimeRecord.hours_deviation im Backend) macht auf einen Blick sichtbar, ob
// mehr (grün) oder weniger (rot) als geplant gearbeitet wurde, ohne dass ein
// Planer dafür erst das Korrigieren-Modal öffnen muss.
function deviationClass(hours) {
  if (hours > 0) return "is-positive";
  if (hours < 0) return "is-negative";
  return "";
}

function formatHoursDeviation(hours) {
  if (!hours) return "±0 h";
  return `${hours > 0 ? "+" : ""}${hours.toFixed(1)} h`;
}

// Nutzer-Feedback (2026-08): "braucht es eine Funktion um alle zu
// bestätigen auf einmal?" -- ja, aber "Bestätigen" ist der eigentliche
// Kontrollschritt (Art. 73 ArGV 1), ein reines "Alle bestätigen" würde den
// gerade gebauten Ist-Zeit/Abweichungs-Check untergraben. Auffällige
// Einträge (grosse Stunden-Abweichung oder Pause unter Minimum) werden
// deshalb von der "Alle auf dieser Seite auswählen"-Checkbox ausgenommen --
// einzeln lassen sie sich weiterhin bewusst mit auswählen.
const RISKY_DEVIATION_HOURS = 1;

function isRisky(record) {
  return Math.abs(record.hours_deviation) > RISKY_DEVIATION_HOURS || record.break_below_minimum;
}

// Nutzer-Feedback (2026-08): "ich muss die Stationen durchsuchen, bis ich
// die zu bestätigende Erfassung finde -- Splitten: alle offenen Bewilligungen
// neben den noch nicht erfassten, tabellarisch wie bei den Einstellungen."
// Ersetzt für Admin/Planer/HR den bisherigen, auf eine Station begrenzten
// Zeiterfassung-Tab durch zwei stationsübergreifende, durchsuch-/sortierbare
// Tabellen (gleiches Muster wie TimeTemplateSettings.jsx) -- Station steht
// direkt in der Zeile, kein manuelles Durchklicken mehr nötig.
export default function TimeRecordOverview({ nodes, initialView = "confirm", onError }) {
  const [view, setView] = useState(initialView);
  const [templates, setTemplates] = useState([]);
  // Bewusst ein eigener, kompletter Abruf statt der employees-Prop von
  // App.jsx: die ist auf die aktuell gewählte Station gefiltert
  // (relevantNodeIds), diese Übersicht ist aber stationsübergreifend --
  // ein Name aus einer anderen Station würde sonst nicht aufgelöst.
  const [employees, setEmployees] = useState([]);

  const [confirmSearchInput, setConfirmSearchInput] = useState("");
  const [confirmSearch, setConfirmSearch] = useState("");
  const [confirmOrdering, setConfirmOrdering] = useState("assignment__date");
  const [confirmNodeFilter, setConfirmNodeFilter] = useState("");
  const [confirmPage, setConfirmPage] = useState(1);
  const [confirmData, setConfirmData] = useState(emptyPage);
  const [confirmLoading, setConfirmLoading] = useState(true);

  const [missingSearchInput, setMissingSearchInput] = useState("");
  const [missingSearch, setMissingSearch] = useState("");
  const [missingOrdering, setMissingOrdering] = useState("date");
  const [missingNodeFilter, setMissingNodeFilter] = useState("");
  const [missingPage, setMissingPage] = useState(1);
  const [missingData, setMissingData] = useState(emptyPage);
  const [missingLoading, setMissingLoading] = useState(true);

  const [modal, setModal] = useState(null); // { kind: "confirm" | "missing", row }
  const [saving, setSaving] = useState(false);

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkConfirming, setBulkConfirming] = useState(false);

  useEffect(() => {
    api
      .getTimeTemplates()
      .then((data) => setTemplates(data.results ?? data))
      .catch((e) => onError(e.message));
    api
      .getEmployees()
      .then((data) => setEmployees(data.results ?? data))
      .catch((e) => onError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timeout = setTimeout(() => setConfirmSearch(confirmSearchInput), 300);
    return () => clearTimeout(timeout);
  }, [confirmSearchInput]);
  useEffect(() => {
    const timeout = setTimeout(() => setMissingSearch(missingSearchInput), 300);
    return () => clearTimeout(timeout);
  }, [missingSearchInput]);

  useEffect(() => {
    setConfirmPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [confirmSearch, confirmOrdering, confirmNodeFilter]);
  useEffect(() => {
    setMissingPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingSearch, missingOrdering, missingNodeFilter]);

  function loadConfirmPage() {
    setConfirmLoading(true);
    return api
      .searchTimeRecords({
        status: "submitted",
        search: confirmSearch,
        ordering: confirmOrdering,
        node: confirmNodeFilter,
        page: confirmPage,
      })
      .then(setConfirmData)
      .catch((e) => onError(e.message))
      .finally(() => setConfirmLoading(false));
  }

  function loadMissingPage() {
    setMissingLoading(true);
    return api
      .getMissingTimeRecords({
        search: missingSearch,
        ordering: missingOrdering,
        node: missingNodeFilter,
        page: missingPage,
      })
      .then(setMissingData)
      .catch((e) => onError(e.message))
      .finally(() => setMissingLoading(false));
  }

  useEffect(() => {
    let cancelled = false;
    setConfirmLoading(true);
    setSelectedIds(new Set());
    api
      .searchTimeRecords({
        status: "submitted",
        search: confirmSearch,
        ordering: confirmOrdering,
        node: confirmNodeFilter,
        page: confirmPage,
      })
      .then((data) => !cancelled && setConfirmData(data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setConfirmLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [confirmSearch, confirmOrdering, confirmNodeFilter, confirmPage]);

  useEffect(() => {
    let cancelled = false;
    setMissingLoading(true);
    api
      .getMissingTimeRecords({
        search: missingSearch,
        ordering: missingOrdering,
        node: missingNodeFilter,
        page: missingPage,
      })
      .then((data) => !cancelled && setMissingData(data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setMissingLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingSearch, missingOrdering, missingNodeFilter, missingPage]);

  function toggleSort(current, setter, field) {
    setter((prev) => (prev === field ? `-${field}` : field));
  }

  function templateFor(id) {
    return templates.find((t) => t.id === id);
  }

  // ShiftAssignmentSerializer trägt bewusst keine Namen (nur employee/node
  // als FK-Id) -- dieser Endpoint wird auch vom Planblatt-Grid für
  // potenziell hunderte Zuweisungen pro Monat genutzt, zusätzliche Felder
  // dort würden den Response unnötig aufblähen. Auflösung stattdessen
  // clientseitig über die bereits geladenen nodes/employees-Listen (gleiches
  // Muster wie TimeTemplateSettings.jsx: nodeName()).
  function employeeName(id) {
    const employee = employees.find((e) => e.id === id);
    return employee ? `${employee.first_name} ${employee.last_name}` : `#${id}`;
  }

  function nodeName(id) {
    return nodes.find((n) => n.id === id)?.name ?? `#${id}`;
  }

  async function handleConfirm(row) {
    try {
      await api.confirmTimeRecord(row.id);
      loadConfirmPage();
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

  // "Alle auswählen" nimmt bewusst nur die unauffälligen Zeilen der
  // aktuellen Seite -- auffällige (siehe isRisky()) bleiben aussen vor,
  // lassen sich aber weiterhin einzeln anhaken. Ist bereits alles
  // Unauffällige ausgewählt, hebt ein erneuter Klick die Auswahl wieder auf.
  const selectableRows = confirmData.results.filter((r) => !isRisky(r));
  const allSelectableSelected =
    selectableRows.length > 0 && selectableRows.every((r) => selectedIds.has(r.id));

  function toggleSelectAll() {
    setSelectedIds((prev) => {
      if (allSelectableSelected) {
        const next = new Set(prev);
        selectableRows.forEach((r) => next.delete(r.id));
        return next;
      }
      const next = new Set(prev);
      selectableRows.forEach((r) => next.add(r.id));
      return next;
    });
  }

  async function handleBulkConfirm() {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBulkConfirming(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => api.confirmTimeRecord(id)));
      const failed = results.filter((r) => r.status === "rejected");
      if (failed.length) {
        onError(`${failed.length} von ${ids.length} Erfassungen konnten nicht bestätigt werden.`);
      }
      setSelectedIds(new Set());
      loadConfirmPage();
    } finally {
      setBulkConfirming(false);
    }
  }

  function openCorrect(row) {
    setModal({ kind: "confirm", row });
  }

  function openCapture(row) {
    setModal({ kind: "missing", row });
  }

  function closeModal() {
    setModal(null);
  }

  async function handleModalSave(payload) {
    setSaving(true);
    try {
      if (modal.kind === "confirm") {
        await api.updateTimeRecord(modal.row.id, payload);
        loadConfirmPage();
      } else {
        await api.createTimeRecord({ assignment: modal.row.id, ...payload });
        loadMissingPage();
      }
      closeModal();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const confirmTotalPages = Math.max(1, Math.ceil(confirmData.count / PAGE_SIZE));
  const missingTotalPages = Math.max(1, Math.ceil(missingData.count / PAGE_SIZE));

  const modalPlannedSegments = useMemo(() => {
    if (!modal) return [];
    const templateId = modal.kind === "confirm" ? modal.row.assignment_template_id : modal.row.template;
    return effectiveTemplateSegments(templateFor(templateId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modal, templates]);

  return (
    <div className="settings-table-layout">
      <div className="panel-list panel-list--full settings-table-panel">
        <div className="segmented-control">
          <button
            type="button"
            className={`segmented-control-btn${view === "confirm" ? " is-active" : ""}`}
            onClick={() => setView("confirm")}
          >
            Zu bestätigen {confirmData.count > 0 && `(${confirmData.count})`}
          </button>
          <button
            type="button"
            className={`segmented-control-btn${view === "missing" ? " is-active" : ""}`}
            onClick={() => setView("missing")}
          >
            Noch nicht erfasst {missingData.count > 0 && `(${missingData.count})`}
          </button>
        </div>

        {view === "confirm" ? (
          <>
            <p className="panel-hint">
              Bereits erfasste, aber noch nicht bestätigte Ist-Zeiten -- über alle Stationen, die du siehst.
            </p>
            <div className="settings-table-toolbar">
              <input
                type="search"
                className="panel-list-filter"
                placeholder="Mitarbeiter suchen …"
                value={confirmSearchInput}
                onChange={(e) => setConfirmSearchInput(e.target.value)}
              />
              <select value={confirmNodeFilter} onChange={(e) => setConfirmNodeFilter(e.target.value)}>
                <option value="">Alle Stationen</option>
                {nodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
              {selectedIds.size > 0 && (
                <span className="settings-table-bulk-bar">
                  <span>{selectedIds.size} ausgewählt</span>
                  <button type="button" onClick={handleBulkConfirm} disabled={bulkConfirming}>
                    {bulkConfirming ? "Bestätigt …" : "Ausgewählte bestätigen"}
                  </button>
                </span>
              )}
            </div>
            {confirmLoading ? (
              <p className="loading-state">Wird geladen …</p>
            ) : !confirmData.results.length ? (
              <p className="empty-state">
                {confirmSearch || confirmNodeFilter ? "Keine Treffer." : "Nichts zu bestätigen."}
              </p>
            ) : (
              <>
                <div className="settings-table-scroll">
                  <table className="settings-table">
                    <thead>
                      <tr>
                        <th className="settings-table-checkbox-col">
                          <input
                            type="checkbox"
                            checked={allSelectableSelected}
                            onChange={toggleSelectAll}
                            disabled={!selectableRows.length}
                            title="Alle unauffälligen Zeilen dieser Seite auswählen"
                          />
                        </th>
                        {CONFIRM_SORT_COLUMNS.map((col) => (
                          <th key={col.field}>
                            <button
                              type="button"
                              className="settings-table-sort"
                              onClick={() => toggleSort(confirmOrdering, setConfirmOrdering, col.field)}
                            >
                              {col.label}
                              {confirmOrdering === col.field && " ▲"}
                              {confirmOrdering === `-${col.field}` && " ▼"}
                            </button>
                          </th>
                        ))}
                        <th>Schichttyp</th>
                        <th>Ist-Zeit</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {confirmData.results.map((r) => {
                        const risky = isRisky(r);
                        return (
                          <tr key={r.id} className="settings-table-row">
                            <td className="settings-table-checkbox-col" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="checkbox"
                                checked={selectedIds.has(r.id)}
                                onChange={() => toggleSelect(r.id)}
                              />
                            </td>
                            <td>{r.assignment_date}</td>
                            <td>{r.assignment_employee_name}</td>
                            <td>{r.assignment_node_name}</td>
                            <td>{r.assignment_template_name}</td>
                            <td className="time-record-ist-cell">
                              <span className="time-record-ist">
                                {(effectiveRecordSegments(r) || [])
                                  .map((s) => `${s.actual_start.slice(0, 5)}–${s.actual_end.slice(0, 5)}`)
                                  .join(", ")}
                              </span>
                              <span
                                className={`time-deviation-badge ${deviationClass(r.hours_deviation)}`}
                                title={risky ? "Auffällig -- bitte vor dem Bestätigen prüfen" : undefined}
                              >
                                ({formatHoursDeviation(r.hours_deviation)}
                                {risky && " ⚠"})
                              </span>
                            </td>
                            <td className="settings-table-actions">
                              <button type="button" className="btn-ghost" onClick={() => openCorrect(r)}>
                                Korrigieren
                              </button>
                              <button type="button" onClick={() => handleConfirm(r)}>
                                Bestätigen
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="settings-table-pager">
                  <span>
                    {confirmData.count} zu bestätigen
                    {confirmTotalPages > 1 ? ` · Seite ${confirmPage} von ${confirmTotalPages}` : ""}
                  </span>
                  <div className="entry-actions">
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={!confirmData.previous}
                      onClick={() => setConfirmPage((p) => p - 1)}
                    >
                      ← Zurück
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={!confirmData.next}
                      onClick={() => setConfirmPage((p) => p + 1)}
                    >
                      Weiter →
                    </button>
                  </div>
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <p className="panel-hint">
              Vergangene Schichten ohne Ist-Zeit -- über alle Stationen, die du siehst.
            </p>
            <div className="settings-table-toolbar">
              <input
                type="search"
                className="panel-list-filter"
                placeholder="Mitarbeiter suchen …"
                value={missingSearchInput}
                onChange={(e) => setMissingSearchInput(e.target.value)}
              />
              <select value={missingNodeFilter} onChange={(e) => setMissingNodeFilter(e.target.value)}>
                <option value="">Alle Stationen</option>
                {nodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
            </div>
            {missingLoading ? (
              <p className="loading-state">Wird geladen …</p>
            ) : !missingData.results.length ? (
              <p className="empty-state">
                {missingSearch || missingNodeFilter ? "Keine Treffer." : "Alles erfasst."}
              </p>
            ) : (
              <>
                <div className="settings-table-scroll">
                  <table className="settings-table">
                    <thead>
                      <tr>
                        {MISSING_SORT_COLUMNS.map((col) => (
                          <th key={col.field}>
                            <button
                              type="button"
                              className="settings-table-sort"
                              onClick={() => toggleSort(missingOrdering, setMissingOrdering, col.field)}
                            >
                              {col.label}
                              {missingOrdering === col.field && " ▲"}
                              {missingOrdering === `-${col.field}` && " ▼"}
                            </button>
                          </th>
                        ))}
                        <th>Schichttyp</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {missingData.results.map((a) => {
                        const template = templateFor(a.template);
                        return (
                          <tr key={a.id} className="settings-table-row">
                            <td>{a.date}</td>
                            <td>{employeeName(a.employee)}</td>
                            <td>{nodeName(a.node)}</td>
                            <td>{template?.name ?? `#${a.template}`}</td>
                            <td className="settings-table-actions">
                              <button type="button" onClick={() => openCapture(a)}>
                                Erfassen
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="settings-table-pager">
                  <span>
                    {missingData.count} noch nicht erfasst
                    {missingTotalPages > 1 ? ` · Seite ${missingPage} von ${missingTotalPages}` : ""}
                  </span>
                  <div className="entry-actions">
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={!missingData.previous}
                      onClick={() => setMissingPage((p) => p - 1)}
                    >
                      ← Zurück
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={!missingData.next}
                      onClick={() => setMissingPage((p) => p + 1)}
                    >
                      Weiter →
                    </button>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>

      {modal && (
        <div className="modal-overlay" onClick={closeModal}>
          <div className="panel-form modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>
                {modal.kind === "confirm"
                  ? `Ist-Zeit korrigieren -- ${modal.row.assignment_employee_name}, ${modal.row.assignment_date}`
                  : `Ist-Zeit erfassen -- ${employeeName(modal.row.employee)}, ${modal.row.date}`}
              </h2>
              <button type="button" className="modal-close" onClick={closeModal} aria-label="Schliessen">
                ×
              </button>
            </div>
            <div className="modal-body">
              <TimeRecordSegmentEditor
                plannedSegments={modalPlannedSegments}
                initialSegments={modal.kind === "confirm" ? effectiveRecordSegments(modal.row) : null}
                initialNote={modal.kind === "confirm" ? (modal.row.note ?? "") : ""}
                saving={saving}
                canDelete={false}
                onSave={handleModalSave}
                onCancel={closeModal}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

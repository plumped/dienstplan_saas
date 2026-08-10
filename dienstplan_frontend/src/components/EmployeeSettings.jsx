import { useEffect, useState } from "react";
import { api } from "../api.js";
import { isTenantAdmin } from "../roles.js";
import BalanceBadge from "./BalanceBadge.jsx";
import EmploymentEditor from "./EmploymentEditor.jsx";
import PregnancyEditor from "./PregnancyEditor.jsx";

// Nutzer-Feedback (2026-08): "die Stammdatenpflege ist bei vielen
// Mitarbeitenden katastrophal -- unsortierte Liste, alles scrollend suchen".
// Ab dieser Grössenordnung ist "alles laden und im Frontend filtern" (die
// bisherige Lösung) keine Option mehr -- Suche/Sortierung/Filterung laufen
// jetzt serverseitig (api.searchEmployees(), EmployeeViewSet:
// ?search=/?ordering=/?node=/?is_active=/?page=), das Frontend hält jeweils
// nur eine Tabellenseite (PAGE_SIZE=50) im Speicher.
// Muss mit REST_FRAMEWORK.PAGE_SIZE in config/settings.py übereinstimmen --
// nur für die "Seite X von Y"-Anzeige, keine Server-Logik hängt daran (der
// eigentliche next/previous-Zustand kommt direkt aus der DRF-Pagination).
const EMPLOYEE_PAGE_SIZE = 50;

const SORT_COLUMNS = [
  { field: "last_name", label: "Nachname" },
  { field: "first_name", label: "Vorname" },
  { field: "employment_pct", label: "Pensum" },
  { field: "is_active", label: "Status" },
];

function emptyForm() {
  return {
    first_name: "",
    last_name: "",
    birth_date: "",
    employment_pct: 100,
    employment_start_date: new Date().toISOString().slice(0, 10),
    employments: [],
    skills: [],
    is_active: true,
    maximum_weekly_hours: "",
    standard_weekly_hours: "",
    overtime_balance_carryover_hours: 0,
    vacation_days_per_year: "",
    last_night_work_medical_exam_date: "",
  };
}

function toFormValues(employee) {
  return {
    first_name: employee.first_name,
    last_name: employee.last_name,
    birth_date: employee.birth_date ?? "",
    employment_pct: employee.employment_pct,
    employment_start_date: employee.employment_start_date,
    // README Punkt 17: employments statt nodes -- siehe EmploymentEditor.jsx.
    // node/pensum_pct/title/is_team_lead werden 1:1 aus der API übernommen,
    // die id lassen wir bewusst weg (wird beim Speichern ohnehin komplett
    // ersetzt, siehe EmployeeSerializer._sync_employments).
    employments: employee.employments.map((e) => ({
      node: e.node,
      pensum_pct: e.pensum_pct,
      title: e.title,
      is_team_lead: e.is_team_lead,
    })),
    skills: employee.skills,
    is_active: employee.is_active,
    maximum_weekly_hours: employee.maximum_weekly_hours ?? "",
    standard_weekly_hours: employee.standard_weekly_hours ?? "",
    overtime_balance_carryover_hours: employee.overtime_balance_carryover_hours ?? 0,
    vacation_days_per_year: employee.vacation_days_per_year ?? "",
    last_night_work_medical_exam_date: employee.last_night_work_medical_exam_date ?? "",
  };
}

function selectedOptions(select) {
  return Array.from(select.selectedOptions, (o) => Number(o.value));
}

// Block 2.10: Mitarbeitenden-Stammdaten inkl. der Wochenstunden-Override-
// Felder aus Block 1.14 (z. B. Ärztin 50h statt der 42h-Tenant-Vorgabe) --
// bisher nur im Django-Admin editierbar, den ein Planer (Membership.Role.
// PLANNER) normalerweise gar nicht erreicht (separates Berechtigungssystem
// über User.is_staff).
//
// Block 2.16: Formular in Abschnitte gegliedert (fieldset), Felder mit
// Erklärtext analog zum Django-Admin-help_text, Fehler direkt am Feld statt
// nur im globalen Banner (error.fields aus api.js).
export default function EmployeeSettings({ nodes, skills, me, onError }) {
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  // Tabellen-Zustand: Freitext-Suche (mit Eingabe-Debounce), Sortierung,
  // Stations-/Status-Filter, Seite -- alles zusammen bestimmt die eine
  // Server-Anfrage, die jeweils geladen wird.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [ordering, setOrdering] = useState("last_name");
  const [nodeFilter, setNodeFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const [page, setPage] = useState(1);
  const [tableData, setTableData] = useState({ count: 0, next: null, previous: null, results: [] });
  const [listLoading, setListLoading] = useState(true);

  // Freitext-Suche debouncen (300ms), Filter/Sortierung/Seite lösen sofort
  // eine neue Anfrage aus -- ein Tippvorgang soll nicht bei jedem Zeichen
  // einen eigenen Request auslösen.
  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  // Bei jeder Änderung an Suche/Sortierung/Filter zurück auf Seite 1 --
  // sonst könnte man auf einer Seite landen, die es im neuen Ergebnis gar
  // nicht mehr gibt.
  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, ordering, nodeFilter, activeFilter]);

  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    api
      .searchEmployees({ search, ordering, node: nodeFilter, isActive: activeFilter, page })
      .then((data) => !cancelled && setTableData(data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setListLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, ordering, nodeFilter, activeFilter, page]);

  // Nach jeder Anlage/Änderung die aktuelle Seite neu laden statt den
  // Eintrag manuell im lokalen Array zu patchen -- so bleiben Sortierung/
  // Filter/Seitenzahl immer korrekt, auch wenn sich durch die Änderung z. B.
  // die Sortierposition oder die Sichtbarkeit unter einem aktiven Filter
  // verschiebt (z. B. Status-Filter "Nur aktive" + gerade deaktiviert).
  function reloadCurrentPage() {
    setListLoading(true);
    api
      .searchEmployees({ search, ordering, node: nodeFilter, isActive: activeFilter, page })
      .then(setTableData)
      .catch((e) => onError(e.message))
      .finally(() => setListLoading(false));
  }

  function toggleSort(field) {
    setOrdering((prev) => (prev === field ? `-${field}` : field));
  }

  function startEditing(employee) {
    setEditingId(employee.id);
    setForm(toFormValues(employee));
    setFieldErrors({});
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm());
    setFieldErrors({});
  }

  function updateField(key) {
    return (e) => {
      const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      setForm((prev) => ({ ...prev, [key]: value }));
      setFieldErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
    };
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.first_name.trim() || !form.last_name.trim()) {
      onError("Vor- und Nachname sind Pflichtfelder.");
      return;
    }
    const payload = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      birth_date: form.birth_date || null,
      employment_pct: Number(form.employment_pct),
      employment_start_date: form.employment_start_date,
      employments: form.employments,
      skills: form.skills,
      is_active: form.is_active,
      maximum_weekly_hours: form.maximum_weekly_hours === "" ? null : Number(form.maximum_weekly_hours),
      standard_weekly_hours: form.standard_weekly_hours === "" ? null : Number(form.standard_weekly_hours),
      overtime_balance_carryover_hours: Number(form.overtime_balance_carryover_hours) || 0,
      vacation_days_per_year: form.vacation_days_per_year === "" ? null : Number(form.vacation_days_per_year),
      last_night_work_medical_exam_date: form.last_night_work_medical_exam_date || null,
    };
    setSaving(true);
    setFieldErrors({});
    try {
      if (editingId) {
        await api.updateEmployee(editingId, payload);
      } else {
        await api.createEmployee(payload);
      }
      reloadCurrentPage();
      startCreating();
    } catch (e) {
      if (e.fields && typeof e.fields === "object") setFieldErrors(e.fields);
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  function nodeNames(ids) {
    return ids.map((id) => nodes.find((n) => n.id === id)?.name ?? `#${id}`).join(", ") || "—";
  }

  // README Punkt 17: Freitext-Autovervollständigung statt eigener
  // Stammdaten-Liste -- Vorschläge kommen aus bereits im Tenant verwendeten
  // Rollentiteln. Seit der Umstellung auf serverseitige Suche/Pagination
  // (Nutzer-Feedback 2026-08) nur noch aus der aktuell geladenen Tabellen-
  // seite abgeleitet statt aus dem kompletten Bestand -- bewusster
  // Kompromiss: ein eigener "alle je verwendeten Titel"-Endpoint wäre für
  // eine reine Autovervollständigung unverhältnismässig.
  const existingTitles = [
    ...new Set(tableData.results.flatMap((emp) => emp.employments.map((e) => e.title)).filter(Boolean)),
  ];

  function fieldError(key) {
    const message = fieldErrors[key]?.[0];
    return message ? <span className="field-error">{message}</span> : null;
  }

  const totalPages = Math.max(1, Math.ceil(tableData.count / EMPLOYEE_PAGE_SIZE));

  return (
    <div className="employee-settings-layout">
      <div className="panel-list panel-list--full employee-table-panel">
        <div className="employee-table-toolbar">
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Name suchen …"
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
          <select value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
            <option value="">Alle</option>
            <option value="true">Nur aktive</option>
            <option value="false">Nur inaktive</option>
          </select>
          <button type="button" className="btn-ghost" onClick={startCreating}>
            + Neuer Mitarbeiter
          </button>
        </div>

        {listLoading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !tableData.results.length ? (
          <p className="empty-state">
            {search || nodeFilter || activeFilter
              ? "Keine Mitarbeitenden gefunden."
              : "Noch keine Mitarbeitenden angelegt."}
          </p>
        ) : (
          <>
            <div className="employee-table-scroll">
              <table className="employee-table">
                <thead>
                  <tr>
                    {SORT_COLUMNS.map((col) => (
                      <th key={col.field}>
                        <button type="button" className="employee-table-sort" onClick={() => toggleSort(col.field)}>
                          {col.label}
                          {ordering === col.field && " ▲"}
                          {ordering === `-${col.field}` && " ▼"}
                        </button>
                      </th>
                    ))}
                    <th>Station(en)</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {tableData.results.map((emp) => (
                    <tr
                      key={emp.id}
                      className={`employee-table-row${editingId === emp.id ? " is-editing" : ""}`}
                      onClick={() => startEditing(emp)}
                    >
                      <td>{emp.last_name}</td>
                      <td>{emp.first_name}</td>
                      <td>{emp.employment_pct}%</td>
                      <td>
                        {emp.is_active ? (
                          "Aktiv"
                        ) : (
                          <span className="status-badge status-badge--cancelled">Inaktiv</span>
                        )}
                      </td>
                      <td>{nodeNames(emp.nodes)}</td>
                      <td className="employee-table-actions" onClick={(e) => e.stopPropagation()}>
                        <BalanceBadge employeeId={emp.id} />
                        <button type="button" className="btn-ghost" onClick={() => startEditing(emp)}>
                          Bearbeiten
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="employee-table-pager">
              <span>
                {tableData.count} Mitarbeitende{totalPages > 1 ? ` · Seite ${page} von ${totalPages}` : ""}
              </span>
              <div className="entry-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={!tableData.previous}
                  onClick={() => setPage((p) => p - 1)}
                >
                  ← Zurück
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={!tableData.next}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Weiter →
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      <form className="panel-form employee-form-panel" onSubmit={handleSubmit}>
        <h2>{editingId ? "Mitarbeiter bearbeiten" : "Mitarbeiter anlegen"}</h2>

        <fieldset className="panel-form-group">
          <h3>Stammdaten</h3>
          <div className="panel-form-row">
            <label>
              Vorname
              <input type="text" value={form.first_name} onChange={updateField("first_name")} required />
              {fieldError("first_name")}
            </label>
            <label>
              Nachname
              <input type="text" value={form.last_name} onChange={updateField("last_name")} required />
              {fieldError("last_name")}
            </label>
          </div>
          <div className="panel-form-row">
            <label>
              Geburtsdatum (optional)
              <input type="date" value={form.birth_date} onChange={updateField("birth_date")} />
              <span className="panel-hint">
                Nötig für den Jugendschutz (ArGV 5) bei Lernenden unter 18 Jahren -- ohne Angabe gilt die
                Person als volljährig.
              </span>
              {fieldError("birth_date")}
            </label>
            <label>
              Pensum (%)
              <input
                type="number"
                min="1"
                max="100"
                value={form.employment_pct}
                onChange={updateField("employment_pct")}
                required
              />
              <span className="panel-hint">
                Gesamtpensum für die Saldo-Berechnung -- sollte ungefähr der Summe der Anstellungen
                unten entsprechen (wird nicht automatisch geprüft).
              </span>
              {fieldError("employment_pct")}
            </label>
          </div>
          <label>
            Skills
            <select
              multiple
              value={form.skills}
              onChange={(e) => setForm((prev) => ({ ...prev, skills: selectedOptions(e.target) }))}
            >
              {skills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {fieldError("skills")}
          </label>
        </fieldset>

        <fieldset className="panel-form-group">
          <h3>Anstellungen (Teams)</h3>
          <p className="panel-hint">
            Ein Eintrag pro Team/Station, in dem diese Person tätig ist -- mehrere Einträge bilden eine
            Mehrfachanstellung ab (z. B. 40% Dozent + 60% Arzt in unterschiedlichen Teams). Ohne
            Anstellung ist die Person im Planblatt nicht einteilbar.
          </p>
          <EmploymentEditor
            employments={form.employments}
            nodes={nodes}
            existingTitles={existingTitles}
            onChange={(employments) => setForm((prev) => ({ ...prev, employments }))}
          />
          {fieldError("employments")}
        </fieldset>

        <fieldset className="panel-form-group">
          <h3>Wochenstunden-Override (Block 1.14)</h3>
          <p className="panel-hint">
            Beide Felder leer lassen, um die Tenant-weiten Standardwerte zu übernehmen -- nur setzen, wenn
            diese Person vertraglich abweicht (z. B. Ärzteschaft mit 50h statt 42h).
          </p>
          <div className="panel-form-row">
            <label>
              Höchstarbeitszeit/Woche, Std.
              <input
                type="number"
                min="1"
                max="80"
                placeholder="z. B. 50 für Ärzteschaft"
                value={form.maximum_weekly_hours}
                onChange={updateField("maximum_weekly_hours")}
              />
              <span className="panel-hint">Gesetzliche/GAV-Höchstgrenze (Art. 9 ArG).</span>
              {fieldError("maximum_weekly_hours")}
            </label>
            <label>
              Normalarbeitszeit/Woche, Std.
              <input
                type="number"
                min="1"
                max="80"
                placeholder="z. B. 42"
                value={form.standard_weekly_hours}
                onChange={updateField("standard_weekly_hours")}
              />
              <span className="panel-hint">Soll-Basis für die Überzeitberechnung (Art. 13 ArG).</span>
              {fieldError("standard_weekly_hours")}
            </label>
          </div>
        </fieldset>

        <fieldset className="panel-form-group">
          <h3>Saldo & Zeiterfassung (Block 2.7 / 1.5)</h3>
          <label>
            Eintrittsdatum
            <input
              type="date"
              value={form.employment_start_date}
              onChange={updateField("employment_start_date")}
              required
            />
            <span className="panel-hint">
              Startpunkt für das Jahressoll im Arbeitszeitmodell -- Wochen vor diesem Datum zählen weder
              als Soll noch als Ist, auch wenn sie im laufenden Kalenderjahr liegen.
            </span>
            {fieldError("employment_start_date")}
          </label>
          <div className="panel-form-row">
            <label>
              Ferienanspruch/Jahr, Tage
              <input
                type="number"
                min="0"
                max="60"
                placeholder="z. B. 25"
                value={form.vacation_days_per_year}
                onChange={updateField("vacation_days_per_year")}
              />
              <span className="panel-hint">Leer = Tenant-Standard (Art. 329a Abs. 1 OR: 20 Tage Minimum).</span>
              {fieldError("vacation_days_per_year")}
            </label>
            <label>
              Überstunden-Startsaldo, Std.
              <input
                type="number"
                step="0.5"
                value={form.overtime_balance_carryover_hours}
                onChange={updateField("overtime_balance_carryover_hours")}
              />
              <span className="panel-hint">Beim Systemstart übernommen, z. B. aus der Vorsystem-Zeiterfassung.</span>
              {fieldError("overtime_balance_carryover_hours")}
            </label>
          </div>
          <label>
            Letzte arbeitsmedizinische Untersuchung
            <input
              type="date"
              value={form.last_night_work_medical_exam_date}
              onChange={updateField("last_night_work_medical_exam_date")}
            />
            <span className="panel-hint">
              Nur relevant bei regelmässiger Nachtarbeit (Art. 17c ArG) -- wird nur ausgewertet, wenn die
              Person laut Saldo/Zeiterfassung regelmässig nachts arbeitet.
            </span>
            {fieldError("last_night_work_medical_exam_date")}
          </label>
        </fieldset>

        {isTenantAdmin(me) && editingId && (
          <fieldset className="panel-form-group">
            <h3>Mutterschutz (Art. 35a ArG, nur Admin sichtbar)</h3>
            <p className="panel-hint">
              Mehrere Einträge pro Person möglich (mehrere Schwangerschaften über die Anstellung
              hinweg). Sensibelste Kategorie personenbezogener Daten in der App -- deshalb
              unabhängig vom übrigen Formular sofort gespeichert und nur für Admin sowie die
              betroffene Person selbst sichtbar, nicht für Planer:innen allgemein.
            </p>
            <PregnancyEditor employeeId={editingId} onError={onError} />
          </fieldset>
        )}

        <label className="checkbox-row">
          <input type="checkbox" checked={form.is_active} onChange={updateField("is_active")} />
          Aktiv (deaktivierte Mitarbeitende erscheinen nicht mehr im Planblatt)
        </label>
        <div className="entry-actions">
          <button type="submit" disabled={saving}>
            {saving ? "Speichert …" : editingId ? "Speichern" : "Anlegen"}
          </button>
          {editingId && (
            <button type="button" className="btn-ghost" onClick={startCreating}>
              Abbrechen
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

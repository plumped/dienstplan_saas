import { useEffect, useState } from "react";
import { api } from "../api.js";
import BalanceBadge from "./BalanceBadge.jsx";

function emptyForm() {
  return {
    first_name: "",
    last_name: "",
    birth_date: "",
    employment_pct: 100,
    nodes: [],
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
    nodes: employee.nodes,
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
// nur im globalen Banner (error.fields aus api.js), Textfilter über der
// Liste ab realistischer Praxisgrösse (30+ Mitarbeitende).
export default function EmployeeSettings({ nodes, skills, onError }) {
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [filter, setFilter] = useState("");

  useEffect(() => {
    let cancelled = false;
    api
      .getEmployees()
      .then((data) => !cancelled && setEmployees(data.results ?? data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      nodes: form.nodes,
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
        const updated = await api.updateEmployee(editingId, payload);
        setEmployees((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
      } else {
        const created = await api.createEmployee(payload);
        setEmployees((prev) => [...prev, created]);
      }
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

  function fieldError(key) {
    const message = fieldErrors[key]?.[0];
    return message ? <span className="field-error">{message}</span> : null;
  }

  const filteredEmployees = employees.filter((emp) =>
    `${emp.first_name} ${emp.last_name}`.toLowerCase().includes(filter.trim().toLowerCase())
  );

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
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
              {fieldError("employment_pct")}
            </label>
          </div>
          <div className="panel-form-row">
            <label>
              Stationen
              <select
                multiple
                value={form.nodes}
                onChange={(e) => setForm((prev) => ({ ...prev, nodes: selectedOptions(e.target) }))}
              >
                {nodes.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
              {fieldError("nodes")}
            </label>
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
          </div>
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

      <div className="panel-list">
        <h2>Mitarbeitende</h2>
        {employees.length > 8 && (
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Name filtern …"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !employees.length ? (
          <p className="empty-state">Noch keine Mitarbeitenden angelegt.</p>
        ) : !filteredEmployees.length ? (
          <p className="empty-state">Keine Mitarbeitenden gefunden.</p>
        ) : (
          <ul className="entry-list">
            {filteredEmployees.map((emp) => (
              <li key={emp.id} className="entry-list-item">
                {!emp.is_active && <span className="status-badge status-badge--cancelled">Inaktiv</span>}
                <span className="entry-main">
                  <strong>
                    {emp.first_name} {emp.last_name}
                  </strong>{" "}
                  · {emp.employment_pct}% · {nodeNames(emp.nodes)}
                  {(emp.maximum_weekly_hours || emp.standard_weekly_hours) && (
                    <span className="entry-note">
                      {" "}
                      · Override: {emp.standard_weekly_hours ? `${emp.standard_weekly_hours}h Soll` : ""}
                      {emp.maximum_weekly_hours ? ` ${emp.maximum_weekly_hours}h Max` : ""}
                    </span>
                  )}
                </span>
                <span className="entry-actions">
                  <BalanceBadge employeeId={emp.id} />
                  <button type="button" className="btn-ghost" onClick={() => startEditing(emp)}>
                    Bearbeiten
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

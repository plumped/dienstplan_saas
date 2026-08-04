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
export default function EmployeeSettings({ nodes, skills, onError }) {
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);

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
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm());
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
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  function nodeNames(ids) {
    return ids.map((id) => nodes.find((n) => n.id === id)?.name ?? `#${id}`).join(", ") || "—";
  }

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <h2>{editingId ? "Mitarbeiter bearbeiten" : "Mitarbeiter anlegen"}</h2>
        <div className="panel-form-row">
          <label>
            Vorname
            <input
              type="text"
              value={form.first_name}
              onChange={(e) => setForm((prev) => ({ ...prev, first_name: e.target.value }))}
              required
            />
          </label>
          <label>
            Nachname
            <input
              type="text"
              value={form.last_name}
              onChange={(e) => setForm((prev) => ({ ...prev, last_name: e.target.value }))}
              required
            />
          </label>
        </div>
        <div className="panel-form-row">
          <label>
            Geburtsdatum (optional, für Jugendschutz)
            <input
              type="date"
              value={form.birth_date}
              onChange={(e) => setForm((prev) => ({ ...prev, birth_date: e.target.value }))}
            />
          </label>
          <label>
            Pensum (%)
            <input
              type="number"
              min="1"
              max="100"
              value={form.employment_pct}
              onChange={(e) => setForm((prev) => ({ ...prev, employment_pct: e.target.value }))}
              required
            />
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
          </label>
        </div>
        <div className="panel-form-row">
          <label>
            Höchstarbeitszeit/Woche, Std. (Block 1.14 -- leer = Tenant-Standard)
            <input
              type="number"
              min="1"
              max="80"
              placeholder="z. B. 50 für Ärzteschaft"
              value={form.maximum_weekly_hours}
              onChange={(e) => setForm((prev) => ({ ...prev, maximum_weekly_hours: e.target.value }))}
            />
          </label>
          <label>
            Normalarbeitszeit/Woche, Std. (Soll für Überzeit -- leer = Tenant-Standard)
            <input
              type="number"
              min="1"
              max="80"
              placeholder="z. B. 42"
              value={form.standard_weekly_hours}
              onChange={(e) => setForm((prev) => ({ ...prev, standard_weekly_hours: e.target.value }))}
            />
          </label>
        </div>
        <div className="panel-form-row">
          <label>
            Ferienanspruch/Jahr, Tage (Block 2.7 -- leer = Tenant-Standard)
            <input
              type="number"
              min="0"
              max="60"
              placeholder="z. B. 25"
              value={form.vacation_days_per_year}
              onChange={(e) => setForm((prev) => ({ ...prev, vacation_days_per_year: e.target.value }))}
            />
          </label>
          <label>
            Überstunden-Startsaldo, Std. (Block 2.7 -- beim Systemstart übernommen)
            <input
              type="number"
              step="0.5"
              value={form.overtime_balance_carryover_hours}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, overtime_balance_carryover_hours: e.target.value }))
              }
            />
          </label>
        </div>
        <label>
          Letzte arbeitsmedizinische Untersuchung (Block 1.5)
          <input
            type="date"
            value={form.last_night_work_medical_exam_date}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, last_night_work_medical_exam_date: e.target.value }))
            }
          />
          <span className="panel-hint">
            Nur relevant bei regelmässiger Nachtarbeit (Art. 17c ArG) -- wird nur ausgewertet, wenn
            die Person laut Saldo/Zeiterfassung regelmässig nachts arbeitet.
          </span>
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(e) => setForm((prev) => ({ ...prev, is_active: e.target.checked }))}
          />
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
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !employees.length ? (
          <p className="empty-state">Noch keine Mitarbeitenden angelegt.</p>
        ) : (
          <ul className="entry-list">
            {employees.map((emp) => (
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

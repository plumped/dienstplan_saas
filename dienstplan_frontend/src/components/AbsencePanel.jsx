import { useEffect, useState } from "react";
import { api } from "../api.js";

const TYPE_OPTIONS = [
  { value: "vacation", label: "Ferien" },
  { value: "sick", label: "Krankheit" },
  { value: "other", label: "Sonstiges" },
];

const TYPE_LABELS = Object.fromEntries(TYPE_OPTIONS.map((t) => [t.value, t.label]));

function emptyForm(employees) {
  return {
    employee: employees[0]?.id ?? "",
    start_date: "",
    end_date: "",
    type: "vacation",
    note: "",
  };
}

export default function AbsencePanel({ employees, onError }) {
  const [absences, setAbsences] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(() => emptyForm(employees));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setForm(emptyForm(employees));
  }, [employees]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getAbsences()
      .then((data) => {
        if (cancelled) return;
        const employeeIds = new Set(employees.map((e) => e.id));
        const list = (data.results ?? data).filter((a) => employeeIds.has(a.employee));
        setAbsences(list);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.employee || !form.start_date || !form.end_date) {
      onError("Mitarbeiter, Start- und Enddatum sind Pflichtfelder.");
      return;
    }
    setSaving(true);
    try {
      const created = await api.createAbsence({
        employee: Number(form.employee),
        start_date: form.start_date,
        end_date: form.end_date,
        type: form.type,
        note: form.note,
      });
      setAbsences((prev) => [created, ...prev]);
      setForm((prev) => ({ ...emptyForm(employees), employee: prev.employee }));
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id) {
    try {
      await api.deleteAbsence(id);
      setAbsences((prev) => prev.filter((a) => a.id !== id));
    } catch (e) {
      onError(e.message);
    }
  }

  function employeeName(id) {
    const employee = employees.find((e) => e.id === id);
    return employee ? `${employee.first_name} ${employee.last_name}` : `#${id}`;
  }

  if (!employees.length) {
    return (
      <p className="empty-state">
        Keine Mitarbeiter dieser Station zugeordnet. Im Admin unter „Employees“ ergänzen.
      </p>
    );
  }

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <h2>Abwesenheit erfassen</h2>
        <div className="panel-form-row">
          <label>
            Mitarbeiter
            <select
              value={form.employee}
              onChange={(e) => setForm((prev) => ({ ...prev, employee: e.target.value }))}
            >
              {employees.map((emp) => (
                <option key={emp.id} value={emp.id}>
                  {emp.first_name} {emp.last_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Art
            <select
              value={form.type}
              onChange={(e) => setForm((prev) => ({ ...prev, type: e.target.value }))}
            >
              {TYPE_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="panel-form-row">
          <label>
            Von
            <input
              type="date"
              value={form.start_date}
              onChange={(e) => setForm((prev) => ({ ...prev, start_date: e.target.value }))}
              required
            />
          </label>
          <label>
            Bis
            <input
              type="date"
              value={form.end_date}
              onChange={(e) => setForm((prev) => ({ ...prev, end_date: e.target.value }))}
              required
            />
          </label>
        </div>
        <label>
          Notiz (optional)
          <input
            type="text"
            value={form.note}
            onChange={(e) => setForm((prev) => ({ ...prev, note: e.target.value }))}
          />
        </label>
        <button type="submit" disabled={saving}>
          {saving ? "Speichert …" : "Anlegen"}
        </button>
      </form>

      <div className="panel-list">
        <h2>Erfasste Abwesenheiten</h2>
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : absences.length === 0 ? (
          <p className="empty-state">Keine Abwesenheiten für diese Station erfasst.</p>
        ) : (
          <ul className="entry-list">
            {absences.map((a) => (
              <li key={a.id} className="entry-list-item">
                <span className={`type-badge type-badge--${a.type}`}>{TYPE_LABELS[a.type] ?? a.type}</span>
                <span className="entry-main">
                  <strong>{employeeName(a.employee)}</strong> · {a.start_date} – {a.end_date}
                  {a.note && <span className="entry-note"> · {a.note}</span>}
                </span>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => handleDelete(a.id)}
                  aria-label="Abwesenheit löschen"
                >
                  Löschen
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

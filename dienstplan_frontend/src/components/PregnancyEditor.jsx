import { useEffect, useState } from "react";
import { api } from "../api.js";

function emptyForm() {
  return { expected_birth_date: "", actual_birth_date: "", notes: "" };
}

// Mutterschutz (Art. 35a ArG, MVP-Fahrplan Block 1.15): eigene, unabhängige
// Ressource (core.permissions.PregnancyPermission) -- anders als
// EmploymentEditor oben in EmployeeSettings.jsx NICHT Teil des
// Employee-Speicher-Payloads, sondern sofort bei jeder Änderung gespeichert
// (analog BalanceBadge), weil eine Schwangerschaft ein eigenständiges,
// admin-only sichtbares Ereignis ist. Mehrere Einträge pro Person möglich
// (mehrere Schwangerschaften über die Anstellung hinweg).
export default function PregnancyEditor({ employeeId, onError }) {
  const [pregnancies, setPregnancies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getPregnancies(employeeId)
      .then((data) => !cancelled && setPregnancies(data.results ?? data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employeeId]);

  function startEditing(pregnancy) {
    setEditingId(pregnancy.id);
    setForm({
      expected_birth_date: pregnancy.expected_birth_date,
      actual_birth_date: pregnancy.actual_birth_date ?? "",
      notes: pregnancy.notes ?? "",
    });
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm());
  }

  // Bewusst kein <form>/onSubmit -- diese Komponente hängt innerhalb des
  // "Mitarbeiter bearbeiten"-Formulars in EmployeeSettings.jsx (verschachtelte
  // <form>-Elemente sind ungültiges HTML: ein Submit hier würde als Bubbling-
  // Submit-Event zusätzlich das äussere Formular auslösen, das den Mitarbeiter
  // neu speichert und dabei editingId zurücksetzt -- die Ansicht springt dann
  // ungewollt aus dem Bearbeiten-Modus, ohne dass die Schwangerschaft sichtbar
  // gespeichert wurde). Der Button löst stattdessen direkt per onClick aus.
  async function handleSubmit() {
    if (!form.expected_birth_date) {
      onError("Voraussichtlicher Geburtstermin ist ein Pflichtfeld.");
      return;
    }
    const payload = {
      employee: employeeId,
      expected_birth_date: form.expected_birth_date,
      actual_birth_date: form.actual_birth_date || null,
      notes: form.notes,
    };
    setSaving(true);
    try {
      if (editingId) {
        const updated = await api.updatePregnancy(editingId, payload);
        setPregnancies((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
      } else {
        const created = await api.createPregnancy(payload);
        setPregnancies((prev) => [created, ...prev]);
      }
      startCreating();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id) {
    if (!window.confirm("Diesen Schwangerschafts-Eintrag wirklich löschen?")) return;
    try {
      await api.deletePregnancy(id);
      setPregnancies((prev) => prev.filter((p) => p.id !== id));
      if (editingId === id) startCreating();
    } catch (e) {
      onError(e.message);
    }
  }

  if (loading) return <p className="loading-state">Wird geladen …</p>;

  return (
    <div className="pregnancy-editor">
      {!pregnancies.length ? (
        <p className="empty-state">Keine Schwangerschaften erfasst.</p>
      ) : (
        <ul className="entry-list">
          {pregnancies.map((p) => (
            <li key={p.id} className="entry-list-item">
              <span className="entry-main">
                Termin {p.expected_birth_date}
                {p.actual_birth_date && <> · effektiv {p.actual_birth_date}</>}
                {p.notes && <span className="entry-note"> · {p.notes}</span>}
              </span>
              <span className="entry-actions">
                <button type="button" className="btn-ghost" onClick={() => startEditing(p)}>
                  Bearbeiten
                </button>
                <button type="button" className="btn-ghost" onClick={() => handleDelete(p.id)}>
                  Löschen
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="panel-form-row">
        <label>
          Voraussichtlicher Geburtstermin
          <input
            type="date"
            value={form.expected_birth_date}
            onChange={(e) => setForm((prev) => ({ ...prev, expected_birth_date: e.target.value }))}
            required
          />
        </label>
        <label>
          Tatsächliches Geburtsdatum (sobald bekannt)
          <input
            type="date"
            value={form.actual_birth_date}
            onChange={(e) => setForm((prev) => ({ ...prev, actual_birth_date: e.target.value }))}
          />
        </label>
        <label>
          Notiz (optional)
          <input
            type="text"
            value={form.notes}
            onChange={(e) => setForm((prev) => ({ ...prev, notes: e.target.value }))}
          />
        </label>
        <div className="entry-actions">
          <button type="button" disabled={saving} onClick={handleSubmit}>
            {saving ? "Speichert …" : editingId ? "Speichern" : "Hinzufügen"}
          </button>
          {editingId && (
            <button type="button" className="btn-ghost" onClick={startCreating}>
              Abbrechen
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

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
  // Nutzer-Feedback (2026-08): ein Fehler (z. B. falsches Datumsformat durch
  // einen Tippfehler in einem der beiden nebeneinanderliegenden Datumsfelder)
  // landete bisher nur im globalen Fehlerbanner oben auf der Seite -- leicht
  // zu übersehen, wenn man mittendrin in den Mitarbeiter-Einstellungen ist.
  // Feldbezogene Anzeige analog EmployeeSettings.jsx (fieldError()).
  const [fieldErrors, setFieldErrors] = useState({});

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
    setFieldErrors({});
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm());
    setFieldErrors({});
  }

  function updateField(key) {
    return (e) => {
      const value = e.target.value;
      setForm((prev) => ({ ...prev, [key]: value }));
      setFieldErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
    };
  }

  function fieldError(key) {
    const message = fieldErrors[key]?.[0];
    return message ? <span className="field-error">{message}</span> : null;
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
      setFieldErrors({ expected_birth_date: ["Pflichtfeld."] });
      return;
    }
    const payload = {
      employee: employeeId,
      expected_birth_date: form.expected_birth_date,
      actual_birth_date: form.actual_birth_date || null,
      notes: form.notes,
    };
    setSaving(true);
    setFieldErrors({});
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
      if (e.fields && typeof e.fields === "object") setFieldErrors(e.fields);
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
          <input type="date" value={form.expected_birth_date} onChange={updateField("expected_birth_date")} />
          {fieldError("expected_birth_date")}
        </label>
        <label>
          Tatsächliches Geburtsdatum (sobald bekannt)
          <input type="date" value={form.actual_birth_date} onChange={updateField("actual_birth_date")} />
          {fieldError("actual_birth_date")}
        </label>
        <label>
          Notiz (optional)
          <input type="text" value={form.notes} onChange={updateField("notes")} />
          {fieldError("notes")}
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

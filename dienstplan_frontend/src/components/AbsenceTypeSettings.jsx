import { useEffect, useState } from "react";
import { api } from "../api.js";
import { chipGlyph } from "../chipGlyph.js";

function emptyForm() {
  return { name: "", color: "#64748b", icon: "", deducts_vacation_days: false };
}

function toFormValues(absenceType) {
  return {
    name: absenceType.name,
    color: absenceType.color,
    icon: absenceType.icon ?? "",
    deducts_vacation_days: absenceType.deducts_vacation_days,
  };
}

// Nutzer-Feedback (2026-08): Absenzarten (Ferien/Krankheit/Sonstiges) waren
// bisher hartcodiert -- jetzt ein tenant-eigener Katalog, analog zu den
// Schichttypen (TimeTemplateSettings.jsx).
export default function AbsenceTypeSettings({ onError }) {
  const [absenceTypes, setAbsenceTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getAbsenceTypes()
      .then((data) => !cancelled && setAbsenceTypes(data.results ?? data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startEditing(absenceType) {
    setEditingId(absenceType.id);
    setForm(toFormValues(absenceType));
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm());
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.name.trim()) {
      onError("Name ist ein Pflichtfeld.");
      return;
    }
    const payload = {
      name: form.name.trim(),
      color: form.color,
      icon: form.icon,
      deducts_vacation_days: form.deducts_vacation_days,
    };
    setSaving(true);
    try {
      if (editingId) {
        const updated = await api.updateAbsenceType(editingId, payload);
        setAbsenceTypes((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      } else {
        const created = await api.createAbsenceType(payload);
        setAbsenceTypes((prev) => [...prev, created]);
      }
      startCreating();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(absenceType) {
    try {
      await api.deleteAbsenceType(absenceType.id);
      setAbsenceTypes((prev) => prev.filter((t) => t.id !== absenceType.id));
    } catch (e) {
      onError(e.message);
    }
  }

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <h2>{editingId ? "Absenzart bearbeiten" : "Absenzart anlegen"}</h2>
        <label>
          Name
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
            required
          />
        </label>
        <div className="panel-form-row">
          <label>
            Farbe
            <input
              type="color"
              value={form.color}
              onChange={(e) => setForm((prev) => ({ ...prev, color: e.target.value }))}
            />
            <span className="panel-hint">Chip-Hintergrundfarbe im Planblatt/Jahresplan.</span>
          </label>
          <label>
            Kürzel (optional)
            <input
              type="text"
              maxLength={50}
              placeholder="z. B. F oder ein Emoji"
              value={form.icon}
              onChange={(e) => setForm((prev) => ({ ...prev, icon: e.target.value }))}
            />
            <span className="panel-hint">
              Erscheint als Chip-Glyphe im Planblatt -- leer lassen übernimmt den ersten Buchstaben
              des Namens.
            </span>
          </label>
        </div>
        <label className="panel-checkbox-row">
          <input
            type="checkbox"
            checked={form.deducts_vacation_days}
            onChange={(e) => setForm((prev) => ({ ...prev, deducts_vacation_days: e.target.checked }))}
          />
          Zieht Ferientage vom Ferienanspruch ab
        </label>
        <p className="panel-hint">
          Genehmigte Absenzen dieser Art zählen als Ferienbezug (Employee.vacation_balance()) --
          typischerweise nur für eine einzige Absenzart wie "Ferien" aktiv.
        </p>

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
        <h2>Absenzarten</h2>
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !absenceTypes.length ? (
          <p className="empty-state">Noch keine Absenzarten angelegt.</p>
        ) : (
          <ul className="entry-list">
            {absenceTypes.map((t) => (
              <li key={t.id} className="entry-list-item">
                <span className="shift-chip" style={{ "--chip-color": t.color }}>
                  {chipGlyph(t)}
                </span>
                <span className="entry-main">
                  <strong>{t.name}</strong>
                  {t.deducts_vacation_days && <span className="entry-note"> · zieht Ferientage ab</span>}
                </span>
                <span className="entry-actions">
                  <button type="button" className="btn-ghost" onClick={() => startEditing(t)}>
                    Bearbeiten
                  </button>
                  <button type="button" className="btn-ghost" onClick={() => handleDelete(t)}>
                    Löschen
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

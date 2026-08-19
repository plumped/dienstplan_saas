import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { chipGlyph } from "../chipGlyph.js";
import ColorPickerField from "./ColorPickerField.jsx";
import { IconCalendar, IconHeartPulse, IconList, IconPencil, IconPlus, IconSearch, IconTrash } from "../icons.jsx";

function emptyForm() {
  return {
    name: "",
    color: "#64748b",
    icon: "",
    deducts_vacation_days: false,
    counts_as_sick_leave: false,
  };
}

function toFormValues(absenceType) {
  return {
    name: absenceType.name,
    color: absenceType.color,
    icon: absenceType.icon ?? "",
    deducts_vacation_days: absenceType.deducts_vacation_days,
    counts_as_sick_leave: absenceType.counts_as_sick_leave,
  };
}

// Nutzer-Feedback (2026-08): Absenzarten (Ferien/Krankheit/Sonstiges) waren
// bisher hartcodiert -- jetzt ein tenant-eigener Katalog, analog zu den
// Schichttypen (TimeTemplateSettings.jsx).
//
// Settings-Panel-Polish (2026-08, Nutzer-Feedback: "Einstellungs-Tabs
// wirken amateurhaft, nicht wie Business-Software", mit Vorbild-
// Screenshot): Kopfzeile mit Icon+Titel+Untertitel, Options-Karten statt
// nackter Checkbox+Text-Zeilen, gestylter Farbwähler-Trigger statt des
// rohen <input type="color">, umrandete Icon-Buttons statt Textlinks,
// durchsuchbare Liste mit Eintrags-Zähler. Erste Umsetzung dieses Musters
// -- als Vorlage für die übrigen Einstellungs-Tabs gedacht (siehe neue,
// generische Klassen in styles.css).
export default function AbsenceTypeSettings({ onError }) {
  const [absenceTypes, setAbsenceTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");

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
      counts_as_sick_leave: form.counts_as_sick_leave,
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

  const visibleAbsenceTypes = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return absenceTypes;
    return absenceTypes.filter((t) => t.name.toLowerCase().includes(term));
  }, [absenceTypes, search]);

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <div className="settings-form-header">
          <span className="settings-form-icon">
            {editingId ? <IconPencil /> : <IconPlus />}
          </span>
          <div>
            <h2>{editingId ? "Absenzart bearbeiten" : "Absenzart anlegen"}</h2>
            <p className="settings-form-subtitle">
              {editingId
                ? "Passe die Eigenschaften dieser Absenzart an."
                : "Definiere eine neue Absenzart für dein Unternehmen."}
            </p>
          </div>
        </div>
        <label>
          <span>
            Name
            <span
              className="field-tooltip"
              title="Wird im Planblatt, Jahresplan und in Abwesenheitsanträgen angezeigt."
            >
              ?
            </span>
          </span>
          <input
            type="text"
            placeholder="z. B. Ferien, Krankheit, Homeoffice"
            value={form.name}
            onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
            required
          />
        </label>
        <div className="panel-form-row">
          <label>
            Farbe
            <ColorPickerField
              value={form.color}
              onChange={(e) => setForm((prev) => ({ ...prev, color: e.target.value }))}
            />
            <span className="panel-hint">Diese Farbe wird als Chip-Hintergrund im Plan und Jahresplan angezeigt.</span>
          </label>
          <label>
            <span>
              Kürzel (optional)
              <span
                className="field-tooltip"
                title="Kurzform für die Chip-Anzeige, z. B. im engen Jahresplan-Raster."
              >
                ?
              </span>
            </span>
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

        <div className="option-card-list">
          <label className="option-card">
            <span className="option-card-icon">
              <IconCalendar />
            </span>
            <span className="option-card-body">
              <span className="option-card-title">
                <input
                  type="checkbox"
                  checked={form.deducts_vacation_days}
                  onChange={(e) => setForm((prev) => ({ ...prev, deducts_vacation_days: e.target.checked }))}
                />
                Zieht Ferientage vom Ferienanspruch ab
              </span>
              <p className="option-card-desc">
                Genehmigte Absenzen dieser Art zählen als Ferienbezug (Employee.vacation_balance()) --
                typischerweise nur für eine einzige Absenzart wie "Ferien" aktiv.
              </p>
            </span>
          </label>
          <label className="option-card">
            <span className="option-card-icon option-card-icon--warn">
              <IconHeartPulse />
            </span>
            <span className="option-card-body">
              <span className="option-card-title">
                <input
                  type="checkbox"
                  checked={form.counts_as_sick_leave}
                  onChange={(e) => setForm((prev) => ({ ...prev, counts_as_sick_leave: e.target.checked }))}
                />
                Zählt gegen den Lohnfortzahlungs-Anspruch bei Krankheit
              </span>
              <p className="option-card-desc">
                Genehmigte Absenzen dieser Art zählen gegen den Anspruch nach Art. 324a OR
                (Employee.sick_pay_summary()) -- typischerweise nur für eine Absenzart wie "Krankheit"
                aktiv.
              </p>
            </span>
          </label>
        </div>

        <div className="entry-actions">
          <button type="submit" className="btn-primary" disabled={saving}>
            {!editingId && <IconPlus width={15} height={15} />}
            {saving ? "Speichert …" : editingId ? "Speichern" : "Absenzart anlegen"}
          </button>
          {editingId && (
            <button type="button" className="btn-ghost" onClick={startCreating}>
              Abbrechen
            </button>
          )}
        </div>
      </form>

      <div className="panel-list">
        <div className="panel-list-header-row">
          <div>
            <h2>Absenzarten</h2>
            <p className="settings-form-subtitle">Verwalte und bearbeite bestehende Absenzarten.</p>
          </div>
          {absenceTypes.length > 1 && (
            <span className="panel-list-search">
              <IconSearch width={15} height={15} />
              <input
                type="search"
                className="panel-list-filter"
                placeholder="Suchen …"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </span>
          )}
        </div>
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !absenceTypes.length ? (
          <p className="empty-state">Noch keine Absenzarten angelegt.</p>
        ) : !visibleAbsenceTypes.length ? (
          <p className="empty-state">Keine Absenzarten gefunden.</p>
        ) : (
          <ul className="entry-list">
            {visibleAbsenceTypes.map((t) => (
              <li key={t.id} className="entry-list-item">
                <span className="shift-chip" style={{ "--chip-color": t.color }}>
                  {chipGlyph(t)}
                </span>
                <span className="entry-main">
                  <strong>{t.name}</strong>
                  {t.deducts_vacation_days && <span className="entry-note"> · zieht Ferientage ab</span>}
                  {t.counts_as_sick_leave && (
                    <span className="entry-note"> · zählt als Krankheit (Lohnfortzahlung)</span>
                  )}
                </span>
                <span className="entry-actions">
                  <button type="button" className="icon-btn" onClick={() => startEditing(t)}>
                    <IconPencil width={14} height={14} />
                    Bearbeiten
                  </button>
                  <button type="button" className="icon-btn icon-btn-danger" onClick={() => handleDelete(t)}>
                    <IconTrash width={14} height={14} />
                    Löschen
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        {!loading && absenceTypes.length > 0 && (
          <p className="panel-list-footer">
            <IconList width={14} height={14} />
            {visibleAbsenceTypes.length === absenceTypes.length
              ? `${absenceTypes.length} ${absenceTypes.length === 1 ? "Absenzart" : "Absenzarten"}`
              : `${visibleAbsenceTypes.length} von ${absenceTypes.length} Absenzarten`}
          </p>
        )}
      </div>
    </div>
  );
}

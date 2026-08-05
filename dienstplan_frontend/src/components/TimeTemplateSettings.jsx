import { useEffect, useState } from "react";
import { api } from "../api.js";
import TimeTemplateSegmentEditor from "./TimeTemplateSegmentEditor.jsx";

function emptyForm(defaultNodeId) {
  return {
    name: "",
    node: defaultNodeId ?? "",
    start_time: "07:00",
    end_time: "15:00",
    break_minutes: 30,
    icon: "",
    color: "#2563eb",
    required_skill: "",
    minimum_staffing: 0,
    segments: [],
  };
}

function toFormValues(template) {
  return {
    name: template.name,
    node: template.node,
    start_time: template.start_time.slice(0, 5),
    end_time: template.end_time.slice(0, 5),
    break_minutes: template.break_minutes,
    icon: template.icon,
    color: template.color,
    required_skill: template.required_skill ?? "",
    minimum_staffing: template.minimum_staffing,
    segments: (template.segments ?? []).map((s) => ({
      start_time: s.start_time.slice(0, 5),
      end_time: s.end_time.slice(0, 5),
    })),
  };
}

// Block 1.9/2.10: Schichttyp-Verwaltung inkl. optionaler Blockstruktur
// (TimeTemplateSegmentEditor) -- bisher nur im Django-Admin
// (TimeTemplateSegmentInline) editierbar.
export default function TimeTemplateSettings({ nodes, skills, onError }) {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(() => emptyForm(nodes[0]?.id));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getTimeTemplates()
      .then((data) => !cancelled && setTemplates(data.results ?? data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startEditing(template) {
    setEditingId(template.id);
    setForm(toFormValues(template));
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm(nodes[0]?.id));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.node) {
      onError("Name und Station sind Pflichtfelder.");
      return;
    }
    const payload = {
      name: form.name.trim(),
      node: Number(form.node),
      start_time: form.start_time,
      end_time: form.end_time,
      break_minutes: Number(form.break_minutes),
      icon: form.icon,
      color: form.color,
      required_skill: form.required_skill || null,
      minimum_staffing: Number(form.minimum_staffing) || 0,
      segments: form.segments.map((s, i) => ({ order: i, start_time: s.start_time, end_time: s.end_time })),
    };
    setSaving(true);
    try {
      if (editingId) {
        const updated = await api.updateTimeTemplate(editingId, payload);
        setTemplates((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      } else {
        const created = await api.createTimeTemplate(payload);
        setTemplates((prev) => [...prev, created]);
      }
      startCreating();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(template) {
    try {
      await api.deleteTimeTemplate(template.id);
      setTemplates((prev) => prev.filter((t) => t.id !== template.id));
    } catch (e) {
      onError(e.message);
    }
  }

  function nodeName(id) {
    return nodes.find((n) => n.id === id)?.name ?? `#${id}`;
  }

  if (!nodes.length) {
    return <p className="empty-state">Zuerst unter „Stationen“ eine Station anlegen.</p>;
  }

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <h2>{editingId ? "Schichttyp bearbeiten" : "Schichttyp anlegen"}</h2>
        <div className="panel-form-row">
          <label>
            Name
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              required
            />
          </label>
          <label>
            Station
            <select
              value={form.node}
              onChange={(e) => setForm((prev) => ({ ...prev, node: e.target.value }))}
              required
            >
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="panel-form-row">
          <label>
            Beginn
            <input
              type="time"
              value={form.start_time}
              onChange={(e) => setForm((prev) => ({ ...prev, start_time: e.target.value }))}
              required
            />
          </label>
          <label>
            Ende
            <input
              type="time"
              value={form.end_time}
              onChange={(e) => setForm((prev) => ({ ...prev, end_time: e.target.value }))}
              required
            />
          </label>
          <label>
            Pause (Min., ohne Segmente unten)
            <input
              type="number"
              min="0"
              value={form.break_minutes}
              onChange={(e) => setForm((prev) => ({ ...prev, break_minutes: e.target.value }))}
            />
          </label>
        </div>
        <div className="panel-form-row">
          <label>
            Farbe
            <input
              type="color"
              value={form.color}
              onChange={(e) => setForm((prev) => ({ ...prev, color: e.target.value }))}
            />
          </label>
          <label>
            Erforderlicher Skill (optional)
            <select
              value={form.required_skill}
              onChange={(e) => setForm((prev) => ({ ...prev, required_skill: e.target.value }))}
            >
              <option value="">— keiner —</option>
              {skills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <h3>Blockstruktur (optional, Block 1.9)</h3>
        <p className="panel-hint">
          Ohne Segmente gilt oben ein einzelnes Zeitfenster mit pauschaler Pause. Mit Segmenten
          (z. B. Vormittag/Nachmittag) ergibt sich die Pause aus der Lücke dazwischen -- die
          Ist-Erfassung übernimmt dann genau diese Blockanzahl.
        </p>
        <TimeTemplateSegmentEditor
          segments={form.segments}
          onChange={(segments) => setForm((prev) => ({ ...prev, segments }))}
        />

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
        <h2>Schichttypen</h2>
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !templates.length ? (
          <p className="empty-state">Noch keine Schichttypen angelegt.</p>
        ) : (
          <ul className="entry-list">
            {templates.map((t) => (
              <li key={t.id} className="entry-list-item">
                <span className="shift-chip" style={{ "--chip-color": t.color }}>
                  {t.name.slice(0, 3)}
                </span>
                <span className="entry-main">
                  <strong>{t.name}</strong> · {nodeName(t.node)} · {t.start_time.slice(0, 5)}–
                  {t.end_time.slice(0, 5)}
                  {t.segments?.length > 1 && (
                    <span className="entry-note"> · {t.segments.length} Segmente</span>
                  )}
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

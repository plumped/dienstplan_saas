import { useState } from "react";
import { api } from "../api.js";

export default function SkillSettings({ skills, onCreated, onUpdated, onDeleted, onError }) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [filter, setFilter] = useState("");
  const filteredSkills = skills.filter((s) => s.name.toLowerCase().includes(filter.trim().toLowerCase()));

  async function handleSubmit(event) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    try {
      const created = await api.createSkill({ name: name.trim() });
      onCreated(created);
      setName("");
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleRename(skill) {
    if (!editName.trim() || editName === skill.name) {
      setEditingId(null);
      return;
    }
    try {
      const updated = await api.updateSkill(skill.id, { name: editName.trim() });
      onUpdated(updated);
    } catch (e) {
      onError(e.message);
    } finally {
      setEditingId(null);
    }
  }

  async function handleDelete(skill) {
    try {
      await api.deleteSkill(skill.id);
      onDeleted(skill.id);
    } catch (e) {
      onError(e.message);
    }
  }

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <h2>Skill anlegen</h2>
        <label>
          Name
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} required />
          <span className="panel-hint">
            Qualifikation, die Mitarbeitende mitbringen können (Employee.skills) und die ein
            Schichttyp optional voraussetzen kann (TimeTemplateSettings.jsx: "Erforderlicher
            Skill") -- gilt tenant-weit, nicht pro Station.
          </span>
        </label>
        <button type="submit" disabled={saving || !name.trim()}>
          {saving ? "Speichert …" : "Anlegen"}
        </button>
      </form>

      <div className="panel-list">
        <h2>Skills</h2>
        {skills.length > 8 && (
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Name filtern …"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        {!skills.length ? (
          <p className="empty-state">Noch keine Skills angelegt.</p>
        ) : !filteredSkills.length ? (
          <p className="empty-state">Keine Skills gefunden.</p>
        ) : (
          <ul className="entry-list">
            {filteredSkills.map((s) => (
              <li key={s.id} className="entry-list-item">
                <span className="entry-main">
                  {editingId === s.id ? (
                    <input
                      type="text"
                      value={editName}
                      autoFocus
                      onChange={(e) => setEditName(e.target.value)}
                      onBlur={() => handleRename(s)}
                      onKeyDown={(e) => e.key === "Enter" && handleRename(s)}
                    />
                  ) : (
                    <strong>{s.name}</strong>
                  )}
                </span>
                <span className="entry-actions">
                  {editingId !== s.id && (
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => {
                        setEditingId(s.id);
                        setEditName(s.name);
                      }}
                    >
                      Umbenennen
                    </button>
                  )}
                  <button type="button" className="btn-ghost" onClick={() => handleDelete(s)}>
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

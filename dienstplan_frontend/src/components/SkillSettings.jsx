import { useState } from "react";
import { api } from "../api.js";
import { IconBadge, IconList, IconPencil, IconPlus, IconSearch, IconTrash } from "../icons.jsx";

// Settings-Panel-Polish (2026-08, Rollout): dasselbe Muster wie
// AbsenceTypeSettings.jsx -- Kopfzeile mit Icon+Titel+Untertitel,
// umrandete Icon-Buttons statt Textlinks, durchsuchbare Liste mit
// Eintrags-Zähler.
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
        <div className="settings-form-header">
          <span className="settings-form-icon">
            <IconBadge />
          </span>
          <div>
            <h2>Skill anlegen</h2>
            <p className="settings-form-subtitle">Definiere eine neue Qualifikation für dein Unternehmen.</p>
          </div>
        </div>
        <label>
          <span>
            Name
            <span
              className="field-tooltip"
              title="Qualifikation, die ein Schichttyp optional voraussetzen kann."
            >
              ?
            </span>
          </span>
          <input
            type="text"
            placeholder="z. B. Reanimationstraining, Stationsleitung"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <span className="panel-hint">
            Qualifikation, die Mitarbeitende mitbringen können (Employee.skills) und die ein
            Schichttyp optional voraussetzen kann (TimeTemplateSettings.jsx: "Erforderlicher
            Skill") -- gilt tenant-weit, nicht pro Station.
          </span>
        </label>
        <div className="entry-actions">
          <button type="submit" className="btn-primary" disabled={saving || !name.trim()}>
            <IconPlus width={15} height={15} />
            {saving ? "Speichert …" : "Skill anlegen"}
          </button>
        </div>
      </form>

      <div className="panel-list">
        <div className="panel-list-header-row">
          <div>
            <h2>Skills</h2>
            <p className="settings-form-subtitle">Verwalte und bearbeite bestehende Skills.</p>
          </div>
          {skills.length > 8 && (
            <span className="panel-list-search">
              <IconSearch width={15} height={15} />
              <input
                type="search"
                className="panel-list-filter"
                placeholder="Name filtern …"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </span>
          )}
        </div>
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
                      className="icon-btn"
                      onClick={() => {
                        setEditingId(s.id);
                        setEditName(s.name);
                      }}
                    >
                      <IconPencil width={14} height={14} />
                      Umbenennen
                    </button>
                  )}
                  <button type="button" className="icon-btn icon-btn-danger" onClick={() => handleDelete(s)}>
                    <IconTrash width={14} height={14} />
                    Löschen
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        {!!skills.length && (
          <p className="panel-list-footer">
            <IconList width={14} height={14} />
            {filteredSkills.length === skills.length
              ? `${skills.length} ${skills.length === 1 ? "Skill" : "Skills"}`
              : `${filteredSkills.length} von ${skills.length} Skills`}
          </p>
        )}
      </div>
    </div>
  );
}

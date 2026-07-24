import { useState } from "react";
import { api } from "../api.js";

function emptyForm() {
  return { name: "", parent: "" };
}

// Block 2.10: einfache Stationsverwaltung (Liste + Anlegen + Umbenennen +
// Löschen). Verschieben eines Knotens im Baum (treebeard, siehe
// scheduling.models.Node) ist bewusst nicht Teil dieser Oberfläche -- dafür
// bräuchte es move_to()-Semantik, die hier nicht gebraucht wird.
export default function NodeSettings({ nodes, onCreated, onUpdated, onDeleted, onError }) {
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      const created = await api.createNode({
        name: form.name.trim(),
        parent: form.parent ? Number(form.parent) : undefined,
      });
      onCreated(created);
      setForm(emptyForm());
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleRename(node) {
    if (!editName.trim() || editName === node.name) {
      setEditingId(null);
      return;
    }
    try {
      const updated = await api.updateNode(node.id, { name: editName.trim() });
      onUpdated(updated);
    } catch (e) {
      onError(e.message);
    } finally {
      setEditingId(null);
    }
  }

  async function handleDelete(node) {
    try {
      await api.deleteNode(node.id);
      onDeleted(node.id);
    } catch (e) {
      onError(e.message);
    }
  }

  return (
    <div className="side-panel">
      <form className="panel-form" onSubmit={handleSubmit}>
        <h2>Station anlegen</h2>
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
            Übergeordnete Station (optional)
            <select
              value={form.parent}
              onChange={(e) => setForm((prev) => ({ ...prev, parent: e.target.value }))}
            >
              <option value="">— keine (oberste Ebene) —</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {"— ".repeat(Math.max(0, n.depth - 1))}
                  {n.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button type="submit" disabled={saving || !form.name.trim()}>
          {saving ? "Speichert …" : "Anlegen"}
        </button>
      </form>

      <div className="panel-list">
        <h2>Stationen</h2>
        {!nodes.length ? (
          <p className="empty-state">Noch keine Stationen angelegt.</p>
        ) : (
          <ul className="entry-list">
            {nodes.map((n) => (
              <li key={n.id} className="entry-list-item">
                <span className="entry-main" style={{ paddingLeft: `${Math.max(0, n.depth - 1) * 16}px` }}>
                  {editingId === n.id ? (
                    <input
                      type="text"
                      value={editName}
                      autoFocus
                      onChange={(e) => setEditName(e.target.value)}
                      onBlur={() => handleRename(n)}
                      onKeyDown={(e) => e.key === "Enter" && handleRename(n)}
                    />
                  ) : (
                    <strong>{n.name}</strong>
                  )}
                </span>
                <span className="entry-actions">
                  {editingId !== n.id && (
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => {
                        setEditingId(n.id);
                        setEditName(n.name);
                      }}
                    >
                      Umbenennen
                    </button>
                  )}
                  <button type="button" className="btn-ghost" onClick={() => handleDelete(n)}>
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

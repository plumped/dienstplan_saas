import { useState } from "react";
import { api } from "../api.js";

function emptyForm() {
  return { name: "", parent: "" };
}

// Nutzer-Feedback (2026-08): "sollten wir die anderen Tabs auch umbauen?" --
// Stationen bewusst NICHT auf das Tabellen-/Modal-Muster von Mitarbeitende/
// Schichttypen umgestellt (Baumstruktur, kein flaches Set -- das würde die
// Eltern-Kind-Beziehung zerstören), stattdessen: Freitextsuche (zeigt einen
// Treffer inkl. seiner Eltern-Kette, damit der Baum weiter Sinn ergibt) plus
// Drag & Drop zum Umhängen. node_order_by=["name"] (siehe Node-Modell)
// sortiert Geschwisterknoten immer automatisch alphabetisch -- Drag & Drop
// reparentet daher nur (verschiebt UNTER einen anderen Knoten oder auf die
// oberste Ebene), sortiert aber nicht manuell innerhalb derselben Ebene um.
export default function NodeSettings({ nodes, onCreated, onUpdated, onDeleted, onNodesChanged, onError }) {
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [search, setSearch] = useState("");
  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);

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

  // Ein Knoten darf nicht auf sich selbst oder einen seiner eigenen
  // Nachfahren fallen gelassen werden (würde einen Zyklus im Baum erzeugen).
  // Der materialized-path-String (n.path) macht das ohne Server-Roundtrip
  // prüfbar: jeder Nachfahre hat den Pfad des Vorfahren als Präfix.
  function isSelfOrDescendant(ancestorId, nodeId) {
    const ancestor = nodes.find((n) => n.id === ancestorId);
    const node = nodes.find((n) => n.id === nodeId);
    if (!ancestor || !node) return false;
    return node.path.startsWith(ancestor.path);
  }

  function canDropOn(targetId) {
    return dragId != null && targetId !== dragId && !isSelfOrDescendant(dragId, targetId);
  }

  function handleDragStart(event, node) {
    setDragId(node.id);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(node.id));
  }

  function handleDragEnd() {
    setDragId(null);
    setOverId(null);
  }

  function handleDragOver(event, node) {
    if (!canDropOn(node.id)) return;
    event.preventDefault();
    setOverId(node.id);
  }

  function handleDragLeave(node) {
    setOverId((prev) => (prev === node.id ? null : prev));
  }

  async function moveNode(id, parentId) {
    try {
      await api.moveNode(id, parentId);
      onNodesChanged();
    } catch (e) {
      onError(e.message);
    }
  }

  async function handleDrop(event, node) {
    event.preventDefault();
    const draggedId = dragId;
    setDragId(null);
    setOverId(null);
    if (draggedId == null || !canDropOn(node.id)) return;
    await moveNode(draggedId, node.id);
  }

  function handleRootDragOver(event) {
    if (dragId == null) return;
    event.preventDefault();
    setOverId("root");
  }

  function handleRootDragLeave() {
    setOverId((prev) => (prev === "root" ? null : prev));
  }

  async function handleRootDrop(event) {
    event.preventDefault();
    const draggedId = dragId;
    setDragId(null);
    setOverId(null);
    if (draggedId == null) return;
    await moveNode(draggedId, null);
  }

  const query = search.trim().toLowerCase();
  const matches = query ? nodes.filter((n) => n.name.toLowerCase().includes(query)) : nodes;
  // Ein Treffer bleibt sichtbar, aber auch seine gesamte Eltern-Kette (sonst
  // hinge er ohne Kontext frei im Baum) -- ein Vorfahre ist jeder Knoten,
  // dessen Pfad Präfix eines Treffer-Pfads ist.
  const visibleNodes = query
    ? nodes.filter((n) => matches.some((m) => m.path.startsWith(n.path)))
    : nodes;

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
        {nodes.length > 0 && (
          <>
            <input
              type="search"
              className="panel-list-filter"
              placeholder="Station suchen …"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <p className="panel-hint">
              Station auf eine andere ziehen, um sie dort unterzuordnen -- innerhalb derselben Ebene
              wird immer alphabetisch sortiert, eine manuelle Reihenfolge ist nicht möglich.
            </p>
          </>
        )}
        {dragId != null && (
          <div
            className={`node-tree-root-drop${overId === "root" ? " is-drop-target" : ""}`}
            onDragOver={handleRootDragOver}
            onDragLeave={handleRootDragLeave}
            onDrop={handleRootDrop}
          >
            ⬆ Auf oberste Ebene verschieben
          </div>
        )}
        {!nodes.length ? (
          <p className="empty-state">Noch keine Stationen angelegt.</p>
        ) : !visibleNodes.length ? (
          <p className="empty-state">Keine Stationen gefunden.</p>
        ) : (
          <ul className="entry-list">
            {visibleNodes.map((n) => (
              <li
                key={n.id}
                draggable={editingId !== n.id}
                onDragStart={(e) => handleDragStart(e, n)}
                onDragEnd={handleDragEnd}
                onDragOver={(e) => handleDragOver(e, n)}
                onDragLeave={() => handleDragLeave(n)}
                onDrop={(e) => handleDrop(e, n)}
                className={`entry-list-item node-tree-row${overId === n.id ? " is-drop-target" : ""}${
                  dragId === n.id ? " is-dragging" : ""
                }`}
              >
                <span className="entry-main" style={{ paddingLeft: `${Math.max(0, n.depth - 1) * 16}px` }}>
                  <span className="drag-handle" title="Ziehen zum Verschieben">
                    ⠿
                  </span>
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

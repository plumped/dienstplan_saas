import { useState } from "react";
import { api } from "../api.js";

// Schritt 2 des OnboardingWizard: die vom Seed vorbefüllten Demo-Stationen
// (core.onboarding.seed_demo_tenant, "(Beispiel)"-Suffix) mit Inline-
// Umbenennen, plus ein Mini-Formular "+ Station hinzufügen" (nur Name, kein
// Parent-Picker/Cost-Center -- bewusst nicht NodeSettings.jsx' Tree-UI).
// "Weiter" ist nie blockierend, die Demo-Stationen existieren bereits.
export default function OnboardingStepStations({ nodes, onNodesChange, onNext, onError }) {
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);

  function startEdit(node) {
    setEditingId(node.id);
    setEditName(node.name);
  }

  async function saveEdit(node) {
    const name = editName.trim();
    if (!name || name === node.name) {
      setEditingId(null);
      return;
    }
    try {
      const updated = await api.updateNode(node.id, { name });
      onNodesChange(nodes.map((n) => (n.id === updated.id ? updated : n)));
    } catch (e) {
      onError(e.message);
    } finally {
      setEditingId(null);
    }
  }

  async function handleAdd(event) {
    event.preventDefault();
    if (!newName.trim()) return;
    setSaving(true);
    try {
      const created = await api.createNode({ name: newName.trim() });
      onNodesChange([...nodes, created]);
      setNewName("");
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel-form onboarding-step-body">
      <h2>Ihre Stationen</h2>
      <p className="panel-hint">
        Zum Ausprobieren bereits zwei Beispiel-Stationen angelegt -- Namen können Sie direkt
        anpassen.
      </p>

      <ul className="onboarding-node-list">
        {nodes.map((node) => (
          <li key={node.id} className="onboarding-node-row">
            {editingId === node.id ? (
              <>
                <input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  autoFocus
                  onKeyDown={(e) => e.key === "Enter" && saveEdit(node)}
                />
                <button type="button" className="btn-ghost" onClick={() => saveEdit(node)}>
                  Speichern
                </button>
              </>
            ) : (
              <>
                <span>{node.name}</span>
                <button type="button" className="btn-ghost" onClick={() => startEdit(node)}>
                  Umbenennen
                </button>
              </>
            )}
          </li>
        ))}
      </ul>

      <form className="panel-form-row" onSubmit={handleAdd}>
        <label>
          Weitere Station hinzufügen
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="z. B. Küche" />
        </label>
        <button type="submit" className="btn-ghost" disabled={saving || !newName.trim()}>
          + Hinzufügen
        </button>
      </form>

      <button type="button" className="btn-primary" onClick={onNext}>
        Weiter
      </button>
    </div>
  );
}

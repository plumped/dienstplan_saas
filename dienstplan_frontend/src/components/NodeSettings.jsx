import { useEffect, useState } from "react";
import { api } from "../api.js";

function emptyForm() {
  return { name: "", parent: "", cost_center: "" };
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
//
// Bugfix (2026-08): "Verschieben funktioniert nicht richtig" -- die erste
// Version nutzte natives HTML5-Drag&Drop (draggable/dragstart/dragover/drop).
// Das ist notorisch zerbrechlich (Firefox verlangt dataTransfer.setData mit
// striktem MIME-Typ, ein Mousedown auf Text priorisiert oft Textauswahl statt
// der Element-Drag-Geste, Chrome verlangt preventDefault auf JEDEM dragover)
// und liess sich selbst mit einer realistischen Maus-Simulation nicht
// zuverlässig auslösen. Jetzt dasselbe robuste, bereits etablierte Muster wie
// PlanGrid.jsx/YearPlan.jsx für Ziehen-mit-gedrückter-Maustaste (Mousedown
// startet, Mouseenter setzt das Ziel, ein globaler window-mouseup-Listener
// schliesst ab -- funktioniert unabhängig von Browser-eigenen Drag-Quirks).
export default function NodeSettings({ nodes, onCreated, onUpdated, onDeleted, onNodesChanged, onError }) {
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editCostCenter, setEditCostCenter] = useState("");
  const [search, setSearch] = useState("");
  // dragSourceId: die gerade gezogene Station. hoverTargetId: aktuelles
  // Ziel unter dem Mauszeiger (Node-ID oder "root" für die oberste Ebene).
  const [dragSourceId, setDragSourceId] = useState(null);
  const [hoverTargetId, setHoverTargetId] = useState(null);

  // Schliesst den Ziehvorgang ab, sobald irgendwo im Fenster losgelassen
  // wird -- nicht nur exakt über einem gültigen Ziel (sonst bliebe ein
  // Ziehvorgang "hängen", wenn die Maustaste knapp daneben losgelassen wird).
  useEffect(() => {
    if (dragSourceId == null) return;
    function handleWindowMouseUp() {
      setDragSourceId(null);
      setHoverTargetId(null);
      if (hoverTargetId == null) return;
      moveNode(dragSourceId, hoverTargetId === "root" ? null : hoverTargetId);
    }
    window.addEventListener("mouseup", handleWindowMouseUp);
    return () => window.removeEventListener("mouseup", handleWindowMouseUp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragSourceId, hoverTargetId]);

  // Bugfix (2026-08, Nutzer-Feedback: "ein Kind Knoten direkt einem
  // Hauptknoten zuzuweisen funktioniert nicht -- ich muss zuerst auf oberste
  // Ebene verschieben und erst dann als Kind auf eine Hauptstation ziehen"):
  // ohne Auto-Scroll ist ein Ziel ausserhalb des sichtbaren Bereichs während
  // EINES durchgehenden Zugs schlicht unerreichbar (die Maus bewegt sich,
  // aber nichts scrollt mit). Der Umweg über die oberste Ebene funktionierte
  // nur zufällig, weil die Dropzone dafür immer ganz oben, also garantiert
  // sichtbar, liegt. Die Seite scrollt trotz `main { overflow: auto }`
  // tatsächlich über das Dokument (main wächst frei mit dem Inhalt, statt
  // selbst intern zu scrollen -- deshalb document.scrollingElement statt
  // main als Scroll-Ziel). Jetzt scrollt die Seite automatisch, wenn der
  // Mauszeiger während eines Zugs nahe an den oberen/unteren Viewport-Rand
  // kommt -- Geschwindigkeit steigt, je näher am Rand.
  useEffect(() => {
    if (dragSourceId == null) return;
    const edgeZone = 60;
    const maxSpeed = 16;
    let latestY = null;
    let rafId = null;

    function handleMouseMove(event) {
      latestY = event.clientY;
    }

    function tick() {
      if (latestY != null) {
        const scrollEl = document.scrollingElement || document.documentElement;
        if (latestY < edgeZone) {
          const intensity = Math.min(1, (edgeZone - latestY) / edgeZone);
          scrollEl.scrollTop -= maxSpeed * intensity;
        } else if (latestY > window.innerHeight - edgeZone) {
          const intensity = Math.min(1, (latestY - (window.innerHeight - edgeZone)) / edgeZone);
          scrollEl.scrollTop += maxSpeed * intensity;
        }
      }
      rafId = requestAnimationFrame(tick);
    }

    window.addEventListener("mousemove", handleMouseMove);
    rafId = requestAnimationFrame(tick);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      cancelAnimationFrame(rafId);
    };
  }, [dragSourceId]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.name.trim()) return;
    setSaving(true);
    try {
      const created = await api.createNode({
        name: form.name.trim(),
        parent: form.parent ? Number(form.parent) : undefined,
        cost_center: form.cost_center.trim(),
      });
      onCreated(created);
      setForm(emptyForm());
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  // MVP-Fahrplan Block 2, Punkt 30/31 (Nutzer-Feedback 2026-08: "was wir
  // völlig vergessen haben sind Kostenstellen auf den Abteilungen"):
  // Umbenennen speichert jetzt Name UND Kostenstelle zusammen, statt nur
  // den Namen -- ein Ort, ein Save-Vorgang pro Station.
  async function handleSaveEdit(node) {
    const name = editName.trim();
    const costCenter = editCostCenter.trim();
    if (!name || (name === node.name && costCenter === (node.cost_center || ""))) {
      setEditingId(null);
      return;
    }
    try {
      const updated = await api.updateNode(node.id, { name, cost_center: costCenter });
      onUpdated(updated);
    } catch (e) {
      onError(e.message);
    } finally {
      setEditingId(null);
    }
  }

  // Name- und Kostenstelle-Feld sitzen nebeneinander im Edit-Modus -- ein
  // Tab-Wechsel zwischen beiden würde sonst (bei einem onBlur PRO Feld) den
  // Edit-Modus vorzeitig schliessen, bevor das zweite Feld überhaupt
  // editiert wurde. onBlur auf der gemeinsamen Umhüllung + ein Tick warten,
  // ob der neue Fokus noch innerhalb der Gruppe liegt, behebt das.
  function handleEditGroupBlur(event, node) {
    const container = event.currentTarget;
    window.requestAnimationFrame(() => {
      if (!container.contains(document.activeElement)) {
        handleSaveEdit(node);
      }
    });
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
    return dragSourceId != null && targetId !== dragSourceId && !isSelfOrDescendant(dragSourceId, targetId);
  }

  function handleRowMouseDown(event, node) {
    // Nur linke Maustaste, nicht während des Umbenennens, und nicht wenn der
    // Klick eigentlich einem Button/Input galt (Umbenennen/Löschen/das
    // Umbenennen-Feld) -- sonst würde jeder Klick darauf einen Ziehvorgang
    // anstossen.
    if (event.button !== 0 || editingId === node.id) return;
    if (event.target.closest("button, input")) return;
    setDragSourceId(node.id);
    setHoverTargetId(null);
  }

  function handleRowMouseEnter(node) {
    if (dragSourceId == null) return;
    setHoverTargetId(canDropOn(node.id) ? node.id : null);
  }

  function handleRowMouseLeave(node) {
    setHoverTargetId((prev) => (prev === node.id ? null : prev));
  }

  function handleRootMouseEnter() {
    if (dragSourceId == null) return;
    setHoverTargetId("root");
  }

  function handleRootMouseLeave() {
    setHoverTargetId((prev) => (prev === "root" ? null : prev));
  }

  async function moveNode(id, parentId) {
    try {
      await api.moveNode(id, parentId);
      onNodesChanged();
    } catch (e) {
      onError(e.message);
    }
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
                  {"— ".repeat(Math.max(0, n.depth - 2))}
                  {n.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Kostenstelle (optional)
            <input
              type="text"
              value={form.cost_center}
              onChange={(e) => setForm((prev) => ({ ...prev, cost_center: e.target.value }))}
            />
          </label>
        </div>
        <p className="panel-hint">
          Für den Lohn-Export (Einstellungen → Lohnarten). Leer lassen vererbt die Kostenstelle der
          übergeordneten Station.
        </p>
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
              Station mit gedrückter Maustaste auf eine andere ziehen, um sie dort unterzuordnen --
              innerhalb derselben Ebene wird immer alphabetisch sortiert, eine manuelle Reihenfolge
              ist nicht möglich.
            </p>
          </>
        )}
        {dragSourceId != null && (
          <div
            className={`node-tree-root-drop${hoverTargetId === "root" ? " is-drop-target" : ""}`}
            onMouseEnter={handleRootMouseEnter}
            onMouseLeave={handleRootMouseLeave}
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
                onMouseDown={(e) => handleRowMouseDown(e, n)}
                onMouseEnter={() => handleRowMouseEnter(n)}
                onMouseLeave={() => handleRowMouseLeave(n)}
                className={`entry-list-item node-tree-row${hoverTargetId === n.id ? " is-drop-target" : ""}${
                  dragSourceId === n.id ? " is-dragging" : ""
                }`}
              >
                <span className="entry-main" style={{ paddingLeft: `${Math.max(0, n.depth - 2) * 16}px` }}>
                  <span className="drag-handle" title="Mit gedrückter Maustaste ziehen zum Verschieben">
                    ⠿
                  </span>
                  {editingId === n.id ? (
                    <span className="node-tree-edit-group" onBlur={(e) => handleEditGroupBlur(e, n)}>
                      <input
                        type="text"
                        value={editName}
                        autoFocus
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && handleSaveEdit(n)}
                      />
                      <input
                        type="text"
                        value={editCostCenter}
                        placeholder="Kostenstelle"
                        onChange={(e) => setEditCostCenter(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && handleSaveEdit(n)}
                      />
                    </span>
                  ) : (
                    <>
                      <strong>{n.name}</strong>
                      {n.effective_cost_center && (
                        <span className="entry-note">
                          {" "}
                          · Kostenstelle {n.effective_cost_center}
                          {!n.cost_center && " (geerbt)"}
                        </span>
                      )}
                    </>
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
                        setEditCostCenter(n.cost_center || "");
                      }}
                    >
                      Bearbeiten
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

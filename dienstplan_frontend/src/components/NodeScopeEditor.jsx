export function isDescendantOf(node, ancestor) {
  return node.id !== ancestor.id && node.path.startsWith(ancestor.path);
}

// Nutzer-Feedback (2026-08): "Wähle ich einen Hauptknoten sind auch die
// Unterknoten markiert, wähle ich nur einen Kindknoten ist nur dieser
// markiert" -- so funktioniert die Einschränkung serverseitig bereits
// (get_descendants() in scheduling.views._employee_scoped_node_ids), diese
// reine Funktion macht das nur für die Checkbox-Liste sichtbar: eine
// markierte Station checkt automatisch alle Unterstationen mit an (dort
// ausgegraut, weil implizit), und macht bereits einzeln markierte
// Unterstationen redundant. Kein eigener State -- der Aufrufer hält das
// `Set<nodeId>` und ruft dies bei jedem Checkbox-Klick auf.
export function toggleNodeSelection(selectedNodeIds, node, nodes) {
  const next = new Set(selectedNodeIds);
  if (next.has(node.id)) {
    next.delete(node.id);
    return next;
  }
  next.add(node.id);
  for (const id of next) {
    if (id === node.id) continue;
    const other = nodes.find((n) => n.id === id);
    if (other && isDescendantOf(other, node)) next.delete(id);
  }
  return next;
}

// Stations-Checkbox-Baum für Planer-/HR-Sichtbarkeit (Membership.
// scoped_nodes) -- gemeinsam genutzt von EmployeeSettings.jsx (Person mit
// Mitarbeiterprofil) und MembershipAccessSettings.jsx (Konto ohne
// Mitarbeiterprofil), seit Nutzer-Feedback (2026-08) "Es gibt nun Tab
// Mitarbeitende, Tab Mitglieder und Zugriff [...] Das muss doch intuitiver
// gelöst werden?" beide Orte dieselbe Bearbeitung brauchen.
export default function NodeScopeEditor({ nodes, selectedNodeIds, onToggle }) {
  return (
    <ul className="node-tree-select">
      {nodes.map((n) => {
        const isExplicit = selectedNodeIds.has(n.id);
        const impliedBy = !isExplicit
          ? nodes.find((a) => selectedNodeIds.has(a.id) && isDescendantOf(n, a))
          : null;
        return (
          <li key={n.id} style={{ paddingLeft: `${Math.max(n.depth - 1, 0) * 20}px` }}>
            <label className={impliedBy ? "is-implied" : undefined}>
              <input
                type="checkbox"
                checked={isExplicit || Boolean(impliedBy)}
                disabled={Boolean(impliedBy)}
                onChange={() => onToggle(n)}
              />
              <span className="node-tree-select-name">{n.name}</span>
              {impliedBy && <span className="node-tree-select-hint">inkl. über {impliedBy.name}</span>}
            </label>
          </li>
        );
      })}
    </ul>
  );
}

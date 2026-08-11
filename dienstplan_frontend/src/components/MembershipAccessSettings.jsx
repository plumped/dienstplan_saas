import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ROLE_LABELS } from "../roles.js";

const ROLE_OPTIONS = ["admin", "planner", "hr", "employee"];

function isDescendantOf(node, ancestor) {
  return node.id !== ancestor.id && node.path.startsWith(ancestor.path);
}

function emptyCreateForm() {
  return { username: "", first_name: "", last_name: "", role: "employee" };
}

// Nutzer-Feedback (2026-08): "Ist das state of the art mit Mailversand? [...]
// Applikationsmanager wird den Benutzer anlegen und nicht per Mail einladen
// -- was ist am effizientesten und intuitivsten?" -- bewusst KEIN
// E-Mail-Einladungs-Flow (README Block 2.1/3.4): Admin legt ein Konto direkt
// mit Benutzername + Rolle an, ein Temp-Passwort wird EINMALIG angezeigt
// (danach nicht mehr abrufbar) und mündlich/auf Papier weitergegeben --
// analog zu etablierten Schichtplanungs-Tools für Personal ohne durchgängig
// gepflegte Firmen-Mail (Deputy, When I Work, Planday).
//
// Nutzer-Feedback (2026-08): "Natürlich gibt es in einer Klinik Planer mit
// unterschiedlichen Zuständigkeiten! Nur Admin darf immer alles sehen." --
// Admin-only Verwaltung von Membership.scoped_nodes (unterer Abschnitt,
// unverändert nur für Planer/HR).
//
// Nutzer-Feedback (2026-08): "Warum sehe ich Peter Meier (Mitarbeiter) unter
// Planer-/HR-Zugriff?" -- Admin (immer uneingeschränkt) und Mitarbeitende
// (eigener Mechanismus über Employee.nodes/Employment, siehe
// EmployeeSettings.jsx) gehören in den Stations-Abschnitt nicht hinein, nur
// was tatsächlich zugewiesen werden kann -- die Rollen-Übersicht oben zeigt
// dagegen bewusst ALLE Mitgliedschaften.
export default function MembershipAccessSettings({ nodes, onError }) {
  const [memberships, setMemberships] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState(new Set());
  const [saving, setSaving] = useState(false);

  // Direktanlage neuer Mitgliedschaften.
  const [createFormOpen, setCreateFormOpen] = useState(false);
  const [createForm, setCreateForm] = useState(emptyCreateForm());
  const [createFieldErrors, setCreateFieldErrors] = useState({});
  const [creating, setCreating] = useState(false);
  const [newCredentials, setNewCredentials] = useState(null); // {username, password} -- einmalige Anzeige
  const [roleChangingId, setRoleChangingId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getMemberships()
      .then((data) => !cancelled && setMemberships(data.results ?? data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sortedMemberships = useMemo(
    () =>
      [...memberships].sort((a, b) =>
        (a.employee_name || a.username).localeCompare(b.employee_name || b.username)
      ),
    [memberships]
  );

  const assignableMemberships = useMemo(
    () => memberships.filter((m) => m.role === "planner" || m.role === "hr"),
    [memberships]
  );

  function createFieldError(key) {
    const message = createFieldErrors[key]?.[0];
    return message ? <span className="field-error">{message}</span> : null;
  }

  async function handleCreateSubmit(e) {
    e.preventDefault();
    setCreating(true);
    setCreateFieldErrors({});
    try {
      const created = await api.createMembership(createForm);
      // Die Create-Response (MembershipCreateSerializer) enthält nur
      // id/role/temporary_password -- username/employee_name/scoped_nodes
      // sind dort bewusst write_only bzw. gar nicht vorhanden (siehe
      // Serializer-Docstring). Statt eines unvollständigen Objekts im State
      // wird die Liste neu geladen, damit MembershipSerializer-Felder wie
      // gewohnt vollständig sind.
      const data = await api.getMemberships();
      setMemberships(data.results ?? data);
      setNewCredentials({ username: createForm.username, password: created.temporary_password });
      setCreateFormOpen(false);
      setCreateForm(emptyCreateForm());
    } catch (err) {
      if (err.fields && typeof err.fields === "object") setCreateFieldErrors(err.fields);
      else onError(err.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleRoleChange(membership, role) {
    if (role === membership.role) return;
    setRoleChangingId(membership.id);
    try {
      const updated = await api.updateMembershipRole(membership.id, role);
      setMemberships((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
    } catch (err) {
      onError(err.message);
    } finally {
      setRoleChangingId(null);
    }
  }

  const filteredMemberships = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return assignableMemberships;
    return assignableMemberships.filter((m) =>
      [m.employee_name, m.username, m.email].filter(Boolean).some((v) => v.toLowerCase().includes(needle))
    );
  }, [assignableMemberships, filter]);

  function nodeName(id) {
    return nodes.find((n) => n.id === id)?.name ?? `#${id}`;
  }

  function startEditing(membership) {
    setEditingId(membership.id);
    setSelectedNodeIds(new Set(membership.scoped_nodes));
  }

  function closeEditing() {
    setEditingId(null);
  }

  // Nutzer-Feedback (2026-08): "Wähle ich einen Hauptknoten sind auch die
  // Unterknoten markiert, wähle ich nur einen Kindknoten ist nur dieser
  // markiert" -- so funktioniert die Einschränkung serverseitig bereits
  // (get_descendants() in _employee_scoped_node_ids), die Checkbox-Liste
  // muss das nur noch sichtbar machen: eine markierte Station checkt
  // automatisch alle Unterstationen mit an (dort ausgegraut, weil implizit),
  // und macht bereits einzeln markierte Unterstationen redundant.
  function toggleNode(node) {
    setSelectedNodeIds((prev) => {
      const next = new Set(prev);
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
    });
  }

  async function handleSave() {
    setSaving(true);
    try {
      const updated = await api.updateMembershipScopedNodes(editingId, [...selectedNodeIds]);
      setMemberships((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
      closeEditing();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="loading-state">Wird geladen …</p>;

  const editingMembership = memberships.find((m) => m.id === editingId);

  return (
    <div className="side-panel">
      <div className="panel-list panel-list--full">
        <div className="panel-list-header">
          <h2>Mitglieder</h2>
          <button type="button" onClick={() => setCreateFormOpen(true)}>
            + Mitglied hinzufügen
          </button>
        </div>
        <p className="panel-hint">
          Konten werden direkt angelegt, nicht per E-Mail eingeladen -- ein Temp-Passwort wird nach dem
          Anlegen einmalig angezeigt. Rolle jederzeit über das Dropdown änderbar; mindestens eine
          Admin-Mitgliedschaft muss je Mandant erhalten bleiben.
        </p>
        {!sortedMemberships.length ? (
          <p className="empty-state">Keine Mitgliedschaften vorhanden.</p>
        ) : (
          <ul className="entry-list">
            {sortedMemberships.map((m) => (
              <li key={m.id} className="entry-list-item">
                <span className="entry-main">
                  <strong>{m.employee_name || m.username}</strong>
                  <span className="entry-note"> · {m.username}</span>
                </span>
                <span className="entry-actions">
                  <select
                    value={m.role}
                    disabled={roleChangingId === m.id}
                    onChange={(e) => handleRoleChange(m, e.target.value)}
                  >
                    {ROLE_OPTIONS.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABELS[role]}
                      </option>
                    ))}
                  </select>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {createFormOpen && (
        <div className="modal-overlay" onClick={() => setCreateFormOpen(false)}>
          <form
            className="panel-form modal-dialog"
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleCreateSubmit}
          >
            <div className="modal-header">
              <h2>Mitglied hinzufügen</h2>
              <button
                type="button"
                className="modal-close"
                onClick={() => setCreateFormOpen(false)}
                aria-label="Schliessen"
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <label>
                Benutzername
                <input
                  type="text"
                  value={createForm.username}
                  onChange={(e) => setCreateForm((f) => ({ ...f, username: e.target.value }))}
                  required
                />
                {createFieldError("username")}
              </label>
              <label>
                Vorname
                <input
                  type="text"
                  value={createForm.first_name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, first_name: e.target.value }))}
                />
              </label>
              <label>
                Nachname
                <input
                  type="text"
                  value={createForm.last_name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, last_name: e.target.value }))}
                />
              </label>
              <label>
                Rolle
                <select
                  value={createForm.role}
                  onChange={(e) => setCreateForm((f) => ({ ...f, role: e.target.value }))}
                >
                  {ROLE_OPTIONS.map((role) => (
                    <option key={role} value={role}>
                      {ROLE_LABELS[role]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-ghost" onClick={() => setCreateFormOpen(false)}>
                Abbrechen
              </button>
              <button type="submit" disabled={creating}>
                {creating ? "Wird angelegt …" : "Anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      {newCredentials && (
        <div className="modal-overlay" onClick={() => setNewCredentials(null)}>
          <div className="panel-form modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Konto angelegt</h2>
            </div>
            <div className="modal-body">
              <p className="panel-hint">
                Dieses Passwort wird nur jetzt angezeigt -- bitte an {newCredentials.username} weitergeben
                (z. B. mündlich oder auf Papier). Beim ersten Login muss es geändert werden.
              </p>
              <p>
                <strong>Benutzername:</strong> {newCredentials.username}
              </p>
              <p>
                <strong>Temp-Passwort:</strong> <code>{newCredentials.password}</code>
              </p>
            </div>
            <div className="modal-footer">
              <button type="button" onClick={() => setNewCredentials(null)}>
                Verstanden, schliessen
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="panel-list panel-list--full">
        <h2>Planer-/HR-Zugriff</h2>
        <p className="panel-hint">
          Ohne Zuweisung sieht ein Planer/HR weiterhin alle Stationen des Tenants (bisheriges Verhalten) --
          erst eine explizite Auswahl hier schränkt die Sicht ein (inkl. aller Unterstationen). Admin ist
          davon nie betroffen und sieht immer alles; Mitarbeitende werden separat unter "Mitarbeitende"
          ihrer Station zugeordnet.
        </p>
        {assignableMemberships.length > 8 && (
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Name/E-Mail filtern …"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        {!assignableMemberships.length ? (
          <p className="empty-state">Keine Planer- oder HR-Mitgliedschaften vorhanden.</p>
        ) : !filteredMemberships.length ? (
          <p className="empty-state">Keine Treffer.</p>
        ) : (
          <ul className="entry-list">
            {filteredMemberships.map((m) => (
              <li key={m.id} className="entry-list-item">
                <span className="entry-main">
                  <strong>{m.employee_name || m.username}</strong>
                  <span className="entry-note"> · {ROLE_LABELS[m.role] ?? m.role}</span>
                  <div>
                    {!m.scoped_nodes.length ? (
                      <span className="entry-note">Alle Stationen (keine Einschränkung)</span>
                    ) : (
                      m.scoped_nodes.map((id) => (
                        <span key={id} className="entry-note entry-note--chip">
                          {nodeName(id)}
                        </span>
                      ))
                    )}
                  </div>
                </span>
                <span className="entry-actions">
                  <button type="button" className="btn-ghost" onClick={() => startEditing(m)}>
                    Bearbeiten
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {editingMembership && (
        <div className="modal-overlay" onClick={closeEditing}>
          <div className="panel-form modal-dialog node-scope-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Stationen für {editingMembership.employee_name || editingMembership.username}</h2>
              <button type="button" className="modal-close" onClick={closeEditing} aria-label="Schliessen">
                ×
              </button>
            </div>
            <div className="modal-body">
              <p className="panel-hint">
                Nichts ausgewählt = keine Einschränkung (alle Stationen sichtbar). Eine ausgewählte Station
                markiert automatisch alle Unterstationen mit -- die lassen sich dann nicht mehr einzeln
                abwählen, solange die Hauptstation ausgewählt ist.
              </p>
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
                          onChange={() => toggleNode(n)}
                        />
                        <span className="node-tree-select-name">{n.name}</span>
                        {impliedBy && (
                          <span className="node-tree-select-hint">inkl. über {impliedBy.name}</span>
                        )}
                      </label>
                    </li>
                  );
                })}
              </ul>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-ghost" onClick={closeEditing}>
                Abbrechen
              </button>
              <button type="button" onClick={handleSave} disabled={saving}>
                {saving ? "Speichert …" : "Speichern"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

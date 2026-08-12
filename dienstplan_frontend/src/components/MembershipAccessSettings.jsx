import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import NodeScopeEditor, { toggleNodeSelection } from "./NodeScopeEditor.jsx";
import { ROLE_LABELS, ROLE_OPTIONS } from "../roles.js";

function emptyCreateForm() {
  return { username: "", first_name: "", last_name: "", role: "admin" };
}

// Nutzer-Feedback (2026-08): "Es gibt nun Tab Mitarbeitende, Tab Mitglieder
// und Zugriff [...] Das muss doch intuitiver gelöst werden? [...] mach ein
// konkretes Konzept" -- der frühere "Mitglieder"-Tab ist aufgelöst: Login-
// Zugang für eine Person MIT Mitarbeiterprofil wird jetzt direkt in
// EmployeeSettings.jsx verwaltet (ein Ort, ein Formular pro Person). Dieser
// Tab bleibt nur noch für den seltenen Sonderfall übrig -- ein Konto OHNE
// Mitarbeiterprofil (z. B. externe IT-Administration, die nie auf dem
// Dienstplan erscheint). Kriterium dafür: `employee_name` ist leer (siehe
// core.serializers.MembershipSerializer.get_employee_name) -- eine Person
// mit Mitarbeiterprofil taucht hier also gar nicht mehr auf, selbst wenn sie
// Planer/HR/Admin ist.
export default function MembershipAccessSettings({ nodes, onError }) {
  const [memberships, setMemberships] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState(new Set());
  const [saving, setSaving] = useState(false);

  const [createFormOpen, setCreateFormOpen] = useState(false);
  const [createForm, setCreateForm] = useState(emptyCreateForm());
  const [createFieldErrors, setCreateFieldErrors] = useState({});
  const [creating, setCreating] = useState(false);
  const [newCredentials, setNewCredentials] = useState(null); // {username, password} -- einmalige Anzeige
  const [roleChangingId, setRoleChangingId] = useState(null);
  const [resettingId, setResettingId] = useState(null);

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

  const orphanMemberships = useMemo(
    () =>
      memberships
        .filter((m) => !m.employee_name)
        .sort((a, b) => a.username.localeCompare(b.username)),
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

  // Nutzer-Feedback (2026-08): "Ja mach passwort reset" -- Admin generiert
  // ein neues Temp-Passwort, gezeigt im selben "einmalige Anzeige"-Modal wie
  // bei der Erstanlage (newCredentials wird hier bewusst wiederverwendet).
  async function handleResetPassword(membership) {
    if (
      !window.confirm(
        `Neues Passwort für "${membership.username}" generieren? Das bisherige Passwort wird dabei ungültig.`
      )
    )
      return;
    setResettingId(membership.id);
    try {
      const result = await api.resetMembershipPassword(membership.id);
      setNewCredentials({ username: result.username, password: result.temporary_password });
    } catch (e) {
      onError(e.message);
    } finally {
      setResettingId(null);
    }
  }

  async function handleDelete(membership) {
    if (
      !window.confirm(
        `Konto "${membership.username}" wirklich löschen? Diese Aktion kann nicht rückgängig gemacht werden.`
      )
    )
      return;
    try {
      await api.deleteMembership(membership.id);
      setMemberships((prev) => prev.filter((m) => m.id !== membership.id));
    } catch (e) {
      onError(e.message);
    }
  }

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
        <h2>Konten ohne Mitarbeiterprofil</h2>
        <p className="panel-hint">
          Seltener Sonderfall: ein Login-Konto, das zu KEINER Person unter "Mitarbeitende" gehört (z. B.
          externe IT-Administration). Für Mitarbeitende mit eigenem Profil wird der Login-Zugang direkt in
          deren Bearbeiten-Formular unter "Mitarbeitende" eingerichtet/verwaltet.
        </p>
        <div className="panel-list-actions">
          <button type="button" className="btn-primary" onClick={() => setCreateFormOpen(true)}>
            + Konto hinzufügen
          </button>
        </div>
        {!orphanMemberships.length ? (
          <p className="empty-state">Keine Konten ohne Mitarbeiterprofil vorhanden.</p>
        ) : (
          <ul className="entry-list">
            {orphanMemberships.map((m) => (
              <li key={m.id} className="entry-list-item">
                <span className="entry-main">
                  <strong>{m.username}</strong>
                  <div>
                    {(m.role === "planner" || m.role === "hr") &&
                      (!m.scoped_nodes.length ? (
                        <span className="entry-note">Alle Stationen (keine Einschränkung)</span>
                      ) : (
                        m.scoped_nodes.map((id) => (
                          <span key={id} className="entry-note entry-note--chip">
                            {nodeName(id)}
                          </span>
                        ))
                      ))}
                  </div>
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
                  {(m.role === "planner" || m.role === "hr") && (
                    <button type="button" className="btn-ghost" onClick={() => startEditing(m)}>
                      Stationen
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={resettingId === m.id}
                    onClick={() => handleResetPassword(m)}
                  >
                    {resettingId === m.id ? "Wird zurückgesetzt …" : "Passwort zurücksetzen"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost btn-danger-ghost"
                    onClick={() => handleDelete(m)}
                  >
                    Löschen
                  </button>
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
              <h2>Konto ohne Mitarbeiterprofil hinzufügen</h2>
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
              <button type="submit" className="btn-primary" disabled={creating}>
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
              <h2>Zugangsdaten</h2>
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
              <button type="button" className="btn-primary" onClick={() => setNewCredentials(null)}>
                Verstanden, schliessen
              </button>
            </div>
          </div>
        </div>
      )}

      {editingMembership && (
        <div className="modal-overlay" onClick={closeEditing}>
          <div className="panel-form modal-dialog node-scope-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Stationen für {editingMembership.username}</h2>
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
              <NodeScopeEditor
                nodes={nodes}
                selectedNodeIds={selectedNodeIds}
                onToggle={(node) => setSelectedNodeIds((prev) => toggleNodeSelection(prev, node, nodes))}
              />
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-ghost" onClick={closeEditing}>
                Abbrechen
              </button>
              <button type="button" className="btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? "Speichert …" : "Speichern"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

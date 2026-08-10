import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ROLE_LABELS } from "../roles.js";

// Nutzer-Feedback (2026-08): "Natürlich gibt es in einer Klinik Planer mit
// unterschiedlichen Zuständigkeiten! Nur Admin darf immer alles sehen." --
// Admin-only Verwaltung von Membership.scoped_nodes. Absichtlich KEINE
// Rollenvergabe/Einladung hier (bleibt Django-Admin-only, siehe
// EmployeeSettings.jsx-Kommentar) -- nur die Stations-Einschränkung einer
// bereits bestehenden Planer-/HR-Mitgliedschaft.
export default function MembershipAccessSettings({ nodes, onError }) {
  const [memberships, setMemberships] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState(new Set());
  const [saving, setSaving] = useState(false);

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

  const filteredMemberships = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return memberships;
    return memberships.filter((m) =>
      [m.employee_name, m.username, m.email].filter(Boolean).some((v) => v.toLowerCase().includes(needle))
    );
  }, [memberships, filter]);

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

  function toggleNode(nodeId) {
    setSelectedNodeIds((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
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
        <h2>Planer-/HR-Zugriff</h2>
        <p className="panel-hint">
          Ohne Zuweisung sieht ein Planer/HR weiterhin alle Stationen des Tenants (bisheriges Verhalten) --
          erst eine explizite Auswahl hier schränkt die Sicht ein (inkl. aller Unterstationen). Admin ist
          davon nie betroffen und sieht immer alles.
        </p>
        {memberships.length > 8 && (
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Name/E-Mail filtern …"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
        {!filteredMemberships.length ? (
          <p className="empty-state">Keine Mitgliedschaften gefunden.</p>
        ) : (
          <ul className="entry-list">
            {filteredMemberships.map((m) => (
              <li key={m.id} className="entry-list-item">
                <span className="entry-main">
                  <strong>{m.employee_name || m.username}</strong>
                  <span className="entry-note"> · {ROLE_LABELS[m.role] ?? m.role}</span>
                  <div>
                    {m.role === "admin" ? (
                      <span className="entry-note">Immer alle Stationen</span>
                    ) : !m.scoped_nodes.length ? (
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
                  {m.role !== "admin" && (
                    <button type="button" className="btn-ghost" onClick={() => startEditing(m)}>
                      Bearbeiten
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {editingMembership && (
        <div className="modal-overlay" onClick={closeEditing}>
          <div className="panel-form modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Stationen für {editingMembership.employee_name || editingMembership.username}</h2>
              <button type="button" className="modal-close" onClick={closeEditing} aria-label="Schliessen">
                ×
              </button>
            </div>
            <div className="modal-body">
              <p className="panel-hint">
                Nichts ausgewählt = keine Einschränkung (alle Stationen sichtbar). Eine ausgewählte Station
                schliesst automatisch alle ihre Unterstationen mit ein.
              </p>
              <ul className="node-checkbox-list">
                {nodes.map((n) => (
                  <li key={n.id} style={{ paddingLeft: `${Math.max(n.depth - 1, 0) * 18}px` }}>
                    <label>
                      <input
                        type="checkbox"
                        checked={selectedNodeIds.has(n.id)}
                        onChange={() => toggleNode(n.id)}
                      />
                      {n.name}
                    </label>
                  </li>
                ))}
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

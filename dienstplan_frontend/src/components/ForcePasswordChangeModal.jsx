import { useState } from "react";
import { api } from "../api.js";

// Nutzer-Feedback (2026-08): "Applikationsmanager legt den Benutzer direkt an
// [...] beim ersten Login muss [das Temp-Passwort] geändert werden" -- Gate
// vor dem Rest der Oberfläche, solange User.must_change_password gesetzt ist
// (core.views.ChangePasswordView, gesetzt bei Direktanlage über
// MembershipAccessSettings.jsx). Bewusst ohne Abbrechen-Möglichkeit --
// anders als die übrigen modal-overlay-Dialoge schliesst ein Klick auf den
// Hintergrund hier NICHT, sonst liesse sich der Zwang umgehen.
export default function ForcePasswordChangeModal({ onDone }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordRepeat, setNewPasswordRepeat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (newPassword !== newPasswordRepeat) {
      setError("Die beiden neuen Passwörter stimmen nicht überein.");
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <form className="panel-form modal-dialog" onSubmit={handleSubmit}>
        <div className="modal-header">
          <h2>Passwort ändern</h2>
        </div>
        <div className="modal-body">
          <p className="panel-hint">
            Dieses Konto wurde mit einem Temp-Passwort angelegt -- bitte jetzt ein eigenes Passwort
            vergeben, bevor es weitergeht.
          </p>
          <label>
            Aktuelles (Temp-)Passwort
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoFocus
              required
            />
          </label>
          <label>
            Neues Passwort
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </label>
          <label>
            Neues Passwort wiederholen
            <input
              type="password"
              value={newPasswordRepeat}
              onChange={(e) => setNewPasswordRepeat(e.target.value)}
              required
            />
          </label>
          {error && (
            <p className="field-error" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="modal-footer">
          <button type="submit" disabled={busy}>
            {busy ? "Wird gespeichert …" : "Passwort ändern"}
          </button>
        </div>
      </form>
    </div>
  );
}

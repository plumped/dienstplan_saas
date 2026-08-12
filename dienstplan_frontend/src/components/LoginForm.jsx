import { useState } from "react";
import { api } from "../api.js";

export default function LoginForm({ onSuccess, onBack }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.login(username, password);
      onSuccess();
    } catch {
      setError("Anmeldung fehlgeschlagen. Benutzername oder Passwort prüfen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="brand-mark-lg" aria-hidden="true">
          ◒
        </div>
        <h1>Dienstplan</h1>
        <p className="login-sub">Anmelden, um das Planblatt zu öffnen.</p>

        <label>
          Benutzername
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            required
          />
        </label>
        <label>
          Passwort
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" disabled={busy}>
          {busy ? "Anmelden …" : "Anmelden"}
        </button>

        {onBack && (
          <p className="landing-login-link">
            <button type="button" className="link-button" onClick={onBack}>
              Zurück
            </button>
          </p>
        )}
      </form>
    </div>
  );
}

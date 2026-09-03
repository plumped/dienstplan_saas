import { useState } from "react";
import { api } from "../api.js";
import { SWISS_CANTONS } from "../cantons.js";

// Self-Signup (README Block 3): Direkt-Registrierung ohne Magic-Link/
// E-Mail-Versand, siehe core.views.SignupView. Struktur analog LoginForm.jsx
// (per-Feld useState, busy/error-State, preventDefault->setBusy->try/catch/
// finally), aber mit Feld-Fehlern statt einer fixen Nachricht -- Signup hat
// deutlich mehr validierbare Felder als der Login.
export default function SignupForm({ onSuccess, onBack }) {
  const [tenantName, setTenantName] = useState("");
  const [canton, setCanton] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // README Block 5 (Datenschutz & Rechtliches, 2026-08): Pflicht-Checkbox für die Einbeziehung
  // von AGB/Datenschutzerklärung. Bewusst als reiner Text ohne Links aus dem Formular heraus --
  // die Rechts-Seiten sind nur per Screen-Switching erreichbar (kein Router im Projekt, siehe
  // App.jsx), ein Klick würde das bereits ausgefüllte Formular verwerfen. Beide Texte sind schon
  // einen Klick vorher über den Landing-Page-Footer erreichbar (LandingPage.jsx).
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setFieldErrors({});
    try {
      await api.signup({
        tenant_name: tenantName,
        canton,
        first_name: firstName,
        last_name: lastName,
        email,
        username,
        password,
      });
      onSuccess();
    } catch (err) {
      if (err.fields && Object.keys(err.fields).length) setFieldErrors(err.fields);
      else setError(err.message || "Registrierung fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card signup-card" onSubmit={handleSubmit}>
        <div className="brand-mark-lg" aria-hidden="true">
          ◒
        </div>
        <h1>Konto erstellen</h1>
        <p className="login-sub">Kostenlos testen -- mit Beispieldaten zum Anfassen.</p>

        <label>
          Firmenname
          <input value={tenantName} onChange={(e) => setTenantName(e.target.value)} autoFocus required />
        </label>
        {fieldErrors.tenant_name?.[0] && (
          <p className="field-error" role="alert">
            {fieldErrors.tenant_name[0]}
          </p>
        )}

        <label>
          Kanton
          <select value={canton} onChange={(e) => setCanton(e.target.value)}>
            <option value="">-- kein Kanton --</option>
            {SWISS_CANTONS.map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </label>

        <div className="signup-name-row">
          <label>
            Vorname
            <input value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </label>
          <label>
            Nachname
            <input value={lastName} onChange={(e) => setLastName(e.target.value)} />
          </label>
        </div>

        <label>
          E-Mail
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <p className="signup-field-hint">Nur Kontaktadresse, nicht fürs Login.</p>
        {fieldErrors.email?.[0] && (
          <p className="field-error" role="alert">
            {fieldErrors.email[0]}
          </p>
        )}

        <label>
          Benutzername
          <input value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        {fieldErrors.username?.[0] && (
          <p className="field-error" role="alert">
            {fieldErrors.username[0]}
          </p>
        )}

        <label>
          Passwort
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {fieldErrors.password?.map((message) => (
          <p className="field-error" role="alert" key={message}>
            {message}
          </p>
        ))}

        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={acceptedTerms}
            onChange={(e) => setAcceptedTerms(e.target.checked)}
            required
          />
          Ich akzeptiere die AGB und habe die Datenschutzerklärung zur Kenntnis genommen.
        </label>

        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" disabled={busy}>
          {busy ? "Konto wird erstellt …" : "Konto erstellen"}
        </button>

        <p className="landing-login-link">
          <button type="button" className="link-button" onClick={onBack}>
            Zurück
          </button>
        </p>
      </form>
    </div>
  );
}

// README Block 5 (Datenschutz & Rechtliches, 2026-08): gemeinsamer Rahmen für die drei
// öffentlichen Rechts-Seiten (Datenschutzerklärung/AGB/Impressum) -- gleiches
// Screen-Switching-Muster wie LoginForm/SignupForm (kein Router im Projekt, siehe App.jsx
// "screen"-State), damit diese Seiten sich konsistent in die bestehende Navigation einfügen.
export default function LegalPage({ title, onBack, children }) {
  return (
    <div className="legal-screen">
      <div className="legal-card">
        <div className="brand-mark-lg" aria-hidden="true">
          ◒
        </div>
        <h1>{title}</h1>
        <div className="legal-content">{children}</div>
        {onBack && (
          <p className="landing-login-link">
            <button type="button" className="link-button" onClick={onBack}>
              Zurück
            </button>
          </p>
        )}
      </div>
    </div>
  );
}

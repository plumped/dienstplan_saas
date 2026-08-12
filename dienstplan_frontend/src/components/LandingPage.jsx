// README Block 3 (Onboarding & Mandantenfähigkeit): erster Bildschirm für
// ausgeloggte Besucher, bevorzugt statt direkt der Login-Maske -- wer noch
// kein Konto hat, sieht zuerst, worum es geht, statt vor einem leeren
// Login-Formular zu stehen. Rein statisch, keine API-Calls. Bildsprache
// von .login-screen/.login-card übernommen (gleicher Farbverlauf,
// --font-display, .btn-primary).
const FEATURES = [
  {
    title: "ArG-konforme Prüfung",
    text: "Ruhezeiten, Höchstarbeitszeit, Pausen und Wochenruhetage werden automatisch geprüft -- Konflikte fallen sofort auf, nicht erst am Monatsende.",
  },
  {
    title: "Ferien- & Überzeitsaldo",
    text: "Laufender Saldo pro Mitarbeitendem, inklusive Jahressoll und Feiertagskalender für jeden Kanton -- keine Excel-Formel mehr zum Pflegen.",
  },
  {
    title: "Für Heime, Spitäler & Pflege",
    text: "Nacht- und Sonntagszuschläge, Jugendschutz, Krankentaggeld-Skalen und Lohn-Export sind auf Schweizer Betriebe mit Schichtbetrieb zugeschnitten.",
  },
  {
    title: "Direkter Zugang für alle",
    text: "Kein E-Mail-Einladungs-Umweg: Admin, Planer und Mitarbeitende erhalten ihren Zugang direkt im System.",
  },
];

export default function LandingPage({ onStart, onLogin }) {
  return (
    <div className="landing-screen">
      <div className="landing-hero">
        <div className="brand-mark-lg" aria-hidden="true">
          ◒
        </div>
        <h1>ArG-konforme Dienstplanung ohne Excel-Chaos</h1>
        <p className="landing-sub">
          Schichtplanung für Heime, Spitäler und Pflegeeinrichtungen in der Schweiz -- inklusive
          automatischer Prüfung gegen das Arbeitsgesetz, Saldoführung und Lohn-Export.
        </p>

        <div className="landing-cta-row">
          <button type="button" className="btn-primary" onClick={onStart}>
            Kostenlos testen
          </button>
          <a className="btn-ghost" href="mailto:demo@dienstplan.example">
            Demo buchen
          </a>
        </div>

        <p className="landing-login-link">
          Bereits ein Konto?{" "}
          <button type="button" className="link-button" onClick={onLogin}>
            Anmelden
          </button>
        </p>
      </div>

      <div className="landing-feature-grid">
        {FEATURES.map((feature) => (
          <div className="landing-feature-tile" key={feature.title}>
            <h3>{feature.title}</h3>
            <p>{feature.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

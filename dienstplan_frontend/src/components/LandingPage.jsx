// README Block 3 (Onboarding & Mandantenfähigkeit): erster Bildschirm für
// ausgeloggte Besucher, bevorzugt statt direkt der Login-Maske -- wer noch
// kein Konto hat, sieht zuerst, worum es geht, statt vor einem leeren
// Login-Formular zu stehen. Rein statisch, keine API-Calls.
//
// Nutzer-Feedback (2026-08): "Die Landingpage ist ultra altbacken! Mach die
// TOP modern! Richtiger Gallery Print!" -- kompletter visueller Neuaufbau
// (Sticky-Nav, grosse Editorial-Typografie, Bento-Feature-Grid, Hero-Mockup,
// Stat-Leiste, Closing-CTA-Band). Bildsprache bleibt an die bestehende Marke
// gebunden (--primary/--font-display, dasselbe Teal wie überall sonst in der
// App) -- nur die Anwendung davon ist jetzt zeitgemäss statt der alten,
// symmetrischen Kachel-Reihe. Die Stat-Leiste zeigt bewusst reale
// Tenant-Default-Werte (core.models.Tenant.minimum_rest_hours/
// maximum_weekly_hours/default_vacation_days_per_year) statt erfundener
// Marketing-Zahlen oder fiktiver Kundenlogos/Testimonials.
const FEATURES = [
  {
    title: "ArG-konforme Prüfung",
    text: "Ruhezeiten, Höchstarbeitszeit, Pausen und Wochenruhetage werden automatisch geprüft -- Konflikte fallen sofort auf, nicht erst am Monatsende.",
    icon: "shield",
    accent: true,
  },
  {
    title: "Ferien- & Überzeitsaldo",
    text: "Laufender Saldo pro Mitarbeitendem, inklusive Jahressoll und Feiertagskalender für jeden Kanton.",
    icon: "trend",
  },
  {
    title: "Für Heime, Spitäler & Pflege",
    text: "Nacht- und Sonntagszuschläge, Jugendschutz, Krankentaggeld-Skalen und Lohn-Export für Schweizer Schichtbetriebe.",
    icon: "pulse",
  },
  {
    title: "Direkter Zugang für alle",
    text: "Kein E-Mail-Einladungs-Umweg: Admin, Planer und Mitarbeitende erhalten ihren Zugang direkt im System.",
    icon: "key",
  },
];

const STATS = [
  { value: "11h", label: "Mindestruhezeit automatisch geprüft" },
  { value: "45h", label: "Höchstarbeitszeit pro Woche im Blick" },
  { value: "20", label: "Ferientage/Jahr als Startwert, pro Mitarbeiter anpassbar" },
];

function FeatureIcon({ name }) {
  const common = {
    width: 22,
    height: 22,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  };
  if (name === "shield") {
    return (
      <svg {...common}>
        <path d="M12 3l7 3v5.2c0 4.6-3 8.3-7 9.8-4-1.5-7-5.2-7-9.8V6l7-3z" />
        <path d="M9 12.2l2 2 4-4.4" />
      </svg>
    );
  }
  if (name === "trend") {
    return (
      <svg {...common}>
        <path d="M4 16l5-5 4 4 7-8" />
        <path d="M15 6.5h5V11.5" />
      </svg>
    );
  }
  if (name === "pulse") {
    return (
      <svg {...common}>
        <path d="M3 12h4l2-6 4 12 2-6h6" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="8" cy="15.5" r="3.5" />
      <path d="M10.6 13 18 5.6" />
      <path d="M15.5 8.1l2.4 2.4" />
      <path d="M18 5.6l2.4 2.4" />
    </svg>
  );
}

export default function LandingPage({ onStart, onLogin }) {
  return (
    <div className="landing-screen">
      <div className="landing-aurora" aria-hidden="true" />

      <nav className="landing-nav">
        <div className="landing-nav-brand">
          <span className="brand-mark-lg landing-nav-mark" aria-hidden="true">
            ◒
          </span>
          Dienstplan
        </div>
        <button type="button" className="landing-nav-login" onClick={onLogin}>
          Anmelden
        </button>
      </nav>

      <header className="landing-hero">
        <p className="landing-kicker">Für Schweizer Heime, Spitäler &amp; Pflegeeinrichtungen</p>
        <h1>
          Dienstplanung,
          <br />
          <span className="landing-headline-accent">die das Arbeitsgesetz gleich mitdenkt.</span>
        </h1>
        <p className="landing-sub">
          Schichtplanung ohne Excel-Chaos -- inklusive automatischer Prüfung gegen das
          Arbeitsgesetz, laufender Saldoführung und Lohn-Export.
        </p>

        <div className="landing-cta-row">
          <button type="button" className="landing-btn-primary" onClick={onStart}>
            Kostenlos testen
            <span aria-hidden="true">→</span>
          </button>
          <a className="landing-btn-ghost" href="mailto:demo@dienstplan.example">
            Demo buchen
          </a>
        </div>

        <p className="landing-login-link">
          Bereits ein Konto?{" "}
          <button type="button" className="link-button" onClick={onLogin}>
            Anmelden
          </button>
        </p>

        <div className="landing-hero-visual" aria-hidden="true">
          <div className="landing-mockup">
            <div className="landing-mockup-bar">
              <span />
              <span />
              <span />
            </div>
            <div className="landing-mockup-grid">
              {Array.from({ length: 28 }).map((_, i) => {
                const variant = [0, 3, 5, 9, 12, 14, 18, 21, 24].includes(i)
                  ? "warn"
                  : [2, 7, 11, 16, 20, 26].includes(i)
                    ? "primary"
                    : "empty";
                return <div key={i} className={`landing-mockup-cell landing-mockup-cell--${variant}`} />;
              })}
            </div>
          </div>
          <div className="landing-float-badge landing-float-badge--check">
            <span>✓</span> ArG geprüft
          </div>
          <div className="landing-float-badge landing-float-badge--balance">Saldo +4.5h</div>
        </div>
      </header>

      <section className="landing-stats">
        {STATS.map((stat) => (
          <div className="landing-stat" key={stat.label}>
            <span className="landing-stat-value">{stat.value}</span>
            <span className="landing-stat-label">{stat.label}</span>
          </div>
        ))}
      </section>

      <section className="landing-feature-grid">
        {FEATURES.map((feature) => (
          <div
            className={`landing-feature-tile${feature.span ? " landing-feature-tile--span" : ""}`}
            key={feature.title}
          >
            <div className="landing-feature-icon">
              <FeatureIcon name={feature.icon} />
            </div>
            <h3>{feature.title}</h3>
            <p>{feature.text}</p>
          </div>
        ))}
      </section>

      <section className="landing-cta-band">
        <div className="landing-cta-band-inner">
          <h2>Bereit für den ersten Dienstplan ohne Excel?</h2>
          <p>In wenigen Minuten eingerichtet, mit Beispieldaten zum Anfassen.</p>
          <button type="button" className="landing-btn-primary landing-btn-primary--inverse" onClick={onStart}>
            Jetzt kostenlos starten
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </section>

      <footer className="landing-footer">
        <span className="brand-mark-lg landing-nav-mark" aria-hidden="true">
          ◒
        </span>
        Dienstplan -- gebaut für Schweizer Schichtbetriebe.
      </footer>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";

import screenshotPlanblatt from "../assets/landing/screenshot-planblatt.png";
import screenshotSettings from "../assets/landing/screenshot-settings.png";

// README Block 3 (Onboarding & Mandantenfähigkeit): erster Bildschirm für
// ausgeloggte Besucher, bevorzugt statt direkt der Login-Maske -- wer noch
// kein Konto hat, sieht zuerst, worum es geht, statt vor einem leeren
// Login-Formular zu stehen. Rein statisch, keine API-Calls.
//
// Nutzer-Feedback (2026-08): erst "TOP modern, richtiger Gallery Print"
// (buntes Aurora-/Gradient-Design), dann konkretisiert auf ein
// zurückhaltendes, whitespace-lastiges "Klinik-Flair" nach eigenem
// Referenz-Layout -- zweispaltiger Hero (Text links, abstrakte
// Puls-Grafik rechts), Serif-Headline mit genau EINEM farbigen Wort statt
// Gradient-Text, dünne Trennlinien statt Schatten/Glow, ein einziger
// Akzentton (--primary). Serif nur lokal für Headline/Stat-Werte über
// System-Font-Stack (kein Font-Nachladen nötig) -- der Rest der App bleibt
// auf --font-display/--font-body. Stat-Leiste zeigt weiterhin reale
// Tenant-Default-Werte (core.models.Tenant.minimum_rest_hours/
// maximum_weekly_hours/default_vacation_days_per_year) statt erfundener
// Marketing-Zahlen oder fiktiver Kundenlogos/Testimonials.
//
// Nutzer-Feedback (2026-08, Folgerunde): "das Grid ist viel zu stark
// sichtbar, zudem braucht es viel mehr Content was das Ding kann,
// printscreens etc." -- Punktraster-Deckkraft reduziert (styles.css), und
// zwei ECHTE Screenshots ergänzt (Playwright gegen den laufenden Dev-
// Server, eingeloggt als Demo-Tenant "Testheim", Saldo-Badge/-Spalte per
// CSS ausgeblendet, weil die Demo-Salden unrealistisch sind und nichts
// über das Feature aussagen -- siehe assets/landing/). Dazu eine
// Checkliste mit weiteren, tatsächlich vorhandenen Funktionen (siehe
// README) statt erfundener Zusatzclaims.
//
// Nutzer-Feedback (2026-08, 3. Runde): "jetzt noch fancy shit wie
// transformation, einfliegen etc. aber mit Gefühl, nicht zu verspielt" --
// Scroll-Reveal je Sektion (siehe Reveal-Komponente unten, einmalig per
// IntersectionObserver, keine Library), Feature-Kacheln und Checklisten-
// Zeilen kaskadieren leicht versetzt statt als ein Block. Dazu ein einmaliges
// "Zeichnen" der Puls-Linie im Hero (stroke-dashoffset) und dezente
// Hover-Effekte auf Screenshot-Rahmen/Feature-Icons -- alles in styles.css
// unter @media (prefers-reduced-motion: no-preference) gekapselt, damit
// reduzierte Bewegung den Inhalt immer sofort und vollständig zeigt.
//
// Nutzer-Feedback (2026-08, 4. Runde): "Komm weg von dem Excel-Müll. Jedes
// Unternehmen hat mittlerweile irgendein Tool. Denk als Marketingexperte!"
// -- alle "ohne Excel"-Formulierungen entfernt, Positionierung zunächst auf
// "generische Tools kennen das Schweizer Arbeitsrecht nicht" umgestellt.
//
// Nutzer-Korrektur (2026-08, 5. Runde): "Du hast mir ganz am Anfang gesagt
// ich sollte nicht 'ich kann das und andere nicht' schreiben. Arbeitsgesetz
// beachtet jedes 2. Füdlitool. Du sollst es schmackhaft machen! Intuitiv,
// effizient, schnell usw." -- die "generische Tools können das nicht"-Linie
// war exakt der Fehler, vor dem am Anfang gewarnt wurde: eine Overclaim-
// artige Abgrenzung nach unten statt einer positiven Verkaufsaussage. Text
// an allen betroffenen Stellen (Hero-Sub, Showcase-Copy, CTA-Band) auf
// reine Vorteilssprache umgestellt -- schnell, intuitiv, effizient, ohne
// Vergleich zu "anderen Tools".
const FEATURES = [
  {
    title: "ArG-konforme Prüfung",
    text: "Ruhezeiten, Höchstarbeitszeit, Pausen und Wochenruhetage werden automatisch geprüft -- Konflikte fallen sofort auf, nicht erst am Monatsende.",
    icon: "shield",
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

const CHECKLIST = [
  "Diensttausch mit Planer-Freigabe statt Zuruf auf dem Gang",
  "Jahresplan pro Mitarbeitendem inklusive Split-Shifts",
  "Planblatt als PDF oder CSV exportieren",
  "Lohnarten-Zuordnung direkt für den Lohn-Export",
  "Fairness-Punktesystem für unpopuläre Schichten",
  "Feiertagskalender je Kanton, mit lokalen Ausnahmen",
  "CSV-Mitarbeiterimport beim Einrichten",
  "Kostenstellen pro Station, vererbt an Mitarbeitende",
  "Rollen mit passgenauen Rechten: Admin, Planer, HR, Mitarbeitende",
];

function CheckIcon() {
  return (
    <svg
      className="landing-checklist-check"
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 12.5l5 5L20 6" />
    </svg>
  );
}

function FeatureIcon({ name }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
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

// Scroll-Reveal (Nutzer-Feedback: "fancy shit wie transformation, einfliegen
// -- aber mit Gefühl, nicht zu verspielt") -- ein einziges IntersectionObserver
// pro Sektion, feuert einmalig, keine Library nötig. Der "unsichtbare"
// Ausgangszustand (opacity/transform) ist bewusst in styles.css unter
// @media (prefers-reduced-motion: no-preference) gekapselt: wer reduzierte
// Bewegung eingestellt hat, sieht den Inhalt immer sofort vollständig --
// unabhängig davon, ob/wann der Observer feuert.
function Reveal({ as: Tag = "div", className = "", children, ...rest }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.15 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const classes = ["landing-reveal", visible ? "landing-reveal--visible" : "", className]
    .filter(Boolean)
    .join(" ");

  return (
    <Tag ref={ref} className={classes} {...rest}>
      {children}
    </Tag>
  );
}

function PulseGraphic() {
  return (
    <svg
      className="landing-pulse-svg"
      viewBox="0 0 420 260"
      fill="none"
      aria-hidden="true"
    >
      <polyline
        className="landing-pulse-line landing-pulse-line--muted"
        points="10,150 90,150 120,178 150,120 180,168 210,150 340,150 410,150"
      />
      <polyline
        className="landing-pulse-line landing-pulse-line--primary"
        points="10,160 70,160 100,230 130,40 160,210 190,110 220,160 260,150 300,175 330,150 410,150"
      />
      <circle className="landing-pulse-dot" cx="130" cy="40" r="4" />
    </svg>
  );
}

export default function LandingPage({ onStart, onLogin }) {
  return (
    <div className="landing-screen">
      <div className="landing-grid-bg" aria-hidden="true" />

      <nav className="landing-nav">
        <div className="landing-nav-brand">
          <span className="brand-mark-lg landing-nav-mark" aria-hidden="true">
            ◒
          </span>
          neravo
        </div>
        <button type="button" className="landing-nav-login" onClick={onLogin}>
          Anmelden
        </button>
      </nav>

      <header className="landing-hero">
        <div className="landing-hero-grid">
          <div className="landing-hero-copy">
            <p className="landing-kicker">
              <span className="landing-kicker-dot" aria-hidden="true" />
              Für Schweizer Heime, Spitäler &amp; Pflegeeinrichtungen
            </p>
            <h1>
              Dienstplanung, die das <span className="landing-headline-accent">Arbeitsgesetz</span> gleich
              mitdenkt.
            </h1>
            <p className="landing-sub">
              Schichten planen, Konflikte sofort sehen, fertig. Ruhezeiten, Höchstarbeitszeit und
              Zuschläge werden automatisch mitgeprüft -- dazu laufende Saldoführung und
              Lohn-Export auf Knopfdruck.
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
          </div>

          <div className="landing-hero-graphic">
            <PulseGraphic />
          </div>
        </div>
      </header>

      <div className="landing-divider" />

      <Reveal as="section" className="landing-stats">
        {STATS.map((stat) => (
          <div className="landing-stat" key={stat.label}>
            <span className="landing-stat-value">{stat.value}</span>
            <span className="landing-stat-label">{stat.label}</span>
          </div>
        ))}
      </Reveal>

      <Reveal as="section" className="landing-showcase">
        <div className="landing-showcase-copy">
          <p className="landing-showcase-eyebrow">Planblatt</p>
          <h2>Alles auf einen Blick planen</h2>
          <p>
            Farbcodierte Dienste, Mehrfachauswahl zum schnellen Stempeln und automatische
            Konflikthinweise direkt in der Zelle -- ein Monatsplan steht in Minuten, Konflikte
            fallen sofort auf statt erst am Monatsende.
          </p>
          <ul className="landing-showcase-points">
            <li>Drag &amp; Drop sowie Mehrfachauswahl zum schnellen Stempeln</li>
            <li>Automatische Prüfung gegen ArG-Regeln direkt in der Zelle</li>
            <li>Laufender Saldo pro Mitarbeitendem, live nachgeführt</li>
          </ul>
        </div>
        <div className="landing-showcase-frame">
          <div className="landing-showcase-frame-bar">
            <span />
            <span />
            <span />
          </div>
          <img src={screenshotPlanblatt} alt="Planblatt mit Schichtplanung, farbcodierten Diensten und automatischen Konflikthinweisen" />
        </div>
      </Reveal>

      <Reveal as="section" className="landing-feature-grid">
        {FEATURES.map((feature, index) => (
          <div className="landing-feature-tile" key={feature.title} style={{ transitionDelay: `${index * 0.08}s` }}>
            <div className="landing-feature-icon">
              <FeatureIcon name={feature.icon} />
            </div>
            <h3>{feature.title}</h3>
            <p>{feature.text}</p>
          </div>
        ))}
      </Reveal>

      <Reveal as="section" className="landing-showcase landing-showcase--reverse">
        <div className="landing-showcase-copy">
          <p className="landing-showcase-eyebrow">Einstellungen</p>
          <h2>Vollständig konfigurierbar, in wenigen Klicks</h2>
          <p>
            Schichttypen, Absenzarten, Stationen, Skills, Lohnarten und die Regel-Engine lassen
            sich direkt im System pflegen -- von der Person, die den Betrieb tatsächlich kennt,
            in Minuten erledigt statt in einem Ticket-System versandet.
          </p>
          <ul className="landing-showcase-points">
            <li>Schichttypen, Absenzarten und Stationen frei definierbar</li>
            <li>Lohnarten-Zuordnung direkt für den Lohn-Export</li>
            <li>Regel-Engine mit Ruhezeit-, Höchstarbeitszeit- und Zuschlagseinstellungen</li>
          </ul>
        </div>
        <div className="landing-showcase-frame">
          <div className="landing-showcase-frame-bar">
            <span />
            <span />
            <span />
          </div>
          <img src={screenshotSettings} alt="Einstellungsübersicht mit Modulen für Schichttypen, Absenzarten, Mitarbeitende, Stationen, Skills, Lohnarten und Regel-Engine" />
        </div>
      </Reveal>

      <Reveal as="section" className="landing-checklist">
        <h2>Und ausserdem</h2>
        <ul className="landing-checklist-grid">
          {CHECKLIST.map((item, index) => (
            <li key={item} style={{ transitionDelay: `${index * 0.05}s` }}>
              <CheckIcon />
              {item}
            </li>
          ))}
        </ul>
      </Reveal>

      <Reveal as="section" className="landing-cta-band">
        <div className="landing-cta-band-inner">
          <h2>Bereit für einen Dienstplan, der einfach mitdenkt?</h2>
          <p>In wenigen Minuten eingerichtet, mit Beispieldaten zum Anfassen.</p>
          <button type="button" className="landing-btn-primary landing-btn-primary--inverse" onClick={onStart}>
            Jetzt kostenlos starten
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </Reveal>

      <footer className="landing-footer">
        <span className="brand-mark-lg landing-nav-mark" aria-hidden="true">
          ◒
        </span>
        neravo -- gebaut für Schweizer Schichtbetriebe.
      </footer>
    </div>
  );
}

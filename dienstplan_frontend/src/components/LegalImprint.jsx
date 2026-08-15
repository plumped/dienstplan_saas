import LegalPage from "./LegalPage.jsx";

// README Block 5 (2026-08): kanonische Quelle ist docs/legal/impressum.md -- siehe Hinweis in
// LegalPrivacyPolicy.jsx. Ein Impressum mit unrichtigen/fehlenden Angaben kann unlauteren
// Wettbewerb nach Art. 3 UWG darstellen -- deshalb bewusst keine erfundenen Firmendaten.
export default function LegalImprint({ onBack }) {
  return (
    <LegalPage title="Impressum" onBack={onBack}>
      <p className="legal-draft-notice">
        <strong>Entwurf.</strong> Mit <code>[…]</code> markierte Stellen MÜSSEN vor
        Live-Schaltung durch reale, geprüfte Angaben ersetzt werden.
      </p>

      <h2>Anbieterin</h2>
      <p>
        [Firmenname / Rechtsform]
        <br />
        [Strasse Nr.]
        <br />
        [PLZ Ort], [Land]
      </p>

      <h2>Kontakt</h2>
      <p>
        Telefon: [Telefonnummer]
        <br />
        E-Mail: [E-Mail-Adresse]
      </p>

      <h2>Handelsregister</h2>
      <p>
        Handelsregistereintrag: [Handelsregisteramt, Ort]
        <br />
        UID: [CHE-xxx.xxx.xxx]
      </p>

      <h2>Vertretungsberechtigte Person</h2>
      <p>[Name der verantwortlichen Person]</p>

      <h2>Haftungshinweis</h2>
      <p>
        Trotz sorgfältiger inhaltlicher Kontrolle übernehmen wir keine Haftung für die Inhalte
        externer Links. Für den Inhalt der verlinkten Seiten sind ausschliesslich deren Betreiber
        verantwortlich.
      </p>
    </LegalPage>
  );
}

import LegalPage from "./LegalPage.jsx";

// README Block 5 (2026-08): kanonische Quelle ist docs/legal/agb.md -- siehe Hinweis in
// LegalPrivacyPolicy.jsx.
export default function LegalTerms({ onBack }) {
  return (
    <LegalPage title="Allgemeine Geschäftsbedingungen" onBack={onBack}>
      <p className="legal-draft-notice">
        <strong>Entwurf — keine Rechtsberatung.</strong> Diese AGB müssen vor Live-Schaltung von
        einer Fachperson geprüft werden. Mit <code>[…]</code> markierte Stellen sind Platzhalter.
      </p>

      <h2>1. Geltungsbereich</h2>
      <p>
        Diese AGB regeln die Nutzung von neravo durch [Firmenname] (nachfolgend "wir") gegenüber
        Betrieben, die neravo zur Dienstplanung ihrer Mitarbeitenden abonnieren ("Kundin").
      </p>

      <h2>2. Vertragsgegenstand</h2>
      <p>
        Wir stellen der Kundin neravo als Software-as-a-Service zur Verfügung: Dienstplanung,
        Arbeitszeiterfassung, Absenzverwaltung, Regel-Engine für das Arbeitsgesetz, Lohn-
        Rohdaten-Export und weitere Funktionen gemäss Produktbeschreibung.
      </p>

      <h2>3. Vertragsschluss und Testphase</h2>
      <p>
        Der Vertrag kommt durch Registrierung eines Kontos zustande. Nach der kostenlosen
        Testphase ist ein kostenpflichtiges Abonnement erforderlich (Details siehe
        Abrechnungsseite in der App).
      </p>

      <h2>4. Pflichten der Kundin</h2>
      <ul>
        <li>Verantwortlich für die Richtigkeit der erfassten Daten.</li>
        <li>Datenschutzrechtlich Verantwortliche gegenüber ihren eigenen Mitarbeitenden.</li>
        <li>Vertrauliche Behandlung der Zugangsdaten.</li>
        <li>
          Die Regel-Checks der App unterstützen bei der Einhaltung des Arbeitsgesetzes, ersetzen
          aber keine eigene Sorgfaltspflicht.
        </li>
      </ul>

      <h2>5. Pflichten der Anbieterin</h2>
      <p>
        Wir stellen den Dienst mit angemessener Sorgfalt und hoher Verfügbarkeit zur Verfügung und
        treffen angemessene technische und organisatorische Massnahmen zum Schutz bearbeiteter
        Personendaten.
      </p>

      <h2>6. Preise und Zahlung</h2>
      <p>
        Es gelten die in der App angezeigten Preise. Die Zahlungsabwicklung erfolgt über Stripe;
        wir speichern selbst keine Zahlungsdaten.
      </p>

      <h2>7. Laufzeit und Kündigung</h2>
      <p>
        Automatische Verlängerung um die gewählte Abrechnungsperiode. Kündigung jederzeit über die
        Abrechnungsseite (Stripe-Kundenportal), wirksam zum Ende der laufenden Periode.
      </p>

      <h2>8. Datenschutz</h2>
      <p>
        Siehe Datenschutzerklärung und, für die Bearbeitung von Mitarbeitendendaten, den separat
        abzuschliessenden Auftragsverarbeitungsvertrag (AVV).
      </p>

      <h2>9. Haftung</h2>
      <p>
        Wir haften nach den gesetzlichen Bestimmungen für Vorsatz und grobe Fahrlässigkeit. Für
        leichte Fahrlässigkeit ist unsere Haftung, soweit gesetzlich zulässig, [Beschränkung
        einsetzen]. Keine Haftung für die Richtigkeit der von der Kundin erfassten Daten oder für
        die Einhaltung arbeitsrechtlicher Vorgaben in der Praxis.
      </p>

      <h2>10. Anwendbares Recht und Gerichtsstand</h2>
      <p>
        Schweizerisches Recht unter Ausschluss des UN-Kaufrechts. Gerichtsstand: [einsetzen].
      </p>

      <h2>11. Kontakt</h2>
      <p>[Firmenname], [Adresse], [E-Mail-Adresse]</p>
    </LegalPage>
  );
}

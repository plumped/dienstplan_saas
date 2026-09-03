import LegalPage from "./LegalPage.jsx";

// README Block 5 (Datenschutz & Rechtliches, 2026-08): Kanonische Quelle ist
// docs/legal/datenschutzerklaerung.md (Markdown, für juristische Prüfung) -- dieser Text ist die
// dazu synchron gehaltene Web-Fassung. Bei inhaltlichen Änderungen IMMER beide Stellen pflegen.
export default function LegalPrivacyPolicy({ onBack }) {
  return (
    <LegalPage title="Datenschutzerklärung" onBack={onBack}>
      <p className="legal-draft-notice">
        <strong>Entwurf — keine Rechtsberatung.</strong> Dieser Text orientiert sich an den
        unten zitierten Bestimmungen des revDSG und, wo einschlägig, der DSGVO. Er ersetzt keine
        anwaltliche Prüfung. Mit <code>[…]</code> markierte Stellen sind Platzhalter.
      </p>

      <h2>1. Verantwortlicher</h2>
      <p>
        [Firmenname], [Strasse, PLZ Ort], [Land]. Kontakt für Datenschutzanliegen:
        [E-Mail-Adresse]. Für Personendaten, die Sie als Kundin/Kunde über neravo zur
        Dienstplanung Ihrer eigenen Mitarbeitenden bearbeiten, sind grundsätzlich Sie (der
        Tenant/Betrieb) Verantwortlicher gemäss Art. 328b OR und revDSG; wir sind insoweit
        Auftragsbearbeiter.
      </p>

      <h2>2. Geltungsbereich: revDSG und ergänzend DSGVO</h2>
      <p>
        neravo wird primär nach dem revidierten Bundesgesetz über den Datenschutz (revDSG, in
        Kraft seit 1. September 2023) betrieben. Sollte ein Tenant Mitarbeitende mit Wohnsitz in
        der EU beschäftigen (z. B. Grenzgänger:innen), kann die DSGVO für die Bearbeitung von
        deren Daten ergänzend zur Anwendung kommen (Art. 3 Abs. 2 DSGVO) — wir richten unsere
        Massnahmen an den Grundsätzen beider Regelwerke aus.
      </p>

      <h2>3. Welche Personendaten werden bearbeitet</h2>
      <ul>
        <li>Konto-/Zugangsdaten: Benutzername, Vor-/Nachname, E-Mail (optional), Passwort-Hash.</li>
        <li>Mitarbeitendendaten: Name, Pensum, Ein-/Austrittsdatum, optional Geburtsdatum.</li>
        <li>Dienstplan- und Arbeitszeitdaten inkl. Zeiterfassung und Saldo-Berechnungen.</li>
        <li>Absenzdaten inkl. optionaler Freitextnotizen.</li>
        <li>
          <strong>Besondere Personendaten</strong> (Art. 5 lit. c revDSG): als krankheitsbedingt
          markierte Absenzen sowie optionale Schwangerschafts-/Mutterschaftsdaten für den
          gesetzlichen Gesundheitsschutz.
        </li>
        <li>Zahlungsdaten: ausschliesslich bei unserem Zahlungsdienstleister Stripe.</li>
      </ul>

      <h2>4. Zweck der Bearbeitung</h2>
      <p>
        Dienstplanung unter Einhaltung des Arbeitsgesetzes, Arbeitszeit- und
        Absenzverwaltung, Saldo-/Fairness-Kennzahlen, Erfüllung gesetzlicher
        Aufbewahrungspflichten, Rechnungsstellung sowie Benachrichtigungen zu Absenz-/
        Diensttausch-Anfragen.
      </p>

      <h2>5. Rechtsgrundlage</h2>
      <p>
        Erfüllung des Arbeitsverhältnisses (Art. 328b OR) sowie gesetzlicher Pflichten (ArG, OR,
        AHVG). Besondere Personendaten werden nur bearbeitet, soweit für die Durchführung des
        Arbeitsverhältnisses und sozialversicherungsrechtliche Pflichten erforderlich
        (Verhältnismässigkeit, Art. 6 Abs. 2 revDSG).
      </p>

      <h2>6. Empfänger und Auftragsbearbeiter</h2>
      <ul>
        <li>
          <strong>Stripe</strong> (Zahlungsabwicklung): erhält Tenant-Name, ggf. Rechnungs-E-Mail
          und Zahlungsdaten — keine Mitarbeitendendaten.
        </li>
        <li>
          <strong>E-Mail-Versand</strong>: [Anbieter einsetzen, sobald produktiver Mailversand
          eingerichtet ist].
        </li>
        <li>
          <strong>Hosting</strong>: [Anbieter und Standort einsetzen, sobald entschieden].
        </li>
      </ul>

      <h2>7. Aufbewahrungsdauer</h2>
      <ul>
        <li>Lohn-/buchhaltungsrelevante Unterlagen: 10 Jahre (Art. 958f OR).</li>
        <li>Arbeitszeit-Kontrollunterlagen ohne Lohnbezug: mind. 5 Jahre (Art. 73 ArGV 1).</li>
        <li>
          Besondere Personendaten ohne eigene Aufbewahrungspflicht: Anonymisierung spätestens 2
          Jahre nach dem jeweiligen Ereignis.
        </li>
        <li>Mitarbeitendendaten nach Austritt: Anonymisierung spätestens nach 10 Jahren.</li>
      </ul>

      <h2>8. Datensicherheit</h2>
      <p>
        Angemessene technische und organisatorische Massnahmen (Art. 8 revDSG): verschlüsselte
        Übertragung, gehashte Passwörter, rollenbasierte Zugriffskontrolle, strikte
        Mandantentrennung.
      </p>

      <h2>9. Rechte der betroffenen Personen</h2>
      <p>
        Auskunftsrecht (Art. 25 revDSG, in der App per Datenexport umsetzbar), Recht auf
        Berichtigung über Ihre Admin-/Planer:in, Recht auf Löschung (Art. 32 revDSG) soweit keine
        gesetzliche Pflicht entgegensteht, Recht auf Datenherausgabe (Art. 28 revDSG) sowie das
        Recht, sich beim EDÖB (edoeb.admin.ch) zu beschweren.
      </p>

      <h2>10. Hosting-Standort und internationale Datenbekanntgabe</h2>
      <p>[Hosting-Standort einsetzen, sobald entschieden].</p>

      <h2>11. Kontakt</h2>
      <p>[E-Mail-Adresse einsetzen]</p>
    </LegalPage>
  );
}

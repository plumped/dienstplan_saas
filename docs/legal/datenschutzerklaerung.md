> **ENTWURF — keine Rechtsberatung.** Dieser Text ist eine fundierte Arbeitsgrundlage, orientiert
> an den unten zitierten Bestimmungen des revDSG und, wo einschlägig, der DSGVO. Er ersetzt keine
> anwaltliche Prüfung und darf erst nach juristischer Freigabe live geschaltet werden. Alle mit
> `[...]` markierten Stellen sind Platzhalter und müssen vor Veröffentlichung durch reale Angaben
> ersetzt werden.

# Datenschutzerklärung

Stand: [Datum einsetzen]

## 1. Verantwortlicher

[Firmenname]
[Strasse, PLZ Ort]
[Land]
Kontakt für Datenschutzanliegen: [E-Mail-Adresse]

Verantwortlich im Sinne des Bundesgesetzes über den Datenschutz (revDSG, SR 235.1) ist die oben
genannte Betreiberin der Software "neravo" (nachfolgend "wir"). Für Personendaten, die Sie als
Kundin/Kunde über neravo zur Dienstplanung Ihrer eigenen Mitarbeitenden bearbeiten, sind grundsätzlich
**Sie** (der Tenant/Betrieb) Verantwortlicher gemäss Art. 328b OR und revDSG; wir sind insoweit
Auftragsbearbeiter (siehe Auftragsverarbeitungsvertrag, `avv-vorlage.md`).

## 2. Geltungsbereich: revDSG und ergänzend DSGVO

neravo richtet sich an Schweizer Betriebe (Heime, Spitäler, Pflegeeinrichtungen) und wird primär
nach dem **revidierten Bundesgesetz über den Datenschutz (revDSG)**, in Kraft seit 1. September
2023, betrieben. Die Verordnung über den Datenschutz (VDSZ) und die Verordnung über die
Datenschutzzertifizierungen werden ergänzend beachtet.

Sollte ein von Ihnen betriebener Tenant Mitarbeitende beschäftigen, die ihren Wohnsitz in der EU
haben (z. B. Grenzgänger:innen), kann die **Datenschutz-Grundverordnung (DSGVO)** für die
Bearbeitung der Daten dieser Personen ergänzend zur Anwendung kommen (Art. 3 Abs. 2 DSGVO). Wir
richten unsere technischen und organisatorischen Massnahmen so aus, dass sie auch den Grundsätzen
der DSGVO (Art. 5 DSGVO) genügen, ohne dass wir uns generell der DSGVO unterstellen.

## 3. Welche Personendaten werden bearbeitet

Im Rahmen der Nutzung von neravo werden folgende Kategorien von Personendaten bearbeitet:

- **Konto-/Zugangsdaten**: Benutzername, Vor-/Nachname, E-Mail-Adresse (optional), Passwort
  (als Hash gespeichert, nie im Klartext).
- **Mitarbeitendendaten**: Vor-/Nachname, Pensum, Ein-/Austrittsdatum, optional Geburtsdatum
  (nur wenn für den Jugendschutz nach ArGV 5 relevant).
- **Dienstplan- und Arbeitszeitdaten**: geplante und effektiv geleistete Schichten, Zeiterfassung,
  Saldo-/Überzeit-Berechnungen.
- **Absenzdaten**: Art und Zeitraum von Absenzen (Ferien, Krankheit, Unfall, Sonstiges je nach
  betriebseigenem Katalog) inkl. optionaler Freitextnotizen.
- **Besondere Personendaten** (Art. 5 lit. c revDSG — Gesundheitsdaten): Absenzen, die als
  krankheitsbedingt markiert sind, sowie optionale Schwangerschafts-/Mutterschaftsdaten
  (voraussichtlicher/tatsächlicher Geburtstermin, Freitextnotizen) für den gesetzlich
  vorgeschriebenen Mutterschafts-/Gesundheitsschutz. Diese Daten werden nur bearbeitet, wenn die
  betroffene Person bzw. der Tenant sie aktiv erfasst, und ausschliesslich für die in Ziffer 4
  genannten Zwecke verwendet.
- **Zahlungsdaten**: werden ausschliesslich von unserem Zahlungsdienstleister Stripe verarbeitet
  (siehe Ziffer 6) — wir selbst speichern keine Kreditkarten- oder Kontodaten.

## 4. Zweck der Bearbeitung

- Erstellung und Verwaltung von Dienstplänen unter Einhaltung der Vorgaben des Arbeitsgesetzes
  (ArG) und der zugehörigen Verordnungen.
- Erfassung und Auswertung der effektiven Arbeitszeit (Grundlage für Lohnlauf/Zeitkonten).
- Verwaltung von Absenzen, Diensttausch und Wunschdiensten.
- Berechnung von Saldo-, Überzeit- und Fairness-Kennzahlen.
- Erfüllung gesetzlicher Aufbewahrungspflichten (siehe Ziffer 7).
- Rechnungsstellung und Verwaltung des Abonnements (Tenant-Ebene, über Stripe).
- Kommunikation im Zusammenhang mit Absenz-/Diensttausch-Benachrichtigungen.

## 5. Rechtsgrundlage

Die Bearbeitung erfolgt zur Erfüllung des Vertrags zwischen dem Tenant und seinen Mitarbeitenden
(Arbeitsverhältnis, Art. 328b OR) sowie zur Erfüllung gesetzlicher Pflichten (ArG, OR, AHVG). Für
besondere Personendaten gilt zusätzlich, dass sie nur bearbeitet werden, soweit dies für die
Durchführung des Arbeitsverhältnisses und die Einhaltung arbeits- und sozialversicherungsrechtlicher
Pflichten erforderlich ist (Verhältnismässigkeitsgrundsatz, Art. 6 Abs. 2 revDSG).

## 6. Empfänger und Auftragsbearbeiter

- **Stripe** (Zahlungsabwicklung, Rechnungsstellung): erhält Tenant-Name, ggf. E-Mail-Adresse der
  Rechnungskontaktperson sowie Zahlungsdaten. Es werden **keine** Mitarbeitendendaten (Namen,
  Absenzen, Zeiterfassung) an Stripe übermittelt. Stripe agiert als Auftragsbearbeiter, Details
  siehe Stripes eigene Datenschutzerklärung.
- **E-Mail-Versand**: für Benachrichtigungen zu Absenz-/Diensttausch-Anfragen wird [Anbieter
  einsetzen, sobald produktiver Mailversand eingerichtet ist] eingesetzt. Bis zur Einrichtung
  eines produktiven Mailversands erfolgt keine tatsächliche Zustellung an Dritte.
- **Hosting**: [Hosting-Anbieter und Standort einsetzen, sobald entschieden — siehe Ziffer 10].

Mit sämtlichen Auftragsbearbeitern besteht bzw. wird ein Auftragsverarbeitungsvertrag gemäss
Art. 9 revDSG abgeschlossen.

## 7. Aufbewahrungsdauer

Wir bewahren Personendaten nur so lange auf, wie es für die jeweiligen Zwecke erforderlich ist
oder gesetzliche Aufbewahrungspflichten bestehen:

- **Lohn-/buchhaltungsrelevante Unterlagen** (u. a. Zeiterfassung, soweit für den Lohnlauf
  verwendet): **10 Jahre** nach Ablauf des Geschäftsjahres (Art. 958f OR).
- **Arbeitszeit-Kontrollunterlagen** ohne unmittelbaren Lohnbezug: mindestens **5 Jahre**
  (Art. 73 ArGV 1).
- **Besondere Personendaten ohne eigenständige gesetzliche Aufbewahrungspflicht** (z. B.
  Freitextnotizen zu Schwangerschaft oder Krankheitsabsenzen, sofern nicht Teil der
  Lohnunterlagen): werden zeitnah nach Wegfall des Bearbeitungszwecks anonymisiert, spätestens
  aber 2 Jahre nach dem jeweiligen Ereignis.
- **Mitarbeitendendaten nach Austritt**: werden spätestens 10 Jahre nach Austrittsdatum
  anonymisiert bzw. gelöscht, sofern keine längere gesetzliche Pflicht entgegensteht.

Details siehe unser internes Löschkonzept (technisch umgesetzt über ein automatisiertes,
regelmässig laufendes Bereinigungsverfahren).

## 8. Datensicherheit

Wir setzen angemessene technische und organisatorische Massnahmen ein (Art. 8 revDSG), u. a.
verschlüsselte Übertragung (HTTPS), gehashte Passwörter, rollenbasierte Zugriffskontrolle sowie
eine strikte Mandantentrennung (jeder Betrieb sieht ausschliesslich seine eigenen Daten).

## 9. Rechte der betroffenen Personen

Sie haben im Rahmen des revDSG folgende Rechte:

- **Auskunftsrecht** (Art. 25 revDSG): Sie können jederzeit Auskunft über die zu Ihrer Person
  bearbeiteten Daten verlangen. Für Ihre eigenen Kontodaten steht Ihnen dafür in der App eine
  Export-Funktion zur Verfügung.
- **Recht auf Berichtigung**: unrichtige Daten können Sie über Ihre vorgesetzte Person (Admin/
  Planer:in Ihres Betriebs) korrigieren lassen.
- **Recht auf Löschung** (Art. 32 revDSG): soweit keine gesetzliche Aufbewahrungspflicht
  entgegensteht.
- **Recht auf Datenherausgabe/-übertragung** (Art. 28 revDSG).
- **Widerspruchsrecht** gegen bestimmte Bearbeitungen, soweit gesetzlich zulässig.

Anfragen richten Sie an Ihre vorgesetzte Person (Admin/Planer:in) oder an [Kontakt einsetzen].
Sie haben zudem das Recht, sich beim Eidgenössischen Datenschutz- und Öffentlichkeitsbeauftragten
(EDÖB, www.edoeb.admin.ch) zu beschweren.

## 10. Hosting-Standort und internationale Datenbekanntgabe

[Hosting-Standort einsetzen, sobald entschieden — Schweiz oder EU wird empfohlen, insbesondere für
die Bearbeitung besonderer Personendaten]. Sollte eine Datenbekanntgabe ins Ausland erfolgen,
stellen wir durch geeignete Garantien (z. B. Standardvertragsklauseln) ein angemessenes
Schutzniveau sicher (Art. 16/17 revDSG).

## 11. Änderungen dieser Erklärung

Wir passen diese Erklärung an, wenn sich die Bearbeitung von Personendaten oder die Rechtslage
ändert. Die jeweils aktuelle Fassung ist über die App abrufbar.

## 12. Kontakt

Für Fragen zum Datenschutz wenden Sie sich an: [E-Mail-Adresse einsetzen]

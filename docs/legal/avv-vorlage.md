> **ENTWURF — keine Rechtsberatung.** Diese Vorlage orientiert sich an Art. 9 revDSG i. V. m.
> Art. 9 der Verordnung über den Datenschutz (VDSZ/revDSGV) sowie, soweit im Einzelfall
> DSGVO-Bezug besteht, an Art. 28 DSGVO. Vor Verwendung mit realen Kundinnen ist eine juristische
> Prüfung zwingend — dies ist ein Vertragsdokument, kein blosser Info-Text. Alle `[...]`-Stellen
> sind Platzhalter.

# Auftragsverarbeitungsvertrag (AVV)

zwischen

**[Firmenname des Kunden/Tenant]**, [Adresse] (nachfolgend "Verantwortliche")

und

**[Firmenname der Anbieterin]**, [Adresse] (nachfolgend "Auftragsbearbeiterin")

## 1. Gegenstand und Dauer

Dieser AVV ergänzt die zwischen den Parteien geschlossenen Allgemeinen Geschäftsbedingungen
("Hauptvertrag") und regelt die Bearbeitung von Personendaten, welche die Verantwortliche über
die Software "neravo" durch die Auftragsbearbeiterin bearbeiten lässt. Der AVV gilt für die
Dauer des Hauptvertrags.

## 2. Gegenstand, Art und Zweck der Bearbeitung

- **Gegenstand**: Personendaten der Mitarbeitenden der Verantwortlichen, die zur Dienstplanung,
  Arbeitszeiterfassung, Absenzverwaltung und den zugehörigen Auswertungen im Dienst erfasst
  werden.
- **Kategorien betroffener Personen**: Mitarbeitende der Verantwortlichen sowie deren
  Nutzerkonten (Admin/Planer:in/Mitarbeitende) im Dienst.
- **Kategorien von Personendaten**: Konto-/Zugangsdaten, Mitarbeitendenstammdaten,
  Dienstplan-/Arbeitszeit-/Absenzdaten, und — sofern von der Verantwortlichen erfasst — besondere
  Personendaten (Gesundheitsdaten) im Zusammenhang mit Krankheitsabsenzen und
  Schwangerschaft/Mutterschaft (Art. 5 lit. c revDSG).
- **Zweck**: ausschliesslich zur Erbringung des Dienstes gemäss Hauptvertrag; keine Bearbeitung
  zu eigenen Zwecken der Auftragsbearbeiterin.

## 3. Pflichten der Auftragsbearbeiterin

Die Auftragsbearbeiterin verpflichtet sich,

1. Personendaten ausschliesslich im Rahmen der dokumentierten Weisungen der Verantwortlichen
   und der Zwecke dieses AVV zu bearbeiten;
2. angemessene technische und organisatorische Massnahmen gemäss Art. 8 revDSG zu treffen
   (siehe Anhang 1);
3. der Vertraulichkeit verpflichtete Personen mit der Bearbeitung zu betrauen;
4. die Verantwortliche unverzüglich, spätestens innert [72 Stunden] nach Kenntnisnahme, über
   Datensicherheitsverletzungen zu informieren, die zu Personendaten der Verantwortlichen
   führen könnten (Grundlage für die Meldepflicht der Verantwortlichen nach Art. 24 revDSG);
5. der Verantwortlichen bei der Erfüllung von Betroffenenrechten (Auskunft, Berichtigung,
   Löschung, Datenübertragbarkeit) angemessen zu unterstützen;
6. Personendaten nach Beendigung des Hauptvertrags gemäss Ziffer 6 dieses AVV zu löschen oder
   zurückzugeben, soweit keine gesetzliche Aufbewahrungspflicht entgegensteht;
7. der Verantwortlichen auf Anfrage die notwendigen Informationen zum Nachweis der Einhaltung
   dieses AVV zur Verfügung zu stellen.

## 4. Unterauftragsbearbeiter

Die Auftragsbearbeiterin darf folgende Unterauftragsbearbeiter einsetzen:

| Unterauftragsbearbeiter | Zweck | Bearbeitete Daten |
|---|---|---|
| Stripe | Zahlungsabwicklung, Rechnungsstellung | Firmen-/Zahlungsdaten der Verantwortlichen, **keine** Mitarbeitendendaten |
| [Hosting-Anbieter einsetzen] | Infrastruktur/Hosting | Alle im Dienst gespeicherten Daten |
| [E-Mail-Anbieter einsetzen, sobald produktiv im Einsatz] | Benachrichtigungen | Name, E-Mail-Adresse, Absenz-/Diensttausch-Betreff |

Der Einsatz weiterer Unterauftragsbearbeiter wird der Verantwortlichen vorgängig angezeigt; sie
kann innert angemessener Frist widersprechen.

## 5. Technische und organisatorische Massnahmen (TOMs)

Siehe Anhang 1. Wesentliche Massnahmen: verschlüsselte Übertragung (HTTPS), gehashte Passwörter,
rollenbasierte Zugriffskontrolle, strikte Mandantentrennung auf Datenbankebene, protokollierte
Änderungshistorie sicherheitsrelevanter Datensätze.

## 6. Löschung und Rückgabe nach Vertragsende

Nach Beendigung des Hauptvertrags löscht die Auftragsbearbeiterin sämtliche Personendaten der
Verantwortlichen, soweit keine gesetzliche Aufbewahrungspflicht (insb. Art. 958f OR, 10 Jahre für
lohnrelevante Unterlagen) entgegensteht. Auf Anfrage stellt die Auftragsbearbeiterin der
Verantwortlichen vor der Löschung einen vollständigen Datenexport zur Verfügung.

## 7. Haftung

Es gelten die Haftungsregelungen des Hauptvertrags (siehe AGB Ziffer 9), soweit dieser AVV nichts
Abweichendes regelt.

## 8. Anwendbares Recht

Es gilt schweizerisches Recht. Gerichtsstand ist [Gerichtsstand einsetzen], soweit gesetzlich
zulässig.

---

## Anhang 1: Technische und organisatorische Massnahmen (TOMs)

- **Zugriffskontrolle**: rollenbasierte Berechtigungen (Admin/Planer:in/HR/Mitarbeitende),
  Mandantentrennung — jeder Tenant sieht ausschliesslich seine eigenen Daten.
- **Übertragungssicherheit**: Verschlüsselung der Datenübertragung mittels HTTPS/TLS.
- **Eingabekontrolle**: Änderungshistorie sicherheitsrelevanter Datensätze (Mitarbeitende,
  Absenzen, Schwangerschaftsdaten, Zeiterfassung, Zuweisungen) wird protokolliert.
- **Verfügbarkeit**: [Backup-/Wiederherstellungskonzept einsetzen, sobald produktiv].
- **Auftragskontrolle**: Unterauftragsbearbeiter gemäss Ziffer 4, mit denen ihrerseits
  entsprechende Verträge bestehen.

---

**Ort, Datum**: _______________________

**Für die Verantwortliche**: _______________________

**Für die Auftragsbearbeiterin**: _______________________

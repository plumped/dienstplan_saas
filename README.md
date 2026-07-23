# Dienstplanungs-SaaS — Backend-Skelett

Django + DRF Skelett gemäss `funktionsumfang-dienstplanung-saas.md` (Abschnitte 1–4, 6, 7, 8, 10).
Multi-Tenancy per `tenant_id` (shared database), getestet inkl. Cross-Tenant-Sicherheitschecks
(`core/tests.py`, `scheduling/tests.py`).

**Zielgruppe**: kleine Kliniken, Arztpraxen und ähnliche Gesundheitsbetriebe in der Schweiz
(typischerweise 5–50 Mitarbeitende, eine bis wenige Stationen/Standorte). Die Regel-Engine bildet
bereits mehrere Kernpunkte des Schweizer Arbeitsgesetzes (ArG) ab (siehe nächster Abschnitt) --
was für einen rechtssicheren Praxiseinsatz noch fehlt (u. a. automatische Ersatzruhetag-Kontrolle,
Überzeit-Zuschläge, Ist-Arbeitszeiterfassung), steht im
[MVP-Fahrplan](#mvp-fahrplan-bis-zur-marktreife). **Kein Ersatz für eine arbeitsrechtliche
Prüfung** — die hinterlegten Grenzwerte sind Standardwerte, kein Rechtsrat.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Admin unter `/admin/`, API unter `/api/` (Browsable API inkl. Login unter `/api-auth/login/`).
`db.sqlite3` ist bewusst nicht (mehr) im Repo (siehe `.gitignore`) — jede Umgebung erzeugt sich
ihre eigene per `migrate`.

## Architektur-Entscheidungen, die wichtig zu kennen sind

- **Multi-Tenancy**: `core.models.TenantScopedModel` (abstract) gibt jedem fachlichen Modell ein
  `tenant`-Feld. Zwei Manager: `objects` (ContextVar-gefiltert, praktisch für Shell/Celery),
  `all_objects` (ungefiltert). Die DRF-ViewSets filtern zusätzlich **explizit** über
  `request.tenant` in `get_queryset()` — das ist die eigentliche Sicherheitsgrenze, nicht die
  ContextVar. Grund: eine `queryset=Model.objects.all()` als Klassenattribut (z. B. in einem
  Serializer-Feld) wird beim Modul-Import ausgewertet, **bevor** ein Request-Tenant existiert,
  und wäre dauerhaft ungefiltert. Deshalb nutzt `NodeSerializer.parent` bewusst kein
  `PrimaryKeyRelatedField` mit `Node.objects`, sondern ein rohes `IntegerField` mit manueller
  Prüfung in der View; `ShiftTradeRequestSerializer` macht es analog mit `all_objects` +
  manueller Tenant-Prüfung in `validate()`. Alle Cross-Tenant-Zugriffe sind end-to-end getestet.

  `request.tenant` wird in `TenantScopedViewSet.initial()` gesetzt — **nicht** in einer
  Django-Middleware. Eine frühere Version tat genau das und funktionierte in Tests mit
  Session-Login, lieferte aber leere Listen für das Frontend, das `TokenAuthentication`
  nutzt: Django-Middleware läuft VOR der DRF-Authentifizierung, `request.user` ist an der
  Middleware-Stelle bei Token-Logins noch `AnonymousUser`. `initial()` ruft zuerst
  `super().initial()` auf (das führt die DRF-Auth aus) und liest `request.user` erst danach.
  Siehe `core/tenancy.py` für den ausführlichen Kommentar dazu.

  `core.middleware.TenantContextCleanupMiddleware` (registriert in `MIDDLEWARE`) setzt die
  ContextVar nach jedem Request zurück. Ohne das bleibt sie auf dem zuletzt aufgelösten Tenant
  stehen, weil WSGI-Worker-Threads über mehrere Requests hinweg wiederverwendet werden — ein
  Cross-Tenant-Leck für jeden Code, der über `TenantScopedManager` ungefiltert zugreift (z. B.
  `employee.skills.all()`, das intern den `objects`-Default-Manager von `Skill` nutzt). Durch
  einen Test (`core.tests.TenantContextCleanupMiddlewareTests`) abgesichert; genau dieses Leck
  hat ursprünglich einen model-Test in einer anderen Tenant-Instanz zum Scheitern gebracht.

- **Auth für die API**: `POST /api/auth/token/` (DRF's `obtain_auth_token`) nimmt
  `{"username", "password"}` entgegen und gibt `{"token": "..."}` zurück. Das
  Frontend-Template (`dienstplan_frontend`) nutzt genau das. Unauthentifizierte Requests liefern
  bewusst `403` statt `401` (DRF-Standardverhalten, weil `SessionAuthentication` zuerst in
  `DEFAULT_AUTHENTICATION_CLASSES` steht und keinen `WWW-Authenticate`-Header anbietet).

- **Custom User Model**: `core.models.User` (leeres `AbstractUser`, `AUTH_USER_MODEL =
  "core.User"`) von Anfang an, damit spätere Erweiterungen (z. B. Sprache, Telefonnummer für
  Diensttausch-Benachrichtigungen) ohne schmerzhafte Auth-Tabellen-Migration möglich sind.

- **Node (Organisationsbaum)**: nutzt `django-treebeard` (Materialized Path). Wichtig:
  Knoten **müssen** über `Node.add_root(...)` / `parent.add_child(...)` erzeugt werden, nicht
  über `.objects.create()` — sonst bleiben `path`/`depth` leer und es gibt einen
  `IntegrityError`. `NodeViewSet.perform_create` kapselt das bereits für die API
  (`POST /api/nodes/` mit optionalem `parent`-Feld).

- **Regel-Engine** (`ShiftAssignment.clean()`, Abschnitt 4, orientiert am Schweizer ArG): die
  numerischen Grenzwerte (`minimum_rest_hours`, `maximum_weekly_hours`,
  `maximum_daily_span_hours`) sitzen als Felder auf `core.models.Tenant` -- pro Klinik/Praxis im
  Admin anpassbar (z. B. für einen strengeren GAV), Defaults entsprechen Art. 9/10/15a ArG für
  Gesundheits-/Büropersonal. Geprüft (harte Ablehnung) werden: Ruhezeit zum Vor-/Folgetag
  (Art. 15a), Wochenhöchstarbeitszeit (Art. 9), Pausenpflicht gestaffelt nach Netto-Arbeitszeit
  (Art. 15), Tagesspanne (Art. 10), mindestens ein freier Tag pro Kalenderwoche (Art. 21),
  Jugendschutz für unter 18-Jährige (`Employee.birth_date`/`is_minor_on()`: erhöhte Ruhezeit,
  kein Nacht-/Sonntagsarbeit, ArGV 5), Pflicht-Qualifikation (`TimeTemplate.required_skill`) und
  Kollision mit einer `Absence`.
  Zusätzlich berechnet (informativ, blockiert nichts): `night_hours` (Überlappung mit
  23:00–06:00, Art. 16) und `is_sunday` -- als Grundlage für Zuschläge/Ersatzruhetag in einer
  künftigen Lohnauswertung (siehe [MVP-Fahrplan](#mvp-fahrplan-bis-zur-marktreife), Block 1).
  Greift über die API, weil `ShiftAssignmentSerializer.validate()` `clean()` aufruft — nicht nur
  im Admin.

- **Absenzen** (`Absence`, Abschnitt 6, Genehmigungs-Workflow siehe Block 2.3): Ferien/Krankheit/
  Sonstiges pro Mitarbeiter und Zeitraum, mit `status` (PENDING/APPROVED/REJECTED). Von
  Mitarbeitenden erstellte Absenzen starten als PENDING und brauchen `POST
  /api/absences/<id>/approve|reject/` durch Admin/Planer; von Admin/Planer selbst erstellte sind
  sofort APPROVED (`AbsenceViewSet.perform_create`). Nur **APPROVED**-Absenzen blockieren
  überlappende `ShiftAssignment`s über die Regel-Engine (`_check_no_absence_conflict`) -- ein
  offener Antrag schränkt die Planung noch nicht ein.

- **Diensttausch** (`ShiftTradeRequest`, Abschnitt 7, Genehmigungs-Workflow siehe Block 2.3): ein
  Mitarbeiter bietet eine eigene Schicht entweder zur einfachen Übernahme an (`target_assignment`
  leer) oder als echten Tausch gegen eine konkrete Schicht von `target_employee`
  (`target_assignment` gesetzt). Zweistufig: `POST .../accept/` durch die Zielperson markiert nur
  die Zustimmung (Status `employee_accepted`), vollzieht **noch keinen** Tausch. Erst `POST
  .../approve/` durch Admin/Planer vollzieht ihn tatsächlich (`ShiftTradeRequest.approve()`) und
  durchläuft dabei zwingend die volle Regel-Engine für die resultierende(n) Zuweisung(en) --
  bleibt sie erfolglos (z. B. Ruhezeit-Konflikt), bleibt die Anfrage im bisherigen Status stehen,
  ohne etwas zu ändern. `approve()` kann auch direkt aus `pending` aufgerufen werden, falls
  Admin/Planer die Zustimmung z. B. telefonisch eingeholt haben. `POST .../reject/` (Admin/Planer)
  lehnt ab und ist von `decline/` (Zielperson lehnt selbst ab) zu unterscheiden.

- **Rollenbasierte Berechtigungen** (`core.permissions`, Abschnitt 10): `Membership.role`
  (Admin/Planer/Mitarbeiter/HR) wird jetzt durchgesetzt, nicht nur gespeichert. Lesen ist für
  alle vier Rollen innerhalb des eigenen Tenants erlaubt (Transparenz: alle sehen den ganzen
  Plan); Schreiben an Stammdaten und am Planblatt (`Node`/`Skill`/`Employee`/`TimeTemplate`/
  `ShiftAssignment`) ist Admin/Planer vorbehalten (`IsTenantManager`). Bei Absenzen und
  Diensttausch dürfen Mitarbeitende zusätzlich für sich selbst schreiben
  (`OwnEmployeeRecordPermission`, `ShiftTradeRequestPermission`) -- "für sich selbst" heisst
  konkret: die `Employee`, die über `Employee.user` mit dem eingeloggten Account verknüpft ist
  (`request.employee_profile`, aufgelöst in `TenantScopedViewSet.initial()`). HR ist überall vom
  Schreiben ausgeschlossen ("nur Reporting"). Wichtige Stolperfalle beim Implementieren: die
  Rollenprüfung muss **vor** `self.check_permissions()` passieren, aber `request.user` ist erst
  **nach** `self.perform_authentication()` bekannt -- `TenantScopedViewSet.initial()` baut daher
  `APIView.initial()` manuell nach, statt es als Ganzes über `super()` aufzurufen, damit
  `request.membership`/`request.employee_profile` rechtzeitig gesetzt sind.

  `GET /api/me/` (`core.views.MeView`) liefert Rolle, Tenant-Name und die verknüpfte `Employee`
  (falls vorhanden) des eingeloggten Users -- die Grundlage, auf der das Frontend entscheidet, ob
  es die volle Planer-Oberfläche oder die read-only Self-Service-Ansicht zeigt (siehe Frontend-
  Abschnitt unten).

## Frontend

Ein kleines React/Vite-Template liegt separat unter `dienstplan_frontend/` (eigenes README dort).
Es spricht die hier beschriebene API an (`/api/auth/token/`, `/api/nodes/`, `/api/employees/`,
`/api/time-templates/`, `/api/shift-assignments/`). Das Planblatt-Grid unterstützt neben
Klick + Dropdown auch:

- **Drag & Drop**: eine belegte Zelle lässt sich auf eine leere Zelle ziehen, um die Schicht zu
  verschieben (Tag und/oder Mitarbeiter). Ist das Ziel bereits belegt, wird die Verschiebung
  abgelehnt (keine automatische Verdrängung/kein Swap).
- **Wochenmuster kopieren**: Icon-Button pro Mitarbeiterzeile (bei Hover sichtbar) kopiert die
  erste angezeigte Woche des Monats auf alle weiteren Wochen desselben Monats. Bereits belegte
  Zieltage werden nicht überschrieben; Tage, die an der Regel-Engine scheitern (z. B.
  Ruhezeit-Konflikt), werden übersprungen und am Ende summarisch gemeldet.
- **Rollenbewusste Oberfläche** (`src/roles.js: canManageSchedule`, gespiesen aus `GET /api/me/`):
  Admin/Planer sehen die volle Bearbeitungs-Oberfläche wie oben beschrieben. Mitarbeitende sehen
  denselben Plan nur lesend (Zellen als `<span>` statt `<button>`, kein Klick, kein Drag, kein
  Wochenmuster-Button) -- ausser dem ⇄-Symbol auf den **eigenen** Schichten, um sie zum Tausch
  anzubieten. Im Tab "Abwesenheiten" ist das Mitarbeiter-Feld im Formular auf die eigene Person
  gesperrt, und der "Löschen"-Button erscheint nur bei eigenen **offenen** Einträgen. Im Tab
  "Diensttausch" erscheinen Annehmen/Ablehnen nur bei Angeboten, deren Zielperson man selbst ist,
  Zurückziehen nur bei selbst erstellten Angeboten. HR sieht überall nur Lesezugriff, keine
  Bearbeitungs-Buttons. Das Frontend blendet damit nur Bedienelemente aus, die der Server über
  `core.permissions` ohnehin mit 403 ablehnen würde -- die eigentliche Absicherung bleibt serverseitig.
- **Genehmigungs-Workflow** (Block 2.3): Admin/Planer sehen bei offenen Absenzanträgen und
  Diensttausch-Angeboten zusätzlich "Genehmigen"/"Freigeben" und "Ablehnen"-Buttons. Im Planblatt
  werden nur genehmigte Absenzen als Sperr-Chip angezeigt -- ein offener Antrag taucht nur im Tab
  "Abwesenheiten" auf, blockiert das Grid aber (noch) nicht.

## MVP-Fahrplan bis zur Marktreife

Priorisiert für den Verkauf an kleine Kliniken/Praxen in der Schweiz. Block 1 ist die
fachliche Kernanforderung (ohne die ist das Produkt für Gesundheitsbetriebe nicht seriös
einsetzbar), Blöcke 2–5 sind nötig, damit eine Praxis das Produkt tatsächlich selbständig
nutzen, bezahlen und rechtlich unbedenklich betreiben kann.

### 1. Schweizer Arbeitsgesetz (ArG) — Regel-Engine vervollständigen

**Bereits umgesetzt** (`scheduling/models.py: ShiftAssignment`, `core/tests.py`,
`scheduling/tests.py`):

1. ✅ **Pro Tenant/Branche konfigurierbare Grenzwerte**: `minimum_rest_hours` (Default 11h,
   Art. 15a ArG), `maximum_weekly_hours` (Default 45h, Art. 9 ArG) und `maximum_daily_span_hours`
   (Default 14h, Art. 10 ArG) sitzen als Felder auf `core.models.Tenant`, im Admin editierbar --
   für Betriebe mit 50h-Regelung oder strengerem GAV (Gesamtarbeitsvertrag, z. B. GAV
   Santésuisse) pro Klinik/Praxis anpassbar.
2. ✅ **Pausenregelung** (Art. 15 ArG): > 5.5h Netto-Arbeitszeit → 15 Min., > 7h → 30 Min.,
   > 9h → 1h Pause, automatisch gegen `TimeTemplate.break_minutes` geprüft statt nur erfasst.
3. ✅ **Tägliche Höchstarbeitszeit inkl. Pausen** (Art. 10 ArG: Tagesspanne max.
   `maximum_daily_span_hours`, Default 14h).
4. ✅ **Wöchentlicher freier Tag**: mindestens ein ganzer freier Tag pro Kalenderwoche
   (Art. 21 ArG) wird geprüft. *Noch offen*: die zusätzliche Anforderung "im Schnitt einmal
   monatlich ein Sonntag frei" ist nicht automatisiert.
5. 🟡 **Nachtarbeit** (23:00–06:00, Art. 16 ff. ArG): wird pro Schicht als `night_hours` erkannt
   und über die API ausgegeben (informativ). *Noch offen*: Zeitzuschlag-Berechnung
   (i. d. R. +10% Zeitgutschrift bei regelmässiger Nachtarbeit), automatische
   Bewilligungs-/Warnhinweise und Tracking der arbeitsmedizinischen Untersuchungspflicht bei
   regelmässiger Nachtarbeit.
6. 🟡 **Sonntagsarbeit** (Art. 19/27 ArG): wird pro Schicht als `is_sunday` erkannt (Gesundheits-
   betriebe sind von der Bewilligungspflicht ausgenommen). *Noch offen*: automatische Kontrolle,
   ob der gesetzlich vorgeschriebene Ersatzruhetag (Art. 20 ArG) tatsächlich gewährt wurde, sowie
   ein allfälliger Lohnzuschlag.
7. ✅ **Jugendschutz** (ArGV 5) für unter 18-Jährige: `Employee.birth_date` (optional) +
   `Employee.is_minor_on(date)`. Für Minderjährige gilt eine erhöhte Mindestruhezeit (12h statt
   der Tenant-Vorgabe) sowie ein hartes Verbot von Nacht- und Sonntagsarbeit. Vereinfachte
   Version ohne die branchenspezifischen Ausnahmetatbestände (z. B. Berufsbildung mit
   Nachtarbeit in bestimmten Branchen) — bei Lernenden im Betrieb empfiehlt sich eine
   arbeitsrechtliche Prüfung der konkreten Ausnahmen.

**Noch offen**:

8. **Überzeitarbeit**: Soll/Ist-Vergleich pro Woche (Soll aus `Employee.employment_pct`) und
   Zuschlag (i. d. R. 25%, Art. 13 ArG). Bewusst nicht Teil der Regel-Engine selbst, sondern der
   geplanten Monatsauswertung (Block 2.6), weil Überzeit eine Auswertungs-/Lohnfrage ist, keine
   Ablehnung einer Zuweisung.
9. **Ist-Arbeitszeiterfassung** (Art. 73 ArGV 1: Pflicht zur Aufzeichnung von Beginn, Ende und
   Pausen der tatsächlich geleisteten Arbeitszeit) — heute bildet die App nur die **Planung**
   (Soll) ab, z. B. Frühdienst geplant 07:00–16:00, aber tatsächlich erst 07:12 begonnen. Konkrete
   Teilschritte für dieses Modul:
   1. **Datenmodell**: neues `TimeRecord` (tenant-scoped, `OneToOneField` auf `ShiftAssignment`,
      damit jede Ist-Erfassung eindeutig einer Soll-Schicht zugeordnet ist) mit `actual_start`,
      `actual_end`, `actual_break_minutes`, `note`, `recorded_by` (User), `recorded_at`.
   2. **Self-Service-Eingabe**: Mitarbeitende dürfen Ist-Zeiten nur für die **eigene**
      (`Employee.user`) und bereits **stattgefundene** Schicht erfassen
      (`ShiftAssignment.date <= heute`) — dasselbe Berechtigungsmuster wie
      `OwnEmployeeRecordPermission` für Absenzen.
   3. **Soll/Ist-Abweichung**: Differenz zu `TimeTemplate.start_time`/`end_time` berechnen und
      anzeigen (z. B. "+12 Min."); ab einer konfigurierbaren Toleranz (z. B. 15 Min.) eine
      Begründung/Notiz verlangen.
   4. **Korrektur-/Prüf-Workflow**: analog zu `Absence`/`ShiftTradeRequest` sollten Ist-Einträge
      von Admin/Planer eingesehen und bei Bedarf korrigiert werden können (z. B. Status
      `submitted`/`confirmed`) — reine Selbstauskunft ohne jede Kontrolle wäre gegenüber
      Behörden/Revision wenig belastbar.
   5. **Regel-Engine-Bezug**: optional die tatsächliche Pause nachträglich gegen Art. 15 ArG
      prüfen (nur als Warnung/Hinweis, da rückwirkend nichts mehr verhindert werden kann).
   6. **API**: `/api/time-records/` als neues ViewSet, Tenant-Scoping und Rollenprüfung nach
      demselben Muster wie die bestehenden Endpunkte (`core.permissions`).
   7. **Frontend**: Eingabemaske für eigene, vergangene Schichten (z. B. Klick auf die eigene
      Zelle im Planblatt öffnet Soll-Zeiten vorausgefüllt zur Korrektur, oder eigener Tab
      "Zeiterfassung"); Planer-Ansicht mit einer Übersicht aller Abweichungen zur Prüfung.
   8. **Anschluss an Block 2.6**: die geplante Monatsauswertung (Soll/Ist-Stunden, Überzeit,
      Nacht-/Sonntagszuschläge) sollte, sobald dieses Modul existiert, auf `TimeRecord` statt nur
      auf der Planung (`ShiftAssignment`) basieren.
   9. **Aufbewahrung**: Ist-Daten fallen unter dieselbe Aufbewahrungspflicht wie Lohnunterlagen
      (siehe Block 5.3) — beim Löschkonzept mitdenken.

### 2. Fehlende Kernfunktionen für den Praxisalltag

1. ✅ **Rollenbasierte Berechtigungen durchsetzen**: `core.permissions` (`IsTenantManager`,
   `OwnEmployeeRecordPermission`, `ShiftTradeRequestPermission`) wertet `Membership.role`
   jetzt in allen ViewSets aus. Lesen bleibt für alle Rollen offen; Schreiben an
   Stammdaten/Planblatt ist Admin/Planer vorbehalten, HR schreibt nirgends, Mitarbeitende dürfen
   nur eigene Absenzen und eigenen Diensttausch verwalten. *Noch offen*: es gibt noch keine
   Verwaltung der Rollen/Einladungen selbst im Frontend (siehe Block 3.4).
2. ✅ **Rollenbewusste Oberfläche**: `GET /api/me/` + `src/roles.js` steuern, was das Frontend
   zeigt -- Admin/Planer die volle Bearbeitungs-Oberfläche, Mitarbeitende eine read-only Ansicht
   mit Selbstbedienung für eigene Absenzen/eigenen Diensttausch, HR nur Lesezugriff (siehe
   Frontend-Abschnitt oben). *Noch offen*: keine dedizierte mobile Ansicht (das bestehende Grid
   ist responsive genug für Desktop/Tablet, aber nicht für schmale Phone-Screens optimiert).
3. ✅ **Genehmigungs-Workflows**: Absenzen (`Absence.status`: PENDING/APPROVED/REJECTED,
   `AbsenceViewSet.approve/reject`) und Diensttausch (`ShiftTradeRequest`: `accept` markiert nur
   die Zustimmung der Zielperson, `approve`/`reject` durch Admin/Planer vollziehen/verwerfen den
   Tausch tatsächlich) haben jetzt eine Planer-Freigabe-Stufe. Details siehe Architektur-Abschnitt
   oben (Absenzen/Diensttausch) und Frontend-Abschnitt (Genehmigungs-Workflow).
4. **Benachrichtigungen** (mind. E-Mail) bei neuer Absenz-/Tauschanfrage, Genehmigung/Ablehnung
   und Veröffentlichung eines neuen Monatsplans.
5. **Export** (PDF/Excel) des Monatsplans — für Aushang in der Praxis und Übergabe an externe
   Lohnbuchhaltung, die selten direkt an die API angebunden ist.
6. **Monatsauswertung Soll/Ist-Stunden pro Mitarbeiter** (inkl. Nacht-/Sonntagszuschläge,
   Überzeit) als Basis für den Lohnlauf.
7. **Diensttausch als echter Swap** auch im Drag & Drop des Planblatt-Grids (aktuell: Ziehen auf
   eine belegte Zelle wird abgelehnt statt getauscht).
8. **Mindestbesetzung pro Schicht/Node** definierbar machen und in der Regel-Engine warnen, wenn
   sie unterschritten wird.

### 3. Onboarding & Mandantenfähigkeit für Self-Signup

1. **Setup-Wizard**: eine neue Praxis registriert sich selbst (Tenant, erster Admin-Account,
   Grundstruktur Node/Skills/TimeTemplates) — heute nur über den Django-Admin möglich, für
   nicht-technische Kund:innen nicht zumutbar.
2. **Einladungs-Flow** für weitere Mitarbeitende (E-Mail-Einladung statt manuellem Anlegen im
   Admin).
3. **Passwort-Reset** — aktuell nicht vorhanden, nur `POST /api/auth/token/` mit bekanntem
   Passwort.
4. **Rollenverwaltung im Frontend**, sobald Block 2.1 (rollenbasierte Berechtigungen) steht.

### 4. Produktionsreife & Sicherheit

1. **Postgres statt SQLite**, saubere Settings-Trennung dev/prod, `SECRET_KEY` aus Env-Variable,
   `DEBUG=False`, HTTPS erzwingen (`python manage.py check --deploy` ist aktuell rot, siehe
   `config/settings.py`).
2. **Auth härten**: Token-Ablauf/-Rotation oder Umstieg auf JWT mit Refresh (DRF's
   `TokenAuthentication` gibt aktuell unbegrenzt gültige Tokens aus), Rate-Limiting auf
   `/api/auth/token/`.
3. **Backups & Restore-Prozess** für die Produktivdatenbank (Patientenzusammenhang macht das
   Praxen besonders wichtig, auch wenn diese App selbst keine Patientendaten speichert).
4. **CI-Pipeline**: Tests + Migration-Check vor jedem Deploy (heute nur lokal per
   `python manage.py test` ausführbar).

### 5. Datenschutz (revDSG) & Rechtliches

1. **Auftragsverarbeitungsvertrag (AVV)**-Vorlage für Kund:innen, da Personendaten von
   Mitarbeitenden (inkl. Krankheitsabsenzen — besondere Personendaten nach Art. 5 lit. c revDSG)
   verarbeitet werden.
2. **Datenschutzerklärung, AGB, Impressum**.
3. **Löschkonzept/Aufbewahrungsfristen** klären (u. a. Lohnunterlagen: 10 Jahre nach OR 958f;
   Absenz-/Krankheitsdaten deutlich kürzer aufbewahren).
4. **Hosting-Standort** (Schweiz/EU) festlegen und dokumentieren — für Gesundheitsbetriebe oft
   ein Verkaufsargument bzw. eine Kundenanforderung.
5. **Betroffenenrechte** (Auskunft/Löschung/Berichtigung) technisch umsetzbar machen.

### 6. Abrechnung (falls kommerziell verkauft)

1. Zahlungsanbieter-Integration (z. B. Stripe) für ein Abo pro Praxis/Anzahl aktiver
   Mitarbeitende.
2. Trial-Phase und Plan-/Mitarbeiterlimits pro Tenant.

# Dienstplanungs-SaaS — Backend-Skelett

Django + DRF Skelett gemäss `funktionsumfang-dienstplanung-saas.md` (Abschnitte 1–4, 6, 7, 8, 10).
Multi-Tenancy per `tenant_id` (shared database), getestet inkl. Cross-Tenant-Sicherheitschecks
(`core/tests.py`, `scheduling/tests.py`).

**Zielgruppe**: kleine Kliniken, Arztpraxen und ähnliche Gesundheitsbetriebe in der Schweiz
(typischerweise 5–50 Mitarbeitende, eine bis wenige Stationen/Standorte). Die Regel-Engine soll
langfristig das Schweizer Arbeitsgesetz (ArG) abbilden — aktuell sind Ruhezeit und
Wochenhöchstarbeitszeit als bewusst einfache Platzhalter umgesetzt, siehe
[MVP-Fahrplan](#mvp-fahrplan-bis-zur-marktreife) für das, was für einen echten Praxiseinsatz noch fehlt.

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

- **Regel-Engine** (`ShiftAssignment.clean()`, Abschnitt 4): bewusst einfache Platzhalter statt
  einer vollen, pro Tenant konfigurierbaren Engine — Konstanten `MINIMUM_REST_HOURS` und
  `MAXIMUM_WEEKLY_HOURS` in `scheduling/models.py`. Geprüft werden: Ruhezeit zum Vor-/Folgetag,
  Wochenhöchstarbeitszeit (Mo–So um das Zieldatum), Pflicht-Qualifikation
  (`TimeTemplate.required_skill`) und Kollision mit einer `Absence`. Greift über die API, weil
  `ShiftAssignmentSerializer.validate()` `clean()` aufruft — nicht nur im Admin.

- **Absenzen** (`Absence`, Abschnitt 6): Ferien/Krankheit/Sonstiges pro Mitarbeiter und
  Zeitraum. Blockiert überlappende `ShiftAssignment`s über die Regel-Engine.

- **Diensttausch** (`ShiftTradeRequest`, Abschnitt 7): ein Mitarbeiter bietet eine eigene Schicht
  entweder zur einfachen Übernahme an (`target_assignment` leer) oder als echten Tausch gegen
  eine konkrete Schicht von `target_employee` (`target_assignment` gesetzt). Der eigentliche
  Tausch läuft **nicht** über ein PATCH auf `status`, sondern über die Custom-Actions
  `POST /api/shift-trade-requests/<id>/accept|decline|cancel/` — `accept()` durchläuft dabei
  zwingend die volle Regel-Engine für die resultierende(n) Zuweisung(en) und bleibt bei einem
  Konflikt (z. B. Ruhezeit) auf `pending`, ohne etwas zu ändern. Bewusst kein
  Genehmigungs-Workflow durch Vorgesetzte (siehe [MVP-Fahrplan](#mvp-fahrplan-bis-zur-marktreife),
  Block 2.3).

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

## MVP-Fahrplan bis zur Marktreife

Priorisiert für den Verkauf an kleine Kliniken/Praxen in der Schweiz. Block 1 ist die
fachliche Kernanforderung (ohne die ist das Produkt für Gesundheitsbetriebe nicht seriös
einsetzbar), Blöcke 2–5 sind nötig, damit eine Praxis das Produkt tatsächlich selbständig
nutzen, bezahlen und rechtlich unbedenklich betreiben kann.

### 1. Schweizer Arbeitsgesetz (ArG) — Regel-Engine vervollständigen

Aktuell (`ShiftAssignment.clean()`) geprüft: Ruhezeit (11h, Art. 15a ArG) und eine globale
Wochenhöchstarbeitszeit-Konstante. Für den echten Einsatz fehlen:

1. **Pro Tenant/Branche konfigurierbare Grenzwerte** statt globaler Konstanten
   (`MINIMUM_REST_HOURS`, `MAXIMUM_WEEKLY_HOURS`) — das ArG kennt 45h/Woche (Büro-,
   Gesundheits-, Detailhandelspersonal u. a.) vs. 50h/Woche (übrige Betriebe, Art. 9 ArG); viele
   Kliniken haben zusätzlich einen strengeren GAV (Gesamtarbeitsvertrag, z. B. GAV Santésuisse).
2. **Pausenregelung** (Art. 15 ArG): > 5.5h Arbeit → 15 Min., > 7h → 30 Min., > 9h → 1h Pause,
   automatisch gegen `TimeTemplate.break_minutes` geprüft statt nur erfasst.
3. **Nachtarbeit** (23:00–06:00, Art. 16 ff. ArG): Zeitzuschlag (i. d. R. +10% Zeitgutschrift bei
   regelmässiger/periodischer Nachtarbeit), Hinweis-/Warnpflicht bei Bewilligungsbedarf und
   arbeitsmedizinische Untersuchungspflicht für Mitarbeitende mit regelmässiger Nachtarbeit.
4. **Sonntagsarbeit** (Art. 19/27 ArG): Gesundheitsbetriebe sind von der Bewilligungspflicht
   ausgenommen, benötigen aber einen Ersatzruhetag (Art. 20 ArG) und ggf. einen Lohnzuschlag —
   beides von der Regel-Engine nachverfolgbar machen.
5. **Wöchentlicher freier Tag**: mind. 1 ganzer freier Tag/Woche, davon im Schnitt einmal
   monatlich ein Sonntag (Art. 21 ArG) — bislang nicht geprüft.
6. **Tägliche Höchstarbeitszeit inkl. Pausen** (Art. 10 ArG: Arbeitszeit-„Fenster" max. 14h/Tag).
7. **Überzeitarbeit**: Nachverfolgung + Zuschlag (i. d. R. 25%, Art. 13 ArG), Jahres-/
   Wochengrenzen für Überzeit.
8. **Jugendschutz**, falls Lernende/Auszubildende eingeplant werden (Art. 31 ArG: strengere
   Ruhezeit- und Nachtarbeitsregeln für unter 18-Jährige).
9. **Ist-Arbeitszeiterfassung** (Art. 73 ArGV 1: Pflicht zur Aufzeichnung von Beginn, Ende und
   Pausen der tatsächlich geleisteten Arbeitszeit) — heute bildet die App nur die **Planung**
   (Soll) ab; ein Ist-Erfassungsmodul (Stempeluhr/Self-Service-Korrektur) ist ein separater
   Ausbauschritt, wird aber für Lohnabrechnung und Rechtskonformität benötigt.

### 2. Fehlende Kernfunktionen für den Praxisalltag

1. **Rollenbasierte Berechtigungen durchsetzen**: `Membership.role` (Admin/Planer/Mitarbeiter/HR)
   existiert als Datenmodell, wird von den ViewSets aber noch nicht ausgewertet — aktuell darf
   jeder authentifizierte Tenant-Angehörige das komplette Planblatt bearbeiten.
2. **Self-Service für Mitarbeitende**: eigenen Plan einsehen (mobilfreundlich), Absenzen/Ferien
   nur *beantragen* statt direkt anzulegen.
3. **Genehmigungs-Workflows**: Absenzen (aktuell: sofort wirksam, keine Freigabe durch
   Vorgesetzte) und Diensttausch (aktuell: `accept` direkt durch den Zielmitarbeiter) brauchen
   für den Praxisbetrieb eine Planer-Freigabe-Stufe.
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

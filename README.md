# Dienstplanungs-SaaS — Backend-Skelett

Django + DRF Skelett gemäss `funktionsumfang-dienstplanung-saas.md` (Abschnitte 1–4, 6, 7, 8, 10).
Multi-Tenancy per `tenant_id` (shared database), getestet inkl. Cross-Tenant-Sicherheitschecks
(`core/tests.py`, `scheduling/tests.py`).

**Zielgruppe**: kleine Kliniken, Arztpraxen und ähnliche Gesundheitsbetriebe in der Schweiz
(typischerweise 5–50 Mitarbeitende, eine bis wenige Stationen/Standorte). Die Regel-Engine bildet
bereits mehrere Kernpunkte des Schweizer Arbeitsgesetzes (ArG) ab (siehe nächster Abschnitt,
inkl. einer vereinfachten, rein informativen Ersatzruhetag-Kontrolle) -- was für einen
rechtssicheren Praxiseinsatz noch fehlt, steht im
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
`db.sqlite3` steht trotz `.gitignore`-Eintrag **im Repo** (bereits vor dem `.gitignore`-Eintrag
committet, ein `.gitignore`-Eintrag entfernt kein bereits getracktes File) und enthält echte,
gepflegte Demo-/Testdaten des Tenants "Testheim" (Stationen, Teams, Mitarbeitende, Zuweisungen) --
`migrate`/`createsuperuser` sind für ein frisches Setup trotzdem nötig, überschreiben die
vorhandenen Daten aber nicht. Änderungen an dieser Datenbank (z. B. lokal zurückgesetzte
Passwort-Hashes für Playwright-Tests) sollten vor jedem Commit per `git checkout -- db.sqlite3`
verworfen werden, ausser die Datenänderung ist selbst der beabsichtigte Commit-Inhalt (z. B. eine
Migration mit Backfill oder neue Beispieldaten).

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
  23:00–06:00, Art. 16) und `is_sunday` -- Grundlage für die Zeitgutschrift bei regelmässiger
  Nachtarbeit (`Employee.night_work_summary()`) sowie den Sonntagszuschlag/die
  Ersatzruhetag-Kontrolle (`Employee.weekly_hours_summary()`/`sunday_replacement_rest_missing`),
  siehe [MVP-Fahrplan](#mvp-fahrplan-bis-zur-marktreife), Block 1, Punkte 5/6.
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

  Innerhalb der Kundenrollen sind Admin und Planer für das Tagesgeschäft **funktional identisch**:
  `MANAGER_ROLES = {ADMIN, PLANNER}` behandelt beide bei Node/Skill/Employee/TimeTemplate/
  ShiftAssignment gleich. Bewusst so -- eine strikte Trennung würde dort nur Reibung erzeugen,
  ohne einen echten Interessenkonflikt abzubilden. Für die Tenant-Konfiguration (numerische ArG-/
  Zuschlags-Grenzwerte, MVP-Fahrplan Block 2, Punkt 14) gilt das nicht: die steuert
  Rechtssicherheit und Lohnzuschläge direkt, deshalb dort **Admin-only**
  (`core.permissions.IsTenantAdmin`) -- die einzige Stelle im System, an der sich die beiden
  Rollen tatsächlich unterscheiden.

- **Django Admin (`/admin/`) ist bewusst kein Kundenzugriff, sondern ein Betreiber-Werkzeug**:
  gesteuert über `User.is_staff`/`is_superuser` (Standard-Django), ein von `Membership.Role`
  komplett getrenntes Berechtigungssystem -- ein Kunden-Admin (`Membership.Role.ADMIN`) hat
  dadurch standardmässig **keinen** Zugriff auf `/admin/`, ein Planer ohnehin nicht. `is_staff`/
  `is_superuser` an einen Kunden-Account zu vergeben, würde die gesamte Mandantentrennung
  aushebeln (siehe unten) -- deshalb bleibt `/admin/` ausschliesslich für das Betreiber-Team
  (Entwicklung/Ops). Alles, was ein Kunde selbst konfigurieren soll, muss über die
  tenant-gescopte API/Frontend-Oberfläche laufen (siehe "Einstellungen"-Bereich unten sowie
  MVP-Fahrplan Block 2, Punkt 14 für die Tenant-Konfiguration).

  **Strukturell erzwungen, nicht nur Konvention**: `User.save()`/`Membership.save()`
  (`core.models`) lehnen jede Kombination aus `is_staff`/`is_superuser` und einer Tenant-
  Mitgliedschaft aktiv ab (`ValidationError`) -- egal ob die Membership zuerst existiert und
  danach `is_staff` gesetzt wird, oder umgekehrt. Bewusst in `save()` statt nur in `clean()`:
  Memberships werden im gesamten Code (Tests, künftige Einladungs-/Onboarding-Flows) über
  `Membership.objects.create(...)` angelegt, was `clean()` nicht automatisch aufruft -- nur
  `save()` wird garantiert bei jedem Erstellungsweg durchlaufen. Getestet in
  `core.tests.StaffAccountsCannotHaveMembershipsTests`.

  **Auch für legitime Betreiber-Accounts war der Admin bis vor Kurzem gefährlich**: die
  ModelAdmins (`EmployeeAdmin`, `NodeAdmin`, ...) waren **nicht** tenant-gescoped --
  `list_filter = ["tenant"]` ist nur ein UI-Filter, keine Zugriffsbeschränkung. Ohne aktiven
  Filter zeigte die Changelist alle Tenants gemischt, und FK-/M2M-Dropdowns in Formularen (z. B.
  Node/Skill beim Anlegen eines Employee) liessen sich versehentlich mit dem Datensatz eines
  ANDEREN Tenants verknüpfen -- ein echtes Cross-Tenant-Risiko selbst für sorgfältiges,
  berechtigtes Betreiber-Personal, nicht nur ein hypothetisches Kundenzugriffs-Szenario. Behoben
  durch drei zusammenspielende Teile:
  - `core.middleware.AdminActiveTenantMiddleware` (nur für `/admin/`-Requests): setzt die
    Tenant-ContextVar (`core.context`) anhand eines in der Session gewählten "aktiven Tenants" --
    anders als bei der API (siehe unten, `core/tenancy.py`) ist eine Middleware hier
    unproblematisch, weil `AuthenticationMiddleware` `request.user` für Session-Logins längst
    aufgelöst hat, bevor sie läuft.
  - `core.admin_views.tenant_switch` (`/admin/tenant-switch/`, im Header jeder Admin-Seite
    verlinkt, siehe `templates/admin/base_site.html`): einfache Auswahlseite, setzt/löscht den
    Session-Wert.
  - `core.admin.TenantScopedAdminMixin`, auf jedem tenant-gescopten `ModelAdmin` (inkl.
    `MembershipAdmin`, aber bewusst **nicht** auf `TenantAdmin` selbst -- sonst liesse sich im
    Umschalter nie ein Tenant auswählen): filtert `get_queryset()` **explizit** nach dem aktiven
    Tenant (dasselbe Prinzip wie bei der API -- explizite Filterung ist die Sicherheitsgrenze,
    nicht die ContextVar/der Default-Manager) und liefert ohne aktiven Tenant eine leere Liste
    plus gesperrtes "Hinzufügen", statt wie zuvor implizit alles zu zeigen. `formfield_for_foreignkey`/
    `formfield_for_manytomany` filtern zusätzlich jedes FK-/M2M-Dropdown auf tenant-gescopte
    Modelle explizit nach, statt sich auf `_default_manager` zu verlassen -- wichtig, weil z. B.
    `Node` durch Mehrfacherbung (`MP_Node`, treebeard) einen **anderen** Default-Manager hat, der
    die ContextVar gar nicht auswertet. Das `tenant`-Feld selbst wird beim Anlegen/Bearbeiten auf
    den aktiven Tenant fixiert (Dropdown zeigt nur diesen einen Eintrag).

  Getestet in `core.tests.AdminTenantScopingTests` (Changelist, FK-/M2M-Dropdown-Scoping,
  gesperrtes Hinzufügen ohne aktiven Tenant, `Membership` trotz fehlendem `TenantScopedModel`-Erbe
  korrekt gescopt, `Tenant` selbst bewusst nicht gescopt) und manuell im Browser verifiziert.
  *Ergänzend denkbar, noch nicht umgesetzt*: zusätzliche Netzwerk-Absicherung von `/admin/` selbst
  (z. B. IP-Allowlist fürs Büro-/VPN-Netz, separates Interface) als weitere, infrastrukturelle
  Verteidigungslinie -- siehe MVP-Fahrplan Block 4.

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
- **Zeiterfassung** (Block 1.8/1.9): eigener Tab listet alle vergangenen Schichten des laufenden
  Monats -- Mitarbeitende sehen nur eigene, Admin/Planer alle. Die geplanten Zeiten sind zur
  Korrektur vorausgefüllt; nach dem Speichern zeigt die Zeile Ist-Zeiten, Abweichung in Minuten
  und ggf. einen Hinweis auf zu kurze Pause. Admin/Planer bestätigen erfasste Einträge
  ("Bestätigen"), danach ist der Eintrag für die erfassende Person schreibgeschützt. Bei
  Schichttypen mit Segmenten (Block 1.9) zeigt die Zeile jeden Block einzeln inkl. Start- und
  Ende-Abweichung. Zusätzlich lässt sich die eigene Ist-Zeit direkt im Planblatt erfassen
  (Block 1.10): ein Badge auf der eigenen, vergangenen Schicht-Zelle (fehlend/erfasst/geprüft)
  öffnet denselben Segment-Editor als Popover, ohne in den Tab wechseln zu müssen.
- **Einstellungen** (Block 2.10): eigener Tab, nur für Admin/Planer sichtbar, mit vier Modulen
  (Schichttypen inkl. Segment-Editor, Mitarbeitende inkl. der Wochenstunden-Override-Felder aus
  Block 1.12, Stationen, Skills) -- ersetzt den Django-Admin für den täglichen Selfservice-Betrieb.
  Bleibt auch ohne jede Station erreichbar, damit ein frischer Tenant die erste Station selbst
  anlegen kann.

## MVP-Fahrplan bis zur Marktreife

Priorisiert für den Verkauf an kleine Kliniken/Praxen in der Schweiz. Block 1 ist die
fachliche Kernanforderung (ohne die ist das Produkt für Gesundheitsbetriebe nicht seriös
einsetzbar), Blöcke 2–5 sind nötig, damit eine Praxis das Produkt tatsächlich selbständig
nutzen, bezahlen und rechtlich unbedenklich betreiben kann.

**Statusübersicht** (Orientierung vor dem Lesen der Details unten — jeder Block ist
chronologisch gewachsen, ✅-Einträge und offene Punkte stehen daher an der Stelle, an der sie
entstanden sind, nicht neu sortiert nach Status):

| Block | Thema | Status | Aktuell offen |
|---|---|---|---|
| 1 | Schweizer Arbeitsgesetz (ArG) | ✅ vollständig umgesetzt (17 von 17 Punkten) | — |
| 2 | Kernfunktionen Praxisalltag | ✅ vollständig umgesetzt (39 von 39 Punkten) | — |
| 3 | Onboarding & Self-Signup | ✅ Punkte 1-4 umgesetzt | Punkt 4: Setup-Wizard mit dem Direktanlage-Formular aus Block 2.1 verschmelzen statt separat zu lassen |
| 4 | Produktionsreife & Sicherheit | nichts umgesetzt | kompletter Block (Postgres, Auth-Härtung, CI, Frontend-Tests) |
| 5 | Datenschutz (revDSG) & Rechtliches | ✅ Punkte 1, 2, 3, 5 umgesetzt | Punkt 4: Hosting-Standort ist eine offene Infrastruktur-Entscheidung |
| 6 | Abrechnung (nur falls kommerziell verkauft) | ✅ vollständig umgesetzt | — |
| 7 | Zeitmanagement | ✅ vollständig umgesetzt | — |
| 8 | Öffentliche/Partner-API für externe Integrationen (Lesen + Schreiben) | nichts umgesetzt | kompletter Block (scoped API-Credentials, Rate-Limiting, Idempotency, OpenAPI-Doku, Fehlerformat, Konflikt-sicheres Schreiben, Versionierung, Webhooks) |

Reihenfolge aktuell: Block 1 ist mit Punkt 16 (Lohnfortzahlung Krankheit) inhaltlich fertig, Block 2
Punkt 30/31 (Lohn-Export) ebenfalls (Nutzerentscheid 2026-08: "näher am Verkaufsargument" als der
einfachere Plan-Export aus Punkt 5) -- Punkt 5 danach als nächstes nachgezogen, danach Punkt 20
(Fairness-Punktesystem, bewusst vor Punkt 19 priorisiert, weil Punkt 19 die Fairness-Punkte als
Eingabe nutzen soll), danach Punkt 19 (Automatisierte Planung) selbst umgesetzt. Block 2 ist damit
vollständig abgeschlossen -- zuletzt Punkt 39 (Auffülldienst, baut auf Punkt 19 auf, 39 von 39
Punkten). Block 4/5 (Produktion) bewusst zurückgestellt, bis die Funktionalität steht.

### 1. Schweizer Arbeitsgesetz (ArG) — Regel-Engine vervollständigen

**Bereits umgesetzt** (`scheduling/models.py: ShiftAssignment`, `core/tests.py`,
`scheduling/tests.py`):

1. ✅ **Pro Tenant/Branche konfigurierbare Grenzwerte**: `minimum_rest_hours` (Default 11h,
   Art. 15a ArG), `maximum_weekly_hours` (Default 45h, Art. 9 ArG) und `maximum_daily_span_hours`
   (Default 14h, Art. 10 ArG) sitzen als Felder auf `core.models.Tenant`, im Admin editierbar --
   für Betriebe mit 50h-Regelung oder strengerem GAV (Gesamtarbeitsvertrag, z. B. GAV
   Santésuisse) pro Klinik/Praxis anpassbar. *Noch offen*: `maximum_weekly_hours` ist aktuell ein
   einzelner Wert **pro Tenant** -- in Spitälern gilt aber je nach Personalkategorie oft
   unterschiedliches (z. B. 42h GAV-Normalarbeitszeit für Pflegepersonal, 50h ArG-Höchstgrenze für
   andere Gruppen). Inzwischen gelöst, siehe Punkt 12 (Employee-Override-Felder). *Noch offen*
   (Compliance-Audit 2026-08): der Default-Wert 45h unterstellt, dass Pflegepersonal unter
   "Gesundheits- und Büropersonal" (Art. 9 Abs. 1 lit. a ArG) statt "übrige Betriebe" (50h, lit. b)
   fällt -- das ist plausibel, aber je nach GAV/SECO-Einordnung nicht einheitlich geklärt. Vor
   Produktivbetrieb pro Tenant gegen den konkreten Gesamtarbeitsvertrag verifizieren, nicht
   blind auf dem Default belassen.
2. ✅ **Pausenregelung** (Art. 15 ArG): > 5.5h Netto-Arbeitszeit → 15 Min., > 7h → 30 Min.,
   > 9h → 1h Pause, automatisch gegen `TimeTemplate.break_minutes` geprüft statt nur erfasst.
3. ✅ **Tägliche Höchstarbeitszeit inkl. Pausen** (Art. 10 ArG: Tagesspanne max.
   `maximum_daily_span_hours`, Default 14h).
4. ✅ **Wöchentlicher freier Tag**: mindestens ein ganzer freier Tag pro Kalenderwoche
   (Art. 21 ArG) wird geprüft. *Noch offen*: die zusätzliche Anforderung "im Schnitt einmal
   monatlich ein Sonntag frei" ist nicht automatisiert. *Noch offen* (Compliance-Audit 2026-08):
   geprüft wird nur, dass ein Kalendertag frei von Zuweisungen bleibt -- nicht explizit, dass
   dieser Tag zusammen mit der angrenzenden Tagesruhezeit einen zusammenhängenden 35h-Block
   ergibt (Art. 21 Abs. 1 ArG). Im Normalbetrieb (freier Tag + 11h-Ruhezeit davor/danach aus
   `_check_rest_period`) ist das faktisch praktisch immer erfüllt, aber nicht verifiziert.
5. ✅ **Nachtarbeit** (23:00–06:00, Art. 16 ff. ArG): wird pro Schicht als `night_hours` erkannt
   und über die API ausgegeben (informativ, blockiert nichts). Zusätzlich
   `Employee.night_work_summary(year)` (API: `GET /api/employees/{id}/night-work/?year=YYYY`):
   zählt Nächte mit Nachtarbeit pro Kalenderjahr und wertet sie als "regelmässig" (ArGV 1 Art. 31),
   sobald `Tenant.night_work_regular_threshold_nights` (Default 25) erreicht ist. Für regelmässige
   Nachtarbeiter:innen liefert das eine Zeitgutschrift (Art. 17b ArG, `Tenant.
   night_work_surcharge_pct`, Default 10% der Nachtstunden), einen Bewilligungs-Warnhinweis
   (`permit_warning`, solange `Tenant.night_work_permit_confirmed` nicht gesetzt ist) sowie die
   arbeitsmedizinische Untersuchungspflicht (Art. 17c ArG/Art. 45 ArGV 1: alle 2 Jahre, ab 45
   Jahren jährlich) als `medical_exam_due`, ausgehend von `Employee.
   last_night_work_medical_exam_date`. Rein informativ wie `night_hours` selbst -- kein
   automatischer Eingriff ins Planblatt, kein Rechtsrat (die Bewilligungspflicht selbst kann die
   App nicht prüfen, nur an sie erinnern). *Noch offen*: der 25%-Lohnzuschlag für **gelegentliche**
   (nicht-regelmässige) Nachtarbeit fehlt noch -- siehe Punkt 17 unten.
6. ✅ **Sonntagsarbeit** (Art. 19/27 ArG): wird pro Schicht als `is_sunday` erkannt (Gesundheits-
   betriebe sind von der Bewilligungspflicht ausgenommen). `Employee.weekly_hours_summary()` (API:
   `weekly-overtime`) liefert zusätzlich `sunday_hours`/`sunday_surcharge_hours` (Art. 19 Abs. 3
   ArG, `Tenant.sunday_work_surcharge_pct`, Default 50% -- auf 0 setzen, falls der Betrieb als
   Dauerbetrieb ausgenommen ist). `ShiftAssignment.sunday_replacement_rest_missing` (im
   `shift-assignments`-Endpoint mitgeliefert) markiert eine Sonntagsschicht, für die im
   14-Tage-Fenster danach keine zwei freien Tage liegen -- eine **vereinfachte** Kontrolle des
   Ersatzruhetags (Art. 20 ArG): sie prüft nur die Anzahl freier Tage, nicht die genaue Vorgabe,
   dass der Ersatzruhetag unmittelbar an eine Tagesruhezeit anschliessen und mit ihr zusammen
   mindestens 35 zusammenhängende Stunden ergeben muss. Beides rein informativ, blockiert nichts.
   *Noch offen* (Compliance-Audit 2026-08): zwei weitere Lücken. Erstens, `is_sunday` prüft den
   Kalendertag (`weekday() == 6`), nicht das arbeitsrechtliche Sonntagsfenster Samstag 23:00 bis
   Sonntag 23:00 (Art. 16 analog) -- bei Schichten, die exakt um Mitternacht Sa/So oder So/Mo
   kippen, ein Randfall mit potenziell falscher Zu-/Nichtzuordnung. Zweitens, anders als bei
   Nachtarbeit (`Tenant.night_work_permit_confirmed` + `permit_warning`) gibt es kein eigenes
   Bestätigungsfeld/Warnhinweis dafür, ob die Sonntagsarbeit-Ausnahme für Dauerbetriebe
   (Gesundheits-/Pflegeeinrichtungen, ArGV 2 Art. 4) tatsächlich zutrifft -- ein Feld analog zu
   Punkt 5 (Nachtarbeit-Bewilligung) nachziehen, falls das für andere Branchen als das aktuelle
   Zielsegment relevant wird.
7. ✅ **Jugendschutz** (ArGV 5) für unter 18-Jährige: `Employee.birth_date` (optional) +
   `Employee.is_minor_on(date)`. Für Minderjährige gilt eine erhöhte Mindestruhezeit (12h statt
   der Tenant-Vorgabe) sowie ein hartes Verbot von Nacht- und Sonntagsarbeit. Vereinfachte
   Version ohne die branchenspezifischen Ausnahmetatbestände (z. B. Berufsbildung mit
   Nachtarbeit in bestimmten Branchen) — bei Lernenden im Betrieb empfiehlt sich eine
   arbeitsrechtliche Prüfung der konkreten Ausnahmen.
8. ✅ **Ist-Arbeitszeiterfassung** (Art. 73 ArGV 1: Pflicht zur Aufzeichnung von Beginn, Ende und
   Pausen der tatsächlich geleisteten Arbeitszeit) — neben der **Planung** (Soll) bildet die App
   jetzt auch die **tatsächlich geleistete** Arbeitszeit ab, z. B. Frühdienst geplant
   07:00–16:00, aber tatsächlich erst 07:12 begonnen:
   - **Datenmodell** (`scheduling.models.TimeRecord`): tenant-scoped, `OneToOneField` auf
     `ShiftAssignment` (jede Ist-Erfassung gehört eindeutig zu einer Soll-Schicht), mit
     `actual_start`, `actual_end`, `actual_break_minutes`, `note`, `status`
     (`submitted`/`confirmed`), `recorded_by` (User), `recorded_at`, plus `HistoricalRecords` für
     die Revisionssicherheit.
   - **Self-Service-Eingabe**: Mitarbeitende dürfen Ist-Zeiten nur für die **eigene**
     (`Employee.user`) und bereits **stattgefundene** Schicht erfassen
     (`ShiftAssignment.date <= heute`), durchgesetzt über `core.permissions.TimeRecordPermission`
     nach demselben Muster wie `OwnEmployeeRecordPermission` für Absenzen.
   - **Soll/Ist-Abweichung**: `TimeRecord.deviation_minutes`/`actual_hours` berechnen die
     Differenz zu `TimeTemplate.start_time`/`end_time` (z. B. "+12 Min."); ab der pro Tenant
     konfigurierbaren Toleranz (`Tenant.time_record_deviation_tolerance_minutes`, Default 15 Min.)
     verlangt `TimeRecord.clean()` eine Begründung/Notiz.
   - **Korrektur-/Prüf-Workflow**: analog zu `Absence`/`ShiftTradeRequest` durchlaufen Ist-Einträge
     `submitted` → `confirmed` (`TimeRecord.confirm()`, nur Admin/Planer); solange ein Eintrag
     `submitted` ist, darf die erfassende Person ihn noch korrigieren oder löschen, danach ist er
     für sie schreibgeschützt.
   - **Regel-Engine-Bezug**: `TimeRecord.break_below_minimum` prüft die tatsächliche Pause
     nachträglich gegen Art. 15 ArG und wird als Hinweis angezeigt (rückwirkend kann nichts mehr
     verhindert werden, daher nur Warnung, keine Ablehnung).
   - **API**: `/api/time-records/` (`TimeRecordViewSet`) inkl. `confirm`-Action, Tenant-Scoping
     und Rollenprüfung nach demselben Muster wie die bestehenden Endpunkte.
   - **Frontend**: eigener Tab "Zeiterfassung" (`TimeRecordPanel.jsx`) listet vergangene Schichten
     mit Soll-Zeiten vorausgefüllt zur Korrektur; Mitarbeitende sehen nur eigene Einträge,
     Admin/Planer alle mit "Bestätigen"-Button.

9. ✅ **Segment-basierte Ist-Zeiterfassung** (löst die frühere pauschale `actual_break_minutes`-Zahl
   ab): der Tag lässt sich als mehrere Arbeitsblöcke abbilden (von–bis, von–bis), die Pause ergibt
   sich aus der Lücke dazwischen und ist damit verortet und prüfbar statt nur als Zahl erfasst.
   Wichtig dabei: **nicht** der Mitarbeiter definiert die Blockstruktur pro Schicht neu, sondern
   das `TimeTemplate` gibt sie vor — der Mitarbeiter verschiebt nur die Uhrzeiten der vorgegebenen
   Blöcke (z. B. "07:03 statt 07:00", "Mittagspause wegen Notfallpatient erst 12:23 statt 12:00"),
   nicht deren Anzahl/Reihenfolge.
   - **Datenmodell**: `TimeTemplateSegment` (FK auf `TimeTemplate`, `order`, `start_time`,
     `end_time`) und `TimeRecordSegment` (FK auf `TimeRecord`, `order`, `actual_start`,
     `actual_end`). Ein Template ohne Segmente verhält sich weiterhin wie zuvor (ein Zeitfenster +
     `break_minutes` pauschal, `TimeTemplate.effective_segments()`/`TimeRecord.effective_segments()`
     fallen automatisch darauf zurück) — Segmente sind pro Template opt-in, aktuell im
     Django-Admin gepflegt (Inline an `TimeTemplate`), eine Frontend-Oberfläche folgt in Block 2.9.
   - **Regel-Engine**: `ShiftAssignment._check_break_minutes`/`_shift_hours` rechnen bei
     Segment-Templates mit der Summe der Blockdauern bzw. der Lücke dazwischen statt mit
     `break_minutes`; `TimeRecord.clean()` verlangt exakt so viele Ist-Blöcke wie das Template
     Segmente hat und lehnt unsortierte/überlappende Blöcke ab.
   - **API**: `TimeTemplateSerializer`/`TimeRecordSerializer` schreiben `segments` als
     verschachtelte Liste (bei jeder Änderung vollständig ersetzt); neue Felder
     `end_deviation_minutes`/`break_minutes_total` neben den bestehenden
     `deviation_minutes`/`actual_hours`/`break_below_minimum`.
   - **Frontend**: gemeinsamer `TimeRecordSegmentEditor` (fixe Blockanzahl aus dem Template, kein
     Hinzufügen/Entfernen, automatisch berechnete Pausenanzeige zwischen den Blöcken, live
     Start-/Ende-Abweichung) wird sowohl im Tab "Zeiterfassung" als auch im Planblatt-Grid
     verwendet (siehe Punkt 10).
10. ✅ **Inline-Erfassung im Planblatt-Grid**: der Tab "Zeiterfassung" bleibt als Prüf-/
    Review-Liste für Admin/Planer bestehen (viele Mitarbeitende auf einen Blick), Mitarbeitende
    erfassen ihre Ist-Zeit aber direkt aus der eigenen, vergangenen Schicht-Zelle im Planblatt
    heraus: ein Badge auf der Zelle (fehlend/erfasst/geprüft, farblich unterschieden) öffnet ein
    Popover mit demselben Segment-Editor, vorausgefüllt mit den Soll-Zeiten. Das Popover wird via
    `FloatingPopover` (React-Portal auf `document.body`, an den Viewport geklemmt) ausserhalb des
    Tabellen-Scrollcontainers gerendert -- sonst würde es bei Zellen nahe dem rechten Rand vom
    horizontalen Grid-Scrolling abgeschnitten.

    - ✅ **UX-Bugfix (2026-08): Zeiterfassung-Tab war unübersichtlich, Schichtname fehlte ganz.**
      Nutzer-Feedback: "zu unübersichtlich ... nicht intuitiv aus Sicht Anwender". Jede Zeile
      presste Status-Badge, Name, Datum, Geplant-/Ist-Zeiten, Abweichung und Hinweise in einen
      einzigen, per " · " getrennten Fliesstext -- und `TimeTemplate` wurde zwar geladen
      (`templateFor(a.template)`), aber nirgends angezeigt: welcher Schichttyp (Früh-/Spätdienst
      etc.) überhaupt erfasst wird, war aus der Liste nicht ersichtlich. Behoben durch klare
      Struktur pro Eintrag (`TimeRecordPanel.jsx`): Kopfzeile mit farbigem Schichttyp-Chip (analog
      zum `.shift-chip` im Planblatt) + Name + Datum + Status, darunter je eine beschriftete Zeile
      "Geplant"/"Ist" statt Fliesstext, Hinweise (Notiz, Pause-unter-Minimum-Warnung) in einem
      eigenen, farblich abgesetzten Block darunter. Keine Änderung an Daten/Logik, rein am Markup
      (`.time-record-entry`) und CSS.
11. ✅ **Überzeitarbeit**: Soll/Ist-Vergleich pro Woche + Zuschlag (Art. 13 ArG). Bewusst nicht
    Teil der Regel-Engine selbst (`ShiftAssignment.clean` lehnt nichts deswegen ab), sondern reine
    Auswertung -- `Employee.weekly_hours_summary(reference_date)` normalisiert auf die
    Kalenderwoche (Montag-Sonntag) des übergebenen Datums und liefert Soll (`employment_pct` ×
    Normalarbeitszeit, Default `Tenant.standard_weekly_hours` = 42h für ein 100%-Pensum -- bewusst
    getrennt von `maximum_weekly_hours`, der gesetzlichen Höchstgrenze), Ist (aus `TimeRecord`,
    sobald für eine Schicht erfasst, sonst aus der Planung), Überzeit- und Zuschlagsstunden
    (`Tenant.overtime_surcharge_pct`, Default 25%). API: `GET
    /api/employees/{id}/weekly-overtime/?week=YYYY-MM-DD`, Lesen für alle Rollen offen wie beim
    übrigen Planblatt. Frontend-Anzeige inzwischen vorhanden (als Pro-Schicht-Feedback nach dem
    Speichern einer Zeiterfassung, siehe Block 2.7). *Noch offen*: keine dauerhafte
    Wochenübersicht ausserhalb dieses Feedbacks, keine Jahres-Kumulierung (nur pro Kalenderwoche
    einzeln abrufbar) -- die Monats-Kumulierung liefert inzwischen Block 2.6 (Punkt 13 unten).
    *Noch offen* (Compliance-Audit 2026-08, Begriffsklärung): `overtime_surcharge_pct` ist
    beschriftet als "Art. 13 ArG", berechnet aber Mehrarbeit oberhalb `standard_weekly_hours`
    (Vertragssoll, z. B. 42h) -- das ist begrifflich **Überstunden** nach Art. 321c OR, nicht die
    gesetzliche **Überzeit** nach Art. 12/13 ArG (die erst oberhalb der Wochenhöchstgrenze
    `maximum_weekly_hours`, 45h/50h, beginnt). Rechnerisch unproblematisch -- `standard_weekly_hours
    < maximum_weekly_hours` gilt immer --, aber die Beschriftung im UI/hier sollte präzisiert
    werden, sonst wird ein OR-Anspruch fälschlich als ArG-Pflicht kommuniziert. Zusätzlich: die
    echte, gesetzliche Überzeit oberhalb `maximum_weekly_hours` wird durch
    `_check_maximum_weekly_hours()` faktisch komplett verhindert (harter Block) statt gemäss
    Art. 12 Abs. 1 ArG bis zu einer Jahresobergrenze (170h bzw. 140h) zuzulassen und zu zählen --
    für den heutigen Zweck (harte Ablehnung) unproblematisch, aber falls das künftig per
    Ausnahmebewilligung geöffnet werden soll, fehlt die Jahres-Obergrenzen-Zählung dafür.
12. ✅ **Wochenstunden-Grenzwerte pro Personalkategorie statt nur pro Tenant**: ein einzelner
    Tenant-Wert reicht nicht, wenn z. B. Ärzteschaft vertraglich 50h und Büropersonal 42h hat.
    `Employee.maximum_weekly_hours`/`standard_weekly_hours` sind jetzt optionale
    Override-Felder (`null` = Tenant-Default gilt) -- `ShiftAssignment._check_maximum_weekly_hours`
    und `Employee.weekly_hours_summary` verwenden `self.employee.<feld> or self.tenant.<feld>`.
    Bewusst als Override direkt auf `Employee` (nicht auf `Node`/als eigenes
    "Personalkategorie"-Modell), da die Grenze eine Eigenschaft der einzelnen Anstellung ist.
    Frontend-Oberfläche zum Setzen inzwischen vorhanden (`EmployeeSettings.jsx`, siehe Block 2.10).
    *Noch offen*: gleiche Überlegung gilt potenziell auch für `minimum_rest_hours`, hier aber erst
    nachziehen, falls in der Praxis tatsächlich gebraucht.

13. ✅ **Anschluss der Ist-Arbeitszeiterfassung an Block 2.6** (2026-08): Block 2.6
    (Monatsauswertung, unten) wurde gebaut und nutzt von Anfang an `TimeRecord` statt nur der
    Planung -- siehe dort für Details. Damit ist auch diese Notiz erledigt.

14. ✅ **Aufbewahrung** (2026-08): Ist-Daten (`TimeRecord`) fallen unter dieselbe
    Aufbewahrungspflicht wie Lohnunterlagen (Art. 958f OR, 10 Jahre). Im Löschkonzept (Block 5.3,
    `purge_expired_personal_data`) explizit mitgedacht: `TimeRecord`/`ShiftAssignment` werden bei
    der Anonymisierung eines ausgetretenen `Employee` **nicht** gelöscht, sondern bleiben als
    Buchungsbeleg bestehen.

15. ✅ **Mutterschutz (Art. 35a ArG)** (2026-08): eigenes `Pregnancy`-Ereignismodell (analog
    `Absence`, FK auf `Employee`, beliebig viele Schwangerschaften über die Anstellung hinweg
    möglich statt eines einzelnen `Employee`-Felds) mit `expected_birth_date` und optionalem,
    nachtragbarem `actual_birth_date` (korrigiert die ab der Niederkunft gerechneten Fristen
    rückwirkend, ohne den ursprünglichen Termin zu verlieren -- `history = HistoricalRecords()`).
    `Pregnancy.protection_status_on()`/`Employee.is_maternity_protected_on()` werten die drei
    Fristen aus Art. 35a ArG relativ zum Termin bzw. zur effektiven Geburt aus: `night_ban`
    (8 Wochen vor der Niederkunft, eigene weiter gefasste 20:00–06:00-Nachtdefinition statt
    23:00–06:00, siehe `ShiftAssignment._maternity_night_hours()`), `full_ban` (8 Wochen ab der
    Niederkunft, generelles Beschäftigungsverbot) und `consent_required` (9.–16. Woche danach --
    mangels Einverständnis-Erfassung in der App vorsorglich hart blockiert, analog zur
    vereinfachten Jugendschutz-Prüfung). Neue `ShiftAssignment._check_maternity_protection()` in
    `clean()`, hart durchgesetzt wie `_check_youth_protection()`. Eigene, strengere
    `PregnancyPermission` (`core.permissions`): nur Admin und die betroffene Person selbst dürfen
    lesen/schreiben, nicht Planer:innen allgemein -- auch die Liste ist serverseitig entsprechend
    gefiltert (`PregnancyViewSet.get_queryset`), nicht nur der Einzelzugriff. Frontend: neuer,
    Admin-only sichtbarer Abschnitt "Mutterschutz" in `EmployeeSettings.jsx`
    (`PregnancyEditor.jsx`), unabhängig vom übrigen Formular sofort gespeichert. Kein separater
    Warnhinweis im Planblatt nötig -- die Regel-Engine-Fehlermeldung läuft über denselben
    bestehenden Fehlerbanner wie beim Jugendschutz.

16. ✅ **Lohnfortzahlung bei Krankheit (Art. 324a OR)** (2026-08). Das Gesetz selbst nennt nur
    "eine beschränkte Zeit" — konkretisiert durch drei kantonal unterschiedlich angewendete
    Gerichts-Skalen (Basler/Berner/Zürcher Skala), die selbst nicht kodifiziert sind. Bewusst
    **keine** automatische Ableitung der Skala aus `Tenant.canton` (ursprünglich so geplant,
    siehe Historie unten) — welche Skala kantonal gilt, ist selbst eine Auslegungsfrage der
    Gerichtspraxis, keine 1:1-Zuordnung; der Admin bestätigt die Wahl deshalb explizit
    (`Tenant.sick_pay_scale`), analog zu `night_work_permit_confirmed` (Punkt 5).

    **Realitäts-Check vor der Umsetzung** (Diskussion 2026-08): viele Betriebe versichern die
    Lohnfortzahlungspflicht stattdessen über eine Krankentaggeldversicherung (typischerweise
    80 % Lohn ab Wartefrist) — die Police ersetzt dann die Skala komplett. `Tenant.sick_pay_model`
    (`scale` / `daily_allowance_insurance`) bildet **beide** Varianten ab, nicht nur die Skala;
    bei `daily_allowance_insurance` ist nur eine Wartefrist (`sick_pay_waiting_days`) relevant,
    kein Tage-Anspruch — die App rechnet bewusst kein Taggeld/keine Lohnprozente aus (Grenzziehung
    Zeitmanagement vs. Lohnbuchhaltung, siehe Block 2, Punkt 30/31).

    Neues `AbsenceType.counts_as_sick_leave`-Flag (analog `deducts_vacation_days`, Block 2 Punkt
    24) — nur Absenzen eines so markierten Typs zählen gegen den Anspruch, kein Rückgriff auf den
    Namen (ein Tenant könnte den Typ z. B. "Unfall/Krankheit" nennen). Neue Methode
    `Employee.sick_pay_summary(reference_date)`: Anspruch/Verbrauch beziehen sich auf das
    **laufende Dienstjahr** (12-Monats-Zyklus ab dem Jahrestag von `employment_start_date`, NICHT
    das Kalenderjahr wie bei `vacation_balance()`) — `Employee._current_service_year_window()`
    ermittelt Fensterstart/-ende/Dienstjahr-Nummer analog zu `_age_on()`. Verbrauch wird in
    **Kalendertagen** gezählt statt Mo-Fr-Werktagen wie bei `vacation_balance()`/
    `_count_workdays()` — einmal krank, zählt auch das Wochenende mit; Halbtags-Absenzen zählen
    0.5 Tage (gleiches Muster wie beim Feriensaldo). Rein informativ (wie die
    Nachtarbeit-Bewilligungswarnung) — blockiert keine Absenz.

    Neuer Endpoint `GET /api/employees/<id>/sick-pay/` (`?as_of=YYYY-MM-DD`, Default heute),
    `SickPaySummarySerializer`, für alle Rollen lesbar (`IsTenantManager`, bewusste
    Mitarbeiter-Selbstauskunft wie bei `balance`/`weekly-overtime`). Frontend:
    `AbsenceTypeSettings.jsx` hat eine zweite Checkbox für `counts_as_sick_leave`,
    `TenantSettings.jsx` eine neue Feldgruppe "Lohnfortzahlung bei Krankheit" (Modell/Skala/
    Wartefrist). `AbsencePanel.jsx` lädt den Anspruch des gewählten Mitarbeitenden nur, wenn im
    Formular eine Absenzart mit `counts_as_sick_leave` gewählt ist, und zeigt ihn als Hinweis
    unter dem Formular — bei ausgeschöpftem Anspruch in derselben Warnbox-Optik wie der
    Gleitzeit-Bandbreiten-Hinweis (`.corridor-callout`, Block 2 Punkt 11). 15 neue Backend-Tests
    (Dienstjahr-Berechnung inkl. Jahrestag-Grenzfall, alle drei Skalen, Kalendertag-/Halbtags-
    Zählung, Fenster-Kappung, beide `sick_pay_model`-Varianten, API), volle Suite grün.

17. ✅ **Gelegentliche Nachtarbeit — 25 % Lohnzuschlag (Art. 17b Abs. 2 ArG)** (2026-08,
    Compliance-Audit). Abgedeckt war bisher nur die Zeitgutschrift für **regelmässige**
    Nachtarbeit (Art. 17b Abs. 1, siehe Punkt 5 oben). Wer die Regelmässigkeits-Schwelle
    (`night_work_regular_threshold_nights`) nicht erreicht, hat trotzdem Anspruch auf einen
    **25 % Lohnzuschlag** (Geld, keine Zeitgutschrift) auf die geleisteten Nachtstunden.

    **Wichtige Einschränkung:** die App kennt keinen Stundenlohn/kein Gehalt (bewusst, siehe
    "Grenzziehung Zeitmanagement vs. Lohnbuchhaltung" in Block 2, Punkt 30/31) — sie kann daher
    keinen CHF-Betrag ausrechnen, nur **Stundenzahl + anzuwendenden Prozentsatz** liefern. Das ist
    genau der Rohinput für die künftige Lohnart-Export-Schnittstelle (Block 2, Punkt 30/31) — eine
    dritte Zuschlagskategorie neben Nacht-Zeitgutschrift und Sonntagszuschlag.

    Neues Tenant-Feld `occasional_night_work_surcharge_pct` (Default 25, analog
    `night_work_surcharge_pct`, editierbar in `TenantSettings.jsx` direkt unter der
    Bewilligungs-Checkbox). `Employee.night_work_summary()` liefert neu zusätzlich zwei Felder:
    `occasional_night_hours` (die vollen Nachtstunden des Jahres, aber nur `> 0` wenn
    `not is_regular` -- regelmässig und gelegentlich schliessen sich bewusst gegenseitig aus, nie
    beide gleichzeitig `> 0` für dieselbe Person/Jahr) und `occasional_night_surcharge_pct` (der
    Tenant-Prozentsatz, unverändert durchgereicht, kein Produkt daraus -- die App liefert nur
    Rohinput, keine CHF-Rechnung). `NightWorkSummarySerializer` entsprechend erweitert. 7 neue
    Backend-Tests (Stunden+Prozentsatz bei gelegentlicher Nachtarbeit, konfigurierbarer
    Prozentsatz, gegenseitiger Ausschluss, API-Response), volle Suite grün.

### 2. Fehlende Kernfunktionen für den Praxisalltag

1. ✅ **Rollenbasierte Berechtigungen durchsetzen**: `core.permissions` (`IsTenantManager`,
   `OwnEmployeeRecordPermission`, `ShiftTradeRequestPermission`) wertet `Membership.role`
   jetzt in allen ViewSets aus. Lesen bleibt für alle Rollen offen; Schreiben an
   Stammdaten/Planblatt ist Admin/Planer vorbehalten, HR schreibt nirgends, Mitarbeitende dürfen
   nur eigene Absenzen und eigenen Diensttausch verwalten. Rollen-/Kontenverwaltung im Frontend
   (siehe unten) schliesst die früher hier vermerkte Lücke.
   - ✅ **Mitgliederverwaltung im Frontend** (2026-08): Nutzer-Feedback: *"Ich frage mich halt
     was am intuitivsten und effizientesten ist aus Sicht des Anwenders. Auf Django Admin habe
     sowieso NUR ich als Entwickler Zugriff und sonst niemand."* + *"Ist das state of the art
     mit Mailversand? Stelle mir nur vor im Betrieb, da wird ein Applikationsmanager den
     Benutzer anlegen und nicht per Mail einladen."* -- bewusst **kein** E-Mail-Einladungs-Flow
     (Begründung: Schichtpersonal in der Zielbranche hat oft keine durchgängig gepflegte
     private E-Mail-Adresse; Deputy/When I Work/Planday lösen das in der Praxis genauso).
     Stattdessen: `POST /api/memberships/` (`MembershipCreateSerializer`) legt Username + Rolle
     direkt an, generiert ein Temp-Passwort serverseitig und gibt es **einmalig** in der
     Response zurück (`temporary_password` -- danach nirgends mehr abrufbar, nur als Hash in
     der DB). `User.must_change_password` erzwingt beim ersten Login einen Passwortwechsel
     (`POST /api/me/change-password/`, `ChangePasswordView`) -- das Frontend blockiert dafür
     die gesamte Oberfläche mit `ForcePasswordChangeModal.jsx`, bis erledigt. Rolle bestehender
     Mitgliedschaften ist jetzt ebenfalls per Dropdown änderbar (`PATCH /api/memberships/<id>/`,
     `role`-Feld in `MembershipSerializer` nicht mehr read-only) -- inkl. zweier Schutzmechanismen
     in `MembershipViewSet.perform_update`: mindestens eine Admin-Mitgliedschaft muss je Mandant
     erhalten bleiben, und Admin-Rolle + `scoped_nodes`-Einschränkung schliessen sich weiterhin
     gegenseitig aus. (Die dabei zuerst eingeführte eigene "Mitglieder"-Sektion in
     `MembershipAccessSettings.jsx` wurde direkt im Anschluss wieder aufgelöst, siehe nächster
     Punkt.)
   - Beim Testen aufgefallener, unabhängiger Bug mitgefixt: DRF wrappt einen einzelnen
     String-Wert innerhalb eines `ValidationError`-Dicts NICHT automatisch in eine Liste (nur
     der Top-Level-Fall tut das) -- `api.js` liest Feldfehler aber konsequent als `[0]` (erwartet
     ein Array). Ohne Liste kam im Frontend nur das erste ZEICHEN der Fehlermeldung an. Betraf
     u. a. die neuen Rollenwechsel-Fehlermeldungen und wurde dort korrigiert (`{"field": [...]}`
     statt `{"field": "..."}`); an anderen, älteren Stellen mit demselben Muster (ausserhalb
     des Rahmens dieser Änderung) kann derselbe Effekt weiterhin auftreten.
   - Zusätzlich behoben: ein latenter Datenkonflikt (Django-Admin-Account mit gleichzeitiger
     Tenant-Mitgliedschaft, strukturell eigentlich durch `User.save()`/`Membership.save()`
     ausgeschlossen, aber via Fixture-Laden entstanden) liess `PATCH .../role` mit einem
     nackten HTTP 500 abstürzen, statt eine verständliche Fehlermeldung zu zeigen --
     `MembershipViewSet.perform_update` prüft das jetzt vorab und liefert eine klare 400-Antwort.
   - ✅ **Konsolidierung: ein Ort pro Person statt getrennter Tabs** (2026-08, direkter
     Folgetag): Nutzer-Feedback auf die obige erste Version: *"Hä aber das ist ja null
     intuitiv. Was genau sind 'Mitglieder'? [...] Es gibt nun Tab Mitarbeitende, Tab Mitglieder
     und Zugriff. Das muss doch intuitiver gelöst werden? Es gibt Employees(Scheduling) und
     Memberships(Core) plus dann auch noch Benutzer(Core). Das ganze wirkt völlig
     ineffizient."* -- berechtigter Einwand: das Datenmodell (Employee/User/Membership
     getrennt, aus gutem Grund -- nicht jede Person braucht einen Login) war 1:1 in zwei
     getrennte Tabs durchgereicht, OHNE dass `Employee.user` beim Anlegen über die UI je
     verknüpft wurde. Konzept (vom Nutzer bestätigt: *"Ja bitte! Das ist viel intuitiver!"*,
     inkl. der Ergänzung, die bestehende Stationszuständigkeits-Bearbeitung für Planer/HR ins
     Konzept aufzunehmen):
     - **Ein Formular pro Person** (`EmployeeSettings.jsx`): "Mitarbeiter anlegen/bearbeiten"
       hat jetzt eine "Login-Zugang"-Sektion (Admin-only). Ohne bestehenden Account: Checkbox
       "Zugang aktiv" + Benutzername (automatisch aus Vor-/Nachname vorgeschlagen,
       z. B. "Anna Berger" → `anna.berger`, editierbar -- Nutzer-Feedback: *"warum soll ich
       Freitext-Benutzernamen vergeben?"*) + Rolle. Mit bestehendem Account: Benutzername
       (fix), Rolle-Dropdown (wirkt sofort), und -- die Ergänzung aus der Nachfrage -- bei
       Rolle Planer/HR zusätzlich die Stations-Sichtbarkeits-Auswahl (`scoped_nodes`) direkt
       hier, mit eigenem "Stationssicht speichern"-Button.
     - **Neuer kombinierter Endpoint**: `POST /api/employees/<id>/setup-access/`
       (`EmployeeViewSet.setup_access`, Admin-only) legt User + Membership in einem Zug an und
       verknüpft `employee.user` -- vorher blieb dieses Feld über die API immer leer.
       `EmployeeSerializer` bekam vier neue Read-only-Felder (`username`, `role`,
       `membership_id`, `scoped_nodes`), damit der Account-Status direkt am
       Mitarbeitenden-Datensatz sichtbar ist. Aus Nutzersicht ein Klick/ein Formular; technisch
       zwei Requests bei einer Neuanlage (die Employee-ID wird erst nach dem ersten Request für
       den zweiten gebraucht).
     - **`MembershipAccessSettings.jsx` bleibt nur noch für den seltenen Sonderfall** übrig:
       ein Login-Konto OHNE Mitarbeiterprofil (z. B. externe IT-Administration, nie auf dem
       Dienstplan). Filterkriterium wechselte von "Rolle ist Planer/HR" auf "kein verknüpftes
       Mitarbeiterprofil" (`!employee_name`), Settings-Kachel entsprechend umbenannt zu "Konten
       ohne Mitarbeiterprofil" und ans Ende der Modul-Liste verschoben (kein Alltagsweg mehr).
       Die Stations-Checkbox-Baum-Logik wurde dafür in eine gemeinsame `NodeScopeEditor.jsx`
       (+ `toggleNodeSelection()`) extrahiert, seit beide Orte (Mitarbeitende UND Konten ohne
       Profil) sie jetzt brauchen.
     - Mit Playwright gegen echte Testheim-Daten verifiziert: Neuanlage mit Zugang in einem
       Formular (Benutzername-Vorschlag `tessa.muster` korrekt übernommen), nachträgliche
       Rollenänderung + Stationszuordnung an einer bestehenden Person, und die Bestätigung,
       dass eine Person MIT Mitarbeiterprofil in "Konten ohne Mitarbeiterprofil" nicht mehr
       auftaucht.
   - **Bugfix (2026-08, gefunden beim ersten echten Test durch den Nutzer)**: *"Ich habe mich
     mit marco.bianchi einloggen können [...] alles tiptop. Nach dem Login wurde ich dann
     weitergeleitet auf die 'Einstellungen'-Seite. Das darf nicht passieren auch wenn ich keine
     Schreibrechte habe."* Ursache: `App.jsx` bleibt beim Login-Wechsel gemountet (nur
     `loggedIn` togglet) -- `tab`/`nodeId` aus einer VORHERIGEN Sitzung blieben stehen. Ein
     Admin hatte zuvor "Einstellungen" offen; als danach ein Mitarbeiter einloggte, rendierte
     `tab === "settings"` weiterhin `SettingsPanel`, obwohl der zugehörige Tab-Button für die
     Mitarbeiter-Rolle gar nicht sichtbar ist (das Rendern selbst war nicht rollengeprüft, nur
     der Button). Behoben mit zwei Massnahmen: (1) `LoginForm.onSuccess` setzt `tab`/`nodeId`
     jetzt explizit zurück, jeder frische Login startet auf "Planblatt" mit `nodeId = null`,
     was den bestehenden Default-Auswahl-Effekt zwingt, die (backend-seitig bereits auf die
     eigene(n) Station(en) gescopte, siehe Punkt 51) erste Station neu zu wählen -- Mitarbeiter
     landen so automatisch auf ihrer eigenen Abteilung. (2) Eine `activeTab`-Variable in
     `App.jsx` prüft für JEDEN Render, ob die aktuelle Rolle den `tab`-Wert überhaupt sehen
     darf, und fällt sonst auf "grid" zurück -- Verteidigungslinie, falls `tab` aus einem
     anderen Grund je wieder einen für die Rolle unsichtbaren Wert trägt. Gleichzeitig
     ergänzt: Name der eingeloggten Person oben links neben der Marke (`GET /api/me/` liefert
     jetzt zusätzlich `username` als Fallback für Accounts ohne Mitarbeiterprofil), Nutzer-
     Feedback: *"Zudem sollte oben Links auch noch der Name stehen, damit man weiss wer gerade
     eingeloggt ist."* Mit Playwright verifiziert: Hans (Admin) lässt "Einstellungen" offen und
     loggt sich aus, danach loggt sich Marco Bianchi (Mitarbeiter) ein und landet korrekt auf
     "Planblatt" seiner Station ("Pflege Tag"), der "Einstellungen"-Tab-Button ist nicht
     sichtbar, und "Marco Bianchi" erscheint oben links.
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

   - ✅ **UX-Bugfix (2026-08): Diensttausch-Tab derselbe Fliesstext-Effekt wie der
     Zeiterfassung-Tab** (siehe dort, Block 1.10) -- "X bietet Schicht ... an Y im Tausch gegen
     deren Schicht ..." mit Status-Badge davor als ein einziger Satz war schwer scannbar
     (`TradeRequestPanel.jsx`). Behoben nach demselben Muster: Kopfzeile mit beiden beteiligten
     Personen + Status, darunter je eine beschriftete Zeile "Bietet"/"Gegen" (letztere nur bei
     einem echten Swap, also gesetztem `target_assignment`) mit Schichttyp-Chip + Datum statt
     Fliesstext, Notiz als eigene Zeile darunter.
4. ✅ **Benachrichtigungen** bei neuer Absenz-/Tauschanfrage und deren Genehmigung/Ablehnung, in
   zwei Kanälen:
   - **E-Mail** (`core/notifications.py`): neue Absenz → Admin/Planer; Absenz-Entscheid →
     antragstellende Person; neue Tauschanfrage → Zielperson; Zustimmung der Zielperson →
     Admin/Planer (zur Freigabe); Freigabe/Ablehnung durch Admin/Planer → beide Beteiligten;
     `decline` durch die Zielperson → anbietende Person. Empfänger über
     `Employee.user.email`/`Membership` aufgelöst, still-silent ohne verknüpften Account bzw.
     ohne hinterlegte Adresse -- das ist der Normalfall bei Mitarbeitenden ohne eigenen Login,
     kein Fehler. `EMAIL_BACKEND` ist auf das Console-Backend gesetzt (Mails landen auf stdout,
     kein SMTP-Server im Dev-Setup) -- für einen echten Betrieb muss das durch einen SMTP-Backend
     ersetzt werden (Umgebungsvariablen, siehe Block 4). "Veröffentlichung eines neuen
     Monatsplans" bewusst **nicht** enthalten -- es gibt aktuell keinen "Veröffentlichen"-
     Workflow (jede Zuweisung ist sofort für alle sichtbar), das wäre eine eigene, grössere
     Funktion.
   - **In-App-Indikator**: Zähler-Badges neben "Abwesenheiten"/"Diensttausch"/"Zeiterfassung" im
     Header (`App.jsx`, `.tab-badge`), gespeist aus `task_counts` in `GET /api/me/`
     (`core.views._task_counts`) -- rollenabhängig: Admin/Planer sehen tenant-weite offene
     Genehmigungen (offene Absenzanträge, Tauschanfragen im Status `employee_accepted` --
     tatsächlich freigabebereit, nicht bereits jede offene `pending`-Anfrage, die meist zuerst
     auf die Zielperson wartet --, offene Zeiterfassungen), Mitarbeitende nur eigene, an sie
     adressierte Tauschanfragen (`target_employee` + `pending`). Aktualisiert sich ohne Reload
     über denselben Pub/Sub-Mechanismus wie der Saldo (`api.js: onTasksChanged`/`affectsTasks`,
     analog zu `onBalanceChanged`/`affectsBalance` aus Block 2.7).
   - ✅ **Bugfix (2026-08): Abwesenheiten-Badge blieb dauerhaft hängen**: der Header-Badge zählt
     PENDING-Absenzen tenant-weit (`core.views._task_counts`), `AbsencePanel.jsx` filterte die
     angezeigte Liste aber client-seitig auf die im Topbar aktuell gewählte Station. Eine offene
     Absenz aus einer **anderen** Station war dadurch nirgends sichtbar/genehmigbar -- der Badge
     zeigte unabhängig von der gewählten Station immer "1" und liess sich nie auf 0 bringen. Fix:
     Admin/Planer sehen jetzt wie im Diensttausch-Tab (`TradeRequestPanel.jsx`, der nie
     stationsgescoped war) alle Absenzen tenant-weit, inkl. einer eigenen, unscoped
     Mitarbeitenden-Liste für die Namensauflösung; Mitarbeitende bleiben weiterhin auf die eigene
     Station beschränkt.
5. ✅ **Export (PDF/CSV) des Monatsplans** (2026-08) — für Aushang in der Praxis und Übergabe an
   externe Lohnbuchhaltung, die selten direkt an die API angebunden ist. Bewusst CSV statt Excel
   für die Datei-Übergabe (öffnet sich in Excel genauso, kein neuer Binärformat-Dependency nötig --
   gleiche Entscheidung wie beim Lohn-Export, Punkt 31).

   Neue `PlanExportView` (`scheduling/views.py`, `?node=<id>&month=YYYY-MM&output=pdf|csv`,
   `?output=pdf` ist Default) -- anders als der Lohn-Export (Punkt 31, Admin-only) KEINE eigene
   Rollen-Einschränkung, sondern derselbe Stations-Scope wie das Planblatt selbst
   (`_employee_scoped_node_ids`): Mitarbeitende können den Export ihrer eigenen Station(en) genauso
   ziehen wie Admin/Planer, weil es exakt dieselben Daten sind, die im Planblatt ohnehin sichtbar
   sind. Eine Zeile pro Mitarbeiter (nicht pro Employment/Team wie im interaktiven Planblatt bei
   mehreren Teams, siehe Punkt 17) -- für einen Aushang ist eine flache "wer arbeitet wann"-Liste
   lesbarer als Team-Trennzeilen.

   - **PDF**: `reportlab` (neue, reine Python-Abhängigkeit ohne Systembibliotheken wie
     WeasyPrint/Cairo). Querformat-Tabelle: eine Zeile pro Mitarbeiter, eine Spalte pro Tag,
     Zellinhalt aus `TimeTemplate.icon`/`AbsenceType.icon` (Fallback auf die ersten Buchstaben des
     Namens), mehrere Ereignisse an einem Tag (Split-Shift, Pikett-Zusatz, Absenz) mit "+"
     zusammengefasst. Wochenenden farblich hervorgehoben, zwei Kopfzeilen (Tag/Wochentag) auf jeder
     Seite wiederholt (`repeatRows`).
   - **CSV**: eine Zeile pro Zuweisung/Absenz und Tag (Personalnummer, Name, Datum, Wochentag, Typ,
     Bezeichnung, Von, Bis) -- flach statt als Gitter, damit eine externe Lohnsoftware die Datei
     ohne Weiterverarbeitung einlesen kann.

   Frontend: zwei Buttons ("Als PDF exportieren"/"Als CSV exportieren", `api.downloadPlanExport()`)
   über dem Planblatt-Grid (`PlanGrid.jsx`), für alle Rollen sichtbar (nicht hinter `canManage`
   versteckt). 10 neue Backend-Tests (Pflichtparameter, Format-Validierung, Stations-Scoping für
   Admin/Planer/Mitarbeitende, PDF-Content-Type, CSV-Inhalt inkl. Absenzen), volle Suite grün.

   **Nachbesserung (Nutzer-Feedback):** die Buttons hatten anfangs nur die Browser-Default-
   Darstellung (`.plan-export-bar button` ohne eigenes CSS -- klobiger 3D-Rand, kein
   Border-Radius) und wirkten neben der fein gestylten `PlacementToolbar` direkt darunter
   deplatziert. Jetzt dieselbe Ghost-Pill-Sprache wie `.balance-badge`/`.fairness-badge` (schlanker
   Rand, runde Form, Akzentfarbe erst bei Hover statt dauerhaftem Rahmen-Kontrast).
6. ✅ **Monatsauswertung Soll/Ist-Stunden pro Mitarbeiter** (2026-08, inkl. Nacht-/
   Sonntagszuschläge, Überzeit) als Basis für den Lohnlauf -- bewusst getrennt von Block 1.11
   (strikt wöchentlich für den Art.-13-ArG-Zuschlag) und Block 2.7 (laufender Jahressaldo, für
   alle Rollen offene Mitarbeiter-Selbstauskunft): hier wird über einen Kalendermonat aggregiert,
   nur für Admin/Planer sichtbar.
   - **Backend**: `Employee.monthly_summary(year, month)` (`scheduling/models.py`) -- Soll
     (Tagessoll × Arbeitstage im Monat, abzüglich Feiertage/genehmigter Absenzen, exakt dieselbe
     soll-neutrale Logik wie `time_account_summary()`), Ist (pro Schicht bevorzugt aus
     `TimeRecord`, sonst aus der Planung als Schätzwert -- löst Punkt 13 oben ein), Überzeit +
     Zuschlag (`Tenant.overtime_surcharge_pct`) als einfacher Monats-Soll/Ist-Vergleich (bewusst
     NICHT die Summe der einzelnen `weekly_hours_summary()`-Wochenwerte, da Kalenderwochen selten
     exakt in einen Monat passen und das an den Monatsgrenzen zu Doppel-/Unterzählungen führen
     würde), Nachtstunden + Zeitgutschrift (`Tenant.night_work_surcharge_pct`, nur falls
     `night_work_summary(year)["is_regular"]` fürs ganze Jahr zutrifft -- "regelmässig" bezieht
     sich per Definition aufs Kalenderjahr, nicht auf den einzelnen Monat), Sonntagsstunden +
     Zuschlag (`Tenant.sunday_work_surcharge_pct`). Kein neues Datenbankfeld, keine Migration.
   - **API**: `GET /api/employees/{id}/monthly-summary/?year=YYYY&month=1-12` (Default aktueller
     Monat), `MonthlySummarySerializer`. Anders als `weekly-overtime`/`night-work`/`balance`
     bewusst NICHT für alle Rollen offen -- das hier ist Lohnlauf-Vorbereitung, keine
     Mitarbeiter-Selbstauskunft, daher nur Admin/Planer (403 sonst).
   - **Frontend**: neues Settings-Modul "Monatsauswertung" (`MonthlySummaryPanel.jsx`,
     `managerOnly` in `SettingsPanel.jsx`, analog zum bestehenden `adminOnly`-Muster für
     "Regel-Engine & Zuschläge") -- Mitarbeiter-/Monats-/Jahresauswahl, Kennzahlen-Tabelle,
     "voraussichtlich"-Hinweis bei `is_provisional` (gleiche Formulierung wie `BalanceBadge.jsx`).
   - Getestet: `MonthlySummaryTests` (`scheduling/tests.py`) -- Soll/Ist ohne bzw. mit Schichten,
     Überzeit + Zuschlag, TimeRecord-Vorrang samt `is_provisional`-Flag, Absenz-Soll-Neutralität,
     Eintritt nach Monatsende, Sonntagszuschlag, Nachtzuschlag nur bei jahresweise regelmässiger
     Nachtarbeit, API-Berechtigung (403 für Mitarbeitende-Rolle).
7. ✅ **Arbeitszeitmodell** (Überstunden-Saldo + Ferien) -- **komplett neu gebaut** (2026-08) nach
   Block 7 unten, weil das ursprüngliche Modell "zu schwammig" war (Nutzer-Feedback nach zwei
   vorangegangenen Bugfix-Runden): statt einer einzelnen, unscharf definierten Zahl jetzt zwei klar
   getrennte, kalenderjahresbezogene Kennzahlen, dazu ein echter Feiertagskalender. Bewusst getrennt
   von Block 1.11 (wöchentliche Über-/Unterzeit für den Art.-13-ArG-Zuschlag, unverändert) und Block
   2.6 (Monatsauswertung/Lohnbuchhaltung), weil es hier um die **Mitarbeiter-Selbstauskunft** geht:
   - **Laufender Saldo** (`Employee.time_account_summary()["saldo_hours"]`): `Ist_kumuliert(t) -
     Soll_kumuliert(t)` im laufenden Kalenderjahr (ab `max(1. Januar, employment_start_date)`) bis
     einschliesslich `t` (Default: heute), plus `overtime_balance_carryover_hours` als Startwert.
     `Soll_kumuliert(t)` ist das anteilige Soll bis heute (Anzahl Mo-Fr-Arbeitstage seit
     Jahres-/Anstellungsbeginn × Tagessoll), abzüglich bereits vergangener Feiertage und genehmigter
     Absenzen (Ferien, Krankheit, Sonstiges) -- diese Tage sind **Soll-neutral**, keine "verpasste
     Sollzeit". Klassisches Gleitzeitkonto-Verhalten: sowohl Ist als auch Soll zählen bewusst nur
     bis `t` -- eine künftig eingeplante Schicht wirkt sich erst aus, sobald ihr Datum erreicht ist
     (bewusste Design-Entscheidung nach Rücksprache, siehe unten "Von Cliff-Edges zum
     Gleitzeitkonto").
   - **Jahressoll/Jahresrestsoll** (`annual_target_hours()`/`time_account_summary()
     ["annual_remaining_hours"]`): `Jahressoll = Wochensoll_effektiv × Pensum% × Mo-Fr-Arbeitstage/
     Jahr (ab employment_start_date) - Feiertage_auf_Arbeitstage - voller Ferienanspruch(Std)` --
     ein fixes Jahresziel, unabhängig davon, wann im Jahr die Ferien tatsächlich bezogen werden
     (Ferienanspruch wird bewusst NICHT anteilig für unterjährigen Eintritt gekürzt, bekannte
     Vereinfachung). `Jahresrestsoll = Jahressoll - Ist_kumuliert(t)` -- die Planungsgrösse "wie
     viel ist bis Silvester noch zu leisten".
   - **Feriensaldo** (`Employee.vacation_balance()`, unverändert): Anspruch (`Tenant.
     default_vacation_days_per_year`, Default 20 Tage nach Art. 329a Abs. 1 OR, oder
     `Employee.vacation_days_per_year` als Override) minus genehmigte Ferien-Absenzen im
     jeweiligen Kalenderjahr, gezählt in Mo-Fr-Werktagen (`_count_workdays`, bewusst weiterhin ohne
     Feiertagsabzug -- eine ältere, unveränderte Kennzahl, siehe unten zum neuen Kalender). *Noch
     offen*: kein Übertrag von Resturlaub zwischen Kalenderjahren, siehe Block 5.3 fürs
     Löschkonzept, sobald ein Übertragsmechanismus feststeht. *Noch offen* (Compliance-Audit
     2026-08): Art. 329a Abs. 3 OR verlangt 25 statt 20 Tage (5 statt 4 Wochen) für Mitarbeitende
     bis zum vollendeten 20. Altersjahr. `Employee.vacation_days_per_year` erlaubt das als Override,
     aber es gibt keine automatische Erhöhung/Warnung anhand `Employee.birth_date` (das für die
     Altersberechnung an anderer Stelle, z. B. Jugendschutz, bereits genutzt wird) -- ein Admin
     kann eine junge Person versehentlich mit nur 20 statt 25 Tagen anlegen, ohne Hinweis.
   - **Feiertagskalender** (`Tenant.canton` + `TenantHolidayOverride` + `Tenant.public_holidays()`):
     über die gepflegte Python-Bibliothek `holidays` (vacanza/holidays) statt eigenem Kalender --
     die Schweiz hat nicht nur pro Kanton, sondern in GR/LU/SZ/SO teils sogar pro Gemeinde
     unterschiedliche Feiertage (Patrozinien) inkl. beweglicher Feste (Ostern-Formel), das selbst zu
     pflegen wäre erheblicher Aufwand. Bewusst offline/library-basiert statt einer REST-API (Nager.
     Date, openholidaysapi.org): keine Laufzeit-Netzwerkabhängigkeit für eine Kernberechnung, die
     laufend abgerufen wird, kein zusätzlicher Cache-Layer nötig, passt zum bestehenden
     Testmuster (deterministisch, ohne Netzwerk-Mocking). `TenantHolidayOverride` (ADD/REMOVE, pro
     Tenant max. 1-2 Einträge in der Praxis) deckt die verbleibenden Gemeinde-Sonderfälle ab, die
     die Bibliothek nicht kennt. Verwaltung im Settings-Tab (`TenantSettings.jsx`: Kanton-Dropdown +
     Ausnahme-Liste), API unter `/api/tenant-holiday-overrides/` (Lesen alle Rollen, Schreiben
     Admin-only wie `/api/tenant/`).
   - **Eintrittsdatum** (`Employee.employment_start_date`, neues Pflichtfeld): Startpunkt für das
     Jahressoll -- Wochen davor zählen weder als Soll noch als Ist, auch wenn sie im laufenden
     Kalenderjahr liegen (wichtig für unterjährig Eingestellte). Migration setzt bei Bestandsdaten
     den 1. Januar des Migrations-Jahres als Default, im Settings-Formular editierbar.
   - **API**: `GET /api/employees/{id}/balance/` (`?as_of=`/`?year=`), Lesen für alle Rollen offen
     wie beim übrigen Planblatt. Response-Felder: `saldo_hours`, `plan_saldo_hours` (primäre Anzeige,
     siehe Redesign unten), `annual_target_hours`, `annual_remaining_hours`, `is_provisional`,
     `vacation_*`.
   - **Frontend** (`BalanceBadge.jsx`): Topbar (eigener Account) und Mitarbeitenden-Verwaltung
     (`variant="pill"`) zeigen den Saldo farbcodiert (`--primary`/"blau" bei positiv = vor Plan,
     `--warn`/"rot" bei negativ = hinter Plan, wie im Block-7-Vorschlag gefordert) plus einen
     schmalen Fortschrittsbalken zum Jahressoll (`annual_remaining_hours` wird NICHT als rohe Zahl
     angezeigt -- die wäre v. a. am 1. Januar mit fast dem vollen Jahressoll erschreckend, siehe
     Block 7 unten -- sondern nur als Prozent-Balken plus Kontext im Tooltip). Planblatt-Grid
     (`variant="cell"`, nur Admin/Planer) zeigt weiterhin den kompakten Saldo ohne Balken (zu wenig
     Platz in der Tabellenzeile). `is_provisional` (True, sobald mindestens eine eingerechnete
     Schicht bis `t` nicht auf einer geprüften Zeiterfassung beruht) weiterhin als dezentes `~` vor
     dem Wert. Reaktives Neuladen nach jeder saldorelevanten Mutation via `onBalanceChanged`
     (unverändert aus der vorherigen UX-Nachbesserung).
   - **Bewusst nicht umgesetzt** (Scope-Abgrenzung zu Block 7 unten, für später): ein eigenes
     Personalkategorie-/Vertragstyp-Modell (die bestehenden Employee-Overrides für Wochenstunden/
     Ferienanspruch decken den praktischen Bedarf für den MVP bereits ab, ohne ein neues Modell
     einzuführen); "offizielle Überzeit" mit Schwellenwert + Genehmigungsworkflow (das wäre eine
     eigene Workflow-Funktion, keine reine Rechenkorrektur); eine echte Datums-Prognose ("Jahresziel
     voraussichtlich am 3. Dezember erreicht") -- `plan_saldo_hours` (siehe Redesign unten) sagt zwar
     bereits, OB der aktuelle Plan das Jahresziel erreicht, aber nicht WANN; die Extrapolation selbst
     bliebe ein separates UI-Feature.
   - **Von Cliff-Edges zum Gleitzeitkonto** (Entwicklungsgeschichte, zum Verständnis der jetzigen
     Design-Entscheidungen): das ursprüngliche Modell zählte nur Kalenderwochen mit mindestens einer
     Zuweisung, was zwei Bugs erzeugte -- künftig eingeplante Schichten fehlten komplett im Saldo
     (erster Fix: künftige Wochen sofort einbeziehen), und danach riss eine einzelne neu eingeplante
     Schicht den Saldo um fast einen vollen Wochensoll ein, bis die Woche aufgefüllt war (zweiter
     Fix: Soll anteilig auf verplante Tage). Die Rücksprache über den zweiten Fix führte zur
     grundsätzlichen Frage, ob Zukunft überhaupt sofort zählen soll -- das jetzige Modell beantwortet
     das bewusst mit "nein" (striktes Gleitzeitkonto, siehe "Laufender Saldo" oben), well-defined
     statt der Cliff-Edge-Heuristik der Vorgängerversionen.
   - **Bugfix: Absenz/Zuweisungs-Konflikt** (2026-08, nach Praxistest mit realistischem Szenario --
     42h-Woche, 5 Wochen Ferien, ganzjährig Frühdienst Mo-Fr): eine genehmigte Ferien-Absenz konnte
     bislang parallel zu bereits bestehenden `ShiftAssignment`-Einträgen an denselben Tagen existieren
     (`ShiftAssignment.clean()` prüfte nur die Richtung "neue Zuweisung gegen bestehende Absenz", nie
     umgekehrt). Der Saldo zählte diese Tage dann **doppelt gutgeschrieben**: einerseits als geleistete
     Ist-Zeit (die Zuweisung war ja noch da), andererseits als Soll-neutral (wegen der Absenz) -- ergab
     im gemeldeten Fall einen falschen Saldo von +211.6h statt der korrekten (durch Effekt A, die
     strukturelle Pausenüberzeit, bereits erklärten) rund +143h. Fix in zwei Ebenen:
     - **Prävention**: `Absence.clean()` lehnt jetzt eine APPROVED-Absenz ab, wenn im selben Zeitraum
       noch `ShiftAssignment`-Zuweisungen bestehen ("... zuerst im Planblatt entfernen ..."). Greift
       sowohl bei `AbsenceViewSet.approve()` (Mitarbeiter-Antrag wird genehmigt) als auch beim
       **direkten Anlegen durch Admin/Planer** (die sofort als APPROVED gespeichert werden) --
       `AbsenceSerializer.validate()` nimmt dafür den erst in `perform_create()` gesetzten Status
       vorweg, sonst hätte `clean()` beim Neuanlegen immer noch mit dem Model-Default PENDING geprüft
       und den Konflikt-Check nie ausgelöst (der eigentliche Grund, warum der erste Fix-Versuch beim
       manuellen Nachstellen zunächst nicht griff).
     - **Verteidigung**: `Employee.time_account_summary()` schliesst zusätzlich jede Zuweisung aus, die
       auf einen genehmigten Absenztag fällt (`_approved_absence_dates()`, gemeinsam für Soll-
       Neutralität und Ist-Ausschluss verwendet) -- falls doch einmal ein Konflikt in der Datenbank
       landet (z. B. Altdaten), verfälscht er den Saldo nicht mehr.
   - **Feiertage im Planblatt/Jahresplan sichtbar**: der Feiertagskalender (siehe oben) war zuvor nur
     backend-intern in die Saldo-Berechnung verdrahtet, ohne dass Feiertage in der Planungsoberfläche
     selbst zu erkennen waren. Neuer Endpoint `GET /api/tenant/holidays/?year=` (`TenantHolidaysView`,
     Lesen für alle vier Rollen offen) liefert die aufgelösten Daten inkl. Namen
     (`Tenant.public_holidays_with_names()`). `PlanGrid.jsx` und `YearPlan.jsx` markieren die
     entsprechenden Spalten/Zellen mit einer eigenen `is-holiday`-Klasse (Tooltip zeigt den
     Feiertagsnamen) -- im Planblatt zusätzlich zur bestehenden `is-weekend`-Markierung, im Jahresplan
     als Rahmen um die Tageszelle.
   - **Redesign (2026-08): `plan_saldo_hours` ersetzt `saldo_hours` als primäre Anzeige, nach
     Nutzer-Feedback.** Konkreter Auslöser: eine für ein künftiges Datum eingeplante Schicht änderte
     den (damals einzig angezeigten) `saldo_hours` nicht -- das ist zwar korrektes, gewolltes
     Gleitzeitkonto-Verhalten (siehe "Von Cliff-Edges zum Gleitzeitkonto" oben), aber als *einzige*
     Anzeige irreführend: bei festem Pensum entscheidet der Planer, WANN die Stunden anfallen, nicht
     die Mitarbeitenden. Ein grosser Minus-Wert (z. B. -1200h) bedeutet dort oft nur "die Tage sind
     noch nicht eingetreten", nicht "zu wenig gearbeitet/geplant" -- für Mitarbeitende mit festem
     Vertrag ist das unnötig beängstigend und beantwortet nicht die eigentlich relevante Frage: "wird
     mein Vertragssoll durch den aktuellen Plan erfüllt".
     - **Neue Kennzahl `plan_saldo_hours`**: wie `saldo_hours`, aber `Ist_kumuliert` schliesst
       zusätzlich bereits eingeplante **künftige** Zuweisungen desselben Kalenderjahres mit ein (mit
       den geplanten Template-Stunden, da für sie naturgemäss noch keine Zeiterfassung existieren
       kann), verglichen gegen das **volle** Jahressoll statt nur das anteilige Soll bis heute. Bei
       einem für das ganze Jahr sauber durchgeplanten Pensum liegt der Wert nahe 0 -- unabhängig vom
       aktuellen Datum. `annual_remaining_hours` bekommt dieselbe Erweiterung und bedeutet jetzt "noch
       nicht verplant" (weder geleistet noch bereits eingeteilt) statt nur "noch nicht gearbeitet".
     - **`saldo_hours` (unverändert in der Berechnung) bleibt als Detail-Kennzahl erhalten**, nicht
       mehr als Hauptanzeige: für Lohn-/Überzeit-relevante Auswertungen darf weiterhin nur
       tatsächlich Geleistetes zählen, dafür ist die strenge Zahl weiterhin korrekt und nötig.
     - **`is_provisional`** gilt jetzt für beide Werte gemeinsam und wird durch jede eingerechnete
       künftige Zuweisung ausgelöst (die kann per Definition nie eine geprüfte Zeiterfassung haben) --
       bei einem durchgeplanten Jahr entsprechend fast immer `True`. Das ist beabsichtigt: der
       Planungs-Saldo ist inhärent eine Prognose, kein festgeschriebener Fakt, und soll auch so
       gekennzeichnet sein.
     - **Frontend** (`BalanceBadge.jsx`): `plan_saldo_hours` ist jetzt die grosse, farbcodierte
       Headline-Zahl (Topbar-Pill + Saldo-Spalte im Planblatt), der Fortschrittsbalken zeigt dadurch
       jetzt sinnvollerweise den Planungsfortschritt fürs Jahr statt nur den Arbeitsfortschritt. Die
       strenge `saldo_hours`-Zahl ("Stand heute, ohne Planung") wandert in den Tooltip.
     - Getestet (`scheduling/tests.py`, `EmployeeBalanceTests`): `saldo_hours` bleibt weiterhin strikt
       unverändert durch künftige Zuweisungen; `plan_saldo_hours`/`annual_remaining_hours` reagieren
       korrekt auf eine neu eingeplante künftige Zuweisung desselben Jahres; Zuweisungen ausserhalb
       des betrachteten Kalenderjahres bleiben unberücksichtigt; eine künftige Zuweisung an einem
       genehmigten Absenztag zählt spiegelbildlich zur bestehenden Vergangenheits-Logik nicht als
       geplante Ist-Zeit. Mit Playwright gegen die echten Testheim-Daten verifiziert (Topbar + Saldo-
       Spalte im Planblatt, korrekter Tooltip-Inhalt mit beiden Kennzahlen).
8. ✅ **Diensttausch als echter Swap** auch im Drag & Drop des Planblatt-Grids (2026-08): Ziehen auf
   eine belegte Zelle wurde bisher abgelehnt ("Zielfeld ist bereits belegt"), tauscht jetzt beide
   Zuweisungen. Neue Classmethod `ShiftAssignment.swap(first_id, second_id)`
   (`scheduling/models.py`) tauscht `employee`, `date` **und** `node` als Einheit zwischen den
   beiden Zeilen (nicht nur `employee` -- ein Grid-Drag kann anders als ein
   `ShiftTradeRequest`-Tausch, der bewusst das Datum je Zeile unverändert lässt, beliebige
   Quell-/Zielzellen über Tage UND Team-Zeilen hinweg kombinieren; `node` ergibt sich dabei korrekt
   automatisch, weil eine gezogene Zuweisung immer `node === rowNodeId` ihrer eigenen Zeile hat,
   Punkt 17), `template`/`note`/`history` bleiben bei ihrer bisherigen Zeile. Läuft in
   `transaction.atomic()` mit `select_for_update()` (deterministische Sperrreihenfolge nach
   sortierten IDs, sonst Deadlock-Risiko bei gegenläufig geordneten Swap-Requests) und
   `full_clean()` auf beiden resultierenden Zeilen -- ein Ruhezeit-/Wochenstunden-/
   Qualifikationskonflikt lehnt den gesamten Tausch ab, ohne etwas zu ändern (per Playwright gegen
   die echten Testheim-Daten verifiziert: Sarah Kellers Wochenstunden hätten 45h überschritten,
   Tausch korrekt abgelehnt). Neue Action `POST /api/shift-assignments/swap/` (`detail=False`, kein
   natürliches "Hauptobjekt" bei einem symmetrischen Tausch zwischen zwei Peers), `first`/`second`
   als Assignment-Ids im Body -- Admin/Planer-only wie der Rest des ViewSets. Frontend:
   `PlanGrid.jsx`s `handleMove` löst die Zielzelle jetzt zusätzlich über `node === toRowNodeId` auf
   (nicht nur `employee`+`date`) -- bei Mehrfachanstellung (Punkt 17) kann dieselbe Person am
   selben Tag bereits eine Zuweisung in einem ANDEREN Team haben, die optisch leere Zielzelle wäre
   sonst fälschlich "belegt"; dieselbe Korrektur fixt nebenbei einen latenten Bug beim Verschieben
   in eine leere Zelle eines anderen Teams (`node` wurde dabei bisher nicht mitgepatcht). Kein
   Bestätigungsdialog vor dem Tausch, konsistent mit dem Rest der App (Fehler laufen über das
   bestehende `onError`-Banner). **Nebenbei gefundener und mitbehobener Bug**:
   `ShiftTradeRequest.approve()`s Voll-Swap-Zweig scheiterte bislang an einem falschen
   Unique-Konflikt, sobald beide getauschten Zuweisungen auf demselben Datum lagen (der häufigste
   Tauschfall) -- `full_clean()` sah beim Prüfen der ersten Zuweisung noch den unveränderten
   DB-Stand der zweiten. Behoben mit demselben Muster (`validate_unique=False` + manueller
   Konfliktcheck, der beide beteiligten Zeilen ausschliesst, + ein Zwischenschritt, der die eine
   Zeile kurz auf ein Sentinel-Datum setzt, damit das Speichern der zweiten nicht mit der noch
   nicht aktualisierten ersten kollidiert).
9. ✅ **Mindestbesetzung pro Schicht/Node** (2026-08): neues Feld `TimeTemplate.minimum_staffing`
   (Default 0 = keine Mindestbesetzung definiert, rein additiv). Bewusst **eine** Zahl pro
   `TimeTemplate`, über alle ihre Zuweisungen gezählt (unabhängig davon, ob das Template
   stationsweit oder einem einzelnen Team zugeordnet ist, siehe Nachbesserung unten) -- "ist diese
   Schicht besetzt" ist inhaltlich eine Frage der physischen Abdeckung dieses Schichttyps, nicht der
   administrativen Team-Zugehörigkeit. Brauchen zwei Teams für dasselbe Zeitfenster unterschiedliche
   Minima, deckt das bestehende Modell das bereits ohne neue Tabelle ab: zwei separat benannte
   `TimeTemplate`s für dasselbe Zeitfenster, je mit eigenem `minimum_staffing`. Rein informativ wie
   `night_hours`/`is_sunday` -- **keine** neue Regel-Engine-Prüfung, `ShiftAssignment.clean()`
   bleibt unverändert, eine unterbesetzte Schicht blockiert nichts. **Kein neuer Endpoint**: die
   Auswertung "Ist-Anzahl vs. Minimum pro Tag" passiert rein clientseitig in `PlanGrid.jsx`, weil
   `templates` (schon auf die Station gefiltert) und `assignments` (schon vollständig paginiert für
   Station + alle Team-Kinder) dort ohnehin komplett geladen sind -- ein Backend-Endpoint würde
   nur dasselbe `GROUP BY (date, template)` redundant übers Netz schicken. Anzeige: klickbares
   Warn-Badge in der Tages-Kopfzelle (`FloatingPopover`, dieselbe Komponente wie die Wunsch-/
   Zeiterfassungs-Badges in `ShiftCell.jsx` -- bewusst kein reines `title=`-Tooltip, das wäre
   hover-only, würde mit dem bestehenden Feiertags-Tooltip auf derselben Zelle kollidieren und
   funktioniert nicht auf Touch/Tablet), öffnet eine Liste aller unterbesetzten Schichttypen dieses
   Tages ("Frühschicht: 3/5 besetzt"). Sichtbar für **alle** Rollen, nicht nur Admin/Planer --
   konsistent mit der bestehenden "Mitarbeitende sehen den ganzen Plan"-Transparenz-Doktrin (anders
   als die Saldo-Spalte, die aus Datenschutz-, nicht aus Autoritätsgründen `canManage`-only ist).
   Neues Zahlenfeld in `TimeTemplateSettings.jsx` (gleiches Muster wie das bestehende
   `break_minutes`-Feld) plus Listen-Badge ("· min. 3 Personen"). Bewusst nur im Planblatt
   (`PlanGrid.jsx`), nicht im Jahresplan -- der ist personenweit und lädt keine Zuweisungen anderer
   Mitarbeitender, "wie viele andere sind an dieser Schicht" ist dort ohne zusätzlichen
   Datenabruf nicht beantwortbar.
10. ✅ **"Einstellungen"-Bereich im Frontend für Admin/Planer** (Stammdaten-Selfservice): zuvor
   liessen sich Nodes/Skills/TimeTemplates/Employee-Zusatzfelder nur über den Django-Admin
   pflegen — nicht praktikabel für Kliniken ohne eigene IT-Abteilung, und ein Planer
   (`Membership.Role.PLANNER`) hat ohnehin **keinen** Zugriff auf `/admin/` (das hängt an
   `User.is_staff`/`is_superuser`, einem von `Membership.Role` komplett getrennten
   Berechtigungssystem). Neuer Tab "Einstellungen" (`SettingsPanel.jsx`, nur sichtbar für
   Admin/Planer via `canManageSchedule`) mit Unternavigation für vier Module:
   - **Schichttypen** (`TimeTemplateSettings.jsx`) — Liste + Formular inkl. `TimeTemplateSegment
     Editor.jsx` zum Definieren der Blockstruktur (Block 1.9): im Gegensatz zum
     `TimeRecordSegmentEditor` (Ist-Zeit, fixe Blockanzahl) darf der Planer hier Segmente frei
     hinzufügen/entfernen, weil das die Struktur ist, die spätere Ist-Erfassungen übernehmen.
   - **Mitarbeitende** (`EmployeeSettings.jsx`) — Liste + Formular (Name, Geburtsdatum, Pensum,
     Stationen/Skills-Mehrfachauswahl, aktiv/inaktiv) **inkl. der beiden Wochenstunden-Override-
     Felder aus Block 1.12**, damit ein Planer z. B. für eine neu eingestellte Ärztin direkt 50h
     statt der 42h-Tenant-Vorgabe hinterlegen kann, ohne Admin-Zugriff zu brauchen. Löschen bewusst
     nicht vorgesehen (kaskadiert auf die Planungshistorie) -- Deaktivieren über `is_active`
     stattdessen.
   - **Stationen** (`NodeSettings.jsx`) — Liste (eingerückt nach `depth`) + Anlegen (optional mit
     übergeordneter Station) + Umbenennen + Löschen. Verschieben eines Knotens im Baum (treebeard)
     ist bewusst nicht Teil dieser Oberfläche.
   - **Skills** (`SkillSettings.jsx`) — Liste + Anlegen + Umbenennen + Löschen.
   - Ein frischer Tenant ohne Stationen kann den Tab trotzdem öffnen (`App.jsx` prüft `tab ===
     "settings"` **vor** der `!nodeId`-Sperre), sonst käme niemand an die erste Stationsanlage
     heran.
   - Serverseitig war alles bereits vorbereitet (`NodeViewSet`/`SkillViewSet`/
     `TimeTemplateViewSet`/`EmployeeViewSet` nutzen alle `IsTenantManager`), es fehlte nur die
     Oberfläche -- keine Backend-Änderungen nötig ausser den neuen `api.js`-CRUD-Methoden.
   - *Noch offen*: Überschneidet sich mit Block 3.1 (Setup-Wizard bei Self-Signup) -- der
     Setup-Wizard sollte dieselben Formulare/Komponenten wiederverwenden, sobald er existiert.
11. ✅ **Mehrfachauswahl + Schicht-Stempel im Planblatt-Grid** (inspiriert von Polypoint): zuvor
    liess sich pro Zelle nur einzeln per Dropdown ein Schichttyp zuweisen (plus "Wochenmuster
    kopieren" für den Sonderfall "gleiche Woche wiederholen"). Polypoint markiert stattdessen
    mehrere Tage und weist ihnen mit einem Klick auf das Schichttyp-Icon alle auf einmal zu -- für
    den Alltag eines Planers, der oft denselben Dienst über viele Tage/Mitarbeitende verteilt
    einträgt, deutlich weniger Klicks als N einzelne Dropdown-Interaktionen. Umsetzung:
    "Mehrfachauswahl"-Umschalter über dem Grid (`PlanGrid.jsx`); solange aktiv, markiert ein Klick
    auf eine Zelle sie (visuell hervorgehoben, `ShiftCell.jsx: selectionMode`) statt die
    Dropdown-Zuweisung zu öffnen; sobald mindestens eine Zelle markiert ist, erscheint eine
    Stempel-Leiste mit den Schichttyp-Chips (plus "leeren"), die per Klick alle markierten Zellen
    auf einmal zuweist (`handleStampAssign`, Regel-Engine-Konflikte werden wie beim
    Wochenmuster-Kopieren pro Zelle übersprungen und summarisch gemeldet). Der bestehende
    Einzel-Dropdown bleibt für gezielte Korrekturen einer einzelnen Zelle erhalten -- Ergänzung,
    kein Ersatz.
    - ✅ **Bugfix (2026-08)**: Nutzer-Feedback ("Ich kann im Planblatt keine Ferien eintragen, im
      Jahresplan jedoch schon") -- die Mehrfachauswahl-Stempelleiste im Planblatt kannte tatsächlich
      nur Schichttyp-Chips, keine Absenz-Chips, obwohl der Jahresplan (Punkt 12) diese Kombination
      von Anfang an hatte. `PlanGrid.jsx` bekommt jetzt dieselben Absenz-Chips
      "Ferien"/"Krankheit"/"Sonstiges" + "Absenz entfernen" wie `YearPlan.jsx`
      (`handleStampAbsence`/`handleRemoveAbsences`, gruppiert nach `employeeId`, weil im Planblatt
      -- anders als im Jahresplan -- Zellen mehrerer Mitarbeiter gleichzeitig markiert sein können).
      Zweiter, tiefer liegender Fund dabei: `ShiftCell.jsx` gab für einen Tag mit bestehender Absenz
      unabhängig vom Modus immer nur eine statische, nicht klickbare `<span>` zurück (der
      `if (absence)`-Zweig stand vor der `selectionMode`-Prüfung) -- ein Absenz-Tag liess sich
      dadurch **nie** markieren, in keinem Kontext. Für reines Schicht-Stempeln war das durchaus so
      gedacht (die Regel-Engine hätte die Zuweisung ohnehin abgelehnt), aber es
      blockierte damit auch "Absenz entfernen" komplett: ein bereits eingetragener Ferientag liess
      sich über die Stempelleiste nie wieder auswählen, um ihn zu löschen. Fix: `selectionMode`
      wird jetzt zuerst geprüft; ein Absenz-Tag bleibt markierbar und zeigt dabei weiterhin seinen
      FER/KRA/SON-Chip statt eines Schichttyps. Verifiziert per Playwright gegen die echten
      Testheim-Daten: Ferien für einen Mitarbeiter über zwei nicht zusammenhängende Tage per
      Stempelleiste eingetragen (zwei separate `Absence`-Datensätze statt einem), danach beide
      wieder über "Absenz entfernen" markiert und gelöscht.
    - ✅ **Mehrzeilige Stempelleiste + Spezialitäten (z. B. Pikettdienst)** (2026-08, weiteres
      Nutzer-Feedback): die Stempelleiste sass als eine einzige lange, umbrechende Zeile aus
      Dienst- und Absenz-Chips direkt neben dem "Mehrfachauswahl"-Umschalter -- auf Wunsch jetzt
      als eigener Block **unterhalb** des Umschalters (`styles.css`:
      `.multi-select-toolbar > .stamp-palette` bekommt `flex-basis: 100%`, analog zur
      Jahresplan-Lösung unten), und **mehrzeilig**: eine Zeile "Dienste", eine Zeile
      "Abwesenheiten" und -- falls konfiguriert -- eine dritte Zeile "Spezialitäten" (neue
      `.stamp-row`/`.stamp-row-label`-Klassen). Dafür neues Feld `TimeTemplate.category`
      (`shift`/`special`, Migration `0016`, Default `shift` -- rein additiv) in
      `TimeTemplateSettings.jsx` editierbar; ein Schichttyp wie "Pikettdienst" wird dort als
      "Spezialität" markiert und erscheint dann in beiden Stempelleisten (Planblatt + Jahresplan,
      Punkt 12) in der eigenen Zeile, **ohne** aus den regulären Diensten technisch etwas anderes
      zu sein -- Zuweisung, Regel-Engine-Prüfung und `handleStampAssign` bleiben identisch, nur
      die Anzeige-Gruppierung unterscheidet sich. Die Spezialitäten-Zeile erscheint nur, wenn
      mindestens ein Schichttyp so kategorisiert ist (sonst keine leere Zeile). Getestet
      (`scheduling/tests.py`: `TimeTemplateCategoryTests`, `TimeTemplateCategoryAPITests` --
      Default `shift`, Spezialität blockiert `ShiftAssignment.clean()` nicht, Serializer-Roundtrip).
      Mit Playwright verifiziert: testweise "Pikettdienst" als Spezialität angelegt, erscheint in
      eigener Zeile in Planblatt und Jahresplan, Stempeln funktioniert (inkl. korrekt vom Backend
      abgelehntem Ruhezeit-Konflikt bei einer unrealistisch langen Testschicht).
    - ✅ **Bugfix (2026-08)**: Nutzer-Feedback -- die drei Stempelleisten-Zeilen erschienen erst,
      sobald der erste Tag markiert wurde, wodurch das ganze Planblatt/der Jahresplan-Kalender genau
      in dem Moment nach unten sprang, in dem der Nutzer den ersten Tag anklickte -- ein
      nachfolgender Klick/Zug traf dadurch die falsche, jetzt verschobene Zelle. Fix: Die
      Stempelleiste (`.stamp-palette`) ist jetzt **immer** sichtbar, sobald die Mehrfachauswahl
      aktiv ist (Planblatt) bzw. immer im Jahresplan -- ihre Chips/Buttons sind nur `disabled`, bis
      mindestens ein Tag markiert ist, reservieren den Platz aber von Anfang an. `stampTemplates`
      (`PlanGrid.jsx`) zeigt vor jeder Markierung bereits die stationsweiten (geteilten) Vorlagen
      statt `[]`, damit auch die "Dienste"-Zeile nicht nachträglich wächst. Per Playwright verifiziert
      (Bounding-Box-Vergleich): Tabellen-Position verschiebt sich beim Umschalten auf Mehrfachauswahl
      genau einmal (vorhersagbar), danach beim Markieren des ersten Tages um 0px.
    - ✅ **Absenzarten sind jetzt ein tenant-eigener Katalog** (2026-08, weiteres Nutzer-Feedback):
      Ferien/Krankheit/Sonstiges waren als `Absence.Type`-`TextChoices` mit genau drei festen Werten
      hartcodiert -- der Nutzer wollte eigene Absenzarten erfassen können (z. B. "Militärdienst"),
      analog zu Schichttypen. Neues Modell `AbsenceType(TenantScopedModel)` (Felder `name`, `color`,
      `deducts_vacation_days`), `Absence.type` von `CharField(choices=...)` zu
      `ForeignKey(AbsenceType, on_delete=PROTECT)` umgestellt -- per dreistufiger, handgeschriebener
      Migration (`0017`-`0019`: Tabelle anlegen, nullable FK + Datenmigration mit Seed dreier
      Standard-Absenzarten pro Tenant mit bestehenden Absenzen inkl. `HistoricalAbsence`-Backfill,
      alte Spalte entfernen/umbenennen/non-nullable machen) statt der von `makemigrations`
      vorgeschlagenen direkten `AlterField`, die die bestehenden String-Werte ("vacation" etc.) beim
      Spaltentyp-Wechsel verloren hätte. `Employee.vacation_balance()` filtert jetzt auf
      `type__deducts_vacation_days=True` statt auf den hartcodierten `Type.VACATION`-Wert -- die
      einzige Stelle mit echter Geschäftslogik auf dem Absenztyp (Regel-Engine, Notifications,
      `task_counts` werten nur `status` aus, nicht `type`). Neuer Endpoint `/api/absence-types/`
      (`AbsenceTypeViewSet`, gleiche Berechtigung wie Schichttypen: Lesen für alle, Schreiben nur
      Admin/Planer) + neues Settings-Modul `AbsenceTypeSettings.jsx` (Name/Farbe/Checkbox "zieht
      Ferientage ab"). Die hartcodierten `ABSENCE_TYPES`/`ABSENCE_TYPE_LABELS`/`TYPE_OPTIONS`-
      Konstanten und `type-badge--vacation/--sick/--other`-CSS-Klassen sind aus allen fünf
      betroffenen Komponenten (`PlanGrid.jsx`, `YearPlan.jsx`, `AbsencePanel.jsx`, `Dashboard.jsx`,
      `ShiftCell.jsx`) entfernt -- Absenzarten werden jetzt überall per `api.getAbsenceTypes()`
      geladen und Farben über das bereits etablierte `--chip-color`-Custom-Property-Muster
      dargestellt (analog zu `TimeTemplate.color`), funktioniert automatisch für beliebig viele neue
      Absenzarten. Getestet (`AbsenceTypeTests`, `AbsenceTypeAPITests`: Defaults, Tenant-Isolation,
      Serializer-Roundtrip, Berechtigungen) sowie alle bestehenden Tests auf die neue FK umgestellt
      (335 Tests grün). Mit Playwright gegen die echten Testheim-Daten verifiziert: Absenzarten-
      Verwaltung, Stempelleiste (Planblatt + Jahresplan), Abwesenheiten-Formular-Dropdown und
      Dashboard-Badge zeigen alle korrekt die geladenen Typen samt Farbe/Kurzcode.
    - ✅ **Spezialitäten (z. B. Pikettdienst) sind jetzt ein additiver Zusatz statt Slot-Konkurrenz +
      Absenzen im Einzelzell-Dropdown** (2026-08, weiteres Nutzer-Feedback): eine Spezialität war
      technisch ein normaler Schichttyp und konkurrierte daher um einen der zwei Zuweisungs-Slots
      einer Zelle -- ein Frühdienst UND gleichzeitig Pikettdienst am selben Tag war so nicht
      abbildbar. Regel-Engine-seitig bereits gelöst (siehe oben, "Pikettdienst"-Bugfix): Backend
      erlaubt beliebig viele Zuweisungen pro Tag, solange sie unterschiedliche Vorlagen haben
      (`unique_together = (employee, date, template)`) und `category="special"` ist von Ruhezeit-/
      Höchstarbeitszeit-/Überlappungs-Checks sowie Soll-/Ist-Stunden ausgenommen. Frontend jetzt
      nachgezogen: `PlanGrid.jsx`/`YearPlan.jsx` splitten die Zuweisungen eines Tages in
      `regularAssignments` (weiterhin Slot 0/1) und `specialAssignments` (neu, additiv, beliebig
      viele). `ShiftCell.jsx` bekommt dafür ein neues Badge+Popover (analog zum bestehenden Wunsch-/
      Ist-Zeit-Badge-Muster dieser Komponente) -- zeigt die Anzahl aktiver Spezialitäten, Klick öffnet
      eine Liste mit ×-Button zum Entfernen sowie Chips für noch nicht hinzugefügte Spezialität-
      Vorlagen zum Hinzufügen; neue Callbacks `onAddSpecial`/`onRemoveSpecial` in `PlanGrid.jsx` sind
      dünne Wrapper um die bestehenden `create`/`deleteShiftAssignment`-Endpunkte (kein neuer
      Endpoint nötig). Die Mehrfachauswahl-Stempelleiste (`handleStampAssign`/`handleStampShift`)
      erkennt `template.category === "special"` und legt für diese Chips ab jetzt IMMER eine additive
      neue Zuweisung an statt fälschlich um Slot 0/1 zu konkurrieren; "— leer —"/"Schicht leeren"
      bleibt bewusst auf Slot 0/1 beschränkt und rührt Spezialitäten nicht an. `YearPlan.jsx` zeigt
      zusätzlich einen kleinen Punkt an der Tageszelle, sobald eine Spezialität besteht (rein
      informativ, kein Popover -- der Jahresplan hat ausserhalb der Mehrfachauswahl kein
      Einzeltag-Bearbeitungs-UI). Gleichzeitig löst das erweiterte Einzelzell-Dropdown in
      `ShiftCell.jsx` das dritte Nutzer-Feedback: eine neue `<optgroup label="Abwesenheit">` mit den
      geladenen Absenzarten steht neben den (jetzt auf `category !== "special"` gefilterten)
      Schichttyp-Optionen zur Auswahl -- ein neuer `onAssignAbsence`-Callback legt eine Ein-Tages-
      `Absence` an und löscht dabei zuerst eine ggf. vorhandene Zuweisung in diesem Slot. Mit
      Playwright gegen die echten Testheim-Daten verifiziert (danach wieder bereinigt): Pikettdienst
      per Popover zu einem Tag mit bestehendem Küchendienst hinzugefügt -- beide gleichzeitig
      sichtbar, Saldo unverändert; dieselbe additive Zuweisung auch über die Mehrfachauswahl-
      Stempelleiste auf einen bereits belegten Tag gestempelt, ohne den bestehenden Dienst zu
      verdrängen; Absenz über den Einzelzell-Dropdown gewählt, ersetzt den Slot korrekt. Ein Bugfix
      dabei: der Spezialitäten-Badge erschien anfangs auf BEIDEN Slots einer Zelle (weil
      `assignableSpecialTemplates` beiden Slots gleichermassen übergeben wurde) -- jetzt nur auf
      Slot 0, analog zu `showWishBadge`.
    - ✅ **Nachbesserung (2026-08): Spezialitäten waren bei Split-Shifts faktisch unsichtbar,
      Zeilen zu hoch** -- Nutzer-Feedback direkt nach dem Rollout des additiven Spezialitäten-Badges
      (oben): das Zähler-Badge sass in der Ecke der jeweiligen `ShiftCell`, und Vormittag/Nachmittag
      wurden als zwei ganze `ShiftCell`-Blöcke UNTEREINANDER gestapelt (je 40px hoch) -- ein Pikett
      war dadurch bei einem Split-Shift-Tag optisch nicht mehr auffindbar, und jede Zeile mit
      Split-Shift wurde unnötig hoch. Fix nach dem Vorbild von PEP: `PlanGrid.jsx` rendert die
      (bis zu zwei) Dienst-Slots jetzt in einer neuen `.shift-slots-row` NEBENEINANDER (gleiche
      Höhe statt gestapelt) statt als vertikalen Stack, und Spezialitäten in einer eigenen, sehr
      dünnen Zeile darunter (`SpecialStrip.jsx`, neue eigenständige Komponente statt
      Badge+Popover in `ShiftCell.jsx` -- rein tagesbezogen, nicht pro Slot, daher ausgelagert).
      Aktive Spezialitäten sind darin als kleine Chips IMMER sichtbar (mit ×-Button zum Entfernen)
      statt hinter einem anklickbaren Zähler versteckt; ein "+"-Chip öffnet weiterhin ein Popover
      zum Hinzufügen. `.shift-chip`-Schriftgrösse im Planblatt-Grid auf 10px reduziert (gescoped auf
      `.plan-grid .shift-chip`, damit Dashboard/Settings-Listen/TradeRequestPanel unverändert
      bleiben) und `.shift-chip-btn`/`.cell-select`-Höhe von 40px auf 30px verkleinert -- Zeilen
      sind dadurch insgesamt deutlich kompakter. Mit Playwright verifiziert: ein Tag mit zwei
      unterschiedlichen Diensten UND einer Spezialität zeigt Vormittag/Nachmittag nebeneinander,
      die Spezialität als eigene dünne Zeile darunter, auch bei einem nur teilweise gefüllten
      zweiten Slot.
    - ✅ **Planblatt im Polypoint-Stil (2026-08)**: Nutzer-Feedback nach einem Referenzbild von
      Polypoint/PEP -- "Ich will es genau so lösen wie polypoint, jedoch optisch moderner". Ersetzt
      das bisherige Klick-auf-Zelle-Dropdown komplett durch eine immer sichtbare Icon-Toolbar
      (`PlacementToolbar.jsx`) oberhalb des Grids: eine Modus-Auswahl **Ganz/Links/Rechts/Pikett**
      plus eine Palette anklickbarer Dienst-/Absenz-/Spezialität-Icons (`chipGlyph()`, neuer Helper
      -- nutzt `TimeTemplate.icon`/`AbsenceType.icon`, ein bisher ungenutztes Feld, mit Fallback auf
      die ersten zwei Buchstaben des Namens, statt der vorherigen 3-Buchstaben-Abkürzung).
      Workflow: Icon anklicken ("bewaffnet" das Werkzeug, sichtbar am farbigen Rahmen), dann eine
      Zielzelle anklicken -- sofortige Zuweisung, kein Dropdown mehr. `AbsenceType` bekam dafür ein
      neues `icon`-Feld (additive Migration, analog `TimeTemplate.icon`). Zweite Kernänderung: die
      Tagesspalten sind jetzt via `table-layout: fixed` unveränderlich breit -- zwei Dienste teilen
      sich per CSS-Grid (`.day-cell-slots`, zwei gleich grosse Spalten) die Breite EINER Zelle
      (Links/Rechts-Split, manuell gewählt), statt dass die Spalte pro Split-Shift-Tag breiter wird;
      ein einzelner Dienst spannt beide Hälften (`.cell-wrap--span`). `handleCellClick()` in
      `PlanGrid.jsx` orchestriert reine Frontend-Verdrahtung bereits vorhandener Mutationen
      (`handleAssign`, `handleAssignAbsence`, `handleAddSpecial`, `handleRemoveSpecial`, neu:
      `handleRemoveAbsence`) -- keine neuen Backend-Endpunkte. `ShiftCell.jsx` verloren: das
      Dropdown, den `editing`-State und die Props `assignableTemplates`/`onChange`/
      `onAssignAbsence`; ein Klick ruft stattdessen `onCellClick()` auf, auch auf einer
      Absenz-Zelle (vorher nicht klickbar). `SpecialStrip.jsx` verlor sein eigenes
      "+"-Add-Popover (Hinzufügen läuft jetzt zentral über die Toolbar im Pikett-Modus) und ist
      jetzt reine Anzeige- + Entfernen-Komponente. Drag & Drop, Tausch-Angebot, Wunsch-/
      Ist-Zeit-Badges, die separate Mehrfachauswahl-Stempelleiste (bulk) und `YearPlan.jsx` bleiben
      unverändert. Mit Playwright gegen echte Testheim-Daten verifiziert (danach wieder bereinigt):
      Ganz-Platzierung, Rechts-Platzierung eines zweiten Diensts (Split ohne Breiten-Sprung),
      Pikett-Platzierung additiv neben bestehenden Diensten, Absenz-Platzierung ersetzt Dienste UND
      Spezialitäten desselben Tages, Radiergummi leert gezielt. Zwei Bugfixes dabei: (1) die
      Icon-Palette zeigte anfangs nur stationsweite Schichttypen (`t.node === stationId`) und blieb
      bei rein teamspezifischen Katalogen komplett leer -- jetzt der volle, für die Ansicht bereits
      geladene Katalog; (2) der `.btn-offer-trade`-Button (Diensttausch anbieten, oben rechts in der
      Zelle) war mit 16x16px in der jetzt nur noch ~21px schmalen Zellhälfte gross genug, um Klicks
      der Icon-Toolbar abzufangen, bevor sie den Chip darunter erreichten -- auf 10x10px verkleinert.
    - ✅ **Planblatt-Nachbesserung: diagonaler Split + randloser Einzeldienst (2026-08)**:
      mehrfaches, konkretes Nutzer-Feedback anhand von Screenshots/Fotos zum Polypoint-Redesign
      oben. Der ursprüngliche Links/Rechts-Spaltensplit liess bei zwei Diensten ohne gesetztes
      Icon die Zwei-Buchstaben-Glyphen sichtbar ineinanderlaufen -- durch einen diagonalen Split
      ersetzt (`.day-cell-slots--diagonal`, `clip-path: polygon(...)`, wie im Jahresplan bei
      `.year-day-fill--shift.is-split`): jedes Dreieck trägt die volle Dienstfarbe, der Glyph sitzt
      gross in der freien Ecke -- kein blasser Pill-Chip mehr. Ein einzelner Dienst ("Alles") zeigt
      keinen Diagonal-Split mehr (nicht nötig bei nur einem Dienst) und spannt stattdessen
      randlos die volle Zelle. Zwei Bugfixes dabei, beide durch Playwright-`getComputedStyle`-
      Inspektion statt nur visueller Screenshots gefunden: (1) mehrere Stellen, an denen die
      CSS-Basisregel `.cell-wrap { position: relative }` ungewollt auch ShiftCell.jsx's eigenes
      inneres `<span className="cell-wrap">` traf und so den falschen Containing Block für
      `position: absolute`-Kinder herstellte -- gezielt auf `position: static` zurückgesetzt, nur
      für die Modifier-Klassen gescoped; (2) der Einzeldienst füllte zwar seine eigene 34px-Box
      randlos aus, aber `.day-cell` selbst war nur so hoch wie sein eigener Inhalt
      (shrink-to-fit) -- sobald eine ANDERE Tageszelle in derselben Tabellenzeile höher war (z. B.
      eine sichtbare `.special-strip` an einem Nachbartag), liess der Browser den Chip mit
      sichtbarem Weissraum oben/unten in der dadurch höheren `<td>` zentriert (`vertical-align:
      middle`, Standardverhalten von `<td>`) statt sie auszufüllen -- vom Nutzer per Foto belegt.
      Gefixt mit dem etablierten `height: 1px` + `height: 100%`-Trick (`<td>` bekommt ein
      explizites, beliebig kleines `height`, das Prozent-Höhen im Kind freischaltet, ohne die
      inhaltsgetriebene Zeilenhöhen-Berechnung der Tabelle selbst zu verändern) -- `.day-cell`
      füllt jetzt immer die tatsächliche, ggf. gestreckte Zeilenhöhe, `.day-cell-slots` wächst als
      Flex-Kind mit Mindesthöhe (`flex: 1 0 34px`) mit, während `.special-strip` ihre natürliche
      Höhe behält. Mit Playwright verifiziert (Messung der Pixel-Lücke zwischen Chip- und
      Zellrand, nicht nur Screenshot-Vergleich): ein Einzeldienst-Tag neben einem Tag mit
      sichtbarer Spezialität in derselben Zeile füllt die dadurch gestreckte Zeile jetzt
      lückenlos aus; diagonaler Split und Klick-Routing (inkl. Klick exakt in die Dreiecksecke)
      weiterhin unverändert korrekt.
    - ✅ **Planblatt-Nachbesserung, Teil 2: randlose Breite (2026-08)**: erneutes Nutzer-Feedback
      per Foto -- auch nach der Höhen-Korrektur oben blieben bei sowohl leeren ("+") als auch
      belegten Einzeldienst-Zellen sichtbare Ränder links/rechts (und minimal oben/unten). Ursache
      war eine ZWEITE, unabhängige Lücke im selben Bereich: `.day-cell-slots .cell-wrap--span
      .shift-chip` setzte `width: 100%; height: 100%` -- aber `.shift-chip` ist ein Flex-Item
      innerhalb von `.shift-chip-btn` (`display: flex`), und dessen Flex-Basis (aus der
      `width`-Eigenschaft über den Default `flex-basis: auto` abgeleitet) löste die
      Prozent-Breite NICHT zuverlässig auf, weil `.shift-chip-btn` selbst kein literales `width`
      trägt, sondern nur über `position: absolute; inset: 0` auf seine Grösse "gestreckt" wird --
      Chromium behandelt das für die Flex-Basis-Auflösung anders als eine gewöhnliche
      Block-Prozent-Breite, der Chip fiel dadurch auf seine Inhaltsgrösse (Glyph + Padding)
      zurück. Per Playwright-`getComputedStyle`/`matches()`-Inspektion aller kaskadierenden Regeln
      bestätigt (nicht nur vermutet): drei Regeln trafen auf denselben `.shift-chip`, die
      Spezifitäts-Reihenfolge stimmte, das Symptom lag tatsächlich an der Flex-Basis-Auflösung,
      nicht an der Kaskade. Gefixt wie an allen anderen Stellen dieses Bereichs: `.shift-chip`
      selbst auf `position: absolute; inset: 0` umgestellt statt Prozent-Grössen -- umgeht die
      Flex-Basis-Auflösung komplett, `.shift-chip-btn` ist als `position: absolute`-Element
      bereits ein gültiger Containing Block. Mit Playwright verifiziert (Pixel-Vergleich der
      Bounding-Box von `.shift-chip` gegen `.shift-chip-btn`, vorher/nachher, für leere UND
      belegte Zellen): beide Boxen sind jetzt deckungsgleich.
    - ✅ **Planblatt-Redesign: horizontaler Split + Eck-Badge statt Diagonale/Spezialitäten-Zeile
      (2026-08, "hand aufs Herz"-Nachbesserung)**: der Nutzer fragte direkt, ob das bisherige
      Diagonal-Dreieck (clip-path) für zwei Dienste an einem Tag wirklich intuitiv sei -- war es
      nicht: oben-links/unten-rechts hat keinen Bezug zur Tageszeit, und allein das randlose Füllen
      einer Farbe hatte mehrere Runden Flex-Basis-/Containing-Block-Hacks gebraucht (s. o.), ein
      Indiz dafür, dass das Design gegen das Layout-Modell statt mit ihm arbeitete. Auf explizite
      Zustimmung ("Ja mach das") umgesetzt:
      1. **Horizontaler Stapel statt Diagonale**: `.day-cell-slots--split` (vorher `--diagonal`)
         ist jetzt ein einfacher Flex-`column`-Container, `.cell-wrap--top`/`--bottom` (vorher
         `--diag-tl`/`--diag-br`) teilen sich die Höhe automatisch via `flex: 1 1 0` -- kein
         `clip-path` mehr. Oben = zeitlich früher, unten = später, eine echte, sofort verständliche
         Achse statt einer willkürlichen Geometrie. Da `.cell-wrap--top`/`--bottom` selbst
         Flex-Items von `.day-cell-slots` sind (nicht mehr ein weiteres Flex-Item INNERHALB eines
         schon gestreckten `position:absolute`-Elements), hat ihr `.shift-chip` ein sauberes,
         definiertes Eltern-Element zum Ausfüllen -- das Flex-Basis-Problem von oben trat hier gar
         nicht erst auf.
      2. **Spezialitäten als Eck-Badge statt eigener Zeile**: `SpecialStrip.jsx` (dünne Chip-Zeile
         UNTER den Slots) ersetzt durch `SpecialBadge.jsx` -- ein kleiner, farbiger Punkt in der
         Zellecke (unten-rechts, um nicht mit dem `.btn-offer-trade`-Button oben-rechts zu
         kollidieren), Klick öffnet ein `FloatingPopover` mit Liste + Entfernen-Button (gleiches
         Muster wie `DayStaffingBadge`). Der Badge ist `position: absolute` und nimmt keinen Platz
         im Layout ein -- **strukturell** kann eine Tageszelle dadurch nicht mehr höher werden als
         eine andere in derselben Zeile, die ganze Klasse von Höhen-Wettrüsten-Bugs (der Grund für
         mehrere vorherige Nachbesserungsrunden) ist damit nicht nur gefixt, sondern unmöglich
         gemacht: `th.col-employee`/`.day-cell` haben jetzt beide eine feste Höhe (36px), kein
         `flex`/`height:100%`/table-row-stretch-Trick mehr nötig.
      3. **Breitere Tagesspalten** (44px -> 56px): zwei gestapelte Kürzel (z. B. "T1"/"T2")
         brauchen bequem lesbaren Platz statt um jedes Pixel zu kämpfen.
      4. **Kräftigere Farbfläche**: `.plan-grid .shift-chip` von einem blassen 16%/80%-Farbmix auf
         30%/85% angehoben (nur für Zuweisungen, `:not(.shift-chip--empty)` -- sonst hätte die
         höhere Spezifität die transparente Füllung leerer "+"-Zellen überschrieben) -- ein Dienst
         wirkt jetzt auf den ersten Blick als klar erkennbare Farbfläche statt als dezenter Pill,
         einheitlich ob ein oder zwei Dienste an einem Tag liegen.
      Mit Playwright verifiziert: (a) Zeilenhöhe aller Tageszellen einer Zeile identisch (37px inkl.
      Rand), unabhängig davon, ob eine Nachbarzelle eine Spezialität trägt; (b) Klick exakt am
      oberen/unteren Rand einer Split-Zelle trifft zuverlässig die richtige Hälfte (per
      `aria-label`-Vergleich zweier unterschiedlicher Dienste verifiziert, nicht nur per
      Screenshot); (c) Spezialitäten-Badge + Popover (Anzeige, Entfernen) funktioniert additiv neben
      einem regulären Dienst; (d) volle Backend-Testsuite weiterhin grün (reine Frontend-Änderung).
    - ✅ **Markieren durch Ziehen** (statt jede Zelle einzeln anklicken zu müssen): `onMouseDown`
      entscheidet anhand des Zustands der zuerst berührten Zelle, ob markiert oder entmarkiert
      wird, und startet damit den Ziehen-Modus; `onMouseEnter` wendet denselben Modus beim
      Drüberziehen mit gedrückter Maustaste auf weitere Zellen an; ein globaler `mouseup`-Listener
      am `window` beendet den Ziehvorgang auch dann, wenn die Maustaste ausserhalb einer Zelle
      losgelassen wird. `onClick` bleibt als Tastatur-Fallback (Enter/Leertaste lösen `click` ohne
      vorheriges `mousedown` aus) -- ein bereits per `mousedown` verarbeiteter Klick unterdrückt das
      nachfolgende `click`, damit nicht doppelt (ent-)markiert wird. Gleiches Muster in
      `ShiftCell.jsx` (Planblatt) und `YearPlan.jsx` (Jahresplan, Punkt 12 unten).
    - ✅ **Planblatt-Nachbesserung, Teil 3: einheitlicher 1px-Rand (2026-08)**: letzte Feinabstimmung
      der randlosen Farbfläche oben -- ein Zwischenschritt hatte `margin: 1px` nur auf
      `.day-cell-slots--split` gelegt (zusätzlich zum bereits vorhandenen `inset: 0.5px` der
      Basisregel), wodurch Split-Zellen einen sichtbar anderen (grösseren) Rand hatten als
      Einzeldienst-Zellen -- vom Nutzer exakt so benannt ("day-cell-slots soll diese komplett
      ausfüllen mit 1 margin rundherum zentriert"). Vereinheitlicht auf eine einzige Regel
      `.day-cell-slots { inset: 1px }` ohne Sonderfall für `--split`: beide Zustände bekommen
      dadurch exakt denselben, zentrierten 1px-Rand auf allen vier Seiten.
    - ✅ **Beplanen neu gedacht: erst markieren, dann stempeln -- immer (2026-08)**: Nutzer-Feedback
      direkt nach der obigen Design-Freigabe ("Änder nichts mehr am Design!") -- das *Beplanen*
      selbst fühlte sich nicht mehr intuitiv an: die Icon-Toolbar (Punkt 11 oben, "Polypoint-Stil")
      verlangte "erst Dienst auswählen, dann Zelle anklicken", während eine parallel bestehende,
      separate Mehrfachauswahl-Stempelleiste genau umgekehrt funktionierte ("erst Tage markieren,
      dann stempeln") -- zwei widersprüchliche Bedienmodelle im selben Grid, je nachdem ob ein
      oder mehrere Tage beplant werden sollten. Auf die Rückfrage "was wäre wirklich effizient und
      intuitiv" (inkl. Klärung, wie nicht-zusammenhängende Tage wie Montag+Mittwoch markiert
      werden -- einfache Klicks schalten einzelne Zellen additiv um, keine Modifier-Taste nötig)
      und explizite Zustimmung ("Ja, setz das so um!") auf EIN einziges Modell vereinheitlicht,
      Excel-artig: Zellen anklicken/durchziehen markiert immer zuerst (egal ob eine oder zwanzig),
      ein Klick auf ein Werkzeug-Icon in der (jetzt einzigen) `PlacementToolbar.jsx` wendet es auf
      ALLE markierten Zellen an. Die alte, separate Mehrfachauswahl-Stempelleiste (Bulk-Feature)
      entfällt dadurch komplett -- ihre teamgefilterte Icon-Logik (nur Templates der markierten
      Zeilen, Fallback auf stationsweite) übernimmt jetzt die Icon-Toolbar. `armedTool`-State
      (das bisherige "Werkzeug bewaffnen") entfällt ersatzlos; `applyToolToMarked()` in
      `PlanGrid.jsx` ersetzt sowohl das alte `handleCellClick()`-Routing als auch
      `handleStampAssign`/`handleStampAbsence`, inkl. Gruppierung zusammenhängender Tage zu
      EINEM `Absence`-Datensatz beim Absenz-Stempeln (`groupConsecutiveDates()`, verhindert
      fragmentierte 1-Tages-Einträge im Abwesenheiten-Tab). Technisch heikelster Teil: Zellen mit
      bestehender Zuweisung sind gleichzeitig per natives HTML5-Drag verschiebbar (bestehendes
      Feature) -- ein naives `mousedown`+`preventDefault()` für das neue Ziehen-zum-Markieren hätte
      `dragstart` unterdrückt (Spezifikationsverhalten) und die Verschieben-Funktion stillgelegt.
      Gelöst durch eine bewusste Asymmetrie in `ShiftCell.jsx`: `handleMouseDown` greift nur bei
      `!templateInfo` (leere/Absenz-Zellen, nie `draggable`) und ruft dort `preventDefault()` +
      startet das Ziehen-Markieren; belegte, `draggable`-Zellen bekommen gar keinen
      `mousedown`-Handler und verlassen sich stattdessen auf `onClick` (feuert nur, wenn kein
      tatsächliches Drag stattfand) zum Markieren per Einzelklick. Die "markiert"-Kennzeichnung
      (`.day-cell-slots.is-marked`, inset-`box-shadow`-Ring) sitzt bewusst am äusseren Container statt
      am innersten `.shift-chip-btn`, weil dessen eigener, deckender `.shift-chip`-Kindknoten (bei
      belegten Zellen) jeden Hintergrund/Schatten des Buttons sonst optisch verdeckt hätte. Mit
      Playwright gegen echte Testheim-Daten verifiziert (danach wieder bereinigt): Einzelzell-Klick
      + Stempel, Ziehen über mehrere leere Zellen + Stempel (Markierungsring sichtbar, Stempel-Icons
      erst ab 1 markierter Zelle aktiv), nicht-zusammenhängendes Markieren zweier Tage per
      Einzelklicks, Absenz-Stempeln dreier zusammenhängender Tage erzeugt EINEN gruppierten
      Abwesenheits-Datensatz (nicht drei), Radiergummi löscht die Zuweisung markierter Zellen,
      und -- kritischster Fall -- natives Ziehen einer belegten Zelle auf einen anderen Tag
      verschiebt weiterhin korrekt und markiert dabei NICHT versehentlich die Quellzelle. Ausserdem
      unterwegs eine bereits unabhängig ausstehende Migration (`AbsenceType.icon`, Punkt 11 oben)
      angewendet, die den lokalen Dev-Stand zuvor mit HTTP-500 bei `/api/absence-types/` blockierte.
    - ✅ **Zwei Nachbesserungen zum Markieren (2026-08)**: Nutzer-Feedback direkt nach dem obigen
      Redesign. (1) Der Markierungsring war bei einem gesetzten Dienst unsichtbar -- er sass auf
      `.day-cell-slots`, aber das füllt die Zelle bei belegtem Tag randlos mit der opaken
      `.shift-chip`-Farbfläche aus, der Ring lag optisch darunter. Nutzer-Hinweis: "links rechts
      oben und unten ist Platz dafür" -- der 1px-Rand aus dem vorherigen Layout-Redesign (Punkt 11,
      Nachbesserung Teil 3) wird von keinem Kind-Element beansprucht. Ring jetzt auf `.day-cell`
      selbst (dem äussersten Container, 1px grösser als `.day-cell-slots`) statt auf
      `.day-cell-slots` -- bleibt dadurch immer sichtbar, ob leer oder belegt. (2) "No Absence
      matches the given query" beim Entfernen/Ersetzen von Ferien: eine Absenz ist EIN Datensatz
      über einen ganzen Zeitraum, aber `applyToolToMarked()` löste pro markierter ZELLE einen
      eigenen `handleRemoveAbsence(absence.id)`-Aufruf aus -- markierte man mehrere Tage derselben
      bestehenden Absenz (z. B. alle drei Tage einer Ferienwoche) und stempelte darüber (Radiergummi
      oder ein anderes Werkzeug), wurde derselbe Datensatz mehrfach zu löschen versucht: der erste
      Versuch löschte ihn wirklich, jeder weitere schlug serverseitig mit 404 fehl. Gefixt durch
      Vorab-Deduplizierung: `applyToolToMarked()` sammelt zuerst alle betroffenen Absenz-IDs über
      ALLE markierten Zellen in einem `Set` (dedupliziert automatisch) und löscht jede genau
      einmal, bevor irgendein Werkzeug angewendet wird -- `applyToolToCell()` kümmert sich seitdem
      nicht mehr selbst um Absenzen. Mit Playwright verifiziert: Markieren einer belegten Zelle
      zeigt den Ring sichtbar am Zellrand (Screenshot-Crop bestätigt); eine bestehende
      Drei-Tage-Ferienwoche komplett markieren und mit einem ANDEREN Absenztyp (Krankheit)
      überstempeln läuft ohne 400/404-Fehler durch und ergibt weiterhin einen einzigen,
      korrekt umgruppierten Datensatz im Abwesenheiten-Tab (nicht drei fragmentierte).
    - ✅ **Bugfix: Klick auf belegte Zelle markierte beim blossen Bewegen der Maus weiter
      (2026-08)**: Nutzer-Beobachtung -- "klicke ich auf eine belegte Zelle aktiviert er sofort
      Mehrfachauswahl und markiert alles was unter den Zeiger kommt", während es bei leeren und
      Absenz-Zellen einwandfrei funktionierte. Ursache lag im Zusammenspiel zweier Funktionen in
      PlanGrid.jsx: `startMark()` setzt beim Markieren-Beginn `dragMarkModeRef` (den Ziehmodus)
      UND wird von `window`s globalem `mouseup`-Listener wieder auf `null` zurückgesetzt, sobald
      die Maustaste losgelassen wird -- das funktioniert für leere/Absenz-Zellen, weil dort
      `mousedown` (VOR dem `mouseup`) `startMark()` aufruft. Bei einer belegten, per natives
      Drag&Drop verschiebbaren Zelle gibt es bewusst KEINEN `mousedown`-Handler (siehe Punkt 11
      oben, damit natives Drag&Drop nicht gestört wird) -- dort rief stattdessen der `onClick`-
      Fallback `startMark()` auf. Ein `click`-Event feuert aber IMMER NACH dem zugehörigen
      `mouseup`: der globale Listener hatte den Ziehmodus zu diesem Zeitpunkt schon (unnötig)
      zurückgesetzt, und `startMark()` setzte ihn im Klick-Handler erneut -- diesmal blieb er
      hängen, weil kein weiteres `mouseup` mehr folgte. Jede spätere Mausbewegung über andere
      Zellen (auch OHNE gedrückte Taste) löste dadurch `continueMark()` aus und markierte ungewollt
      weiter. Gefixt durch klare Trennung: eine neue Funktion `toggleMark()` (reines Ein-Zellen-
      Toggle OHNE `dragMarkModeRef`-Seiteneffekt) übernimmt jetzt den `onClick`-Fallback in
      `ShiftCell.jsx` (neue Prop `onMarkToggle`, in beiden betroffenen Zweigen -- belegte Zelle und
      der Tastatur-Fallback bei Absenz/leerer Zelle); `startMark()`/`onMarkStart` bleiben
      ausschliesslich dem `mousedown`-gestarteten Ziehen vorbehalten. Mit Playwright verifiziert:
      Klick auf eine belegte Zelle, danach die Maus (OHNE gedrückte Taste) über zehn weitere Zellen
      bewegt -- bleibt bei "1 markiert" (vorher hätte jede überstrichene Zelle mitmarkiert). Ziehen
      über mehrere leere Zellen sowie Einzelklick + anschliessende Mausbewegung ohne Taste auf
      leeren Zellen funktionieren unverändert korrekt (Regressionscheck).
    - ✅ **Bugfix ("massiver Bug"): unsichtbare Mehrfachanstellungs-Konflikte als Warn-Badge
      (2026-08)**: Nutzer-Meldung mit Screenshot -- eine leere Zelle liess sich trotzdem nicht
      beplanen ("Nina Kaufmann hat am 2026-08-11 bereits 'Frühschicht' (07:00–17:00), das sich
      zeitlich mit 'Küchendienst' überschneidet"), obwohl weder das Grid noch die Admin-Liste einen
      Dienst zeigten -- der Verdacht: unsichtbare Geisterdaten. Ursache im Code lokalisiert, keine
      Datenkorruption: bei einer Mehrfachanstellung (README Punkt 17) prüft
      `ShiftAssignment._check_no_overlap()` im Modell zu Recht ALLE Zuweisungen der Person
      TENANT-WEIT über alle Teams/Stationen hinweg (niemand kann an zwei Orten gleichzeitig
      arbeiten), aber das Planblatt lädt pro Ansicht nur die aktuell gewählte Station
      (`?node=<Station>`) -- ein blockierender Dienst in einer ANDEREN Station der Person war für
      den Planer dadurch nirgends auffindbar, ausser über die kryptische Fehlermeldung beim
      tatsächlichen Beplanungsversuch. Per Rückfrage (`AskUserQuestion`) bestätigt: Nina Kaufmann
      hat tatsächlich mehrere Teams/Anstellungen. Neuer Endpoint
      `GET /api/shift-assignments/other-team-conflicts/?employees=&date_from=&date_to=&exclude_node=`
      (`ShiftAssignmentViewSet.other_team_conflicts`, Admin/Planer-only -- bewusst NICHT node-
      gescoped wie der Haupt-Fetch, da stationsübergreifend suchen der ganze Zweck ist) liefert für
      die sichtbaren Mitarbeitenden/den sichtbaren Monat genau die Minimal-Info (Dienstname,
      Uhrzeit, Team-/Stationsname) für eine proaktive Warnung -- keine neuen Daten gegenüber dem,
      was die Fehlermeldung beim Versuch ohnehin preisgibt, nur VOR statt erst NACH einem
      gescheiterten Versuch. `exclude_node` = die aktuell gewählte Station (nicht der einzelne
      Knoten): alle Teams DERSELBEN Station sind ohnehin schon über den normalen Grid-Fetch geladen
      und brauchen keine Extra-Warnung, nur eine wirklich ANDERE Station ist sonst unsichtbar.
      Frontend: neues `CrossTeamConflictBadge` (kleines ⚠-Icon unten links auf einer sonst leeren
      Zelle, eigene Ecke getrennt von Spezialitäten-Badge unten rechts und Tauschangebot-Button
      oben rechts -- Popover mit Details per Klick, gleiches `FloatingPopover`-Muster wie
      `DayStaffingBadge`/`SpecialBadge`). Mit Playwright gegen echte Testheim-Daten verifiziert:
      der Testdatensatz enthielt bereits organisch vier solche versteckten Konflikttage für eine
      Mitarbeiterin (Küche/Pflege Tag überschnitten sich am 3./4./10./11.8.) -- alle vier wurden
      korrekt als Warn-Badge sichtbar, Klick öffnet das Popover mit Dienstname/Uhrzeit/Team; sieben
      neue Backend-Tests (`ShiftAssignmentOtherTeamConflictsAPITests`) decken Cross-Station-Fund,
      Ausschluss der eigenen Station, Spezialitäten-Ausnahme (additiv, kein Konflikt), Datumsfilter,
      leere Parameter, Rollen-Restriktion (403 für Mitarbeitende) und Tenant-Isolation ab.
12. ✅ **Jahresplan pro Mitarbeiter -- anzeigbar und bearbeitbar**: das Planblatt (`PlanGrid.jsx`)
    zeigt weiterhin nur einen Monat (`MonthNav.jsx`). Der Jahresplan (`YearPlan.jsx`, neuer Tab
    "Jahresplan") deckt den Hauptfall ab, eine einzelne Person übers ganze Jahr zu bearbeiten --
    z. B. Anfang Jahr alle Ferien auf einmal eintragen, statt zwölfmal ins Monatsblatt zu wechseln
    oder viele einzelne Absenz-Formulare auszufüllen:
    - **Mitarbeiter-Auswahl** (Dropdown) zusätzlich zur bestehenden, globalen Stations-Auswahl aus
      dem Topbar (`App.jsx: nodeId`) -- nötig, weil `TimeTemplate`/`ShiftAssignment.node` pro
      Station gelten und `Employee.nodes` (ManyToMany) einen Mitarbeiter an mehreren Stationen
      zulässt. Admin/Planer wählen frei aus der (bereits stations-gefilterten) Mitarbeiterliste,
      Mitarbeitende sehen nur die eigene Person fest (kein Dropdown).
    - Alle 12 Monate als Mini-Kalender (Mo-So-Wochenraster), mit bestehenden Schicht-Zuweisungen
      (Farbe/Kürzel wie im Monatsblatt) und Absenzen (grau gestreift wie `shift-chip--absence`)
      dargestellt.
    - **Bearbeitbar über dieselbe "Markieren, dann stempeln"-Interaktion** wie der Schicht-Stempel
      im Monatsblatt (Punkt 11), aber mit einer **kombinierten Stempel-Leiste**: Schichttyp-Chips
      der gewählten Station (nur Admin/Planer, `handleStampShift`/"Schicht leeren") **und**
      Absenz-Typ-Chips "Ferien"/"Krankheit"/"Sonstiges" plus "Absenz entfernen" (für alle,
      analog zu `AbsencePanel`s Schreibrechten). Zusammenhängende markierte Tage werden beim
      Absenz-Stempeln zu **einer** `Absence` mit Start-/Enddatum zusammengefasst
      (`groupConsecutiveDates`), nicht ein Datensatz pro Tag. Tage mit bereits bestehender Absenz
      werden beim Stempeln übersprungen (nicht überlappt) und summarisch gemeldet, analog zum
      Wochenmuster-Kopieren. "Absenz entfernen" löscht die **ganze** Absenz eines markierten Tages
      (kein Aufsplitten von Zeiträumen). Bestehender Genehmigungs-Workflow (Block 2.3) und
      Regel-Engine (bei Schicht-Konflikten, z. B. Ruhezeit) gelten unverändert -- beides läuft über
      dieselben API-Endpoints wie Monatsblatt/Abwesenheiten-Tab.
    - Reine Frontend-Änderung: `api.getShiftAssignments`/`api.getAbsences` unterstützen bereits
      beliebige Zeiträume, keine neuen Endpoints nötig.
    - *Noch offen*: "Wunschfrei" ist weiterhin kein eigener `Absence.Type` (nur `vacation`/`sick`/
      `other`) -- im Jahresplan aktuell nicht separat abgebildet, siehe Diskussion oben. Eine
      read-only Mehr-Personen-Jahres-Heatmap pro Station (ursprünglich als Alternative skizziert)
      bleibt eine mögliche spätere Ergänzung, aber nachrangig.
    - ✅ **Bugfix (2026-08)**: `YearPlan.jsx` fragte `api.getShiftAssignments(nodeId, dateFrom,
      dateTo)` für ein **ganzes Kalenderjahr und die ganze Station** ab, aber `api.js` las nur
      `response.results` -- die **erste Seite** der DRF-Pagination (`PAGE_SIZE = 50`, siehe
      `config/settings.py`). Bei durchgehender Mo-Fr-Planung reichte das schon nach gut 10 Wochen
      nicht mehr: alle weiteren Zuweisungen (z. B. im August) fehlten im Jahresplan kommentarlos,
      obwohl sie im Planblatt (das nur je einen Monat abfragt, nie in die Nähe von 50 Einträgen
      kommt) korrekt sichtbar waren. Fix: neue Funktion `requestAllPages()` in `api.js`, die den
      `next`-Link der DRF-Pagination verfolgt und alle Seiten zu einem `{results: [...]}` zusammen-
      führt -- bestehende Aufrufer (`data.results ?? data`) mussten dafür nicht angepasst werden.
      Angewendet auf alle Listen-GET-Endpoints (`getShiftAssignments`, `getAbsences`,
      `getShiftPreferences`, `getTimeRecords`, `getEmployees`, `getNodes`, `getSkills`,
      `getTimeTemplates`, `getShiftTradeRequests`, `getTenantHolidayOverrides`), nicht nur an der
      ursprünglich gemeldeten Stelle, weil derselbe Bug bei jedem dieser Endpoints latent
      vorlag, sobald ein Tenant über 50 Datensätze in einer Liste ansammelt. Verifiziert per
      Playwright: 51 Zuweisungen (50 Mo-Fr-Tage Jan-Mitte März + 1 im August) -- vor dem Fix fehlte
      der August-Eintrag im Jahresplan, danach sichtbar.
    - ✅ **UX-Feedback (2026-08)**: die kombinierte Stempel-Leiste (Schichttyp- + Absenz-Chips) sass
      in derselben umbrechenden Flex-Zeile wie "Mitarbeiter"-Dropdown und Jahr-Navigation und brach
      dadurch nur bei Platzmangel irgendwo mitten im Fluss um, statt an einer festen, vorhersagbaren
      Stelle. `styles.css`: `.year-plan-toolbar > .stamp-palette` /
      `.year-plan-toolbar > .multi-select-hint` bekommen `flex-basis: 100%`, wodurch die Leiste
      jetzt immer eine eigene Zeile unterhalb von Mitarbeiter-Auswahl + Jahr-Navigation bekommt,
      unabhängig von der Fensterbreite. Bewusst auf `.year-plan-toolbar` gescoped statt global auf
      `.stamp-palette`/`.multi-select-hint`, damit die gleichnamigen, aber separaten Klassen im
      Planblatt (`.multi-select-toolbar`, Punkt 11) unverändert bleiben.
13. ✅ **Wunschfrei + Wunschdienst -- Mitarbeitende tragen eigene Wünsche selbst ein**: löst die in
    Punkt 12 offen gelassene Frage nach "Wunschfrei" auf. Bewusst **kein** neuer `Absence.Type`,
    sondern ein eigenständiges, leichtgewichtiges Modell `ShiftPreference` (`employee`, `date`,
    `type` "wunschfrei"/"wunschdienst", `template` nur bei Wunschdienst, `note`,
    `unique_together` auf `employee`+`date`), weil sich Wünsche fundamental von Absenzen
    unterscheiden:
    - Eine Absenz ist -- einmal `approved` -- eine harte Sperre (`_check_no_absence_conflict`)
      mit Genehmigungs-Workflow. Ein Wunsch ist dagegen nur ein **Hinweis für den Planer**, kein
      Anspruch und keine Sperre -- die Regel-Engine kennt `ShiftPreference` gar nicht, kein
      `approve`/`reject` nötig.
    - **Höchstpersönlich, strenger als bei Absenzen**: `ShiftPreferencePermission` erlaubt
      Schreiben ausschliesslich für die eigene Person (`request.employee_profile`) -- anders als
      bei `Absence` gibt es hier **keinen** Manager-Override, auch Admin/Planer dürfen keine
      Wünsche für andere anlegen/ändern/löschen. `ShiftPreferenceViewSet.perform_create` erzwingt
      `employee=request.employee_profile` serverseitig, unabhängig vom Payload. Lesen ist wie
      beim übrigen Planblatt für alle Rollen offen.
    - **Selbsteingabe im Planblatt** (`ShiftCell.jsx`): neues Badge oben links (spiegelbildlich
      zum Ist-Zeit-Badge aus Block 1.10 unten rechts) auf der eigenen, heutigen/zukünftigen Zelle
      -- "?" wenn noch kein Wunsch besteht, "F"/"D" (grün/blau) sobald einer gesetzt ist, Klick
      öffnet ein `WishEditor`-Popover (Typ + bei Wunschdienst Schichttyp-Auswahl) über
      `FloatingPopover`. Für alle anderen Zellen mit bestehendem Wunsch zeigt der Planer
      (`canManage`) denselben Buchstaben nur als nicht klickbaren Hinweis.
    - **Selbsteingabe im Jahresplan** (`YearPlan.jsx`): dieselbe "Markieren, dann
      stempeln"-Interaktion wie Schicht-/Absenz-Stempeln (Punkt 11/12), aber die
      "Wunschfrei"/"Wunschdienst: <Typ>"-Chips (gestrichelter Rahmen, `.stamp-chip--wish`) und
      "Wunsch entfernen" erscheinen nur, wenn die ausgewählte Person die eigene ist
      (`isOwnEmployeeSelected`) -- ein Planer, der den Jahresplan einer anderen Person betrachtet,
      sieht deren Wünsche zwar als kleinen farbigen Punkt an der Tages-Zelle (unten rechts,
      unabhängig von einer bereits zugewiesenen Schicht sichtbar), kann sie aber nicht bearbeiten.
      Anders als beim Absenz-Stempeln wird ein bestehender Wunsch beim erneuten Stempeln
      überschrieben (kein Überspringen) -- "Meinung ändern" ist bei einem Wunsch jederzeit
      erwartbar, anders als bei einer bereits genehmigten Absenz.
    - Kein Effekt auf die Regel-Engine, auf `ShiftAssignment.clean()` oder auf den Saldo
      (Block 2.7).
    - *Noch offen*: ob ein Wunsch nach der Planung (sobald eine Schicht tatsächlich zugewiesen
      wurde) automatisch verschwinden/als "erfüllt"/"nicht erfüllt" markiert werden soll, oder ob
      er unabhängig davon stehen bleibt.
14. ✅ **Tenant-Konfiguration im Settings-Tab (neues 5. Modul)**: alle numerischen ArG-/
    Zuschlags-Grenzwerte sassen bisher ausschliesslich als Felder auf `core.models.Tenant`, nur im
    Django-Admin editierbar -- ein Planer (`Membership.Role.PLANNER`) hat dort ohnehin **keinen**
    Zugriff (separates Berechtigungssystem über `User.is_staff`, siehe Architektur-Abschnitt), und
    selbst ein Kunden-Admin sollte `/admin/` nie erreichen (siehe dort). Betrifft
    `minimum_rest_hours`, `maximum_weekly_hours`, `maximum_daily_span_hours`,
    `time_record_deviation_tolerance_minutes`, `standard_weekly_hours`, `overtime_surcharge_pct`,
    `default_vacation_days_per_year`, `night_work_surcharge_pct`,
    `night_work_regular_threshold_nights`, `night_work_permit_confirmed`,
    `sunday_work_surcharge_pct` (Block 1.1/1.5/1.6/1.11/2.7).
    - **Backend**: `GET`/`PATCH /api/tenant/` (`core.views.TenantView`, Single-Object statt
      ViewSet-Liste, analog zu `MeView`) + `core.serializers.TenantSerializer` für alle oben
      genannten Felder (`name`/`id` read-only). Lesen ist wie überall in der App für alle vier
      Rollen offen; Schreiben ist **Admin-only** über die neue Permission-Klasse
      `core.permissions.IsTenantAdmin` -- die erste Stelle im System, an der sich Admin und Planer
      tatsächlich unterscheiden (siehe Architektur-Abschnitt).
    - **Frontend**: neues Modul "Regel-Engine & Zuschläge" in `SettingsPanel.jsx`, thematisch in
      fünf Abschnitte gruppiert (Ruhezeit & Höchstarbeitszeit, Zeiterfassung, Überzeit, Ferien,
      Nacht-/Sonntagsarbeit), jedes Feld mit demselben Gesetzesartikel-Hinweis, den bisher nur der
      Django-Admin über `help_text` zeigte (`TenantSettings.jsx`). Der Tab erscheint in der
      Modul-Navigation nur für `Membership.Role.ADMIN` (`src/roles.js: isTenantAdmin`, gespiegelt
      aus `IsTenantAdmin`) -- ein Planer sieht ihn gar nicht erst, obwohl er alle anderen
      Settings-Module weiterhin sieht/bearbeitet. Die eigentliche Absicherung bleibt serverseitig
      (403 bei PATCH-Versuch), wie beim übrigen rollenbewussten Frontend.
15. ✅ **Employee-Zusatzfelder im Settings-Tab vervollständigen**: `last_night_work_medical_exam_date`
    (Block 1.5) ist jetzt auch im `EmployeeSettings.jsx`-Formular editierbar, nicht mehr nur über
    den Django-Admin erreichbar. Entscheidung zur im Vorfeld offenen Frage: immer sichtbares
    Datumsfeld mit Erklärtext ("nur relevant bei regelmässiger Nachtarbeit ... wird nur
    ausgewertet, wenn die Person laut Saldo/Zeiterfassung regelmässig nachts arbeitet"), nicht
    bedingt ausgeblendet -- konsistent mit den übrigen optionalen Override-Feldern
    (`maximum_weekly_hours`, `standard_weekly_hours`, `vacation_days_per_year`), die ebenfalls
    immer sichtbar sind statt bedingt versteckt, und ohne den zusätzlichen API-Aufwand, "ist diese
    Person aktuell regelmässige Nachtarbeiterin" pro Zeile in der Mitarbeitendenliste zu ermitteln.
16. ✅ **UX-Überarbeitung der gesamten Einstellungen-Oberfläche**: alle fünf Ansatzpunkte
    umgesetzt (kein fixes Detail-Design war vorgegeben, siehe Umsetzung als Antwort auf die
    jeweils offen formulierte Frage):
    - **Einstiegsseite** (`SettingsPanel.jsx`): `module`-State startet jetzt bei `null` statt
      `"templates"` -- zeigt eine Kachel-Übersicht (`.settings-module-grid`) mit Kurzbeschreibung
      pro Modul (inkl. des Tenant-Moduls aus Punkt 14, dort mit Admin-only-Hinweis in der
      Beschreibung selbst). Klick auf eine Kachel öffnet das Modul, ein "← Übersicht"-Link in der
      Modul-Navigation führt zurück.
    - **Formulare gegliedert**: `EmployeeSettings.jsx` in drei `<fieldset>`-Abschnitte
      ("Stammdaten", "Wochenstunden-Override (Block 1.14)", "Saldo & Zeiterfassung (Block 2.7 /
      1.5)") -- dieselbe `.panel-form-group`-Klasse, die `TenantSettings.jsx` (Punkt 14) bereits
      für seine fünf Themenblöcke nutzt.
    - **Feldnahe Erklärungen**: `EmployeeSettings.jsx` hat jetzt zu den ArG-/GAV-relevanten
      Feldern (Geburtsdatum, Wochenstunden-Override, Ferienanspruch, arbeitsmedizinische
      Untersuchung) denselben `.panel-hint`-Text wie der Django-Admin-`help_text`, nicht mehr nur
      knappe Klammer-Hinweise im Label.
    - **Feldbezogene Fehlermeldungen**: `api.js: request()` hängt die von DRF strukturiert
      gelieferten Validierungsfehler jetzt zusätzlich als `error.fields` an (`.message` bleibt für
      bestehende Aufrufer unverändert ein einzelner String, rein additiv). `EmployeeSettings.jsx`
      und `TenantSettings.jsx` zeigen `error.fields[feldname]` direkt unter dem betroffenen Feld
      (`.field-error`, in `--warn`-Farbe) zusätzlich zum weiterhin bestehenden globalen Banner --
      geprüft per Vergleich mit der tatsächlichen DRF-Fehlerform (`curl -X PATCH .../api/tenant/`
      mit ungültigem Wert liefert exakt `{"feldname": ["Meldung"]}`).
    - **Suchfunktion**: Textfilter (`.panel-list-filter`) über der Liste in `EmployeeSettings.jsx`
      und `SkillSettings.jsx`, erscheint erst ab 8 Einträgen (kleine Listen brauchen keinen
      Filter). `NodeSettings.jsx`/`TimeTemplateSettings.jsx` bewusst ausgenommen -- typische
      Stations-/Schichttyp-Anzahl pro Tenant bleibt klein, ein Filter wäre dort Overhead ohne
      echten Nutzen.
    - Manuell im Browser verifiziert (Playwright): Kachel-Übersicht, Formular-Gliederung,
      Hint-Texte, Suchfilter (11 Testmitarbeitende, korrekt gefiltert), Rücksprung zur Übersicht.
17. ✅ **Teams pro Station + Mehrfachanstellungen -- Anstellung statt Person als Planungseinheit**
    (2026-08, Nutzer-Feedback + Neuentwurf; ersetzt eine erste, unvollständige Fassung dieses
    Punkts). **Umgesetzt** wie unten geplant, mit einer bewussten Vereinfachung gegenüber dem
    ursprünglichen Zielbild: `ShiftAssignment` wurde NICHT auf eine neue `Employment`-FK
    umgestellt (das hätte Saldo-/ArG-Berechnung, Absenz-Konflikt-Check, alle Serializer und weite
    Teile des Frontends riskant mitverändert) -- stattdessen bleibt `Employee`/`node` unverändert,
    und das bereits vorhandene `ShiftAssignment.node`-Feld übernimmt die Rolle, eine Zuweisung
    eindeutig einem Team zuzuordnen. `Employment` ist eine rein additive Tabelle (Team + Pensum +
    Rollentitel + Teamleiter-Flag), `Employee.nodes` bleibt die tatsächliche Berechtigungs-/
    Saldogrundlage, wird aber jetzt serverseitig aus den `Employment`-Zeilen abgeleitet statt direkt
    editierbar zu sein. Ergebnis: alle 241 zuvor bestehenden Tests blieben unverändert grün, 14 neue
    kamen dazu (u. a. der erste Migrations-State-Test dieses Projekts, der den Backfill bestehender
    `Employee.nodes`-Zuordnungen in `Employment`-Zeilen absichert). Zwei zusätzliche, beim Umsetzen
    gefundene "Verdrahtungslücken" mussten mitgelöst werden, ohne die waren die beiden Kernfakten
    unten faktisch nicht nutzbar: `_employee_scoped_node_ids()` (Mitarbeiter-Scoping) musste den
    direkten Elternknoten mit einschliessen, sonst verlor ein Mitarbeiter mit Team-Anstellung den
    Zugriff auf die stationsweiten Schichttypen; `ShiftAssignmentViewSet`s `?node=`-Filter musste
    von exaktem Treffer auf "Knoten + direkte Kinder" erweitert werden, sonst lieferte eine
    Stations-Anfrage bei einer Station mit Teams grundsätzlich keine Zuweisungen zurück (die liegen
    ja auf den Team-Knoten). Zwei Entscheidungen wurden dem Nutzer explizit vorgelegt: **Direktbuchung
    auf eine Station mit Teams wird verhindert** (`ShiftAssignment._check_node_has_no_children()`,
    neue Zeile in `clean()`) -- Mitarbeitende ohne passende Team-Anstellung erscheinen im Planblatt
    sichtbar, aber schreibgeschützt ("Kein Team zugeordnet"), bis ihnen im Mitarbeiter-Formular ein
    Team zugewiesen wird; und die Backfill-Migration bekam den **vollständigen Migrations-State-Test**
    (Django `MigrationExecutor`, migriert die Test-DB explizit vor/nach 0013) statt eines einfacheren
    Unit-Tests der Backfill-Funktion. Mit Playwright end-to-end gegen die echten, gepushten
    Testheim-Daten verifiziert: Team-Trennzeilen, Teamleiter-Badge, pro-Team gefilterte Zellen bei
    einer Person mit zwei Anstellungen (Schicht erscheint nur im richtigen Team-Block, nie doppelt),
    sowie zwei unterschiedliche Jahresplan-Kalender für dieselbe Person je nach gewählter Anstellung.

    - **Zwei Nachbesserungen nach dem Rollout** (2026-08, Nutzer-Feedback anhand der echten
      Testheim-Daten -- beide bereits behoben und verifiziert):
      - **Teamleiter-Badge fehlte bei flachen (teamlosen) Abteilungen**: `PlanGrid.jsx`s
        `rows`-Aufbau setzte im Zweig ohne Teams (`teamNodes.length === 0`) `employment` pauschal
        auf `null`, obwohl für jede Zeile weiterhin eine echte `Employment` existiert -- Pensum-/
        Rollen-Anzeige und `★`-Badge blieben dadurch für jede Person in einer teamlosen Abteilung
        (z. B. "Therapie") unsichtbar, obwohl `is_team_lead` korrekt gesetzt war. Fix: auch der
        Flach-Zweig sucht jetzt die zur aktuellen Station passende `Employment` aus
        `emp.employments`, statt sie zu verwerfen.
      - **Jahresplan zeigte keine Dienste für Team-Anstellungen**: `YearPlan.jsx` filterte
        `TimeTemplate`s nach `selectedNode` (der Team-Id bei einer Employment), `TimeTemplate.node`
        war zu diesem Zeitpunkt aber ausnahmslos die Station. `templates` war dadurch für jede
        Team-Anstellung leer, der Template-Lookup pro Tageszelle schlug folglich immer fehl und die
        Zelle zeigte "frei" an, obwohl die zugrunde liegende `ShiftAssignment` korrekt geladen war.
        Fix zu diesem Zeitpunkt: Filter wie in `PlanGrid.jsx` nach der Station (`nodeId`), nicht
        nach dem Team -- **diese Annahme ("TimeTemplate.node ist immer die Station") wurde in der
        folgenden Nachbesserung revidiert**, siehe unten.

    - **Weitere Nachbesserung (2026-08): Schichttypen pro Team statt zwingend stationsweit** --
      Nutzer-Feedback anhand der echten Testheim-Daten deckte zwei zusammenhängende Lücken auf:
      - **Direkte Team-Auswahl zeigte keine Schichten**: wählte man im NodeSelector ein Team direkt
        (statt die Station), blieb `templates` in `PlanGrid.jsx` leer (`t.node === nodeId` traf nie
        zu, weil `TimeTemplate.node` bis dahin ausnahmslos die Station war) -- das Planblatt liess
        sich für diese Ansicht gar nicht befüllen.
      - **Team-Zeilen zeigten den kompletten Stations-Katalog statt nur die eigenen Schichten**: in
        der gruppierten Stationsansicht bekam jede Team-Zeile identisch alle Schichttypen der
        Station zur Auswahl -- ein Tagdienst-Team sah z. B. auch "Nachtwache" im Dropdown und in der
        Mehrfachauswahl-Stempelleiste, obwohl die nur für das Nacht-Team gilt.
      Beide Symptome haben dieselbe Ursache: `TimeTemplate.node` musste bislang zwingend die Station
      sein, obwohl das Datenmodell (einfache FK auf `Node`) ein Team dort schon immer zugelassen
      hätte -- nur die Frontend-Filterung ging überall stur von "Station" aus. Fix, ohne
      Backend-/Migrationsänderung (rein Frontend + Nutzung des bereits flexiblen Feldes):
      - Neuer Helper `stationScope(nodes, nodeId)` (`PlanGrid.jsx`) löst zu einem beliebig gewählten
        Knoten (Station oder eines ihrer Teams) die zugehörige Station plus alle ihre Team-Kinder
        auf (Eltern-Suche über `depth`/`path`, nicht nur Kinder-Suche wie das ältere
        `App.jsx: relevantNodeIds`) -- damit lädt `templates` jetzt vollständig, unabhängig davon,
        ob im NodeSelector die Station selbst oder direkt eines ihrer Teams gewählt wurde.
      - Pro Zeile filtert `PlanGrid.jsx` jetzt zusätzlich auf `t.node === rowNodeId` (neue Prop
        `assignableTemplates` an `ShiftCell.jsx`, getrennt von der weiterhin vollen `templates`-Liste,
        die z. B. der personenweite Wunschdienst-Editor unverändert braucht) -- eine Team-Zeile sieht
        dadurch nur noch ihre eigenen Schichttypen. Die Mehrfachauswahl-Stempelleiste bildet dafür die
        Vereinigung der Schichttypen aller aktuell markierten Zeilen (`stampTemplates`, aus dem
        Team-Anteil jedes `markedCells`-Schlüssels). `YearPlan.jsx` (das immer nur eine Anstellung
        gleichzeitig zeigt) filtert dafür schlicht nach `t.node === selectedNode`.
      - `TimeTemplateSettings.jsx`: die "Station"-Auswahl beim Anlegen/Bearbeiten eines Schichttyps
        listet Teams jetzt eingerückt wie im `NodeSelector` (vorher nicht von Stationen zu
        unterscheiden) plus einem Hinweistext, dass ein Schichttyp auf der Station allen ihren Teams
        gemeinsam zur Verfügung steht, ein Schichttyp direkt auf einem Team dagegen nur dort.
      - Reine Zusatz-Möglichkeit, kein Zwang: ein Schichttyp bleibt weiterhin gültig auf der Station
        (geteilter Katalog) -- Teams mit tatsächlich geteilten Schichten müssen nichts ändern. Die
        Testheim-Demodaten wurden auf die jetzt mögliche, sauberere Zuordnung nachgezogen
        (Frühschicht/Spätschicht → Pflege Tag, Nachtwache → Pflege Nacht, MPA → Sekretariat A,
        Küchendienst → Küche, Reinigungsdienst → Reinigung), weil jede dieser Schichten in der
        Praxis ohnehin exklusiv von genau einem Team genutzt wurde.
      - Mit Playwright verifiziert: direkte Team-Auswahl zeigt die Schichten korrekt; in der
        Stationsansicht zeigt die Tagdienst-Zeile nur Früh-/Spätschicht, die Nachtdienst-Zeile nur
        Nachtwache (weder im Einzel-Dropdown noch in der Stempelleiste mischen sich die Kataloge
        mehr); Jahresplan einer Team-Anstellung zeigt ebenfalls nur deren eigene Schichttypen.

    - **Regressions-Fix (2026-08, unmittelbar danach): "geteilter Katalog"-Anspruch war real
      kaputt** -- der obige Fix behauptete, ein Schichttyp bleibe auf der Station gültig und stehe
      allen ihren Teams gemeinsam zur Verfügung; tatsächlich implementiert war das nicht.
      `rowAssignableTemplates` (und analog `stampTemplates`) filterten mit `t.node === rowNodeId`
      -- einem exakten Team-Treffer --, ohne je den stationsweiten Fall zu berücksichtigen. Bei
      jedem Tenant, der (wie der reale Nutzer dieser Session) noch keinen einzigen Schichttyp
      manuell auf Team-Ebene verschoben hatte -- also dem eigentlichen Normalfall --, blieben
      dadurch sämtliche Team-Zeilen einer Station-mit-Teams komplett leer, sowohl im
      Einzel-Dropdown als auch in der Mehrfachauswahl ("GAR KEINE Dienste mehr", reproduziert mit
      genau den Live-Daten des Nutzers: alle `TimeTemplate.node` zeigten noch auf die Station,
      nicht auf ein Team). Die vorherige Playwright-Verifikation hatte das nicht aufgedeckt, weil
      dabei ausschliesslich mit bereits auf Team-Ebene verschobenen Testheim-Demodaten getestet
      wurde -- der (weitaus häufigere) stationsweite Fall kam nie vor. Fix: `rowAssignableTemplates`
      und `stampTemplates` (`PlanGrid.jsx`) sowie der Templates-Filter in `YearPlan.jsx` matchen
      jetzt zusätzlich auf die aufgelöste Station-Id (`t.node === rowNodeId || t.node === stationId`
      bzw. `t.node === selectedNode || t.node === selectedStationId`) -- ein Schichttyp direkt auf
      einem Team bleibt exklusiv für dieses Team, einer auf der Station ist wie ursprünglich
      versprochen für jede ihrer Team-Zeilen sichtbar. Mit Playwright gegen beide Datenlagen
      verifiziert (stationsweit: alle Team-Zeilen sehen den Katalog; team-exklusiv: weiterhin nur
      die eigene Zeile).

    Zwei Fakten, die zusammen betrachtet werden müssen, weil sie dieselbe Modell-Lücke
    treffen:
    - **Mehrere Teams pro Station müssen sichtbar sein** (Beispiel ICT): eine Abteilung wie "ICT"
      hat oft mehrere Teams (Infrastruktur, Applikationen, Support), die ein **gemeinsames
      Planblatt** teilen, aber im Alltag primär das eigene Team im Blick haben wollen, ohne den
      Abteilungs-Überblick zu verlieren.
    - **Mehrfachanstellungen sind im Spital üblich**: eine Person kann gleichzeitig mehrere
      Anstellungen mit je eigenem Pensum haben, z. B. 40% Dozent + 60% Arzt, typischerweise in
      unterschiedlichen Teams/Abteilungen.

    Der gemeinsame Nenner: das heutige Modell geht von **"eine Person = eine Stelle = ein Team"**
    aus (`Employee.nodes` ist ein einfaches M2M, `Employee.employment_pct` ein einziges globales
    Feld, `ShiftAssignment` referenziert direkt `Employee`). Beide Fakten oben widerlegen genau
    diese Annahme -- Teams brauchen eine Gruppierungsebene *unterhalb* der Station, und
    Mehrfachanstellungen brauchen eine Aufteilungsebene *oberhalb* der einzelnen Schicht. Statt zwei
    separate Sonderfälle zu flicken, deshalb ein einziger, state-of-the-art Neuentwurf: die
    **Anstellung (`Employment`) wird die eigentliche Planungseinheit**, nicht mehr die Person direkt
    -- analog dazu, wie reale Spital-HR-Systeme "Person" und "Beschäftigungsverhältnis" trennen.

    - **Datenmodell (ursprüngliche Skizze -- die tatsächliche Umsetzung weicht bewusst ab, siehe
      "Umgesetzt" oben)**: `Employment` als eigenständiges Modell zwischen `Employee` (die reale
      Person, bleibt Login/Stammdaten-Träger) und `Node` (jetzt konsequent als Team-Ebene genutzt,
      dank `django-treebeard` bereits ein Baum -- eine Station wie "ICT" bekommt Kind-Knoten
      "ICT/Infrastruktur", "ICT/Applikationen", "ICT/Support"). Felder: `employee` (FK), `node` (FK,
      das konkrete Team), `pensum_pct` (statt des heutigen globalen `Employee.employment_pct`),
      `title` (Freitext-Bezeichnung der Rolle, z. B. "Arzt"/"Dozent" -- bewusst getrennt von `Skill`,
      das weiterhin die schicht-relevante Qualifikation abbildet, nicht den Vertragstitel),
      `is_team_lead` (Boolean, **pro Anstellung**, nicht pro Person -- so kann dieselbe Person in
      Team A Teamleiterin sein und in Team B nicht) -- alle vier Felder wurden 1:1 umgesetzt. Zwei
      Abweichungen von dieser ersten Skizze: kein `active`-Feld (YAGNI -- eine beendete Anstellung
      wird gelöscht, nicht deaktiviert), und `ShiftAssignment` referenziert **weiterhin** `Employee`
      statt `Employment` -- das bereits vorhandene `ShiftAssignment.node`-Feld übernimmt die
      Rollen-Eindeutigkeit stattdessen, ohne die riskante FK-Migration. `TimeRecord`/`Absence`/
      `ShiftPreference` blieben wie hier vermutet an `Employee` (personenweit), siehe die
      "Entscheidungen" weiter unten.

    - **Planblatt-UX (intuitiv, ein Blick genügt)**: Stations-Auswahl (`NodeSelector`) zeigt weiterhin
      Stationen, aber eine Station mit Kind-Knoten (Teams) öffnet **eine gemeinsame Tabelle mit
      eingebetteten Team-Trennzeilen** (dezente, ggf. einklappbare Zwischenzeile mit Team-Namen
      zwischen den Blöcken, ähnlich der bestehenden `<th>`-Kopfzeile) statt separater Tabellen pro
      Team -- damit bleiben abteilungsweite Muster (z. B. Ferien-Überschneidungen über Teams hinweg)
      auf einen Blick sichtbar, während der Alltag (das eigene Team) weiterhin klar gruppiert bleibt.
      Jede Zeile im Grid repräsentiert eine **Anstellung**, nicht mehr zwingend eine Person: die
      Dozentin/der Arzt mit Doppelanstellung erscheint als zwei Zeilen in zwei Team-Blöcken, je mit
      einem kleinen Pensum-/Rollen-Badge ("60% Arzt" / "40% Dozent") statt als eine mehrdeutige Zeile
      -- intuitiver als eine einzelne Zeile, die rät, in welcher Rolle die Person an einem Tag
      arbeitet. Teamleiter-Highlighting (fette Schrift/Badge in `.employee-name`) hängt konsequent an
      der jeweiligen `Employment`-Zeile, nicht an der Person, und erscheint deshalb korrekt nur im
      Team-Block, in dem die Anstellung tatsächlich die Leitung ist. Mitarbeitende (Rolle `EMPLOYEE`)
      sehen weiterhin nur ihre eigenen Team-Zeilen (ggf. mehrere, bei eigener Mehrfachanstellung),
      Admin/Planer die volle, gruppierte Stationsansicht.

    - **Saldo/ArG-Auswirkungen**: das Jahressoll (Block 2.7) müsste pro `Employment` einzeln geführt
      werden (60% Arzt und 40% Dozent haben unterschiedliche Soll-Basis, ggf. sogar unterschiedliche
      Ferienregelungen je nach Anstellungsvertrag), aber die **ArG-Grenzwerte für Ruhezeit und
      Höchstarbeitszeit gelten personenbezogen über alle Anstellungen desselben Tenants hinweg** --
      rechtlich zählt die tatsächlich geleistete Gesamtzeit einer Person, nicht die einzelne Rolle.
      Die bestehenden Prüfungen (`ShiftAssignment._check_maximum_weekly_hours`,
      Ruhezeit-Check) müssten also weiterhin über `Employee` (alle Anstellungen zusammen) laufen,
      während Soll/Saldo pro `Employment` getrennt bleibt -- zwei unterschiedliche
      Aggregationsebenen im selben Datenmodell, die beim Umbau nicht verwechselt werden dürfen.

    - **Migrationspfad**: bestehende `Employee.nodes`-Einträge liessen sich 1:1 in je eine
      `Employment`-Zeile mit `pensum_pct = Employee.employment_pct` überführen (Default: eine
      Anstellung pro bisher zugeordnetem Node), rückwärtskompatibel für alle heutigen
      Ein-Anstellungs-Fälle -- der Umbau betrifft strukturell vor allem Mehrfachanstellungen und
      Team-Gruppierung, nicht die Mehrheit der heutigen, einfachen Datensätze.

    - **Entscheidungen** (aufgelöst, jeweils zugunsten des einfachsten mentalen Modells für die
      Anwenderin/den Anwender -- nicht die technisch flexibelste Variante, sondern die, die am
      wenigsten neue Konzepte auf einmal einführt):
      - **`Absence`/`ShiftPreference` bleiben an `Employee`, nicht an `Employment`**: Ferien/Krankheit
        und "ich will an dem Tag frei" sind in der Realität personenweit, nicht rollenweise -- niemand
        ist "in der Dozentur krank, aber als Arzt gesund". Eine Absenz einer Person mit
        Mehrfachanstellung blockiert dadurch automatisch **alle** ihre Teams gleichzeitig, ohne dass
        sie sie zweimal erfassen muss -- das ist der intuitivere Normalfall und erspart eine sonst
        verwirrende Rückfrage ("für welche meiner Anstellungen gilt das?"). `TimeRecord` braucht gar
        keine eigene Entscheidung: es hängt bereits 1:1 an `ShiftAssignment` (`assignment`-FK), und
        `ShiftAssignment` zeigt neu auf `Employment` -- die Zuordnung "welche Rolle wurde gearbeitet"
        ergibt sich automatisch, ohne zusätzliches Feld.
      - **Jahresplan zeigt weiterhin genau eine Kalenderansicht** -- keine Team-Trennzeilen (die
        ergeben bei einer Einzelperson über 12 Monate keinen Sinn, anders als im Planblatt mit vielen
        Personen nebeneinander). Bei Mehrfachanstellung wird die bestehende Mitarbeiter-Auswahl
        (`YearPlan.jsx`) einfach um die zusätzlichen Anstellungen derselben Person ergänzt (z. B.
        "Peter Meier -- 60% Arzt" und "Peter Meier -- 40% Dozent" als zwei Einträge im selben
        Dropdown) -- wer nur eine Anstellung hat, sieht exakt dieselbe Auswahl wie heute. Bewusst so
        gelöst, weil zusätzliche Komplexität nur dort auftaucht, wo sie gebraucht wird, statt für alle
        sichtbar zu werden (progressive disclosure).
      - **Team-Verschachtelung: genau eine Ebene unter der Station, nicht beliebig tief.** Der
        `Node`-Baum könnte technisch beliebig tief verschachtelt werden, aber ein UI-Konzept
        "Station → Team → Unterteam → ..." wäre für die tägliche Nutzung nicht mehr auf einen Blick
        erfassbar. Die Planblatt-Gruppierung schaut deshalb bewusst nur auf die **direkten**
        Kind-Knoten der gewählten Station -- ein einfaches, konstantes mentales Modell ("eine Station
        hat Teams", nicht "eine Station hat eine Organisationshierarchie").
      - **`title` bleibt Freitext, mit Autovervollständigung aus bereits im Tenant verwendeten
        Bezeichnungen** -- keine neue, separat zu pflegende Stammdaten-Liste (das wäre ein weiterer
        Einstellungen-Screen, den eine Klinik vor dem ersten Einsatz erst füllen müsste) und bewusst
        nicht an `Skill` gekoppelt (das würde zwei unterschiedliche Konzepte -- Vertragsrolle vs.
        schicht-relevante Qualifikation -- künstlich vermischen). Die Autovervollständigung sorgt
        trotzdem für konsistente Schreibweisen ("Arzt" vs. "Ärztin" vs. "Arzt/Ärztin"), ohne eine
        Vorab-Konfiguration zu erzwingen -- tippt man einen neuen Titel, wird er beim nächsten Mal
        einfach mit vorgeschlagen.

18. ✅ **Geteilte Dienste (Split-Shifts): mehrere Zuweisungen pro Mitarbeiter und Tag** (2026-08).
    Nutzer-Feedback: in einer Vergleichsanwendung ("im Büro") lassen sich pro Tag zwei Dienste
    einplanen -- konkreter Praxisfall ICT: Frühdienst (07:00–12:00) und Spätdienst (13:00–17:30)
    werden am selben Tag von derselben Person geleistet, mit einer echten, variablen Mittagspause
    dazwischen statt einer festen Pause innerhalb eines einzigen Zeitfensters. Bewusst **nicht**
    über die bereits vorhandene Blockstruktur (`TimeTemplateSegment`, Block 1.9/1.12) gelöst:
    Segmente gehören zu *einem* `TimeTemplate` mit fixer Segmentanzahl/-reihenfolge -- hier sind es
    zwei eigenständige, potenziell unterschiedliche Schichttypen (andere Farbe, ggf. anderer
    `required_skill`, unabhängig tauschbar), die zufällig am selben Tag derselben Person zugewiesen
    sind. Umgesetzt wie in der ursprünglichen Skizze geplant, mit einer während der Umsetzung
    gefundenen Korrektur an der Ruhezeit-Prüfung (siehe unten).

    - **Backend**: `ShiftAssignment.unique_together` gelockert von `("employee", "date")` auf
      `("employee", "date", "template")` (Migration `0015`, rein additiv) -- identische Schicht
      zweimal am selben Tag bleibt weiterhin sinnlos und blockiert. Neue Regel-Engine-Prüfung
      `_check_no_overlap()` vergleicht alle Zuweisungen derselben Person am selben Tag paarweise
      auf Zeit-Überlappung (`_shifts_overlap()`, nutzt das bereits vorhandene `_shift_datetimes()`).
      `_check_daily_span()` (Art. 10 Abs. 3 ArG) wurde erweitert: die Tagesspanne ist jetzt "erster
      Arbeitsbeginn bis letztes Arbeitsende" über **alle** Zuweisungen des Tages kombiniert, nicht
      mehr nur die Spanne der einzelnen Zuweisung -- sonst liesse sich die gesetzliche Tagesgrenze
      durch Aufteilen in mehrere kurze Templates umgehen.
    - **Korrektur gegenüber der ursprünglichen Skizze**: die 11h-Ruhezeit-Prüfung
      (`_check_rest_period`, Art. 15a ArG) wurde bewusst **nicht** verändert. Die erste Skizze
      dieses Punkts nahm an, die Lücke zwischen zwei Diensten desselben Tages (die Mittagspause)
      müsse ebenfalls gegen die 11h-Ruhezeit geprüft werden -- das ist rechtlich falsch: Art. 15a
      ArG regelt die Ruhezeit *zwischen Kalendertagen*, nicht Pausen *innerhalb* eines
      Arbeitstages. Eine 1h-Mittagspause zwischen Früh- und Spätdienst hätte sonst grundsätzlich
      geblockt, was das Feature für den namensgebenden Anwendungsfall unbrauchbar gemacht hätte.
      `_check_rest_period()` vergleicht wie bisher nur gegen Zuweisungen an `date - 1`/`date + 1`
      (Vor-/Folgetag) -- Zuweisungen am selben Tag werden von dieser Abfrage strukturell gar nicht
      erst erfasst, was sich beim Umsetzen als bereits korrekt herausstellte. Die Begrenzung des
      Arbeitstages selbst übernimmt stattdessen die oben beschriebene erweiterte
      `_check_daily_span()`.
    - **Transienter Validierungs-Fallstrick beim Swap/Tausch**: `ShiftAssignment.swap()` (Block
      2.8) und `ShiftTradeRequest.approve()` prüfen während einer laufenden Zwei-Zeilen-Transaktion
      testweise per `full_clean(validate_unique=False)`, sähen dabei aber ohne Weiteres noch den
      Vor-Tausch-Datenbankstand des jeweils anderen Tauschpartners und würden `_check_no_overlap()`
      sowie die erweiterte `_check_daily_span()` fälschlich einen Konflikt mit sich selbst melden
      lassen (dasselbe Muster wie beim bereits bekannten `validate_unique`-Problem, siehe Block
      2.8). Gelöst über ein transientes (nicht persistiertes) Attribut `_overlap_exclude_pks`, das
      beide Methoden vor dem `full_clean()`-Aufruf auf beide beteiligten Zeilen-IDs setzen -- im
      Normalfall (Speichern über die API) ist das Attribut nicht gesetzt und es gilt schlicht "alle
      anderen Zuweisungen ausser mir selbst". Die manuellen Drittkonflikt-Checks in beiden Methoden
      wurden von "irgendeine andere Zuweisung am selben Tag" auf "eine zeitlich überschneidende"
      umgestellt (`_overlapping_conflict()`, gemeinsam mit `_check_no_overlap()` genutzt).
    - **Planblatt-UX**: eine Tageszelle mit mehreren Zuweisungen zeigt zwei kompakte Chips
      übereinander (chronologisch nach Beginnzeit sortiert) statt eines -- technisch zwei
      `ShiftCell`-Instanzen pro Zelle statt einer, jede mit vollem Funktionsumfang (Ziehen/Ablegen
      inkl. echtem Swap, Diensttausch-Angebot, Ist-Zeit-Erfassung), da dieselbe, bereits bewährte
      Komponente einfach zweimal instanziiert wird statt neu gebaut zu werden. Ein zweiter,
      leerer "+"-Slot erscheint nur für Admin/Planer, sobald der erste Slot befüllt ist (kein
      unbedienbares leeres "+" für reine Betrachter); das Wunschfrei/Wunschdienst-Badge erscheint
      bewusst nur am ersten Slot (`ShiftPreference` gilt personen-/tagesweise, nicht pro
      Zuweisung -- ein zweites Badge wäre ein irreführendes Duplikat).
    - **Ziehen/Ablegen wurde auf Zuweisungs-IDs statt Person+Datum umgestellt**: da eine
      Tageszelle jetzt mehrdeutig sein kann, trägt der Drag jetzt die konkrete `assignmentId` im
      Payload statt sie beim Ablegen über employee+date neu (und seit Split-Shifts mehrdeutig)
      aufzulösen -- eine sauberere, eindeutige Lösung statt eines Sonderfalls für "welcher von
      zwei Diensten wurde gezogen".
    - **Wochenmuster-Kopieren** (`handleCopyWeekPattern`) kopiert jetzt alle Zuweisungen eines
      Quelltages (nicht mehr nur die erste) und wurde dabei zusätzlich auf die eigene Team-Zeile
      eingeschränkt (`rowNodeId`-Filter) -- vorher las die Funktion die global erste Zuweisung
      eines Tages unabhängig vom Team, was bei Mehrfachanstellung (Punkt 17) latent falsch war und
      durch die Umstellung auf Arrays ohnehin entschieden werden musste.
    - **Bewusst unverändert**: die Mehrfachauswahl-Stempelleiste (Block 2.13-Vorläufer) bleibt auf
      den ersten Slot einer Zeile beschränkt -- kein UI für "welchen von zwei Slots stempeln" beim
      Massen-Zuweisen, ein zweiter Dienst bleibt eine bewusste Einzelzell-Aktion im normalen
      Zuweisungs-Dropdown. `weekly_hours_summary`/`monthly_summary`/`time_account_summary`
      brauchten keine Änderung -- sie summierten schon vor diesem Punkt über eine Ergebnismenge
      (nicht `.get()` einer einzelnen Zeile) und funktionieren dadurch bereits korrekt für mehrere
      Zuweisungen pro Tag. `TimeRecord` bleibt unverändert 1:1 pro `ShiftAssignment` -- zwei
      Dienste ergeben automatisch zwei unabhängig erfassbare Ist-Zeiten.
    - **Bekannte Lücke**: `YearPlan.jsx` (Jahresplan-Tab) zeigt bei einem Split-Shift-Tag weiterhin
      nur eine (die zuletzt geladene, nicht notwendigerweise chronologisch erste) Zuweisung --
      `assignmentByDate` ist dort unverändert einwertig. Bewusst nicht in diesem Durchgang
      mitgezogen: das Planblatt ist der primäre Bearbeitungsort für Split-Shifts, der Jahresplan
      ein sekundärer Kalenderüberblick pro Person. Niedrigere Priorität, sollte aber nachgezogen
      werden, falls Split-Shifts in der Praxis auch von dort aus verwaltet werden sollen.
    - **Menge der Slots bewusst auf zwei begrenzt** (nicht technisch erzwungen -- die Regel-Engine
      erlaubt beliebig viele nicht überlappende Zuweisungen pro Tag, nur das Planblatt-UI zeigt
      höchstens zwei Chips): der genannte Praxisfall (Früh + Spät) braucht genau zwei, ein drittes
      UI-Slot hätte die Zelle unnötig unübersichtlich gemacht, ohne einen bekannten realen
      Bedarf zu bedienen.
    - Getestet: `SplitShiftTests`/`ShiftTradeRequestSplitShiftTests` (`scheduling/tests.py`) --
      nicht überlappende Zuweisungen gültig, überlappende abgelehnt, identisches Template zweimal
      abgelehnt, exakt angrenzende Zuweisungen (Ende == Beginn) gültig, kombinierte Tagesspanne,
      Ruhezeit ignoriert die Tageslücke aber greift weiterhin zum Vor-/Folgetag, Wochen-/
      Monatsauswertung summiert beide Schichten, Swap eines von zwei Tages-Slots, Swap lehnt
      echten Drittkonflikt ab, `ShiftTradeRequest.approve()` mit bereits vorhandenem zweitem
      Dienst. Mit Playwright im Browser gegen die echten Testheim-Daten verifiziert: zwei Chips
      übereinander nach Doppelzuweisung, Fehlermeldung bei Überlappungsversuch im UI (Banner statt
      stillem Fehlschlag), Ziehen eines einzelnen Slots auf einen anderen Tag lässt den anderen
      Slot unverändert zurück.

    - ✅ **Bugfix/Härtung (2026-08): `ManyToManyField`s in Serializern konnten `TypeError: Direct
      assignment to the forward side of a many-to-many set is prohibited` auslösen.** Betroffen
      war `EmployeeSerializer` (`Employee.skills`, echtes M2M-Feld) -- dessen eigene
      `create()`/`update()`-Logik (nötig für die verschachtelte `employments`-Synchronisation,
      Punkt 17 oben) reichte `validated_data` unverändert an `Employee.objects.create(**...)` bzw.
      einen generischen `setattr(instance, field, value)`-Loop weiter. DRFs eigene
      `ModelSerializer`-Basisklasse filtert M2M-Felder automatisch heraus, bevor sie das tut --
      genau diesen Schutz hebelt jede eigene `create()`/`update()`-Überschreibung aus, sofern sie
      ihn nicht selbst nachbaut. Live in Produktion: `EmployeeSettings.jsx` schickt `skills` bei
      jedem Anlegen/Ändern eines Mitarbeitenden mit, kein bestehender Test rief je `POST
      /api/employees/` auf, daher unbemerkt. Behoben durch zwei wiederverwendbare Helfer
      (`pop_m2m_fields`/`set_m2m_fields` in `scheduling/serializers.py`, basierend auf
      `Model._meta.many_to_many`-Introspektion statt hartkodierter Feldnamen): trennen alle
      M2M-Felder aus `validated_data` heraus, bevor die Instanz erzeugt/gespeichert wird, und
      synchronisieren sie danach explizit über `.set()`. Angewendet auf `EmployeeSerializer`
      (der eigentliche Bug) sowie defensiv auf `TimeTemplateSerializer`/`TimeRecordSerializer`
      (aktuell keine M2M-Felder, aber dieselbe eigene create()/update()-Struktur -- schützt
      automatisch, falls dort je ein M2M-Feld dazukommt). Alle anderen Serializer mit
      `setattr`-Nutzung (`ShiftAssignment`/`Absence`/`ShiftPreference`/`ShiftTradeRequest`) wurden
      geprüft und sind unkritisch: sie schreiben nur ein festes Whitelist von Nicht-M2M-Feldern auf
      eine Wegwerf-Instanz, rein um `clean()` vor dem eigentlichen Speichern zu triggern, und keines
      der zugehörigen Models hat ein M2M-Feld. Getestet (`EmployeeSkillsM2MTests`,
      `scheduling/tests.py`): POST mit initialer Skill-Zuweisung, POST mit leerer Skill-Liste,
      PATCH ändert Skills, PATCH entfernt alle Skills, PATCH kombiniert normale Felder + Skills in
      einem Request, PATCH ohne `skills`-Key lässt bestehende Skills unangetastet, Kombination aus
      Skills- und `employments`-Sync in einem Request (Sonderlogik aus Punkt 17 bleibt intakt).

    - ✅ **Bugfix (2026-08): Mehrfachauswahl-Stempelleiste konnte keinen zweiten (Split-Shift-)
      Dienst setzen -- weder im Planblatt noch im Jahresplan.** Nutzer-Feedback: "ich kann keinen
      zweiten Dienst eintragen in der Mehrfachauswahl". Zwei unabhängige Ursachen:
      1. **Datenbank-Constraint nicht nachgezogen**: Migration `0015_alter_shiftassignment_
         unique_together` (lockert `unique_together` von `(employee, date)` auf
         `(employee, date, template)`, siehe Split-Shifts oben) war zwar im Code vorhanden, aber
         nie gegen die getrackte `db.sqlite3` ausgeführt worden -- jede zweite Zuweisung
         desselben Mitarbeitenden/Tages schlug serverseitig mit `IntegrityError: UNIQUE constraint
         failed` (HTTP 500) fehl, unabhängig vom Eingabeweg (Mehrfachauswahl UND Einzelzell-
         Dropdown betroffen). Behoben durch `python manage.py migrate` gegen die getrackte
         Datenbank; die Migration selbst musste nicht geändert werden.
      2. **UI-seitig fehlte der Weg überhaupt**: die Stempelleiste zielte hart auf den ersten Slot
         einer Zelle/eines Tages (`handleStampAssign`/`handleStampShift`), ein zweiter Dienst war
         nur über das Einzelzell-Dropdown erreichbar -- im Jahresplan (`YearPlan.jsx`) sogar gar
         nicht, weil dort *jeder* Tagesklick ohnehin der Mehrfachauswahl-Mechanismus ist (keine
         Einzelzell-Alternative) und `assignmentByDate` pro Tag zusätzlich nur eine Zuweisung hielt
         (eine zweite wurde beim Aufbau der Map stillschweigend überschrieben -- Split-Shifts waren
         im Jahresplan dadurch nicht einmal sichtbar, nicht nur nicht stempelbar). Behoben durch
         einen neuen Umschalter "Als zweiten Dienst hinzufügen" in beiden Stempelleisten
         (`stampSecondSlot`-State): zielt aktiv auf den zweiten statt den ersten Slot, überspringt
         Tage ohne bestehenden ersten Dienst (analog zum "+"-Slot im Einzelzell-Dropdown, der
         ebenfalls erst ab einer vorhandenen ersten Zuweisung erscheint). `YearPlan.jsx`:
         `assignmentByDate` (Einzelwert) → `assignmentsByDate` (Array pro Datum, chronologisch
         sortiert wie in `PlanGrid.jsx`); die kompakte Jahres-Tageskachel zeigt einen Split-Shift
         jetzt als diagonal zweigeteilte Füllung (beide Schichtfarben) mit beiden Diensten im
         Tooltip. `PlanGrid.jsx`: die Mehrfachauswahl-Zelle (ein einzelnes `ShiftCell` pro Tag,
         anders als die Normalansicht mit bis zu zwei `renderSlot`-Instanzen) zeigte nach dem
         Stempeln des zweiten Slots weiterhin nur den ersten Chip -- `ShiftCell.jsx` bekam dafür
         eine rein informative `secondTemplateInfo`-Prop für einen zweiten Chip in der
         Mehrfachauswahl-Ansicht. Mit Playwright gegen die echten Testheim-Daten verifiziert:
         zwei nicht überlappende Dienste (`Therapie Vormittag`/`Therapie Nachmittag`) per
         Mehrfachauswahl auf denselben Tag gestempelt -- beide Chips im Planblatt, beide im
         Jahresplan-Tooltip + geteilte Tageskachel-Füllung; ein Tag ohne ersten Dienst wird bei
         aktivem Umschalter korrekt übersprungen statt einen "zweiten" Dienst ohne ersten
         anzulegen.

19. ✅ **Automatisierte Planung (One-Click Planning)** (2026-08). Admin/Planer wählen eine Station
    und einen Monat, klicken "Automatisch planen" -- `scheduling/planning.py` (Google OR-Tools
    CP-SAT) generiert einen vollständigen, regelkonformen Dienstplan-Entwurf, statt jede Schicht
    manuell zu ziehen. Explizite Nutzer-Vorgabe ("Ich will eine extrem intuitive Automatisierung.
    Sie soll alles berücksichtigen. Das wird eines meiner Key Features im Verkauf") verwarf einen
    ursprünglich vorgeschlagenen, funktional eingeschränkten MVP-Zuschnitt: der Solver bildet
    **exakt dieselben Regeln wie die manuelle Planung** ab, keine Teilmenge davon.

    - **Volle Regel-Parität mit `ShiftAssignment.clean()`**: Ruhezeit (inkl. Jugendschutz-Sonderfall),
      Höchstarbeitszeit pro Woche, Pausenregelung (Art. 15 ArG, Templates, die sie nicht erfüllen
      können, werden mit Warnung von der Automatik ausgeschlossen), Tagesspanne, Wochenruhetag,
      Qualifikation/`required_skill`, Jugendschutz (kein Nacht-/Sonntagsdienst für Minderjährige),
      Mutterschutz (`full_ban`/`consent_required`/`night_ban` je nach Schwangerschaftsstatus),
      Absenz-Konflikte (inkl. Halbtags-Präzision über `Absence._half_day_window()`) -- und, gemäss
      der "alles berücksichtigen"-Vorgabe, auch **Pikett/Spezialitäten** (`category=SPECIAL`, mit
      einem eigenen Ausgleichs-Term, der Pikett-Zuweisungen gleichmässig über berechtigte
      Mitarbeitende verteilt) und **Split-Shifts** (mehrere nicht überlappende Zuweisungen pro
      Tag, README Punkt 18). Bestehende, bereits manuell erstellte Zuweisungen im Zielmonat werden
      nie verändert, nur als Konstanten in alle Regeln einbezogen und um sie herum ergänzt.
    - **Mindestbesetzung als echte Zielgrösse**: anders als das bisher rein informative
      Dashboard-Badge (Block 2.9) erzwingt der Solver `minimum_staffing` als harte Ober- **und**
      Untergrenze (eine Schlupfvariable erlaubt kontrollierten Fehlbedarf statt Unlösbarkeit, statt
      unnötig zu überbesetzen). Weiche Ziele (sollen möglichst gut erfüllt, blockieren aber nichts):
      `ShiftPreference`-Wünsche (Block 2.13, Wunschdienst bevorzugt/Wunschfrei vermieden, aber mit
      Warnung überschreibbar), Fairness-Bias über die Bonus-Punkte aus Punkt 20 (unpopuläre
      Schichten bevorzugt an Mitarbeitende mit aktuell niedrigem Punktestand) und Monats-Soll-Nähe
      (`Employee.target_hours_for_period()`, neu, verallgemeinert das `time_account_summary()`-Muster
      auf einen beliebigen Zeitraum statt "Jahresbeginn bis heute").
    - **Vorschau statt Blindautomatik**: `GeneratePlanView` (`GET /api/plan-generate/`) liest nur
      und schreibt nie in die Datenbank -- das Ergebnis ist eine Diff-Ansicht im Planblatt (neue
      Vorschläge mit gestricheltem Rand, `DraftShiftChip.jsx`/`DraftSpecialDot`), **alle Vorschläge
      sofort sichtbar und einzeln per "×" entfernbar** (bestätigte Nutzer-Vorgabe, keine
      Alles-oder-Nichts-Übernahme). Erst ein bewusster Klick auf "Entwurf übernehmen"
      (`CommitPlanView`, `POST /api/plan-commit/`) persistiert die ggf. reduzierte Liste --
      zeilenweise validiert (`commit_draft_assignments()`, eigener Savepoint pro Zeile), damit eine
      einzelne zwischenzeitlich kollidierende Zeile (z. B. eine parallel manuell gestempelte
      Schicht) nicht die ganze Übernahme blockiert, sondern nur diese eine Zeile überspringt.
    - **Umgang mit Unlösbarkeit**: liefert der Solver keine vollständige Lösung, ist das Ergebnis
      trotzdem der bestmögliche Teilentwurf plus eine für Menschen lesbare Warnliste ("Frühdienst
      am 07.09.2026: nur 0 von 1 Person(en) verfügbar").
    - **Scope pro Lauf**: eine Station + ein Monat, synchroner Request (`SOLVER_TIME_LIMIT_SECONDS
      = 20`, `num_search_workers = 8`) -- eine Architekturgrenze, kein funktionaler Kompromiss: es
      gibt keine Task-Queue/Celery im Projekt, alle bestehenden Views sind synchron. Ein Planer
      wiederholt den Lauf pro Station, um eine ganze Einrichtung abzudecken; ein tenant-weiter
      Mehrmonats-Lauf bliebe eine benannte künftige Erweiterung mit echter
      Hintergrund-Task-Infrastruktur. Zusätzlich als CLI verfügbar:
      `python manage.py generate_plan --node <id> --year <jjjj> --month <m>` (nur Vorschau; erst
      `--commit` speichert -- bewusste Umkehrung der `--dry-run`-Konvention anderer Commands, weil
      "nie blind speichern" das Kernversprechen dieses Features ist).

20. ✅ **Bonus-/Fairness-Punktesystem für unpopuläre Schichten** (2026-08). Ziel: sichtbar und
    nachvollziehbar machen, wer wie oft unpopuläre Schichten (Sonntag, Nacht) übernommen hat --
    sowohl als Transparenz-/Planungshilfe für Admin/Planer als auch als künftige Fairness-Eingabe
    für die automatisierte Planung (Punkt 19 oben), damit nicht dieselbe Person systematisch
    überproportional oft Sonntags-/Nachtdienst leistet.

    **Nachbesserung ggü. dem ursprünglichen Entwurf** (Nutzer-Feedback: "wie würdest du das
    Bonussystem am intuitivsten aufbauen?", drei Designfragen neu durchdacht statt den
    Erstentwurf unverändert umzusetzen):

    - **Beide Kategorien einheitlich in Stunden statt gemischter Einheiten**: der Erstentwurf
      hatte "1 Punkt pro Sonntagsdienst" (pro Schicht) gegen "0.5 Punkte pro Nachtstunde" (pro
      Stunde) gesetzt -- damit hätte eine 2h- und eine 12h-Sonntagsschicht gleich viel gezählt,
      obwohl die tatsächliche Belastung sehr unterschiedlich ist. Jetzt beide Signale konsistent
      auf Stundenbasis: `Tenant.sunday_shift_bonus_points_per_hour` × Sonntagsstunden +
      `Tenant.night_shift_bonus_points_per_hour` × Nachtstunden, beide neue, konfigurierbare
      `FloatField`s nach demselben Muster wie `overtime_surcharge_pct`/`sunday_work_surcharge_pct`.
      Bewusst weiterhin keine manuelle Punktevergabe pro Schichttyp -- baut auf den bereits
      vorhandenen, pro Zuweisung berechneten Signalen `ShiftAssignment.is_sunday`/`night_hours`
      (Block 1.5/1.6) auf, neue Schichttypen sind automatisch korrekt eingebunden.
    - **Wunschdienste zählen nicht als Belastung**: eine Zuweisung, für die am selben Tag ein
      `ShiftPreference` vom Typ `wunschdienst` (Block 2.13) existiert, fliesst nicht in die
      Fairness-Punkte ein -- wer sich die Schicht gewünscht hat, wurde dadurch nicht unfair
      behandelt. Ohne diese Ausnahme hätte die Zahl Leute, die freiwillig oft Sonntag/Nacht
      übernehmen, fälschlich als "benachteiligt" ausgewiesen.
    - **Gleitendes 365-Tage-Fenster statt Kalenderjahr oder Dienstjahr**: bewusst NICHT das
      Kalenderjahr-Muster von `night_work_summary()`/`vacation_balance()` übernommen (harter
      Reset am 1. Januar würde die reale, über den Jahreswechsel hinweg spürbare Belastung
      verschleiern) und auch NICHT das Dienstjahr-Muster der Lohnfortzahlung (an
      `employment_start_date` gekoppelt -- für Fairness sachlich nicht begründbar, "wie lange
      bin ich schon hier" hat nichts mit "wie oft war ich zuletzt an der Reihe" zu tun). Stattdessen
      täglich gleitend: die letzten 365 Tage ab dem Stichtag, kein fixer Reset-Zeitpunkt.
    - **Vergleichsbasis**: Durchschnitt über alle aktiven Mitarbeitenden, die mindestens eine
      Station mit der Person teilen (`Employee.nodes`-Schnittmenge, dieselbe Abgrenzung wie bei
      `effective_cost_center()`, Punkt 31), inkl. der Person selbst -- **auf Vollzeit (100%)
      normalisiert** (Nutzer-Feedback nach Erstauslieferung: "wird das Pensum berücksichtigt?").
      Ein roher Punkte-Vergleich hätte Teilzeit-Mitarbeitende systematisch benachteiligt -- wer 40%
      arbeitet, hat schlicht weniger Gelegenheit, Sonntags-/Nachtschichten zu übernehmen, unabhängig
      davon, ob die Verteilung untereinander fair ist. Der Team-Durchschnitt wird deshalb je
      Kolleg:in auf Punkte/100%-Pensum umgerechnet, gemittelt und danach auf das eigene Pensum
      zurückskaliert -- bleibt dadurch direkt mit `points` vergleichbar, ohne eine zusätzliche,
      erklärungsbedürftige Kennzahl in der Badge anzuzeigen.
    - **Transparente Bausteine statt einer Blackbox-Zahl**: `Employee.fairness_summary()`
      (`scheduling/models.py`, Aufbau analog `night_work_summary()`) liefert Sonntagsstunden,
      Nachtstunden, deren Einzelpunktzahlen sowie die Summe und den Team-Durchschnitt getrennt --
      konsistent mit dem Rest der App (`monthly_summary()` etc.), nicht nur eine Endsumme.
    - **API**: `GET /api/employees/<id>/fairness/` (`EmployeeViewSet.fairness`, analog
      `night_work`/`balance`), Lesen für alle vier Rollen offen wie die übrigen
      Selbstauskunfts-Endpunkte.
    - **Sichtbarkeit bewusst auf Admin/Planer beschränkt** (Rückfrage beantwortet): Badge
      (`FairnessBadge.jsx`, analog `BalanceBadge.jsx`, gleiches `onBalanceChanged`-Pub/Sub wie
      der Saldo) nur in der Mitarbeitendenliste (`EmployeeSettings.jsx`), NICHT zusätzlich in der
      Topbar für Mitarbeitende selbst -- ein sichtbarer Punkte-Vergleich mit Kolleg:innen könnte
      in manchen Teams unerwünschten Konkurrenzdruck erzeugen, anders als der bestehende Saldo,
      der rein personenbezogen ist und nie verglichen wird. Kann jederzeit später ergänzt werden.
    - **Verzahnung mit Punkt 19** (umgesetzt, siehe dort): der Solver berücksichtigt beim Verteilen
      einer unpopulären Schicht neben den harten Regeln den *aktuellen* Punktestand aller in Frage
      kommenden Mitarbeitenden als weichen Zielfunktions-Term (`scheduling/planning.py`:
      `FAIRNESS_TIE_BREAK_WEIGHT`, gespeist aus `Employee._bulk_fairness_points()`) -- wer zuletzt
      überdurchschnittlich oft Sonntag/Nacht gemacht hat, wird bei der nächsten automatischen
      Zuteilung tendenziell übersprungen, ohne dass das je hart erzwungen würde.
    - **Performance-Fix**: die ursprüngliche Team-Durchschnitt-Berechnung hat pro Kolleg:in zwei
      eigene Datenbankabfragen ausgelöst (O(Teamgrösse) Queries bei JEDEM `fairness_summary()`-
      Aufruf) -- bei z. B. 20 Mitarbeitenden auf derselben Station also ~40 zusätzliche Abfragen
      *pro Badge* auf der Mitarbeitendenliste, spürbar langsam ab ca. 15-20 Mitarbeitenden.
      `Employee._bulk_fairness_points()` ersetzt das durch je eine Query für alle Zuweisungen/
      Wunschdienste der ganzen Vergleichsgruppe, danach in Python pro Mitarbeiter aggregiert --
      konstant statt quadratisch mit der Teamgrösse.
    - **Bulk-Endpoint (Nutzer-Feedback, zweite Performance-Runde)**: der N+1-Fix oben behebt die
      Query-Last pro Request, aber die Mitarbeitendenliste blieb trotzdem spürbar langsam -- jede
      Zeile lud Saldo UND Fairness über zwei eigene HTTP-Requests (`BalanceBadge.jsx`/
      `FairnessBadge.jsx`), bei 20 Mitarbeitenden also 40 Requests, ausgebremst durch die
      Verbindungslimite des Browsers (~6 gleichzeitige Requests pro Host). Neuer Endpoint `GET
      /api/employees/balance-fairness-bulk/?ids=1,2,3` (`EmployeeViewSet.balance_fairness_bulk`)
      liefert Saldo+Fairness für eine ganze Liste von IDs in einem einzigen Request.
      `BalanceBadge.jsx`/`FairnessBadge.jsx` bekommen dafür einen optionalen `data`-Prop: wird er
      übergeben, holt sich die Badge ihre Daten NICHT mehr selbst (Bulk-Modus) -- ohne Prop
      (Topbar-Saldo, Saldo-Spalte im Planblatt-Grid) bleibt das bisherige Selbst-Laden unverändert.
      `EmployeeSettings.jsx` lädt die sichtbare Tabellenseite jetzt mit einem Bulk-Request statt
      2×N Einzelrequests, inkl. einem einzigen `onBalanceChanged`-Abo auf Tabellenebene statt N×2
      Einzel-Abos in den Badges.

21. ✅ **Dashboard/Übersicht für Admin/Planer** (2026-08). Nutzer-Anfrage: eine zentrale Seite,
    auf der auf einen Blick sichtbar ist, was gerade Handlungsbedarf hat -- offene Genehmigungen,
    wer wann abwesend ist, unterbesetzte Schichten -- statt das über mehrere Tabs verstreut selbst
    zusammensuchen zu müssen. Neuer Tab "Übersicht" (`Dashboard.jsx`), nur für Admin/Planer
    sichtbar (`managerOnly`, gleiches Muster wie der bestehende "Einstellungen"-Tab).

    - **Aggregations-, keine Zweitbearbeitungs-Oberfläche für die Datenbasis**: "Offene
      Absenzanträge" und "Diensttausch wartet auf Freigabe" laden dieselben Endpoints wie
      `AbsencePanel.jsx`/`TradeRequestPanel.jsx` (`api.getAbsences()`/`api.getShiftTradeRequests()`)
      und filtern clientseitig nach denselben Kriterien wie `core.views._task_counts`
      (`status === "pending"` bzw. `"employee_accepted"`) -- keine eigene, potenziell abweichende
      Business-Logik. Einzige neue Backend-Komponente ist `GET /api/understaffed-shifts/`
      (`scheduling.views.UnderstaffedShiftsView`): die Mindestbesetzungs-Auswertung (Block 9/2.9)
      lief bisher nur clientseitig in `PlanGrid.jsx`, pro einzeln gewählter Station -- fürs
      Dashboard braucht es den ganzen Tenant auf einen Blick. Entscheidung aus der ursprünglichen
      "offenen technischen Frage": ein serverseitiges `GROUP BY (template, date)` über alle
      Stationen statt eines Frontend-Loops mit einem Request pro Station. Bewusst nur die nächsten
      7 Tage (`UPCOMING_DAYS`) -- weiter in der Zukunft ist typischerweise noch nicht geplant, ein
      "unterbesetzt" wäre dort nur Rauschen statt Signal. "Wer ist heute abwesend" kommt komplett
      ohne neuen Endpoint aus (`api.getAbsences()`, clientseitig auf `status === "approved"` und
      Datumsüberlappung mit heute gefiltert).
    - **Quick-Actions direkt im Dashboard, ohne Logik-Duplikat**: Genehmigen/Ablehnen ruft exakt
      dieselben `api.js`-Funktionen auf wie `AbsencePanel.jsx`/`TradeRequestPanel.jsx`
      (`approveAbsence`/`rejectAbsence`/`approveShiftTradeRequest`/`rejectShiftTradeRequest`) --
      reine UI-Wiederverwendung der Mutation. Nach einer Aktion verschwindet der Eintrag automatisch
      aus der Liste (derselbe `useMemo`-Filter, der ihn ursprünglich zeigte, greift danach nicht
      mehr), kein manuelles Neuladen nötig.
    - **Deep-Links statt genereller Tab-Wechsel**: eine "Unterbesetzt"-Karte öffnet über
      `onNavigate({ tab: "grid", nodeId, year, month })` direkt Station + Monat im Planblatt (App.jsx:
      `handleNavigate`), nicht nur generisch den Tab "Planblatt". Absenz-/Tauschanfrage-Karten haben
      zusätzlich einen "Details"-Link in den jeweiligen Tab, für alles, was über die Quick-Action
      hinausgeht (z. B. eine Absenz löschen statt nur genehmigen/ablehnen).
    - **Zusätzlicher Tab, keine neue Standard-Landing-Page**: die Diskussion aus der ursprünglichen
      Planung wurde zugunsten von "kein zusätzlicher Klick beim schnellen Wieder-Einloggen für eine
      einzelne Aufgabe" entschieden -- wer sich nur kurz einloggt, um eine Schicht einzutragen,
      landet weiterhin direkt im Planblatt wie bisher.
    - **Bewusst kein Reporting-/Auswertungstool**: nur "was JETZT ansteht", keine historischen
      Kennzahlen, Charts oder Zeitraum-Filter.
    - Getestet (`scheduling/tests.py`: `UnderstaffedShiftsViewTests`,
      `UnderstaffedShiftsTenantIsolationTests`): Authentifizierung erforderlich, Templates ohne
      Mindestbesetzung nie gelistet, Unterbesetzung korrekt innerhalb des 7-Tage-Fensters erkannt,
      ausreichend besetzte Tage fehlen in der Liste, Tage ausserhalb des Fensters (Vergangenheit wie
      Zukunft) nicht gelistet, Tenant-Isolation. Mit Playwright gegen die echten Testheim-Daten
      verifiziert: Tab nur für Admin/Planer sichtbar, alle vier Kategorien zeigen echte Daten
      korrekt an, Genehmigen/Ablehnen-Quick-Actions funktionieren und aktualisieren die Liste
      reaktiv, Deep-Link zu einer unterbesetzten Schicht wechselt Tab UND Station korrekt.
    - **UX-Bugfix (2026-08)**: Nutzer-Feedback ("Das Dashboard 'Übersicht' ist nichtssagend, ich
      sehe nur 'Heute Abwesend' sonst gar nichts") -- kein echter Datenfehler, aber ein reales
      UX-Problem. `Dashboard.jsx` blendete eine Sektion komplett aus, sobald ihre Liste leer war,
      statt aktiv "alles im grünen Bereich" zu bestätigen -- bei wenig offenen Fällen (der
      Normalzustand) wirkte die Seite dadurch wie kaputt/leer statt informativ. Jede der vier
      Sektionen wird jetzt immer gerendert und zeigt bei leerer Liste eine eigene, positive
      Bestätigungszeile (z. B. "Keine offenen Absenzanträge."). Zusätzlich musste
      `UnderstaffedShiftsView` (Backend) um `has_configured_templates` erweitert werden: eine
      leere `shortfalls`-Liste war bisher doppeldeutig -- sie bedeutete entweder "aktuell überall
      ausreichend besetzt" ODER "für keinen Schichttyp ist überhaupt eine Mindestbesetzung
      hinterlegt" (Punkt 2.9 ist rein optional, `minimum_staffing` defaultet auf 0). Ohne dieses
      Flag hätte die Karte im häufigen Fall "noch nirgends konfiguriert" fälschlich "voll besetzt"
      suggeriert; jetzt zeigt sie stattdessen einen Hinweis mit direktem Link in die Einstellungen.
      Response-Form geändert von `[...]`/bare Liste zu `{"has_configured_templates": bool,
      "shortfalls": [...]}`. Getestet (`scheduling/tests.py`:
      `UnderstaffedShiftsViewTests.test_no_templates_configured_reports_has_configured_templates_false`,
      `test_configured_templates_report_has_configured_templates_true`, plus alle bestehenden
      Understaffed-Tests an die neue Response-Form angepasst). Mit Playwright gegen die echten
      Testheim-Daten verifiziert: alle vier Sektionen zeigen im aktuellen Datenstand (0 offene
      Absenzen, 0 wartende Tauschanfragen, 1 Abwesenheit heute, keine Mindestbesetzung
      konfiguriert) korrekt ihren jeweiligen Zustand statt zu verschwinden; nach testweisem Setzen
      einer `minimum_staffing` zeigt die Karte korrekt die entstandenen Unterbesetzungen.

22. ✅ **Drei Nutzer-Feedbacks zum Planblatt (2026-08)**: nach dem Icon-Toolbar-Redesign (Block 2
    Punkt 11) drei konkrete Rückmeldungen behoben.
    - ✅ **Mehrere Spezialitäten pro Tag waren additiv möglich, aber nicht erkennbar**: der
      Eck-Badge (`SpecialBadge.jsx`, Block 2 Punkt 11) zeigte bisher nur einen einzelnen,
      neutral gefärbten Punkt plus Zähler ("+2") -- bei zwei verschiedenen Spezialitäten (z. B.
      Pikett UND Rufbereitschaft am selben Tag) war weder erkennbar, welche Farben das sind,
      noch wie viele es genau sind, ohne das Popover zu öffnen. Neu: ein eigener, individuell
      gefärbter Punkt (`--chip-color` der jeweiligen Spezialität) pro Zuweisung, alle
      nebeneinander im Eck (`.special-day-badges`/`.special-day-dot`), gemeinsames Popover für
      Details/Entfernen bleibt.
    - ✅ **Spezialitäten liessen sich nicht über die Toolbar entfernen**: im Pikett-Modus der
      `PlacementToolbar.jsx` fehlte der Radiergummi komplett (nur einzeln über das
      `SpecialBadge`-Popover löschbar) -- ergänzt, entfernt beim Klick alle Spezialitäten der
      markierten Tage. Ausserdem toggelt ein erneuter Klick auf dasselbe Spezialität-Icon jetzt
      ab statt einen Duplikat-Eintrag zu erzeugen (`applyToolToCell` in `PlanGrid.jsx` prüft, ob
      die Zuweisung schon existiert).
    - ✅ **Dienst- und Absenz-Stempel in der Toolbar waren nicht klar getrennt**: Nutzer-Feedback
      explizit zu den Icons in der oberen Werkzeugleiste (nicht zur Tageszelle selbst) --
      `PlacementToolbar.jsx` gruppiert Dienst-Icons und Absenz-Icons jetzt in zwei sichtbar
      abgetrennte Blöcke (`.placement-palette-group`, `.placement-palette-divider`) statt einer
      einzigen ununterschiedenen Reihe.
    - ✅ **Halbtags-Absenzen** ("ich kann auch einen Nachmittag frei nehmen"): `Absence` hat ein
      neues Feld `day_portion` (`full`/`morning`/`afternoon`, Migration
      `0021_absence_day_portion`, additiv mit Default `full`), nur bei einem einzelnen Tag
      wählbar (`start_date == end_date`, in `Absence.clean()` erzwungen -- das Formular
      deaktiviert das Auswahlfeld sonst automatisch). Fester Mittagsschnitt um 12:00 (kein
      tenant-konfigurierbares Feld, bewusste Vereinfachung). Konfliktprüfung zwischen Absenz und
      `ShiftAssignment` ist jetzt zeitbewusst statt den ganzen Tag zu blockieren
      (`Absence._half_day_window()` liefert das Start/Ende-Zeitfenster, sowohl
      `ShiftAssignment._check_no_absence_conflict()` als auch die umgekehrte Prüfung in
      `Absence.clean()` vergleichen echte Zeitüberlappung) -- ein Vormittagsdienst neben einer
      Nachmittags-Absenz ist damit möglich, ein überlappender Dienst weiterhin blockiert.
      `Employee.vacation_balance()` zieht bei einer Halbtags-Absenz nur 0.5 Ferientage statt
      einem ganzen Tag ab (verifiziert exakt gegen das vom Nutzer genannte Beispiel: 100%-Pensum,
      25 Ferientage Anspruch, ein freier Nachmittag → 24.5 Tage verbleibend). Ferienanspruch
      selbst bleibt weiterhin eine feste, nicht pensumsskalierte Zahl (unverändertes
      Bestandsverhalten, vom Nutzer mit der Rückfrage bestätigt). Frontend:
      `AbsencePanel.jsx` bekommt ein neues "Tagesanteil"-Auswahlfeld (deaktiviert, sobald Von ≠
      Bis, mit Tooltip-Erklärung), die Liste zeigt den gewählten Tagesanteil an; `ShiftCell.jsx`
      zeigt im Planblatt-Grid ein hochgestelltes "½" neben dem Absenz-Glyph plus entsprechenden
      Tooltip-Zusatz; `YearPlan.jsx` zeigt denselben Hinweis im Tooltip sowie einen
      halbtransparenten Zellhintergrund (`.year-day-fill--absence.is-half-day`) statt der vollen
      Streifenfüllung. Bewusst unverändert: die Soll/Ist-Stundenkonten
      (`time_account_summary()`/`_approved_absence_workdays()`) behandeln einen
      Halbtags-Absenztag weiterhin als voll entschuldigt -- der Nutzer fragte gezielt nach der
      Ferientage-Zählung, nicht nach stundengenauer Excusal-Logik; das bleibt eine bewusste
      Vereinfachung für eine spätere Iteration. 8 neue Backend-Tests (`AbsenceModelTests`,
      `EmployeeBalanceTests`), volle Suite (349 Tests) grün. Mit Playwright gegen echte
      Testheim-Daten verifiziert: Toolbar-Gruppierung mit zwei getrennten Icon-Blöcken plus
      Trennlinien im DOM bestätigt; Halbtags-Formular aktiviert/deaktiviert korrekt je nach
      Von/Bis; erfasste Halbtags-Absenz erscheint in der Liste mit "· Nur nachmittags" und im
      Planblatt-Grid als "F½"; Ferien-Saldo einer Testperson sank exakt von 25/0/25 auf
      25/0.5/24.5 nach Anlage einer Nachmittags-Absenz.

23. ✅ **Absenzen genau gleich zuteilbar wie Dienste (Oben/Unten/Alles) (2026-08)**.
    Nutzer-Feedback: "wofür haben wir die ganze Logik Alles/Oben/Unten gebaut? Absenzen sollen
    genau gleich zuteilbar sein" -- bisher legte das Absenz-Werkzeug in der `PlacementToolbar.jsx`
    IMMER eine ganztägige Absenz an, egal welcher Platzierungsmodus gerade gewählt war (der
    Modus wirkte nur bei Dienst-Icons). Jetzt bestimmt der Modus auch bei einer Absenz, welche
    Tageshälfte betroffen ist -- reine Frontend-Verdrahtung des bereits bestehenden
    `Absence.day_portion`-Felds (Block 2 Punkt 22), keine Backend-Änderung nötig.
    - `PlanGrid.jsx` `applyToolToMarked()`: das Absenz-Werkzeug legt bei Modus "Oben" eine
      vormittags-, bei "Unten" eine nachmittags-, bei "Alles" weiterhin eine ganztägige Absenz
      an (`day_portion` aus `placementMode` abgeleitet). Ein Tagesanteil ist laut Backend nur für
      einen einzelnen Tag gültig (`Absence.clean()`) -- bei mehreren markierten Tagen in
      Oben/Unten-Modus entsteht daher pro Tag ein eigener Datensatz statt einer zusammenhängenden
      Ferienwoche (nur im Modus "Alles" weiterhin zu möglichst wenigen Zeiträumen gruppiert, wie
      bisher).
    - Räum-Logik vor dem Stempeln wurde von "blind nach Slot-Index" auf "nach echter
      Zeitüberlappung" umgestellt (neuer Helper `shiftOverlapsPortion()`, JS-Gegenstück zu
      `Absence._half_day_window()` im Backend): ein Dienst, der die neue Halbtags-Absenz zeitlich
      gar nicht berührt (z. B. ein einzelner Nachmittagsdienst, der zufällig der einzige des Tages
      und damit visuell "oben" ist), bleibt unangetastet -- vorher hätte "Oben" ihn blind gelöscht,
      nur weil er an Position 0 lag. Symmetrisch auch für den umgekehrten Fall: ein Dienst- oder
      Radiergummi-Klick in Oben/Unten-Modus lässt eine bestehende Absenz der jeweils ANDEREN
      Tageshälfte jetzt in Ruhe (vorher wurde bei jedem Zellklick unconditional jede Absenz des
      Tages entfernt).
    - Grid-Darstellung (`PlanGrid.jsx` Render-Loop): eine Halbtags-Absenz belegt jetzt nur noch
      EINEN der beiden Slots (`slotAbsence()`/`slotAssignment()`, neue Helper) -- die andere
      Hälfte bleibt frei für einen regulären Dienst oder ein leeres "+", genau wie bei zwei
      normalen Diensten. Eine ganztägige Absenz spannt weiterhin wie bisher die volle Zellbreite.
      `resolveCellState()` sortiert Zuweisungen jetzt zusätzlich chronologisch (analog zum
      Render-Loop), damit "Oben"/"Unten" beim Stempeln denselben Slot trifft, der auch angezeigt
      wird.
    - Mit Playwright gegen echte Testheim-Daten verifiziert (Station "Therapie", Vormittags-/
      Nachmittags-Dienst rund um 12:00 Uhr geteilt): leerer Tag + Modus "Unten" + Ferien-Icon
      füllt nur die untere Hälfte, die obere bleibt ein leeres "+"; anschliessendes Stempeln der
      oberen Hälfte mit dem Vormittagsdienst (Modus "Oben") koexistiert konfliktfrei neben der
      Nachmittags-Ferienabsenz in derselben Zelle (kein Backend-Fehler, Ferien-Saldo bleibt
      korrekt bei 0.5 abgezogenen Tagen).

24. ✅ **Nachbesserung: durchgehender Dienst bleibt bei Halbtags-Absenz unverändert stehen
    (2026-08)**. Punkt 23 räumte einen bestehenden Dienst noch anhand reiner Zeitüberlappung mit
    der Zielhälfte (`shiftOverlapsPortion()`) -- das räumte fälschlich auch einen durchgehenden
    Dienst (z. B. Frühschicht 07:00-17:00), sobald er die Zielhälfte überhaupt berührte.
    Konkretes Nutzer-Feedback mit zwei Beispielen: "Habe ich einen normalen Dienst eingeplant und
    nehme den Nachmittag frei, bleibt oben Dienst und unten wird frei" (und umgekehrt für
    Vormittag frei). Vorab per Rückfrage geklärt: der bestehende Dienst-Eintrag bleibt bei einer
    Halbtags-Absenz bewusst unverändert (kein neues Feld an `ShiftAssignment`, keine
    Stunden-Aufteilung) -- Begründung des Nutzers: Krankheit/Ferien sind arbeitszeitrechtlich
    weiterhin Arbeitszeit (Lohnfortzahlungspflicht, Schweizer ArG). Existiert dagegen bereits ein
    ECHTER Split (zwei eigenständige Dienste), wird der in der Zielhälfte weiterhin ersetzt.
    - Backend (`scheduling/models.py`): neuer Helper `Absence._shift_extends_into_other_half()`
      -- ein Dienst blockiert eine Halbtags-Absenz nicht (und umgekehrt), wenn sein Zeitfenster
      auch die jeweils ANDERE (nicht beanspruchte) Tageshälfte abdeckt. Genutzt in
      `Absence.clean()` (Rückwärtsprüfung) und `ShiftAssignment._check_no_absence_conflict()`
      (Vorwärtsprüfung) -- beide Richtungen teilen sich damit dieselbe Ausnahme. Eine ganztägige
      Absenz (`day_portion=full`) bleibt davon unberührt und blockiert weiterhin jeden Dienst.
      4 neue Tests (`test_afternoon_absence_does_not_conflict_with_whole_day_shift`,
      `test_morning_absence_does_not_conflict_with_whole_day_shift`,
      `test_full_day_absence_still_conflicts_with_whole_day_shift`,
      `test_whole_day_shift_not_blocked_by_existing_half_day_absence`) -- die 4 bestehenden
      Halbtags-Tests (mit sauber halbtägigen Test-Diensten) bleiben unverändert grün.
    - Frontend (`PlanGrid.jsx`): `shiftOverlapsPortion()` ersetzt durch
      `shiftShouldBeReplacedByAbsencePortion()` (JS-Gegenstück zum Backend-Helper) -- ein Dienst
      wird beim Stempeln einer Halbtags-Absenz nur noch geräumt, wenn er AUSSCHLIESSLICH in der
      Zielhälfte liegt. Zusätzlich `applyToolToCell()`-Fix für Dienst-Stempeln in Oben/Unten-Modus
      (beim Playwright-Test dieser Session entdeckt): ein bestehender EINZELNER Dienst wurde
      bisher fälschlich umbenannt/ersetzt statt dass ein echter zweiter, unabhängiger Dienst
      entsteht -- jetzt wird nur dann gezielt ersetzt, wenn bereits ZWEI eigenständige Dienste an
      dem Tag liegen, sonst immer ein neuer angelegt (ein echter Zeit-Overlap wird weiterhin vom
      bestehenden `ShiftAssignment._check_no_overlap()` verhindert).
    - Phase 2 (ebenfalls vom Nutzer bestätigt): eine bestehende GANZTÄGIGE Absenz an einem
      EINZELNEN Tag wird beim Bestempeln nur einer Hälfte mit einem Dienst oder dem Radiergummi
      nicht mehr komplett gelöscht, sondern auf die nicht angeklickte Hälfte reduziert (neuer
      Helper `handleShrinkAbsence()`: löscht die ganztägige Absenz und legt sie mit
      `day_portion` = der verbleibenden Hälfte neu an). Bewusst nur für Dienst-/Radiergummi-Klicks
      und nur bei einer Absenz über genau einen Tag -- stempelt man selbst eine ANDERE Absenzart
      auf die Zielhälfte, bleibt es beim einfachen Ein-Absenz-pro-Tag-Modell (volles Löschen), da
      die Zelldarstellung (`findAbsence()`) pro Tag nur eine Absenz kennt; bei einer mehrtägigen
      Absenz (Ferienwoche) wäre eine Reduktion ein Range-Split und bewusst nicht Teil dieses
      Features (dort bleibt es beim vollständigen Löschen).
    - Mit Playwright gegen echte Testheim-Daten verifiziert (Station "Pflege Tag",
      Frühschicht 07:00-17:00 als durchgehender Dienst): "Alles"+Frühschicht, dann "Unten"+Ferien
      -- Frühschicht bleibt oben unverändert (per DB-Abfrage bestätigt: derselbe Datensatz), Ferien
      erscheint unten; umgekehrt "Oben"+Krankheit auf einem frischen Frühschicht-Tag -- Krankheit
      oben, Frühschicht bleibt unten unverändert. Phase 2: "Alles"+Ferien (ganztägig), dann
      "Unten"+Frühschicht -- Ferien wird zu `day_portion=morning` reduziert (nicht gelöscht),
      Frühschicht entsteht unten, keine zwei Anfragen/kein Konflikt. Echter Split (Station
      "Therapie", Vormittag- + Nachmittag-Dienst) weiterhin korrekt: "Unten"+Krankheit ersetzt nur
      den Nachmittag-Dienst, der Vormittag-Dienst bleibt unangetastet. Volle Test-Suite (353 Tests)
      grün.

25. ✅ **Bugfix: Halbtags-Absenz zog einen ganzen statt einen halben Ferientag ab (2026-08)**.
    Nutzer-Feedback: "wenn ich einen halben Tag Ferien eingebe zieht es einen ganzen Tag ab".
    Ursache: `EmployeeBalanceSerializer` deklarierte `vacation_used_days`/`vacation_remaining_days`
    als `IntegerField` -- `Employee.vacation_balance()` selbst rechnete korrekt (0.5-Schritte,
    siehe Block 2 Punkt 22), aber DRF schnitt beim Serialisieren für die API-Antwort
    stillschweigend auf `int()` ab (24.5 → 24), wodurch die im Frontend angezeigte "Ferientage"-
    Zahl (`BalanceBadge.jsx`) einen vollen statt einen halben Tag Abzug zeigte. Der bestehende Test
    `test_vacation_balance_half_day_deducts_half_a_day` prüfte nur das Modell direkt und fing den
    Bug deshalb nicht ab (die volle Test-Suite war trotz des Fehlers grün).
    - `scheduling/serializers.py`: beide Felder auf `FloatField` umgestellt.
      `vacation_entitlement_days` bleibt `IntegerField` (Ferienanspruch ist immer eine ganze Zahl,
      `PositiveSmallIntegerField` am Modell).
    - Neuer API-Test `test_api_returns_half_day_vacation_balance_as_float` (prüft die tatsächliche
      HTTP-Response von `/api/employees/<id>/balance/`, nicht nur das Modell) -- verhindert, dass
      diese Lücke zwischen Modell- und API-Test erneut unbemerkt bleibt. Volle Test-Suite
      (354 Tests) grün.

26. ✅ **Jahresplan auf dieselbe Alles/Oben/Unten-Beplanungslogik wie das Planblatt umgestellt
    (2026-08)**. Nutzer-Feedback: "der Jahresplan hat noch die alte Logik, übernimm die genau
    gleiche Logik wie im Planblatt um zu beplanen" -- der Jahresplan (`YearPlan.jsx`) beplante bisher
    mit einem eigenen, älteren Modell (mehrzeilige Stempelleiste + `stampSecondSlot`-Checkbox für
    Split-Shifts), das die in dieser Session eingeführte Halbtags-Absenz-Logik des Planblatts
    (Alles/Oben/Unten, durchgehender Dienst bleibt bei Halbtags-Absenz unverändert stehen, siehe
    Punkt 24) nicht kannte.
    - `PlacementToolbar.jsx` (bereits eine reine, wiederverwendbare Komponente ohne
      Planblatt-spezifische Abhängigkeiten) direkt im Jahresplan eingesetzt statt einer zweiten,
      abweichenden Stempelleiste -- ersetzt die drei alten Zeilen "Dienste"/"Abwesenheiten"/
      "Spezialitäten". Mitarbeitende ohne Planer-/Admin-Rolle erhalten weiterhin nur die
      Absenz-Gruppe (Dienste/Spezialitäten-Templates werden als leere Arrays übergeben); die
      "Wünsche"-Zeile (Wunschfrei/Wunschdienst, kein Planblatt-Äquivalent) bleibt unverändert
      bestehen.
    - `applyToolToCell()`/`applyToolToMarked()`/`shiftShouldBeReplacedByAbsencePortion()`/
      `shrinkAbsence()` 1:1 aus `PlanGrid.jsx` auf die einfacheren Jahresplan-Datenstrukturen
      portiert (ein Mitarbeiter/eine Anstellung immer fix, `markedDates` bleibt ein flaches
      `Set<date>`) -- identisches Verhalten: ein durchgehender Dienst bleibt bei einer
      Halbtags-Absenz unverändert stehen, eine bestehende ganztägige Einzeltag-Absenz wird beim
      Bestempeln nur einer Hälfte auf die verbleibende Hälfte reduziert statt gelöscht.
    - Zellenfüllung erweitert: koexistieren ein Dienst und eine Halbtags-Absenz am selben Tag
      (die neue Logik lässt genau das zu), zeigt die Zelle jetzt einen diagonalen Farb-Split
      (Dienstfarbe/Absenzfarbe, Reihenfolge nach `day_portion`) statt die Absenz den Dienst
      komplett zu verdecken (vorher: `kind = absence ? "absence" : ...` blendete einen
      koexistierenden Dienst unsichtbar aus). Nebenbei behoben: die reine
      Halbtags-Absenz-Füllung (`.year-day-fill--absence.is-half-day`) färbte bisher immer die
      rechte Zellenhälfte, unabhängig von `day_portion` -- jetzt vormittags links, nachmittags
      rechts.
    - Playwright-Verifikation (Station "Pflege Tag", durchgehende Frühschicht 07:00–17:00):
      Tag markieren, Modus "Unten" + Ferien stempeln -- kein `DELETE` auf die Frühschicht-Zuweisung
      (nur ein `POST /api/absences/` mit `day_portion=afternoon`), Zellen-Tooltip nennt beides
      ("Frühschicht ... + Ferien, Nur nachmittags"), Zellenfüllung zeigt den erwarteten
      grün/grau-Diagonalsplit. Mitarbeiter-Ansicht (`peter`, ohne Planer-Rolle) bestätigt: keine
      Dienst-Chips in der Toolbar, Absenz-Chips + Wünsche-Zeile weiterhin nutzbar. Volle
      Test-Suite (354 Tests) grün.

27. ✅ **Bugfix: Radiergummi in Oben/Unten löschte einen einzelnen durchgehenden Dienst gar nicht
    (2026-08)**. Nutzer-Feedback: "ganzen Tag Dienst eingeplant, dann halben Tag frei genommen,
    jetzt will ich den Dienst 'oben' löschen -- ich glaube das geht nicht korrekt". Zutreffend:
    `applyToolToCell()`s Radiergummi-Zweig für Oben/Unten prüfte `isSplit = Boolean(slot0) &&
    Boolean(slot1)` -- das verlangt ZWEI eigenständige Dienst-Zuweisungen. Eine koexistierende
    Halbtags-Absenz (siehe Punkt 24/26) zählt nicht als zweiter Slot, also war `isSplit` bei einem
    einzelnen durchgehenden Dienst immer `false`, und der Code brach mit einem stillen `if
    (!isSplit) return;` ab -- Klick auf "Oben"/"Unten" + × tat buchstäblich nichts, ohne
    Fehlermeldung. Klargestellt: der Radiergummi in Oben/Unten betrifft ausschliesslich den
    *Plan* (ShiftAssignment) -- tatsächlich abweichend geleistete Ist-Zeit (z. B. "nur bis Mittag
    gearbeitet, dann krank") wird unverändert separat über die Zeiterfassung erfasst (Block 1.13),
    nicht durch Kürzen des Plan-Datensatzes.
    - `PlanGrid.jsx` und `YearPlan.jsx` (`applyToolToCell()`): bei `!isSplit` wird jetzt, falls
      genau ein Dienst existiert (`slot0`), dieser komplett gelöscht -- unabhängig ob "Oben" oder
      "Unten" geklickt wurde, da beide auf denselben, einzigen (nicht halbierbaren) Datensatz
      zielen. Der echte Split-Fall (zwei eigenständige Dienste, `isSplit === true`) ist
      unverändert: weiterhin wird gezielt nur der Dienst der Zielhälfte gelöscht.
    - Playwright-Verifikation (Station "Pflege Tag"/"Therapie"): durchgehender Dienst +
      Halbtags-Absenz, "Oben" + × -- Dienst wird komplett gelöscht (ein `DELETE
      /api/shift-assignments/<id>/`), Absenz bleibt unangetastet stehen (Tooltip zeigt danach nur
      noch die Absenz). Regressionstest mit echtem Split (Therapie Vormittag + Nachmittag): "Oben"
      + × löscht weiterhin nur den Vormittag-Dienst, Nachmittag-Dienst bleibt unangetastet.

28. ✅ **Bugfix: Stundensaldo zog bei einer Halbtags-Absenz den vollen statt den halben Tagessoll ab
    (2026-08)**. Nutzer-Feedback (anhand eines konkreten Beispiels, David Brunner 42h/Woche, 25
    Ferientage, Frühdienst 07:00–12:00 & 13:00–16:24 das ganze Jahr eingeplant): "was passiert,
    wenn ich einen halben Tag Ferien eingebe -- stimmt die Rechnung dann noch?". Antwort: nein.
    `vacation_balance()` (Ferientage-Zähler) zählt eine Halbtags-Absenz seit Punkt 25 korrekt als
    0.5 Tage, aber `time_account_summary()`/`monthly_summary()` (Stundensaldo, Basis für
    `plan_saldo_hours` und den Lohnlauf) nutzten dafür die separate `_approved_absence_dates()` --
    eine reine Datumsmenge ohne `day_portion`-Bezug. Jede Absenz, auch eine Halbtags-Absenz, machte
    ein Datum komplett "arbeitsfrei": weder Soll noch die (weiterhin bestehende, siehe Punkt 24)
    Dienst-Zuweisung dieses Tages zählten, statt nur die Hälfte. Empirisch nachgewiesen: ein halber
    Ferientag senkte `plan_saldo_hours` um die vollen 8.4h (Tagessoll) statt um 4.2h.
    - `scheduling/models.py`: `_approved_absence_dates()`/`_approved_absence_workdays()` ersetzt
      durch `_approved_absence_day_weights()` -- liefert `Datum -> Anteil` (1.0 ganztags, 0.5
      halbtags) statt einer reinen Datumsmenge. `time_account_summary()` (beide Zweige,
      vergangen UND geplant/künftig) und `monthly_summary()` nutzen diese Gewichtung jetzt sowohl
      für die Soll-Excusierung (`excused_units` als gewichtete Summe statt `len(set)`) als auch für
      den Ist-Ausschluss: eine GANZTÄGIGE Absenz schliesst eine Zuweisung weiterhin komplett aus,
      eine HALBTAGS-Absenz schliesst nur eine bereits erfasste `TimeRecord` NICHT aus (die ist
      schon korrekt), sondern gewichtet nur die mangels Zeiterfassung geschätzten Plan-Stunden
      (`ShiftAssignment._shift_hours()`) zur Hälfte.
    - Neue Tests `test_saldo_half_day_absence_excuses_only_half_the_day` (historischer Zweig: Soll
      steigt um 4.2h, Ist sinkt um 4h relativ zu einer Woche ohne Absenz, Saldo -18.6h statt
      fälschlich -18.0h wie bei einem ganzen Tag) und
      `test_plan_saldo_halves_future_assignment_hours_on_half_day_absence_day` (künftiger Zweig:
      `plan_saldo_hours` sinkt um genau die Hälfte der Schichtstunden). Bestehende Tests für den
      GANZTAGS-Fall (`test_saldo_approved_absence_is_soll_neutral`,
      `test_plan_saldo_excludes_future_assignment_on_approved_absence_day`) bleiben unverändert
      grün -- volle Test-Suite (356 Tests) grün.
    - Empirisch mit den gepushten Praxisdaten (David Brunner) nachverifiziert: ein einzelner
      halber Ferientag senkt `plan_saldo_hours` jetzt exakt um 4.2h (vorher 8.4h), ein ganzer Tag
      weiterhin um 8.4h -- nach 25 vollständig eingetragenen Ferientagen (in beliebiger
      Ganz-/Halbtags-Kombination) konvergiert der Saldo korrekt auf 0.

29. ✅ **Bugfix: Radiergummi in Oben/Unten löschte auch den Dienst beim Entfernen einer
    Halbtags-Absenz (2026-08)**. Regression aus Punkt 27: dessen Fix ("ein einzelner,
    durchgehender Dienst lässt sich in Oben/Unten nicht löschen") hatte einen unbeabsichtigten
    Nebeneffekt -- Nutzer-Feedback: "Frühdienst eingeplant, Unten halber Tag Ferien, Unten halber
    Tag Ferien wieder entfernen löscht auch den Dienst" (ebenso mit "Oben"). Ursache: der
    Radiergummi in Oben/Unten hat zwei Aufgaben zugleich -- eine deckungsgleiche Absenz räumen
    UND (seit Punkt 27) einen einzelnen, durchgehenden Dienst löschen, falls kein echter Split
    vorliegt. Trifft der Klick genau die Hälfte, in der eine Absenz sitzt (z. B. "Unten" bei einer
    nachmittags-Absenz), lief bisher BEIDES: die Absenz wurde korrekt entfernt, aber im selben
    Zug auch der koexistierende, durchgehende Dienst gelöscht -- obwohl der gar nicht das Ziel des
    Klicks war (er wird ja per Definition nicht durch eine Halbtags-Absenz ersetzt, siehe Punkt
    24).
    - `PlanGrid.jsx`/`YearPlan.jsx` (`applyToolToMarked()`): neues `datesWithHandledAbsence`-Set
      merkt sich, für welche markierten Tage die Absenz-Vorräumung tatsächlich etwas getan hat
      (Absenz gelöscht oder reduziert). `applyToolToCell()` bekommt diese Information als neuen
      Parameter `absenceHandled` und löscht den einzelnen, durchgehenden Dienst nur noch, wenn für
      den Tag KEINE Absenz-Aktion stattgefunden hat -- trifft der Klick stattdessen die Hälfte, in
      der der Dienst selbst (nicht die Absenz) sichtbar ist, bleibt Punkt 27 unverändert wirksam
      und löscht ihn weiterhin.
    - Playwright-Verifikation (David Brunner, durchgehender Frühdienst): "Unten"+Ferien
      hinzufügen, dann "Unten"+Radiergummi -- nur `DELETE /api/absences/…`, Dienst bleibt (Titel
      zeigt danach wieder nur den Frühdienst). Identisch für "Oben". Regressionstest: Ferien
      "Unten", Radiergummi in "Oben" (trifft die Dienst-Hälfte) -- löscht weiterhin korrekt den
      Dienst (`DELETE /api/shift-assignments/…`), Ferien bleibt bestehen.

**Grenzziehung Zeitmanagement vs. Lohnbuchhaltung** (Diskussion 2026-08, Grundlage für Punkt 30/31
unten): eine vollständige Schweizer Lohnabrechnung braucht neben den Stunden/Zuschlägen aus Block 1
noch AHV/IV/EO (5.3 % AN-Anteil), ALV (1.1 % bis CHF 148'200 Jahreslohn), BVG
(Koordinationsabzug CHF 26'460, altersgestaffelt), Quellensteuer, Familienzulagen — ein eigenes,
hochreguliertes Feld mit Zertifizierungspflicht (Swissdec/ELM für die Meldung an Behörden/
Versicherer). Das baut diese App bewusst **nicht** nach. Ihr Job endet dort, wo die
Sozialabzüge anfangen: sie liefert **Stunden pro Zuschlagskategorie** sauber und exportierbar,
eine zertifizierte Lohnsoftware (Abacus, Sage, SwissSalary, Bexio …) übernimmt den Rest. Für die
Übergabe an ein Lohnsystem gibt es keinen einzigen verpflichtenden CH-Standard (anders als ELM für
die Behörden-Meldung) — üblich sind CSV-/Excel-Importe mit frei vergebenen "Lohnart"-Codes pro
Kundensystem, deshalb Punkt 30 (Mapping) vor Punkt 31 (Export).

30. ✅ **Lohnart-Mapping** (2026-08, Nutzer-Entscheidung: "Lohn-Export, das ist näher am
    Verkaufsargument" -- vor dem einfacheren Plan-PDF-Export aus Punkt 5 priorisiert). Neues
    tenant-gescoptes Modell `PayrollCategoryMapping`: `category` (feste Auswahl -- Normalstunden,
    Überstunden, Nacht-Zeitgutschrift/-Lohnzuschlag, Sonntagszuschlag, Ferien-/Krankheits-/
    sonstige Absenztage, Feiertage) ODER `special_template` (FK auf eine zuschlagspflichtige
    Spezialität, z. B. "Pikett Wochentag"/"Pikett Wochenende" mit potenziell unterschiedlichen
    Lohnart-Codes) -- ein DB-`CheckConstraint` erzwingt, dass genau eines der beiden gesetzt ist,
    zwei partielle `UniqueConstraint`s verhindern doppelte Zuordnungen pro Tenant/Kategorie bzw.
    pro Tenant/Spezialität. `payroll_code` (Freitext, vom Kunden vergeben), `payroll_label`
    (Freitext, nur Anzeige), `is_active` (inaktive Zeilen fallen beim Export weg, z. B. falls ein
    Kunde eine Kategorie bereits anders löst).

    `PayrollCategoryMappingViewSet` (`scheduling/views.py`), admin-only fürs Schreiben
    (`IsTenantAdmin`, gleiches Muster wie die übrige Tenant-Konfiguration, Block 2 Punkt 14),
    Lesen für alle Rollen offen. Frontend: neues 6. Settings-Modul "Lohnarten"
    (`PayrollSettings.jsx`) -- Tabelle mit fester linker Spalte (die neun festen Kategorien plus
    eine Zeile pro zuschlagspflichtiger Spezialität) und Eingabefeldern rechts (Code +
    Bezeichnung); "Speichern" pro Zeile legt beim ersten Mal an, danach aktualisiert es dieselbe
    Zeile (kein separates Anlegen-Formular nötig).

31. ✅ **CSV-Export + API-Endpoint für Lohn-Rohdaten** (2026-08) -- baut auf Punkt 30 auf. Neue
    Methode `Employee.payroll_raw_lines(year, month)` übersetzt `monthly_summary()` (inkl. der
    dafür neu ergänzten Felder `occasional_night_surcharge_hours`, analog zur jährlichen
    `night_work_summary()`-Variante aus Punkt 17, und `holiday_days`) sowie die neue
    `Employee._monthly_absence_day_breakdown()` (Kalendertage pro Absenztyp im Monat, Halbtags
    0.5 -- gruppiert nach `AbsenceType.deducts_vacation_days`/`counts_as_sick_leave`, alles andere
    "sonstige Absenztage") in rohe `{category, special_template_id, amount, unit}`-Zeilen, noch
    ohne Lohnart-Code -- diese Übersetzung (über `PayrollCategoryMapping`) passiert bewusst erst
    in der View, damit `Employee` (scheduling) nicht von einer jederzeit änderbaren Kunden-
    Konfiguration abhängt. Bei den Stunden-Kategorien zählt für Überstunden nur der über den
    Gleitzeit-Korridor bereits **bestätigte** Anteil (`overtime_surcharge_hours`), nicht der rohe
    Ist-Soll-Überschuss -- unbestätigte Überzeit ist noch nicht abrechnungsreif (README,
    Gleitzeit-Entscheidung).

    Neuer Endpoint `GET /api/payroll-export/?month=YYYY-MM`, Admin-only (bewusst NICHT über
    `IsTenantAdmin`, das Lesen für alle Rollen offen liesse -- ein expliziter Rollen-Check wie bei
    `EmployeeViewSet.monthly_summary`, da hier fertig übersetzte Lohn-Rohdaten über ALLE
    Mitarbeitenden hinweg ausgegeben werden, kein Selbstauskunfts-Endpoint). Kategorien ohne
    konfiguriertes Mapping werden nicht stillschweigend ausgelassen, sondern als separate
    Warnliste im Response mitgeliefert ("Normalstunden: kein Lohnart-Code konfiguriert"). CSV-
    Variante über `?output=csv` (bewusst NICHT `?format=csv` -- DRF reserviert `format` selbst für
    die Content-Negotiation, ein unbekannter Wert dort liess die Anfrage mit 404 statt der
    erwarteten CSV-Antwort fehlschlagen, empirisch beim ersten Testlauf gefunden), Spalten:
    Personalnummer (Employee-ID, kein eigenes Feld dafür nötig), Name, Kostenstelle, Lohnart-Code,
    Bezeichnung, Menge, Einheit, Periode.

    Frontend: `api.downloadPayrollExportCsv()` löst den ersten Datei-Download der App aus (eigener
    `fetch`-Aufruf mit Auth-Header statt des JSON-`request()`-Helpers, `Blob` + temporärer
    `<a download>`). Der "CSV herunterladen"-Button im "Lohnarten"-Modul lädt vorher die JSON-
    Vorschau, um die Warnliste zusätzlich sichtbar im UI anzuzeigen.

    **Nachbesserung (2026-08, Zweitmeinung eingeholt):** zwei Lücken nachgezogen --
    - **Kostenstelle**: komplett vergessen. Neues `Node.cost_center` (Freitext, leer = Vererbung
      von der nächsten Vorfahren-Station, `effective_cost_center()`) -- ein Team ohne eigene
      Kostenstelle übernimmt automatisch die seiner Station. `Employee.effective_cost_center()`
      ist nur eindeutig, wenn alle Stationen des Mitarbeitenden (`Employee.nodes`) auf dieselbe
      Kostenstelle auflösen, sonst `None` (bekannte Vereinfachung -- eine echte Aufteilung nach
      Station müsste `monthly_summary()` selbst pro Station aufschlüsseln, siehe deren Docstring).
      Editierbar in Einstellungen → Stationen (Anlegen-Formular + Inline-Bearbeiten, zeigt bei
      geerbtem Wert "(geerbt)" an).
    - **Bugfix Warnliste**: eine vom Admin bewusst deaktivierte Kategorie
      (`PayrollCategoryMapping.is_active=False`) landete bisher fälschlich in derselben
      Warnliste wie eine nie konfigurierte -- "deaktiviert" ist aber eine bewusste Entscheidung
      ("diese Kategorie lösen wir anders"), keine vergessene. Die View unterscheidet jetzt explizit
      zwischen "nie konfiguriert" (warnt) und "konfiguriert, aber deaktiviert" (bewusst
      ausgeschlossen, keine Warnung).

    31 neue Backend-Tests insgesamt (Modell-Constraints, API-Berechtigungen inkl. Tenant-Grenze
    für `special_template`, `payroll_raw_lines` -- Normalstunden/Überstunden/Sonntagszuschlag/
    Absenztage-Gruppierung/Spezialitäten-Zeilen --, Export-View JSON/CSV/Warnliste/
    Berechtigungen, Kostenstellen-Vererbung entlang der Stationshierarchie, Eindeutigkeits-Logik
    bei mehreren Stationen), volle Suite grün.

    **Nachbesserung 2 (2026-08, Frage "wo sind Pikettzulagen etc.?"):** Pikett & Co. waren bereits
    über `special_template` abgedeckt (dynamisch, siehe oben) -- zwei echte Lücken kamen dabei aber
    zutage und wurden behoben:
    - **Sonstige Absenztage aufgeschlüsselt**: die feste Kategorie `OTHER_ABSENCE_DAYS` ist entfallen.
      `PayrollCategoryMapping` bekam ein drittes exklusives Ziel `absence_type` (FK auf
      `AbsenceType`, analog `special_template`) -- jede Absenzart ohne Ferien-/Krankheits-Flag (z. B.
      Militärdienst, unbezahlter Urlaub) bekommt jetzt eine eigene Export-Zeile mit eigenem
      Lohnart-Code statt in einem gemeinsamen "Sonstiges"-Topf zu landen. UI: eigene Tabellenzeile
      pro Absenzart in Einstellungen → Lohnarten, analog den Spezialitäten.
    - **Sick-Pay-Skala im Export**: `Employee.payroll_raw_lines()` teilt Krankheitstage bei
      `Tenant.sick_pay_model = SCALE` jetzt in `SICK_DAYS` ("mit Lohnfortzahlung") und die neue
      Kategorie `SICK_DAYS_EXHAUSTED` ("Anspruch erschöpft") auf, anhand des zu Monatsbeginn bereits
      verbrauchten Anspruchs im laufenden Dienstjahr (`_sick_pay_entitlement_split()`, siehe
      Docstring für die bekannte Randfall-Vereinfachung bei Dienstjahr-Wechsel mitten im Monat). Beim
      Taggeldversicherungs-Modell bleibt es bei einer Zeile (die App rechnet bewusst keine
      Wartefrist pro Krankheitsfall aus, Grenzziehung Zeitmanagement vs. Lohnbuchhaltung). Der
      Export liefert zusätzlich pro Mitarbeiter mit Krankheitstagen einen `sick_pay_context`
      (Modell, Skala, Anspruch, verbleibender Anspruch, Wartefrist) als Kontext für die
      Lohnbuchhaltung -- nicht Teil der CSV-Zeilen, nur der JSON-Vorschau.

    Insgesamt 12 weitere Backend-Tests (Modell-Constraints für `absence_type`, API-Berechtigungen,
    Aufschlüsselung mehrerer Absenzarten, Sick-Pay-Split unter beiden Modellen, Export-Warnungen/
    -Kontext), volle Suite grün.

32. ✅ **Stammdatenpflege: Mitarbeitenden-Tabelle statt Liste** (2026-08). Nutzer-Feedback: "die
    Stammdatenpflege ist bei 1200 Mitarbeitenden katastrophal -- unsortierte Liste, scrollend
    suchen, Bearbeiten klicken, Werte anpassen". `EmployeeViewSet` bekam `filter_backends`
    (`SearchFilter` auf `first_name`/`last_name`, `OrderingFilter` auf `last_name`/`first_name`/
    `employment_pct`/`is_active`/`employment_start_date`) sowie `?node=`/`?is_active=`-Filter in
    `get_queryset()` -- Suche/Sortierung/Filterung laufen jetzt serverseitig statt "alles laden
    und im Frontend filtern". Neue `api.searchEmployees()` (eine Tabellenseite, roh, ohne
    `requestAllPages`) ergänzt die bestehende `api.getEmployees()` (kompletter Bestand, weiterhin
    unverändert für Planblatt/Absenzen/Diensttausch/Dashboard genutzt). Frontend:
    `EmployeeSettings.jsx` zeigt eine sortierbare, filterbare, paginierte Tabelle (Spaltenköpfe
    klickbar, Stations-/Status-Filter, Freitextsuche mit 300ms-Debounce, "Weiter"/"Zurück" über
    die DRF-Pagination) über die volle Breite; Klick auf eine Zeile oder "Bearbeiten" öffnet das
    Formular als Modal (`.modal-overlay`/`.modal-dialog`, Escape/Backdrop-Klick/"×" schliessen) --
    Folge-Feedback "die Zeile rechts ist Mist, ich würde ein sauberes Popup erwarten" ersetzte die
    ursprüngliche sticky Sidebar. Modal-Breite wuchs in zwei weiteren Feedback-Runden auf
    `min(1080px, 94vw)`; `.panel-form-row` bekam `align-items: flex-end`, damit Buttons ohne
    eigenes Label (z. B. "Hinzufügen" im Mutterschutz-Editor) nicht verrutschen, wenn ein
    Nachbar-Label mehrzeilig umbricht. Nach Anlage/Änderung wird die aktuelle Tabellenseite neu
    geladen statt den lokalen State zu patchen, damit Sortierung/Filter/Seitenzahl auch bei einer
    Statusänderung (z. B. "Nur aktive"-Filter + gerade deaktiviert) korrekt bleiben. Bekannter,
    bewusster Kompromiss: die Autovervollständigung für Anstellungs-Rollentitel
    (`existingTitles`) deckt seit dieser Umstellung nur noch die aktuell geladene Tabellenseite
    ab, nicht mehr den kompletten Bestand -- ein eigener Endpoint dafür wäre unverhältnismässig.
    11 neue Backend-Tests (Suche, Sortierung auf-/absteigend, Stations-/Status-Filter,
    Kombination, Tenant-Isolation), mit Playwright gegen 80 Testdatensätze (2 Seiten) end-to-end
    verifiziert. Tabellen-/Modal-CSS-Klassen (`settings-table-*`, `.modal-*`) bewusst generisch
    benannt statt Employee-spezifisch, um sie mit weiteren Settings-Modulen zu teilen (siehe
    Punkt 33).

33. ✅ **Stammdatenpflege: gleiches Tabellen-/Modal-Muster für Schichttypen** (2026-08).
    Nutzer-Feedback nach Punkt 32: "sollten wir die anderen Tabs auch umbauen?" -- Schichttypen
    identifiziert als Modul mit dem grössten Nutzen (bei vielen Stationen z. B. 15 Stationen x 10
    Schichttypen = 150 Einträge, bisher alle ungefiltert in einer Dauer-Sidebar-Liste), Absenzarten/
    Skills bewusst nicht angefasst (kleine, tenant-weite Kataloge mit typischerweise 5-20
    Einträgen), Stationen bewusst nicht angefasst (Baumstruktur, kein flaches Set -- Tabelle+Modal
    würde die Eltern-Kind-Beziehung zerstören). `TimeTemplateViewSet` bekam `filter_backends`
    (`SearchFilter` auf `name`, `OrderingFilter` auf `name`/`start_time`/`end_time`/`category`/
    `node__name`, Default-Sortierung `node__name`/`start_time`) sowie `?node=`/`?category=`-Filter
    in `get_queryset()` (zusätzlich zur bestehenden rollenbasierten Stations-Einschränkung über
    `_employee_scoped_node_ids`). Neue `api.searchTimeTemplates()` ergänzt die bestehende
    `api.getTimeTemplates()` (kompletter Bestand, weiterhin unverändert für Planblatt/Jahresplan/
    Stempelleisten genutzt). `TimeTemplateSettings.jsx` komplett auf dasselbe Muster wie
    `EmployeeSettings.jsx` umgestellt (Suche/Sortierung/Stations-/Kategorie-Filter, Seite,
    Bearbeiten öffnet Modal inkl. `TimeTemplateSegmentEditor`). Die dafür nötigen CSS-Klassen
    waren bereits in Punkt 32 generisch benannt (`settings-table-*`) und wurden unverändert
    wiederverwendet, keine Duplizierung. 7 neue Backend-Tests (Suche, Default-/Namens-Sortierung,
    Stations-/Kategorie-Filter, Tenant-Isolation), mit Playwright end-to-end verifiziert (Suche,
    Kategorie-Filter, Sortierung, Modal öffnen/bearbeiten/speichern/neu anlegen).

    **UX-Nachschlag (2026-08, Nutzer-Feedback)**: "Neuer Schichttyp" bei aktivem Stations-Filter
    öffnete das Modal bisher immer mit der ersten Station der Gesamtliste vorausgewählt statt der
    gerade gefilterten -- `startCreating()` übernimmt jetzt `nodeFilter` als Vorauswahl, sofern
    einer aktiv ist (Fallback weiterhin `nodes[0]?.id` ohne Filter).

34. ✅ **Stammdatenpflege: Stationen-Baum -- Suche + Drag & Drop verschieben** (2026-08).
    Nutzer-Vorgabe: "Nun Punkt 2. Stationen umsetzen. Es wäre zudem wünschenswert wenn ich die
    Stationen per drag & drop verschieben kann." Anders als Mitarbeitende/Schichttypen (Punkt 32/33)
    bewusst NICHT auf Tabelle+Modal umgestellt -- eine Baumstruktur ist kein flaches Set, das würde
    die Eltern-Kind-Beziehung zerstören. Backend: `NodeViewSet.move()` (`POST /api/nodes/{id}/move/`,
    Body `{"parent": <id>|null}`) nutzt `node.move(target, pos="sorted-child")` zum Umhängen unter
    einen anderen Knoten bzw. `pos="sorted-sibling"` gegen einen bestehenden Wurzelknoten fürs
    Verschieben auf die oberste Ebene -- `node_order_by = ["name"]` (Node-Modell) erzwingt ohnehin
    automatische alphabetische Sortierung innerhalb einer Ebene, Drag & Drop reparentet daher nur,
    sortiert nicht manuell um. Zyklus-Versuche (in sich selbst oder einen eigenen Nachfahren
    verschieben) fangen sowohl treebeards `InvalidMoveToDescendant` (Server, autoritativ) als auch
    ein `path`-Präfix-Check im Frontend ab (verhindert das Drop-Target optisch schon vor dem
    Request). Frontend: `NodeSettings.jsx` bekam Griff-Icon ⠿, ziehbare Zeilen, farblich
    hervorgehobenes Drop-Ziel, eigene "Auf oberste Ebene verschieben"-Dropzone (nur während eines
    aktiven Zugs sichtbar) sowie ein Freitext-Suchfeld, das einen Treffer zusammen mit seiner
    kompletten Eltern-Kette zeigt (sonst hinge ein gefundener Unterknoten ohne Kontext im Baum) --
    beides ebenfalls rein clientseitig über den `path`-String gelöst, ohne Server-Roundtrip. Da ein
    Verschieben potenziell viele Knoten gleichzeitig betrifft (der Knoten selbst plus alle
    Nachfahren ändern `depth`/`path`), lädt `SettingsPanel.jsx` nach jedem Move den kompletten
    (kleinen) Baum neu, statt das clientseitig nachzurechnen.

    **Bugfix (2026-08, Nutzer-Feedback: "das verschieben der stationen funktioniert nicht
    richtig")**: die erste Version nutzte natives HTML5 `draggable`/`dragstart`/`dragover`/`drop`.
    Das erwies sich als zu zerbrechlich, um zuverlässig auszulösen -- u. a. weil ein Mousedown auf
    dem Stationsnamen-Text die native Textauswahl-Geste statt der Element-Drag-Geste startete.
    Ersetzt durch dasselbe robuste, bereits etablierte Muster wie PlanGrid.jsx/YearPlan.jsx
    ("Ziehen mit gedrückter Maustaste": Mousedown startet, Mouseenter setzt das aktuelle Ziel, ein
    globaler `window`-`mouseup`-Listener schliesst ab) plus `user-select: none` auf der Zeile,
    damit ein Zug nicht mehr als Textauswahl interpretiert wird.

    6 neue Backend-Tests
    (Umhängen, auf oberste Ebene verschieben, Nachfahren-Tiefe nach Verschieben, Zyklus-Schutz
    gegen sich selbst/eigene Nachfahren, fremder Tenant, Berechtigung), mit Playwright end-to-end
    verifiziert (realistische Maus-Simulation über `page.mouse`, kein natives Drag&Drop mehr).

    **Bugfix 2 (2026-08, Nutzer-Feedback: "ein Kind Knoten direkt einem Hauptknoten zuzuweisen
    funktioniert nicht -- ich muss zuerst auf oberste Ebene verschieben und erst dann als Kind auf
    eine Hauptstation ziehen")**: kein Auto-Scroll während des Zugs. `main` (App.jsx) deklariert
    zwar `overflow: auto`, wächst aber tatsächlich frei mit dem Inhalt statt selbst zu scrollen --
    die Seite scrollt über das Dokument. Ohne Auto-Scroll war ein Ziel ausserhalb des sichtbaren
    Bereichs während EINES durchgehenden Zugs schlicht unerreichbar; der Umweg über die oberste
    Ebene funktionierte nur zufällig, weil deren Dropzone immer ganz oben und damit garantiert
    sichtbar liegt. Jetzt scrollt `document.scrollingElement` automatisch (Geschwindigkeit
    proportional zur Nähe), sobald der Mauszeiger während eines Zugs in die obere/untere 60px-Zone
    des Viewports kommt -- mit Playwright verifiziert (21-Stationen-Baum, Viewport kleiner als die
    Liste, direkter Zug von einem sichtbaren Kind-Knoten auf einen erst nach Auto-Scroll
    sichtbaren, weit entfernten Hauptknoten).

    **Bugfix 3 (2026-08, Nutzer-Feedback: "ich kann die Station Küche direkt in Station A ziehen.
    Nicht aber von Station A zurück in Hauswirtschaft")**: ein echter Bug in `django-treebeard`
    (5.3.0) selbst. `move(pos="sorted-child")` wandelt das intern in `"sorted-sibling"` gegen
    `target.get_last_child()` um und bricht dabei früh ab, sobald die letzte Pfad-Ziffer des
    gezogenen Knotens (seine Geschwister-Position unter dem ALTEN Elternknoten) zufällig mit der
    berechneten Position unter dem NEUEN Elternknoten übereinstimmt ("bereits an der richtigen
    Stelle") -- ohne zu prüfen, ob es sich überhaupt um denselben Elternknoten handelt. Bei kleinen
    Bäumen (Position 1 unter dem alten UND unter dem neuen Elternknoten) ist das der Normalfall,
    nicht die Ausnahme, und der Knoten bleibt dabei unbemerkt (Response 200, aber unverändert) an
    alter Stelle. Workaround in `NodeViewSet.move()`: beim Umhängen zwischen zwei echten (nicht
    Wurzel-)Elternknoten wird zuerst automatisch über die oberste Ebene geroutet (bereits einzeln
    erprobt: Wurzel↔Kind funktioniert immer zuverlässig, weil dabei echte, unabhängig berechnete
    Positionen verglichen werden statt einer zufälligen Kollision) -- inklusive `target.refresh_from_db()`
    nach dem Zwischenschritt, da eine Wurzel-Einfügung die Pfad-Ziffern bestehender Wurzeln
    verschieben kann und `target` sonst mit einem veralteten Pfad weiterrechnet. Zusätzliche
    Post-Move-Verifikation (`node.get_parent() == target`) wirft einen klaren Fehler, statt je
    wieder still zu scheitern. 2 neue Regressionstests (bewusst mit einem Knoten konstruiert, der
    alphabetisch an Position 1 unter BEIDEN Elternknoten liegt, um die Kollision gezielt zu
    erzwingen -- Hin- und Rückrichtung), volle Suite (404 Tests) grün, mit Playwright end-to-end
    verifiziert.

35. ✅ **Absenzarten & Skills kosmetisch an das Modal-Formular-Styling angeglichen** (2026-08).
    Nutzer-Vorgabe: "Gleiche Absenzarten & Skills kosmetisch an das neue Modal-Formular-Styling an,
    damit es sich einheitlich anfühlt." Bewusst nur kosmetisch -- keine Umstellung auf das
    Tabelle+Modal-Muster von Mitarbeitende/Schichttypen (Punkt 32/33), das für diese beiden
    kleinen, tenant-weiten Kataloge (typischerweise 5-20 Einträge) explizit ausgeschlossen wurde.
    `AbsenceTypeSettings.jsx`/`SkillSettings.jsx` bekamen `.panel-hint`-Erklärtexte unter den
    Eingabefeldern, analog zum bereits etablierten Muster in `EmployeeSettings.jsx`: Skills einen
    Hinweis, dass der Katalog tenant-weit statt pro Station gilt; Absenzarten Hinweise zu
    Chip-Farbe und Chip-Glyphe. Dabei zunächst eine Layout-Regression eingebaut und per
    Playwright-Screenshot entdeckt: die bestehende `.panel-form-row { align-items: flex-end }`
    (ursprünglich für den Mutterschutz-Editor gedacht, siehe Punkt 32) bodenbündig ausgerichtete
    drei Spalten Name/Farbe/Kürzel, von denen nur Farbe/Kürzel einen Hinweistext und damit mehr
    Höhe bekamen -- Name wirkte dadurch verrutscht. Statt die gemeinsame CSS-Regel anzufassen
    (die an anderer Stelle weiterhin ihren Zweck erfüllt) oder Name einen rein kosmetischen,
    inhaltsleeren Hinweistext zu verpassen, wurde das Formular umstrukturiert: Name jetzt allein
    auf einer eigenen Zeile, Farbe und Kürzel (beide nun mit Hinweistext, also gleich hoch)
    gemeinsam auf einer zweiten Zeile. Mit Playwright verifiziert (Absenzarten-Formular zeigt
    Name sauber oben, Farbe/Kürzel bündig nebeneinander; Skills-Formular unverändert korrekt, da
    dort nur ein einzelnes Feld existiert und kein Zeilen-Ausrichtungsrisiko besteht).

36. ✅ **Zeiterfassung: Planer-/HR-Stationsscoping + stationsübergreifende "Zu bestätigen"/
    "Noch nicht erfasst"-Übersicht** (2026-08).
    Nutzer-Vorgabe, zwei Fragen in einer Nachricht: "1. Welche Abteilungen sieht der Planer? Kann
    man diese zuweisen? -- Natürlich gibt es in einer Klinik Planer mit unterschiedlichen
    Zuständigkeiten! Gleiches gilt auch für HR. Nur Admin darf immer alles sehen. 2. [...] ich
    muss die Stationen durchsuchen, bis ich die zu bestätigende Erfassung finde [...] macht es
    Sinn tabellarisch zu arbeiten wie bei den Einstellungen? Eine alle noch offenen meines
    Bereichs und eine die zu bestätigen listet?" Vorausgegangen war eine reine Analyse-Notiz (als
    Artifact geliefert), die den Ist-Zustand dokumentierte: Admin/Planer/HR sahen bislang
    ausnahmslos alle Stationen des Tenants, `TimeRecordViewSet` kannte weder Status- noch
    Stations-Filter, und der Badge-Zähler in `GET /api/me/` war ein reiner tenant-weiter Zähler
    ohne Sprungziel.

    **Backend, Teil 1 -- Scoping**: `Membership.scoped_nodes` (neues `ManyToManyField` auf `Node`,
    lazy `"scheduling.Node"`-Referenz, damit `core` weiterhin kein Modul-Level-Abhängigkeit zu
    `scheduling` bekommt). `_employee_scoped_node_ids()` (`scheduling/views.py`, bisher nur für
    `EMPLOYEE` ausgewertet) umgebaut: Signatur jetzt `(membership, employee_profile)` statt
    `request` (Wiederverwendbarkeit aus `core.views._task_counts`), ADMIN immer `None`
    (uneingeschränkt), PLANNER/HR neu über `Membership.scoped_nodes` -- inklusive aller
    Unterstationen (`get_descendants()`, anders als bei EMPLOYEE reicht hier eine Ebene nicht: ein
    "Bereich" wie "Pflege" soll automatisch seine Teams mit einschliessen). Leere `scoped_nodes` =
    `None` (keine Einschränkung) -- migrationssicher, jede heute schon bestehende Planer-/
    HR-Mitgliedschaft sieht ohne Zutun weiterhin alles, erst eine explizite Zuweisung schränkt
    ein. Automatisch wirksam für alle bestehenden Aufrufer derselben Funktion (`NodeViewSet`,
    `TimeTemplateViewSet`, `ShiftAssignmentViewSet`) sowie neu für `TimeRecordViewSet`/
    `MissingTimeRecordViewSet`. Bewusst NICHT angefasst (Scope-Grenze, siehe unten):
    `EmployeeViewSet`/`AbsenceViewSet`/Dashboard-Endpunkte -- deren `GET /api/employees/` wird an
    mehreren Stellen ohne Filter für den KOMPLETTEN Bestand gebraucht (siehe
    `EmployeeViewSet`-Docstring), unconditional Scoping dort hätte unklare Nebenwirkungen auf
    Diensttausch/Dashboard gehabt.

    **Backend, Teil 2 -- Zugriffsverwaltung**: `MembershipViewSet` (neu, `core` App,
    `GET/PATCH /api/memberships/`) -- Lesen für alle vier Rollen offen (Organigramm-artige Info,
    analog zur Mitarbeitenden-Liste), `scoped_nodes` ändern ist `IsTenantAdmin`-only; bewusst kein
    `create`/`destroy` (`http_method_names`), Rollenvergabe/Einladung bleibt weiterhin
    Django-Admin-only (unverändert). `MembershipSerializer.validate_scoped_nodes()` prüft
    `node.tenant_id` explizit -- ohne gesetzte Tenant-ContextVar wäre das von `ModelSerializer`
    automatisch erzeugte Feld sonst tenant-übergreifend ungefiltert. `perform_update()` lehnt
    `scoped_nodes` auf einer ADMIN-Mitgliedschaft mit klarem Fehler ab (hätte wegen des
    ADMIN-Sonderfalls in `_employee_scoped_node_ids()` ohnehin nie einen Effekt).

    **Backend, Teil 3 -- Zeiterfassung**: `TimeRecordViewSet` bekam `?status=`/`?node=` sowie
    `SearchFilter`/`OrderingFilter` (analog `EmployeeViewSet`/`TimeTemplateViewSet`) und wertet
    jetzt ebenfalls `_employee_scoped_node_ids()` aus (`assignment__node_id__in=...`) -- bisher
    unstationsgebunden, eine Lücke, die für die neue Übersicht mitbehoben wurde.
    `TimeRecordSerializer` um `assignment_date`/`assignment_employee_name`/`assignment_node_id`/
    `assignment_node_name`/`assignment_template_id`/`assignment_template_name` (alle read-only)
    erweitert, damit eine Tabellenzeile ohne Zusatz-Request Station/Mitarbeiter/Datum zeigen kann.
    Neues `MissingTimeRecordViewSet` (`GET /api/missing-time-records/`, read-only,
    `IsTenantManagerOrHR` -- neue Permission-Klasse, blendet anders als `IsTenantManager` auch
    Mitarbeitende beim Lesen aus, die ihre eigene Sicht bereits über `TimeRecordPanel` haben):
    vergangene `ShiftAssignment`s ohne `TimeRecord`, gleiches Scoping/Suche/Sortierung/Filter
    (inkl. `?date_from=`/`?date_to=`). `core.views._task_counts`: der `time_records`-Zähler für
    PLANNER wird jetzt ebenfalls über `_employee_scoped_node_ids()` gescoped, statt tenant-weit zu
    zählen -- sonst hätte die Badge-Zahl wieder nicht zu dem gepasst, was ein Klick darauf zeigt.

    **Frontend**: Neues Settings-Modul "Planer-/HR-Zugriff" (`MembershipAccessSettings.jsx`,
    admin-only wie "Regel-Engine & Zuschläge") -- Liste aller Mitgliedschaften, "Bearbeiten" öffnet
    ein Modal mit einer eingerückten Checkbox-Liste aller Stationen (gleiche Einrückung wie das
    Stations-Dropdown in `TimeTemplateSettings.jsx`). *(Stand zum Zeitpunkt dieses Eintrags --
    Modul, Filter und Kachel-Name wurden später zweimal überarbeitet, siehe Block 2, Punkt 1 für
    den aktuellen Stand: Rollen-/Zugriffsverwaltung liegt inzwischen bei "Mitarbeitende", diese
    Kachel heisst "Konten ohne Mitarbeiterprofil" und filtert auf fehlendes Mitarbeiterprofil statt
    auf Rolle.)* Neue `TimeRecordOverview.jsx` ersetzt für
    Admin/Planer/HR (`canViewScheduleReports()`, neu in `roles.js` -- deckt sich mit
    `MANAGER_AND_HR_ROLES`) den bisherigen, auf eine Station begrenzten Zeiterfassung-Tab: zwei
    per Segmented-Control umschaltbare Tabellen ("Zu bestätigen"/"Noch nicht erfasst") im
    `settings-table`-Muster von `TimeTemplateSettings.jsx` (Suche, Stations-Filter, Sortierung,
    Pagination), stationsübergreifend über den gesamten sichtbaren Bereich. Zeilenaktionen öffnen
    ein Modal mit dem bestehenden `TimeRecordSegmentEditor` (Korrigieren+Bestätigen bzw. Erfassen)
    -- kein neuer Editor nötig. Bewusst ein eigener `api.getEmployees()`-Abruf statt der
    `employees`-Prop von `App.jsx`: die ist auf die aktuell im Kopfbereich gewählte Station
    gefiltert (`relevantNodeIds`), diese Übersicht ist aber stationsübergreifend. Mitarbeitende
    (Self-Service) behalten unverändert die stationsgebundene `TimeRecordPanel.jsx`, da deren
    eigene Sicht bereits korrekt eingeschränkt ist und keine Umbau-Notwendigkeit bestand. Ein
    Klick auf den Zeiterfassung-Tab (der Badge sitzt im selben Button, kein eigenes Klickziel)
    erzwingt per `key`-Remount immer die "Zu bestätigen"-Ansicht, auch wenn zuvor auf "Noch nicht
    erfasst" umgeschaltet war.

    18 neue Backend-Tests (Scoping inkl. Unterstationen-Einschluss, ADMIN-Sonderfall,
    `MembershipViewSet`-Berechtigungen inkl. Tenant-Fremdstations-Ablehnung,
    `TimeRecordViewSet`-Status-Filter, `MissingTimeRecordViewSet` inkl. Zukunfts-Ausschluss/
    Read-only, `task_counts`-Scoping), volle Suite (424 Tests) grün, mit Playwright end-to-end
    verifiziert (gescopter Planer sieht in "Zu bestätigen" nur den Datensatz der eigenen Station,
    Badge-Zahl deckt sich mit der Zeilenzahl, Stations-Dropdown im Kopfbereich zeigt nur die
    zugewiesene Station samt Unterstationen, Zugriffsverwaltung im Modal zeigt/speichert die
    Stations-Checkboxen korrekt).

    **UX-Nachbesserung (2026-08, Nutzer-Feedback: "Warum sehe ich Peter Meier (Mitarbeiter) unter
    Planer-/HR-Zugriff? Und das Bearbeiten-Modal sieht beschissen aus, muss viel intuitiver
    laufen. Wähle ich einen Hauptknoten sind auch die Unterknoten markiert, wähle ich nur einen
    Kindknoten ist nur dieser markiert")**: die Liste zeigte bisher ungefiltert ALLE
    Mitgliedschaften inkl. Admin ("immer alles", nichts zu konfigurieren) und Mitarbeitende (die
    einen eigenen, hier irrelevanten Mechanismus über Employee.nodes/Employment haben) --
    `MembershipAccessSettings.jsx` filtert jetzt auf `role === "planner" || role === "hr"`. Das
    Bearbeiten-Modal war eine flache Checkbox-Liste ohne erkennbare Eltern/Kind-Beziehung, obwohl
    die Einschränkung serverseitig bereits Unterstationen automatisch mit einschliesst
    (`get_descendants()`) -- das war für die Nutzerin unsichtbar und wirkte wie unabhängige
    Checkboxen. Jetzt: eine markierte Hauptstation checkt ihre Unterstationen sichtbar mit an
    (ausgegraut, mit Begründung "inkl. über &lt;Station&gt;", nicht mehr einzeln abwählbar,
    solange die Hauptstation ausgewählt bleibt) -- click auf einen Kindknoten allein markiert
    weiterhin nur diesen. Zusätzlich ein schmaleres, eigenständiges Modal (560px statt der breiten
    1080px-Tabellen-Modal-Klasse, die für eine reine Checkbox-Liste unpassend war) mit klarerer
    Typografie/Zeilenabständen. Mit Playwright verifiziert (Liste zeigt nur noch Planer/HR-Zeilen,
    Modal markiert alle drei Unterstationen von "Station A" korrekt als eingeschlossen+deaktiviert
    mit Begründungstext).

    **UX-Nachbesserung (2026-08, Nutzer-Feedback: "Bei der Zeiterfassung fehlt noch die
    Sichtbarkeit der IST-Zeit um schnell zu prüfen. Eventuell sogar farblich grün für + und rot
    für -")**: die "Zu bestätigen"-Tabelle zeigte bisher nur Datum/Mitarbeiter/Station/Schichttyp
    -- um zu sehen, ob eine Erfassung plausibel ist, musste ein Planer immer erst das
    Korrigieren-Modal öffnen. Neue Spalte "Ist-Zeit" zeigt jetzt direkt die erfassten
    Uhrzeiten sowie eine farbige Stunden-Abweichung Ist ./. Soll darunter (grün bei mehr, rot bei
    weniger als geplant gearbeitet). Neues `TimeRecord.hours_deviation`-Property (Backend) --
    bewusst nicht `deviation_minutes`/`end_deviation_minutes` wiederverwendet (die messen nur die
    Abweichung von Start-/Endzeitpunkt, nicht die tatsächliche Netto-Stunden-Differenz) --
    wiederverwendet stattdessen `ShiftAssignment._shift_hours()` (dieselbe Soll-Berechnung wie im
    Saldo) für `Ist minus Soll`. 3 neue Backend-Tests (positiv/negativ/exakt Plan), volle Suite
    (427 Tests) grün, mit Playwright end-to-end verifiziert.

    **UX-Nachbesserung (2026-08, Nutzer-Feedback: "braucht es eine Funktion um alle zu bestätigen
    auf einmal?" / "Ja gerne, zudem gibt es im Styling Unschönheiten" plus Screenshot mit
    rot markierter Verschiebung um die Spalten "Schichttyp"/"Ist-Zeit")**: zwei separate Punkte.
    Erstens Mehrfachauswahl in der "Zu bestätigen"-Tabelle (`TimeRecordOverview.jsx`) --
    Kopfzeilen-Checkbox wählt alle Zeilen der aktuellen Seite aus, eine Sammel-Leiste
    ("N ausgewählt" + "Ausgewählte bestätigen") erscheint nur, sobald etwas markiert ist. Bewusst
    kein blindes "alles bestätigen": Zeilen mit auffälliger Abweichung (`|hours_deviation| > 1h`
    oder `break_below_minimum`) werden von "Alle auswählen" automatisch ausgeschlossen und mit
    einem "⚠"-Hinweis auf dem Abweichungs-Badge markiert (Tooltip "Auffällig -- bitte vor dem
    Bestätigen prüfen") -- einzeln bleiben sie weiterhin manuell auswählbar, damit nichts
    verschleiert wird, aber der Sammel-Klick räumt nur die unauffälligen Fälle weg. Bestätigung
    läuft über `Promise.allSettled` auf den bestehenden `POST /api/time-records/{id}/confirm/`-
    Endpunkt (kein neuer Bulk-Endpunkt nötig), Fehlschläge einzelner Einträge werden gemeldet statt
    die übrigen zu blockieren. Zweitens die im Screenshot markierte Zeilenverschiebung: die neue,
    zweizeilige "Ist-Zeit"-Zelle (Uhrzeiten + Abweichungs-Badge) machte ihre Zeile höher als die
    übrigen einzeiligen Zellen -- ohne `vertical-align` hängen kürzere Zellen im Browser-Default
    ("middle") mittig statt oben, was uneins bündig wirkte. Fix: `vertical-align: top` auf
    `.settings-table td`. Mit Playwright end-to-end verifiziert (Kopfzeilen-Checkbox wählt korrekt
    nur unauffällige Zeilen aus, Sammel-Leiste zeigt richtige Anzahl, Sammel-Bestätigung bestätigt
    alle ausgewählten Einträge und aktualisiert die Badge-Zahl live, alle Tabellenzellen sind nach
    dem Fix konsistent oben ausgerichtet).

    **UX-Nachbesserung (2026-08, Nutzer-Feedback: "styling passt noch nicht. ich schlage vor mach
    es so 07:00–12:00, 13:00–16:24 (±0 h) nebeneinander")**: die Ist-Zeit-Spalte stand bisher auf
    zwei Zeilen (Uhrzeiten, darunter das Abweichungs-Badge) -- jetzt eine Zeile, Abweichung in
    Klammern direkt dahinter, exakt wie vom Nutzer vorgeschlagen. Mit Playwright verifiziert
    (Zellentext `07:00–12:00, 13:00–16:24 (±0 h)`, Screenshot bestätigt einzeilige Darstellung).

    **UX-Nachbesserung (2026-08, Nutzer-Feedback mit Screenshot: "Es ist immer noch verschoben!
    kannst du nicht einfach alles vertikal zentrieren in der Zeile? Es soll perfekt aussehen ohne
    Abstufungen etc.")**: der vorherige `vertical-align: top`-Fix stammte aus der Zeit, als die
    Ist-Zeit-Zelle noch zweizeilig war -- seit der Umstellung auf eine Zeile (siehe oben) war er
    hinfällig und sorgte stattdessen dafür, dass die Aktionsspalte (Button "Bestätigen" mit eigener
    Höhe durch Padding/Border) oben an der Zeile hing statt zentriert zu sein, was wie eine
    Verschiebung wirkte. Fix: `.settings-table td` auf `vertical-align: middle` zurückgestellt --
    jetzt zentrieren sich alle Zellen unabhängig von ihrer individuellen Höhe einheitlich in der
    Zeile. Mit Playwright verifiziert (Screenshot zeigt Checkbox, Text, Ist-Zeit und
    "Bestätigen"-Button auf gemeinsamer vertikaler Mitte, kein Versatz mehr).

    **Bugfix (2026-08, Nutzer-Feedback: "gut aber warum hat der bottom border immer noch eine
    Abstufung unter 'Korrigieren'?")**: der `vertical-align`-Fix allein reichte nicht -- die
    eigentliche Ursache war `display: flex` direkt auf dem `<td className="settings-table-actions">`.
    Ein `<td>`, dessen `display` auf `flex` überschrieben wird, verlässt sein normales
    table-cell-Boxmodell; der Browser berechnete die Zeilenhöhe für genau diese eine Zelle 0.5px
    abweichend von den übrigen Zellen (per `getBoundingClientRect()` gemessen: 46px statt 46.5px),
    was als minimal versetzte Zeilenborder sichtbar wurde. Der Effekt betraf denselben,
    wiederverwendeten `.settings-table-actions`-Klassennamen auch in `TimeTemplateSettings.jsx`
    und `EmployeeSettings.jsx` -- dort bisher nur nicht aufgefallen. Fix an allen drei Stellen:
    `display: flex` liegt jetzt auf einem `<span className="settings-table-actions">` innerhalb
    eines normalen `<td>`, das Boxmodell der Zeile bleibt für alle Zellen identisch. Mit
    Playwright verifiziert (`getBoundingClientRect()` aller Zellen einer Zeile liefert jetzt
    exakt gleiche top/bottom/height-Werte, Screenshot zeigt eine durchgehende, unversetzte
    Border).

    **Spezialitäten-Zuschlag, z. B. Pikett** (2026-08, Nutzer-Feedback: "Spezialitäten Schichttypen
    können auch zuschlagspflichtig sein, korrekt? wenn jemand Pikett macht, ist dieser
    zuschlagsberechtigt" / "setz das so um ABER mit der Aufschlüsselung -- Grund, auf der
    Lohnabrechnung und dem Mapping in Punkt 30 brauchen wir das sowieso fürs Mapping"): neues Feld
    `TimeTemplate.surcharge_pct` (Migration `0023`, Default 0) -- Lohnzuschlag in % der geplanten
    Stunden, wirkt bewusst nur bei `category == "special"` (reguläre Dienste laufen bereits über
    Überzeit- sowie die zeitpunktbasierten Nacht-/Sonntagszuschläge, ein weiterer Prozentsatz dort
    würde sich überschneiden). Berechnung analog Nacht-/Sonntagszuschlag: `geplante Stunden ×
    surcharge_pct / 100`, nicht auf der Ist-Zeit (dieselbe Begründung wie bei den bestehenden
    Zuschlägen -- die Erfassung zeigt nur *wann*, nicht *ob Pikett*). Dabei ein Bugfix nebenbei
    gefunden und behoben: `Employee.monthly_summary()` schloss Spezialitäten -- anders als
    `weekly_hours_summary()`/`time_account_summary()` -- bisher nicht von den normalen
    Ist-/Nacht-/Sonntagsstunden aus; eine Pikett-Zuweisung mit nichtleerer Zeitspanne wäre sonst
    fälschlich in die normale Ist-Stundenzahl eingeflossen.

    Bewusst **pro Spezialität einzeln aufgeschlüsselt** (`special_surcharge_breakdown`, Liste aus
    `{template_id, template_name, surcharge_pct, hours, surcharge_hours}`) statt nur eine
    Gesamtsumme: das künftige Lohnart-Mapping (Punkt 30) braucht pro Spezialität einen eigenen
    Lohnart-Code, da z. B. "Pikett Wochentag" und "Pikett Wochenende" in der Kundenlohnsoftware
    unterschiedliche Codes haben können -- eine reine Summe liesse sich später nicht mehr
    aufteilen. `special_surcharge_hours` bleibt zusätzlich als Gesamtsumme für die
    Kennzahlen-Tabelle erhalten. `MonthlySummarySerializer`/`TimeTemplateSerializer` entsprechend
    erweitert. Frontend: `TimeTemplateSettings.jsx` zeigt das Zuschlag-%-Feld nur bei Kategorie
    "Spezialität" (kein irreführendes Feld bei regulären Diensten, wo es ohnehin wirkungslos wäre)
    plus einen "Zuschlag X%"-Hinweis in der Tabelle; `MonthlySummaryPanel.jsx` zeigt eine
    "Spezialitäten-Zuschlag total"-Zeile in der Haupttabelle sowie -- falls vorhanden -- eine
    zweite Tabelle mit der Aufschlüsselung pro Schichttyp. 8 neue Backend-Tests (Bugfix-Regression,
    Aufschlüsselung bei einer/mehreren Spezialitäten, Spezialität ohne Zuschlag bleibt aussen vor,
    Serializer-Roundtrip, API-Response), volle Suite (433 Tests) grün.

    **Gleitzeit-Korridor statt automatischer Überzeitauszahlung** (2026-08, Nutzer-Rückfrage anhand
    eines echten Beispiels: Marco Bianchi, 80%-Pensum, Mo-Do volle Frühschicht statt 5x reduziert --
    "würde die Lohnbuchhaltung das 'Rauschen' nun auszahlen? Bei uns gilt Gleitzeit, nur angeordnete
    Überstunden werden effektiv abgerechnet" / "Was ist state of the art? Wie machen das andere
    Tools?"): bis hierhin wurde jede positive Monats-Soll/Ist-Differenz automatisch mit dem
    Überzeitzuschlag versehen -- bei einer Gleitzeit-Vereinbarung zu grosszügig, da reines
    Kalenderrauschen (z. B. ein Monat mit einem Montag mehr als Freitagen bei einem festen
    4-Tage-Muster) genauso abgerechnet würde wie echte angeordnete Mehrarbeit. Statt einer Markierung
    pro einzelner Schicht (zu aufwendig, siehe Diskussion) das branchenübliche Muster: eine
    **Gleitzeit-Bandbreite** pro Tenant (`Tenant.flextime_corridor_hours`, Default 20h, neues
    Settings-Feld unter "Regel-Engine & Zuschläge" → "Überzeit") -- der laufende Jahressaldo
    (`Employee.time_account_summary()["saldo_hours"]`) darf sich frei darin bewegen, erst der
    positive Anteil darüber hinaus wird in der Monatsauswertung als "ausserhalb des Korridors"
    vorgeschlagen und muss dort **einmal pro Mitarbeiter und Monat** per Klick bestätigt werden,
    bevor er in `overtime_surcharge_hours` (und damit später in den Lohnlauf) einfliesst -- kein
    Zutun der Planer beim alltäglichen Einteilen nötig, nur eine bewusste Entscheidung am
    Monatsende für die tatsächlich abrechnungsrelevanten Fälle.

    Neues Modell `OvertimeSettlement` (ein Datensatz pro Mitarbeiter/Monat, `hours` +
    `surcharge_hours` zum Bestätigungszeitpunkt fixiert, `HistoricalRecords` für die
    Nachvollziehbarkeit analog `Absence`/`ShiftTradeRequest`) -- `Employee.
    _flextime_corridor_status(year, month)` vergleicht den kumulierten Jahressaldo (abzüglich bereits
    in früheren Monaten desselben Jahres bestätigter Beträge) mit der Bandbreite;
    `confirm_overtime_settlement()` legt den Datensatz an (idempotent -- ein zweiter Klick zahlt
    nicht doppelt aus) und wirft einen Fehler, wenn nichts ausserhalb des Korridors liegt. Neuer
    Endpoint `POST /api/employees/{id}/settle-overtime/` (Body `{year, month}`, gleiche
    Admin/Planer-Berechtigung wie `monthly-summary`). `monthly_summary()` liefert neu `saldo_hours`,
    `flextime_corridor_hours`, `flextime_corridor_excess_hours`, `is_overtime_settled` zusätzlich zu
    den bestehenden Feldern -- `overtime_hours` bleibt als reine Kennzahl "wie weit war dieser Monat
    vom Soll entfernt" bestehen, ist aber nicht mehr automatisch abrechnungsrelevant.

    Bewusste Abgrenzung: die persönliche Gleitzeit-Saldo-Anzeige (BalanceBadge in Topbar/
    Mitarbeiterliste, `time_account_summary()`) bleibt unverändert und zeigt weiterhin den reinen
    Ist/Soll-Verlauf, unabhängig von bereits bestätigten `OvertimeSettlement`-Beträgen -- eine
    Rückkopplung dorthin (bestätigte/ausbezahlte Stunden aus dem persönlichen Saldo herausrechnen)
    wäre ein sinnvoller nächster Schritt, war aber nicht Teil dieser Anfrage und hätte den
    bereits breit verwendeten `time_account_summary()` angefasst.

    Frontend: `TenantSettings.jsx` neues Feld "Gleitzeit-Bandbreite (h)"; `MonthlySummaryPanel.jsx`
    zeigt den kumulierten Saldo als eigene Zeile, bei Überschuss eine Hinweisbox
    (`.corridor-callout`) mit "Überschuss bestätigen"-Button, nach Bestätigung stattdessen einen
    Bestätigungs-Hinweis. 12 neue Backend-Tests (Korridor-Erkennung, Bestätigung inkl. Idempotenz,
    Ablehnung ohne Überschuss, kein erneutes Flaggen bereits bestätigter Beträge in einem
    Folgemonat, API-Berechtigung/-Response), volle Suite grün. Mit Playwright gegen echte
    Testheim-Daten verifiziert: Marco Bianchis 1.68h Kalenderrauschen (weit unter dem 20h-Korridor)
    erscheint korrekt ohne Hinweisbox und mit 0h Zuschlag; ein Testmitarbeiter mit 27h Saldo zeigt
    die Hinweisbox mit 7h Überschuss, Bestätigen setzt den Zuschlag auf 1.75h (25%) und bleibt nach
    Neuladen der Seite bestehen.

37. ✅ **Abwesenheiten & Diensttausch: gleiches stationsübergreifendes Muster wie Zeiterfassung**
    (2026-08, Nutzer-Feedback: "Ich finde der Tab Abwesenheiten und der Tab Diensttausch sollten
    gleich aufgebaut sein wie Zeiterfassung"). Baut auf Punkt 36 auf, überträgt dasselbe Muster
    1:1.

    **Backend**: `AbsenceViewSet`/`ShiftTradeRequestViewSet` erhalten `?search=`/`?ordering=`/
    `?node=` (DRF `SearchFilter`/`OrderingFilter`) sowie dasselbe Stations-Scoping über
    `_employee_scoped_node_ids()` wie `TimeRecordViewSet` -- schliesst eine Lücke: Planer/HR mit
    `Membership.scoped_nodes` sahen bei Abwesenheiten/Diensttausch bisher trotzdem den ganzen
    Tenant. Bei Absenzen (`Employee.nodes` ist M2M) zusätzlich ein Sonderfall: eigene Absenzen
    bleiben immer sichtbar/verwaltbar, auch ohne zugeordnete Station -- sonst hätten
    `approve()`/`reject()`/`delete()` auf die eigene Absenz 404 statt 403 geliefert (DRF prüft
    Objektberechtigungen erst nach dem Queryset-Filter). Bei Diensttausch ein analoger Sonderfall
    für Zielperson/anbietende Person, sonst könnte eine ausserhalb der eigenen Station adressierte
    Anfrage nicht mehr gesehen/angenommen werden. `AbsenceSerializer`/`ShiftTradeRequestSerializer`
    um denormalisierte Anzeige-Felder erweitert (Mitarbeitername, Stationen-Text,
    Schichttyp-Farbe/-Name etc.), analog `TimeRecordSerializer` -- vermeidet N+1-artige
    Detail-Lookups im Frontend. `core/views.py::_task_counts` zieht dieselbe Scoping-Logik für die
    `absences`/`trades`-Zähler nach (vorher tenant-weit, inkonsistent zur neuen gescopten
    Übersicht).

    **Frontend**: Neue `AbsenceOverview.jsx`/`TradeRequestOverview.jsx` (Admin/Planer/HR,
    `canViewScheduleReports()`) ersetzen den bisherigen, stationsgebundenen Tab durch dasselbe
    `settings-table`-Muster wie `TimeRecordOverview.jsx`: Segmented-Control ("Zu genehmigen"/
    "Alle" bzw. "Offen"/"Alle"), Suche, Stationsfilter, Sortierung, Pagination,
    stationsübergreifend. Bei Abwesenheiten zusätzlich Checkbox-Spalte + Massenaktion
    "Ausgewählte genehmigen" (keine Risiko-Ausschluss-Logik wie bei Zeiterfassung nötig, da es
    kein Abweichungsmass gibt); bei Diensttausch bewusst keine Massenaktion, weil die richtige
    Zeilenaktion vom Betrachter abhängt (Admin/Planer: Freigeben/Ablehnen; Zielperson: Annehmen/
    Ablehnen; anbietende Person: Zurückziehen -- dieselbe Logik wie zuvor in
    `TradeRequestPanel.jsx`). "+ Absenz erfassen" öffnet ein Modal mit neu extrahiertem
    `AbsenceForm.jsx` (aus `AbsencePanel.jsx` herausgelöst, von beiden Komponenten
    wiederverwendet). `AbsencePanel.jsx`/`TradeRequestPanel.jsx` bleiben unverändert für die
    Mitarbeiter-Rolle (Self-Service, stationsgebunden) -- `AbsencePanel.jsx` dabei vereinfacht:
    keine `canManage`-Verzweigung und kein tenant-weiter `allEmployeesById`-Workaround mehr, da
    beides nur wegen des vorher fehlenden Backend-Scopings nötig war.

    24 neue Backend-Tests (Suche/Sortierung/Filter/Scoping für beide Ressourcen,
    Serializer-Feldassertions inkl. `target_assignment=None`-Fall, Regressionstest für den
    Diensttausch-Mitarbeiter-Sonderfall), plus zwei Regressionsfixes bei bestehenden
    Berechtigungstests, die durch das neue Scoping aufgedeckt wurden (ein Mitarbeiter ohne eigene
    Stationszuordnung hätte sonst 404 statt 403 auf die eigene Absenz erhalten -- behoben über
    denselben Eigentümer-Sonderfall wie beim Diensttausch). Volle Suite (581 Tests) grün. Mit
    Playwright gegen echte Testheim-Daten für alle vier Rollen verifiziert: Admin sieht beide Tabs
    stationsübergreifend mit echten Zeilen (Segmented-Control, Sortierung, Aktionen), gescopter
    Planer sieht nur die eigene Station, HR sieht dieselbe Übersicht rein lesend (keine Erfassen-/
    Genehmigen-Buttons -- dabei einen bei der Implementierung selbst gefundenen
    Berechtigungs-Mismatch behoben: HR erreicht die Übersicht über `canViewScheduleReports`, aber
    `MANAGER_ROLES` schliesst HR von Schreibaktionen aus, das Frontend blendete diese Buttons
    anfangs nicht konsequent für HR aus), Mitarbeiter behält die unveränderte
    Self-Service-Ansicht.

38. ✅ **Einheitliches Button-Styling für Abwesenheiten/Diensttausch/Zeiterfassung** (2026-08,
    Nutzer-Feedback: "Kannst du bitte alle Buttons schön stylen auf Abwesenheiten,
    Zeiterfassung"). Bis hierhin gab es im ganzen Frontend ausser `.btn-ghost` keine eigene
    Button-Klasse -- jede primäre Aktion (Genehmigen, Freigeben, Annehmen, Bestätigen, Erfassen,
    Anlegen, "+ Absenz erfassen") war ein `<button>` ohne eigenes Styling, reiner
    Browser-Default-Look. Neue `.btn-primary`-Klasse (gefüllt, `--primary`-Farbe, dezenter
    Hover/Active-Zustand über `filter: brightness()`) deckt jetzt jede Hauptaktion in
    `AbsenceOverview.jsx`/`TradeRequestOverview.jsx`/`AbsenceForm.jsx` sowie
    `TimeRecordOverview.jsx`/`TimeRecordPanel.jsx`/`TimeRecordSegmentEditor.jsx` ab (Genehmigen,
    Freigeben, Annehmen, Bestätigen, Erfassen, Speichern, Anlegen, die Massenaktions-Buttons in
    den Bulk-Bars). Neue `.btn-ghost.btn-danger-ghost`-Modifikatorklasse tönt destruktive
    Ghost-Buttons (Ablehnen, Löschen) beim Hover warnfarben (`--warn`/`--warn-soft`) statt
    primärfarben, ohne eine zweite, optisch schwerere Button-Klasse einzuführen -- neutrale
    Ghost-Aktionen wie Korrigieren/Abbrechen/Zurückziehen bleiben unverändert. Globales
    `button:disabled` (reduzierte Deckkraft, `cursor: not-allowed`) sorgt dafür, dass z. B. der
    Speichern-Button im Segment-Editor bei einem Blöcke-überlappen-sich-Fehler sichtbar deaktiviert
    wirkt statt weiterhin klickbar auszusehen. Mit Playwright gegen echte Testheim-Daten für alle
    drei Tabs verifiziert (Zu-genehmigen-/Offen-/Zu-bestätigen-Zeilen, Bulk-Bar, Korrektur-Modal).

    **Nachtrag (2026-08, Nutzer-Feedback: "Gleiches gilt für Lohnarten, Regel-Engine & Zuschläge
    und Konten ohne Mitarbeiterprofil")**: dieselbe `.btn-primary`/`.btn-ghost.btn-danger-ghost`-
    Behandlung auf die drei verbliebenen Admin-only-Settings-Module ausgeweitet --
    `PayrollSettings.jsx` (die Speichern-Buttons pro Lohnart-Zeile + "CSV herunterladen"),
    `TenantSettings.jsx` (Speichern, "Hinzufügen" bei den Feiertags-Ausnahmen, "Entfernen" als
    Danger-Ghost) und `MembershipAccessSettings.jsx` ("+ Konto hinzufügen", "Anlegen",
    "Verstanden, schliessen", Speichern im Stationen-Modal). Dabei einen Bug aufgedeckt, der durch
    `.btn-primary` erst sichtbar wurde: `.panel-form-group` ist ein
    `display: flex; flex-direction: column`-Container mit dem Flex-Default `align-items: stretch`
    -- ein Button darin wurde schon vorher auf die volle Breite gestreckt, fiel als unstyled
    grauer Rahmen aber kaum auf. Mit gefülltem `--primary`-Hintergrund war der Effekt (z. B.
    "Hinzufügen" bei den Feiertags-Ausnahmen) deutlich sichtbar falsch.

    **Korrektur (2026-08, Nutzer-Feedback: "CSV herunterladen auf Lohnarten steht nun irgendwo im
    Raum, zentriere ihn vertikal mit dem Monatsfeld" / "bei Konten ohne Mitarbeiterprofil steht
    der Button irgendwo und überschneidet den Text fast")**: der erste Fix-Versuch setzte
    `align-self: flex-start` pauschal auf `.btn-primary` selbst -- das behob zwar das
    `.panel-form-group`-Streck-Problem, riss aber zwei bereits absichtlich austarierte
    Flex-Row-Ausrichtungen wieder ein, die `align-self: flex-start` stillschweigend
    überschreibt: `.panel-form-row` nutzt bewusst `align-items: flex-end` (Button auf Höhe des
    Eingabefelds, nicht des Labels darüber -- genau das liess "CSV herunterladen" neben dem
    Monatsfeld nach oben wegdriften) und `.panel-list-header` nutzt `align-items: center` (Button
    neben dem Titel -- das liess "+ Konto hinzufügen" an den Titel heranrücken). Fix jetzt gezielt
    als `.panel-form-group > .btn-primary { align-self: flex-start; }` statt auf der Klasse selbst
    -- betrifft nur den einen Container, in dem das Streck-Problem tatsächlich auftrat, alle
    anderen Flex-Kontexte behalten ihre eigene, bereits korrekte `align-items`-Regel. Mit
    Playwright erneut gegen alle sechs Module verifiziert (inkl. Regressionscheck der zuvor
    verifizierten Abwesenheiten-/Zeiterfassungs-Screens).

    **Weiterer Nachtrag (2026-08, Nutzer-Feedback: "Konten ohne Mitarbeiterprofil sieht nicht gut
    aus! Platziere den Button besser!")**: die korrekt ausgerichtete `align-items: center`-Zeile
    aus dem vorherigen Fix (Titel + Button nebeneinander) sah bei mehrzeiligem Beschreibungstext
    darunter trotzdem unbalanciert aus -- der Button klebte oben rechts am Titel, während rechts
    neben den umgebrochenen Zeilen der Beschreibung ein grosser, ungenutzter Leerraum entstand.
    `.panel-list-header` (Titel+Button nebeneinander) entfernt, stattdessen fliessen Titel und
    Beschreibung normal untereinander, der Button bekommt danach eine eigene, rechtsbündige Zeile
    (neue `.panel-list-actions`-Klasse) -- liest sich jetzt als klar abgesetzte Aktion statt als
    Kopfzeilen-Dekoration neben unausgeglichenem Fliesstext.

    **Dritte Runde (2026-08, Nutzer-Feedback: "Ernsthaft? Sieht das für dich stimmig aus?" mit
    Screenshot)**: ohne Abstand nach unten rückte der rechtsbündige Button direkt über die
    ebenfalls rechtsbündige Rollen-Auswahl der ersten Kontenzeile darunter -- beide sahen wie eine
    zusammengehörige Gruppe aus, obwohl sie inhaltlich nichts miteinander zu tun haben ("+ Konto
    hinzufügen" vs. Rollen-Dropdown eines bestehenden Kontos). `.panel-list-actions` bekommt jetzt
    `margin-bottom: 16px` plus `border-bottom`, damit Aktion und Liste sichtbar getrennt sind statt
    ineinander zu verschwimmen.

    **Löschfunktion (2026-08, Nutzer-Feedback: "Konten ohne Mitarbeiterprofil sollten ebenfalls eine
    löschfunktion haben")**: `MembershipViewSet` erlaubt jetzt `DELETE` (vorher explizit
    ausgeschlossen). `perform_destroy` löscht den `User` (nicht nur die Membership), sonst bliebe ein
    verwaister Login-Account ohne jede Mitgliedschaft übrig -- die `Membership` verschwindet über die
    CASCADE-FK automatisch mit. Bewusst nur für den Sonderfall OHNE Mitarbeiterprofil erlaubt: ein
    Konto MIT Mitarbeiterprofil über diesen Weg zu löschen würde `User.delete()` via
    `Employee.user` (OneToOneField, `on_delete=CASCADE`) das Mitarbeiterprofil samt Historie
    mitreissen -- für Mitarbeitende mit Profil bleibt die "Aktiv"-Checkbox in `EmployeeSettings.jsx`
    die vorgesehene Deaktivierung (entfernt nur die Sichtbarkeit im Planblatt, nicht den Login --
    `Employee.is_active` und `User.is_active` sind unabhängige Felder). Vier Sicherheitschecks vor dem
    Löschen: Konto mit Mitarbeiterprofil, `is_staff`/`is_superuser`-Konto, eigener Account, letzte
    Admin-Mitgliedschaft eines Mandanten -- alle vier per `ValidationError` (400) abgefangen statt
    einer harten 500/403. Frontend: neuer "Löschen"-Button (`.btn-ghost.btn-danger-ghost`) je
    Kontenzeile mit `window.confirm()`-Bestätigung (gleiches Muster wie
    `PregnancyEditor.jsx`). Backend-Tests decken alle vier Sperren plus den Erfolgsfall ab, volle
    Suite (586 Tests) grün. Mit Playwright verifiziert: Löschen entfernt das Konto sofort aus der
    Liste, der Leerzustand "Keine Konten ohne Mitarbeiterprofil vorhanden." erscheint korrekt.

    **Mitarbeiter deaktivieren inkl. Login-Sperre + Austrittsdatum (2026-08, Nutzer-Feedback: "ein
    Deaktivieren Button [...] deaktiviert diesen inklusive seines Logins! Wenn einer Austritt aus dem
    Unternehmen muss das Handlebar sein [...] ein Mitarbeiter braucht auch ein Austrittsdatum")**: die
    bestehende "Aktiv"-Checkbox betrifft bewusst nur `Employee.is_active` (Planblatt-Sichtbarkeit),
    für einen tatsächlichen Austritt reicht das nicht -- neuer `Employee.termination_date`
    (optional) plus zwei neue Wege, die dieselbe Wirkung erzielen: sofort per Klick oder automatisch
    zum Stichtag.
    - **Sofort**: `EmployeeViewSet.deactivate` (POST `.../deactivate/`, Admin-only wie jede
      Kontoverwaltung, die den Login anfasst -- vgl. `setup_access`) setzt `Employee.is_active = False`
      und, falls ein Login-Zugang existiert, zusätzlich `user.is_active = False`. Frontend: neuer
      "Deaktivieren"-Button je Zeile in der Mitarbeitenden-Tabelle (nur sichtbar, solange die Person
      noch aktiv ist), mit `window.confirm()`-Bestätigung.
    - **Automatisch zum Austrittsdatum**: neues Feld "Austrittsdatum" im Bearbeiten-Formular
      (`Employee.termination_date`, optional). Ein neuer management command
      `deactivate_expired_employees` (gedacht für einen täglichen Cronjob, z. B. 00:05 Uhr) sucht
      tenant-übergreifend (`Employee.all_objects`, da ausserhalb eines Requests kein "aktueller
      Tenant" in der ContextVar existiert) nach noch aktiven Employees mit erreichtem/verstrichenem
      Austrittsdatum und wendet dieselbe Deaktivierungs-Logik an wie der manuelle Button -- idempotent,
      ein zweiter Lauf am selben Tag ändert nichts mehr.
    - Beide Wege sind bewusst dieselbe Operation (Employee.is_active + ggf. user.is_active), nur
      unterschiedlich ausgelöst -- kein Duplikat-Code, der auseinanderlaufen könnte.
    - Backend-Tests: `EmployeeDeactivateTests` (Admin kann deaktivieren inkl. Login-Sperre, Planer
      darf nicht, Konto ohne Login wird nur über `is_active` deaktiviert, ein Login-Versuch nach der
      Deaktivierung schlägt tatsächlich am Token-Endpoint fehl -- nicht nur der DB-Flag wird geprüft)
      und `DeactivateExpiredEmployeesCommandTests` (Stichtag heute/vergangen wird deaktiviert,
      zukünftiges Datum bleibt unangetastet, kein Datum bleibt unangetastet, mandantenübergreifender
      Lauf, Idempotenz). Volle Suite (596 Tests) grün. Mit Playwright verifiziert: "Austrittsdatum"-
      Feld im Formular, "Deaktivieren"-Button pro Zeile, nach Bestätigung erscheint "INAKTIV" im
      Status, Login-Zugang (`user.is_active`) tatsächlich gesperrt.

      **Nachtrag (2026-08, Nutzer-Feedback: "Ja mach den Zusatz")**: Gegenstück `EmployeeViewSet.
      reactivate` (POST `.../reactivate/`, Admin-only wie `deactivate`) -- ohne diesen Weg gäbe es
      keine Möglichkeit, einen versehentlich deaktivierten oder wieder eingestellten Mitarbeitenden
      inklusive Login zurückzuholen. Setzt `Employee.is_active = True` und, falls ein Login-Zugang
      existiert, `user.is_active = True`. Setzt zusätzlich `termination_date` auf `None` zurück --
      sonst würde ein noch gesetztes, bereits verstrichenes Austrittsdatum die gerade reaktivierte
      Person beim nächsten Lauf von `deactivate_expired_employees` automatisch wieder deaktivieren,
      eine stille Falle bei einer Wiedereinstellung oder einer Korrektur nach Fehlklick. Frontend:
      der Zeilen-Button wechselt je nach `is_active` zwischen "Deaktivieren" (rot) und "Reaktivieren"
      (neutral), beide mit `window.confirm()`-Bestätigung. Backend-Tests (Admin kann reaktivieren
      inkl. Login, Planer darf nicht, Austrittsdatum wird zurückgesetzt -- mit Regressionstest, dass
      ein nachfolgender `deactivate_expired_employees`-Lauf die Person NICHT erneut deaktiviert, ein
      erneuter Login-Versuch nach Reaktivierung klappt tatsächlich). Volle Suite (600 Tests) grün, mit
      Playwright verifiziert (Deaktivieren → Reaktivieren → Status wieder "Aktiv", Login-Zugang
      tatsächlich wiederhergestellt).

39. ✅ **Auffülldienst: Automatisierte Planung für Teams ohne Zahlen-Ziel pro Dienst** (2026-08).
    Nutzer-Feedback am konkreten Fallbeispiel (ICT: Frühdienst 1 Person, Spätdienst 1 Person,
    "Gleitzeit" für alle übrigen Teammitglieder): `minimum_staffing` (Punkt 19) ist eine feste
    Zielzahl, Boden UND Deckel -- dafür gibt es keine sinnvolle Zahl, wenn "alle, die sonst nichts
    haben" gemeint ist, die tatsächliche Anzahl schwankt täglich mit Absenzen/Teilzeit-Mustern.
    Deckt drei zusammenhängende Lücken ab, die dieses Fallbeispiel aufdeckte:

    - **`Employee.fixed_weekdays_off`**: bislang kannte das Modell nur `employment_pct` (eine
      Prozentzahl, kein Wochenmuster). Neues Feld (Liste 0=Montag..6=Sonntag) deckt sowohl das
      individuelle Teilzeit-Muster als auch, bei allen Vollzeitkräften gesetzt, ein Team ohne
      Wochenend-Betrieb ab -- bewusst kein separates "Betriebstage"-Konzept auf Node/TimeTemplate,
      um das Modell nicht zu verdoppeln. Blockt diese Tage hart in der Automatik (wie ein
      Absenz-Volltag), lässt manuelles Stempeln aber unberührt. Settings-UI: Wochentag-Checkboxen
      in `EmployeeSettings.jsx`; `PlanGrid.jsx` markiert den Tag rein informativ.
    - **Genehmigungsprozess für Wunschfrei/Wunschdienst**: Nutzer-Feedback "auch hier braucht es
      einen Genehmigungsprozess" -- `ShiftPreference` bekommt ein `status`-Feld
      (PENDING/APPROVED/REJECTED, analog `Absence`) mit `approve()`/`reject()`, ausschliesslich
      Admin/Planer vorbehalten (nie der eigenen Person, auch nicht dem Antragsteller selbst).
      Anlegen bleibt höchstpersönlich (kein Manager-Override, anders als bei Absence). PENDING
      bleibt weich (überschreibbar wie bisher), APPROVED gilt für die Automatik hart, REJECTED wird
      ignoriert. Frontend: der bisher rein informative Wunsch-Badge im Planblatt ist für Admin/
      Planer jetzt anklickbar (Popover Freigeben/Ablehnen), solange der Wunsch offen ist.
    - **`TimeTemplate.fills_remaining_capacity`**: kein Zahlen-Ziel, sondern "jede an diesem Tag
      arbeitspflichtige, für dieses Team eingeteilte Person ohne anderen regulären Dienst bekommt
      automatisch diesen". Validiert: keine Mindestbesetzung, keine Pflicht-Qualifikation, keine
      Spezialität, höchstens einer pro Team (`TimeTemplate.clean()`). Im Solver
      (`scheduling/planning.py`) von der Boden/Deckel-Logik aus Punkt 19 ausgenommen -- stattdessen
      eine eigene Pflicht-Anwesenheits-Untergrenze pro Woche: wer bereits einen strukturell freien
      Tag hat (`fixed_weekdays_off`), muss an allen übrigen verfügbaren Tagen arbeiten (der
      gesetzliche Wochenruhetag ist damit schon erfüllt); ohne festes Muster bleibt weiterhin ein
      frei wählbarer Ruhetag pro Woche. Mit Schlupfvariable statt hartem Zwang (`CATCHALL_
      UNCOVERED_WEIGHT`, zwischen Mindestbesetzung und Wunsch-Präferenzen eingeordnet) -- ein
      einzelner Regelkonflikt (z. B. Ruhezeit) macht das Modell dadurch nie unlösbar, sondern
      erzeugt nur eine lesbare Warnung ("X: an Y Tag(en) im Monat konnte kein Dienst automatisch
      zugeteilt werden"). Settings-UI: Checkbox in `TimeTemplateSettings.jsx`, setzt Mindest-
      besetzung/Pflicht-Skill beim Aktivieren automatisch zurück.

    In drei Etappen umgesetzt und einzeln committet (Wochenmuster, Genehmigungsprozess,
    Auffülldienst-Schichttyp) -- jede Etappe für sich lauffähig und mit eigenen Tests, da die
    dritte auf den ersten beiden aufbaut. Volle Suite (790 Tests) grün.

### 3. Onboarding & Mandantenfähigkeit für Self-Signup

**Grundsatzentscheid (2026-08)**: kein reines Consumer-Self-Signup, sondern ein Hybrid — passend
zu einem B2B-Vertical-SaaS für Heime/Kliniken, wo Datenschutz (revDSG) und ArG-Konformität eine
höhere Vertrauenshürde als bei einem generischen Tool bedeuten. Landing Page mit zwei
gleichwertigen CTAs ("Kostenlos testen" für Self-Serve, "Demo buchen" für Ketten/grössere Häuser,
die vor dem Hochladen echter Mitarbeiterdaten mit jemandem sprechen wollen).

**Revision (2026-08, Nutzer-Feedback: "Reviewe aber den gesamten Task ob der noch sinn macht mit
den bisherigen vergangenen entwicklungen")**: Punkt 2 skizzierte ursprünglich einen
E-Mail-basierten Magic-Link-Signup -- das widersprach der seither mehrfach dokumentierten
Grundsatzentscheidung gegen jeden E-Mail-Versand-Flow (siehe Punkt 6/Block 2.1: *"Applikationsmanager
wird den Benutzer anlegen und nicht per Mail einladen"*). Umgesetzt wurde stattdessen ein
**Direkt-Signup**: die Person setzt beim Signup sofort ihr eigenes Passwort (kein Magic-Link, keine
E-Mail-Infrastruktur), wird direkt eingeloggt und landet in einem **einzigen, sofort
vorbefüllten Tenant** mit Beispieldaten (statt zwei getrennter Demo-/Echt-Tenants mit späterem
Wechsel) -- der Setup-Wizard bietet an, die Beispieldaten zu löschen/ersetzen.

1. ✅ **Landing Page** (`LandingPage.jsx`, ausgeloggter Startbildschirm statt direkt der
   Login-Maske): Nutzenversprechen konkret statt generisch ("ArG-konforme Dienstplanung ohne
   Excel-Chaos"), vier Feature-Kacheln, zwei CTAs nebeneinander -- "Kostenlos testen" (→ Punkt 2)
   und "Demo buchen" (reiner `mailto:`-Link, bewusst kein Code-Task, siehe Punkt 5 unten).
2. ✅ **Self-Serve-Signup-Flow, direkt statt Magic-Link** (`SignupForm.jsx` +
   `POST /api/signup/`, `core.views.SignupView`): erzeugt Tenant + User + Admin-Membership +
   Beispieldaten in einem Zug (`core/onboarding.py::seed_demo_tenant` -- 2 Stationen, 3
   Absenzarten, 3 Schichttypen, 2 Beispiel-Mitarbeitende, alle Demo-Objekte mit
   `"(Beispiel)"`-Namenssuffix ausser den Absenzarten, die echte Dauerkonfiguration sind) und
   loggt sofort per Token ein (`AllowAny`, neben `obtain_auth_token` der einzige unauthentifizierte
   Schreib-Endpoint der API). `Tenant.onboarding_completed` (default `True`, nur hier `False`
   gesetzt) steuert, ob das Frontend statt der normalen App den Setup-Wizard zeigt.
3. ✅ **Geführter Setup-Wizard** (`OnboardingWizard.jsx` + `OnboardingStepCanton/Stations/
   ShiftTypes/Employees.jsx`, ersetzt die vorherige Django-Admin-Pflicht für die Erstkonfiguration):
   Kanton (Feiertagskalender, Block 2.7 Punkt 7) → Stationen (Demo-Stationen umbenennen oder
   weitere hinzufügen) → Schichttypen (hartcodierte Presets zur Auswahl: Früh-/Spät-/Tag-/
   Nachtdienst) → Mitarbeitende (CSV-Import via `POST /api/employees/import-csv/` mit
   Zeilen-Fehlerbericht statt Komplettabbruch, `GET .../import-csv-template/` für die
   Beispiel-Vorlage, plus die Möglichkeit, die Demo-Stationen/-Mitarbeitenden zu löschen).
   Sichtbarer Fortschritts-Indikator, "Später fertigstellen" ist auf jedem Schritt klickbar (kein
   Zwang, jedes Tenant-Feld hat sinnvolle Defaults) und setzt einfach `onboarding_completed=true`.
4. **Konten für weitere Mitarbeitende anlegen** — inzwischen NICHT mehr per E-Mail-Einladung
   geplant (Grundsatzentscheid revidiert, siehe Punkt 6): Block 2.1 hat mit
   `EmployeeViewSet.setup_access`/`EmployeeSettings.jsx` (Direktanlage direkt im
   Mitarbeiter-Formular, siehe dort für die Historie inkl. der zwischenzeitlich wieder
   verworfenen separaten "Mitglieder"-Ansicht) bereits eine Direktanlage (Username + Rolle +
   einmalig angezeigtes Temp-Passwort, erzwungener Wechsel beim ersten Login) für den laufenden
   Betrieb umgesetzt. *Noch offen*: dieselbe Direktanlage in den Setup-Wizard (Punkt 3)
   integrieren, statt sie separat in "Einstellungen" zu suchen.
5. ✅ **Passwort-Reset bei vergessenem Passwort** (2026-08, Nutzer-Feedback: "Ja mach passwort
   reset"): wie hier ursprünglich skizziert läuft der Reset über den Applikationsmanager statt
   per Magic-Link/E-Mail (weiterhin keine E-Mail-Infrastruktur, siehe Punkt 6) --
   `MembershipViewSet.reset_password` (POST `.../reset-password/`, Admin-only über die
   Viewset-weite `IsTenantAdmin`-Berechtigung) generiert ein neues Temp-Passwort, erzwingt
   `must_change_password` und gibt das Passwort EINMALIG im Response zurück, exakt dasselbe
   Muster wie die Erstanlage (`MembershipCreateSerializer`/`EmployeeViewSet.setup_access`).
   Deckt beide Kontotypen ab: Mitarbeitende mit Login (Button "Passwort zurücksetzen" im
   "Login-Zugang"-Abschnitt des Bearbeiten-Formulars, `EmployeeSettings.jsx`) und Konten ohne
   Mitarbeiterprofil (gleicher Button je Kontenzeile, `MembershipAccessSettings.jsx`) -- beide
   nutzen dasselbe "Zugangsdaten"-Anzeigemodal wie die Erstanlage. Django-Admin-Accounts
   (`is_staff`/`is_superuser`) sind explizit ausgeschlossen (analog zu den anderen
   Kontoverwaltungs-Actions), das eigene Passwort zurückzusetzen ist dagegen bewusst erlaubt
   (legitimer Wiederherstellungsfall bei einem noch gültigen Zweitgerät -- anders als beim
   Löschen des eigenen Accounts gibt es hier keinen Grund für eine Sperre). Backend-Tests decken
   Admin-Erfolg, Nicht-Admin-403, ungültig-Werden des alten Passworts, den
   Django-Admin-Ausschluss und den Self-Reset-Fall ab, volle Suite (605 Tests) grün. Mit
   Playwright verifiziert: beide Buttons funktionieren, das Temp-Passwort erscheint korrekt im
   Modal.
6. ✅ **Rollenverwaltung im Frontend** (2026-08, siehe Block 2.1 für Details): Nutzer-Feedback
   zu diesem Punkt führte zur Revision des ursprünglich hier notierten E-Mail-Einladungs-Plans
   — *"Ist das state of the art mit Mailversand? [...] Applikationsmanager wird den Benutzer
   anlegen und nicht per Mail einladen"*. Direktanlage + Rollen-Dropdown sind seitdem bereits
   im laufenden Betrieb (Settings-Tab) nutzbar, nicht erst an den künftigen Setup-Wizard
   (Punkt 3) gekoppelt -- der Wizard kann diese bestehende Funktion später wiederverwenden,
   statt sie neu zu bauen.

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
5. **Automatisierte Frontend-Tests**: `dienstplan_frontend` hat aktuell keine persistierte
   Testsuite -- jedes Feature wurde bei der Entwicklung manuell per Playwright im Browser
   verifiziert, aber nichts davon liegt als wiederholbarer Test im Repo. Regressionen im
   Frontend fallen damit nicht automatisch auf, anders als im Backend (296 Tests, `python
   manage.py test`).

### 5. Datenschutz (revDSG) & Rechtliches

✅ **Punkte 1, 2, 3, 5 umgesetzt (2026-08)**, orientiert am revDSG (in Kraft seit 1.9.2023) als
primärer Rechtsgrundlage (Schweizer Anbieter, Schweizer Kundschaft), ergänzt um einen expliziten
DSGVO-Hinweis für den Grenzfall EU-ansässiger Grenzgänger-Mitarbeitender (Art. 3 Abs. 2 DSGVO).
**Wichtig: alle Rechtstexte (Punkt 1+2) sind als Entwurf gekennzeichnet und ersetzen keine
anwaltliche Prüfung** — vor Live-Schaltung müssen sie von einer Fachperson geprüft und alle
Platzhalter (Firmenname/Adresse/UID/Kontakt) mit echten Daten befüllt werden. Ein erfundenes
Impressum wäre selbst ein Rechtsverstoss (Art. 3 UWG), deshalb wurden dort bewusst keine
Firmendaten geraten.

1. ✅ **Auftragsverarbeitungsvertrag (AVV)**-Vorlage: `docs/legal/avv-vorlage.md` — Gegenstand/
   Dauer, Pflichten der Auftragsbearbeiterin (inkl. Meldepflicht bei Datenschutzverletzung binnen
   72 Stunden, Art. 24 revDSG), Unterauftragsbearbeiter-Tabelle (Stripe konkret, Hosting/E-Mail als
   Platzhalter da noch nicht produktiv), TOMs-Anhang (Art. 8 revDSG), Löschung/Rückgabe nach
   Vertragsende, Signaturblock. Wird pro Kundschaft unterschrieben, nicht öffentlich angezeigt.
2. ✅ **Datenschutzerklärung, AGB, Impressum**: `docs/legal/datenschutzerklaerung.md`,
   `docs/legal/agb.md`, `docs/legal/impressum.md` — jeweils mit Artikel-Zitaten (Art. 5 lit. c, 6,
   19-21, 25, 28, 32 revDSG etc.) und einer "Entwurf — keine Rechtsberatung"-Warnung. Im Frontend
   als eigene Seiten (`LegalPrivacyPolicy.jsx`/`LegalTerms.jsx`/`LegalImprint.jsx`, gerendert über
   den gemeinsamen `LegalPage.jsx`-Wrapper) über das bestehende Screen-State-Muster aus `App.jsx`
   erreichbar, verlinkt im `LandingPage.jsx`-Footer. `SignupForm.jsx` verlangt seit diesem Schritt
   eine Pflicht-Checkbox ("AGB akzeptiert/Datenschutzerklärung zur Kenntnis genommen").
3. ✅ **Löschkonzept/Aufbewahrungsfristen**: `scheduling/management/commands/
   purge_expired_personal_data.py` (analog `deactivate_expired_employees.py`, mit `--dry-run`).
   Anonymisiert `Employee`-Datensätze 10 Jahre nach `termination_date` (Art. 958f OR) —
   Name/Geburtsdatum werden überschrieben, der verknüpfte `User`-Account hart gelöscht,
   `ShiftAssignment`/`TimeRecord` bleiben als Buchungsbeleg bestehen. Leert `Absence.note` (bei
   `counts_as_sick_leave=True`) und `Pregnancy.notes` 2 Jahre nach dem jeweiligen Ereignis
   (Verhältnismässigkeit, Art. 6 Abs. 2 revDSG). **Behandelt explizit auch die
   `django-simple-history`-Zeilen dieser Modelle mit** (`Employee.history.filter(...).update(...)`
   etc.) — ohne das wäre die Anonymisierung nur Fassade, da der Audit-Trail unabhängig vom
   Live-Datensatz weiterbesteht und ihn sonst überleben würde. Kein automatischer Cron in diesem
   Schritt (kein Scheduler im Projekt vorhanden); empfohlen wird ein monatlicher manueller/
   Cron-Aufruf. Kein Self-Service-Hard-Delete durch Mitarbeitende selbst, da datenschutzrechtlich
   der Tenant/Arbeitgeber Verantwortlicher ist (Art. 328b OR), nicht die einzelne Person —
   Löschung/Berichtigung läuft über den bestehenden Admin/Planer-Weg (`deactivate`-Action,
   Employee-Bearbeiten-Formular).
4. **Offen** — **Hosting-Standort** (Schweiz/EU) ist eine reine Infrastruktur-Entscheidung, die
   nicht im Rahmen dieses Schritts getroffen wurde (überschneidet sich mit Block 4/Postgres-
   Umstieg). In der Datenschutzerklärung als `[Platzhalter]` vorgesehen (Abschnitt 10).
5. ✅ **Betroffenenrechte technisch**: `GET /api/me/data-export/`
   (`scheduling/views.py::EmployeeDataExportView`) liefert alle personenbezogenen Daten der
   eingeloggten Person als JSON (Account, Employee-Profil, Absenzen, Zuweisungen, Zeiterfassung,
   Wünsche, Schwangerschaften) — Umsetzung von Art. 25 revDSG (Auskunft) und Art. 28 revDSG
   (Datenübertragbarkeit), rein self-scoped über `request.user`. Im Frontend als Button "Meine
   Daten exportieren" in der Topbar (sichtbar für jede eingeloggte Person, nicht nur Admins),
   lädt die JSON-Antwort per Blob-Download herunter (`api.downloadMyDataExport()`, gleiches Muster
   wie der bestehende Lohn-/Plan-CSV-Export).

### 6. Abrechnung (falls kommerziell verkauft)

✅ **Umgesetzt (2026-08)** -- Stripe Checkout + Billing Portal (redirect-gehostet, kein
Kartendaten-Handling im eigenen Frontend/Backend), ein Plan mit Preis pro aktivem Mitarbeitenden
(Nutzer-Entscheidung), 14 Tage Trial für per Self-Signup angelegte Tenants (Block 3), Trial-Limit
15 aktive Mitarbeitende.

- **Modell** (`core/models.py::Tenant`): `subscription_status` (ACTIVE/TRIALING/PAST_DUE/CANCELED/
  INCOMPLETE), `trial_ends_at`, `trial_employee_limit`, `stripe_customer_id`,
  `stripe_subscription_id`. Default bewusst "voller Zugriff, kein Trial" (analog
  `onboarding_completed`) -- nur `core.views.SignupView` setzt TRIALING + `trial_ends_at` explizit,
  Admin/Fixture/Test-Tenants bleiben unverändert sofort nutzbar. `Tenant.has_active_access()`
  (ACTIVE oder laufende Trial-Frist) und `Tenant.active_employee_count()` sind die zentralen
  Abfragen.
- **`core/billing.py`**: Stripe-Integration hinter einer schmalen Funktions-Schicht --
  `create_checkout_session`/`create_billing_portal_session` (Subscription-Menge = aktuelle aktive
  Mitarbeitende, mind. 1), `sync_subscription_quantity` (best effort, Stripe-Fehler blockieren nie
  eine normale Mitarbeitenden-Aktion), `handle_webhook_event` (checkout.session.completed,
  customer.subscription.updated/deleted, invoice.payment_failed) und `enforce_billing_access`
  (402 Payment Required für schreibende Requests eines Tenants ohne aktiven Zugriff, GET bleibt
  immer offen). Fehlende Stripe-Konfiguration wirft `BillingNotConfigured` statt eines rohen
  Stripe-Fehlers -- die Test-Suite läuft dadurch ohne echte Stripe-Keys.
- **Zugriffssperre**: `enforce_billing_access()` läuft zentral in
  `scheduling.views.TenantScopedViewSet.initial()` und `core.views.TenantScopedAPIMixin.initial()`
  -- denselben Stellen, an denen `request.tenant` aufgelöst wird. Die Billing-Views selbst
  (Checkout/Portal/Status) sind bewusst davon ausgenommen, sonst könnte sich ein gesperrter
  Tenant nicht mehr selbst freischalten.
- **Trial-Limit**: `EmployeeSerializer.validate()` weist neue Mitarbeitende ab, sobald
  `active_employee_count() >= trial_employee_limit` UND `subscription_status == TRIALING` --
  deckt sowohl den normalen POST-Weg als auch den CSV-Import (Block 3) mit einem Check ab. Kein
  Limit mehr nach Abo-Abschluss (ACTIVE), dort wird stattdessen die Stripe-Menge bei jeder
  relevanten Änderung (anlegen/deaktivieren/reaktivieren/CSV-Import) synchronisiert.
- **API** (`core/billing_views.py`): `GET /api/billing/status/` (Abo-Status, Trial-Restzeit,
  aktive Mitarbeitende, plus -- sobald ein Abo läuft -- die LIVE-Stripe-Subscription-Details über
  `core.billing.get_subscription_details()`: nächstes Rechnungsdatum, Preis × Menge, Zahlungsmittel
  Marke/Endziffern, Status + offener Betrag der letzten Rechnung), `POST /api/billing/checkout/`,
  `POST /api/billing/portal/` -- alle drei Admin-only. `POST /api/billing/webhook/` ist ein reiner
  Django-View (kein DRF, roher Body für die HMAC-Signaturprüfung nötig), `AllowAny` mit
  Absicherung ausschliesslich über `STRIPE_WEBHOOK_SECRET`.
- **Setup**: `python manage.py setup_stripe_billing` legt Product+Price einmalig an (muss auf
  einer Maschine mit Zugriff auf `api.stripe.com` laufen) und gibt die Price-ID für `.env` aus.
  Stripe-Keys/Preis-ID/Webhook-Secret kommen über `.env` (`python-dotenv`, siehe
  `config/settings.py`) -- `.env` ist `.gitignore`d, nie committen.
- **Lokale Entwicklung & Webhook**: `stripe_customer_id` wird sofort beim Checkout-Start im
  Backend selbst angelegt, `stripe_subscription_id` dagegen erst über das
  `checkout.session.completed`-Webhook-Event -- `http://localhost:8000` ist von Stripe aus nicht
  erreichbar, ohne Weiterleitung bleibt die Subscription-Verknüpfung also leer (Status zeigt z. B.
  weiterhin nur "Aktiv" ohne die Live-Details Preis/nächste Abrechnung/Zahlungsmittel). Für lokale
  Tests: `stripe listen --forward-to localhost:8000/api/billing/webhook/` (Stripe CLI) liefert ein
  lokal gültiges Webhook-Secret für `.env`. `python manage.py sync_stripe_subscriptions` trägt bei
  bereits betroffenen Tenants (Customer vorhanden, Subscription fehlt) die Verknüpfung nachträglich
  über die Stripe-API nach.
- **Frontend**: neues Settings-Modul "Abrechnung" (`BillingSettings.jsx`, Admin-only) mit
  Status/Trial-Countdown und "Abo abschliessen"/"Abrechnung verwalten"-Buttons (Redirect auf die
  Stripe-gehostete Seite). Dezenter Banner in der App-Kopfzeile für Admin, sobald kein aktiver
  Zugriff mehr besteht.
- **Tests**: `core/tests_billing.py` -- Modell-Methoden, `core/billing.py` mit gemockten
  `stripe.*`-Aufrufen (keine echten Netzwerk-Calls), alle vier Webhook-Event-Typen, 402-Gate
  End-to-End über einen echten `TenantScopedViewSet`-Endpoint, Trial-Limit, Billing-Views inkl.
  Webhook-Signaturprüfung (echtes `stripe.Webhook.construct_event`, kein Mock).
- **Nicht Teil dieser Runde**: der konkrete CHF-Betrag pro Mitarbeitendem/Monat ist ein
  Platzhalter in `setup_stripe_billing` (`DEFAULT_AMOUNT_RAPPEN`) und muss vor dem Produktivumstieg
  festgelegt werden; ein echter End-to-End-Smoke-Test gegen Stripe Test-Mode (Checkout
  durchklicken, `stripe trigger ...`) wurde nicht gemacht, da die Entwicklungsumgebung keinen
  Netzwerkzugriff auf `api.stripe.com`/`checkout.stripe.com` hatte -- vor dem produktiven Umstieg
  nachholen.

### 7. Zeitmanagement

✅ **Umgesetzt (2026-08)** -- vollständig implementiert wie unten spezifiziert, siehe Block 2 Punkt
7 ("Arbeitszeitmodell") für die technische Umsetzung inkl. API/Frontend/Tests. Scope-Abgrenzungen
(bewusst nicht Teil dieser Runde, für später): Personalkategorie-Modell als eigenständiges Konzept
(Employee-Overrides decken den MVP-Bedarf ab), offizielle Überzeit mit Schwellenwert +
Genehmigungsworkflow, Prognose-Extrapolation im Frontend. Die ursprüngliche Spezifikation bleibt
unten als Referenz stehen.

Die intuitivste Lösung: zwei Zahlen statt einer

Laufender Saldo — das, was der Mitarbeitende täglich sieht:
Saldo(t) = Ist_kumuliert(t) − Soll_kumuliert(t)
Wichtig: Soll_kumuliert(t) ist nicht das Jahresziel, sondern das anteilige Soll bis zum heutigen Datum (Wochen × Wochensoll, abzüglich bereits vergangener Ferien/Feiertage). So zeigt der Saldo sofort "ich bin gerade 20h im Minus" — unabhängig davon, wie weit das Jahr noch geht. Das ist die Zahl aus dem Chart oben: sie schwankt übers Jahr (z.B. Einbruch im Sommer wegen Ferien) und nähert sich gegen Jahresende der Null bzw. geht ins Plus, sobald Überzeit anfällt.
Jahresrestsoll — eher eine Planungsgrösse für die Dienstplanung: Restsoll = Jahressoll − Ist_kumuliert(t), sagt "wie viele Stunden muss ich bis Silvester noch leisten". Mathematisch äquivalent zum Polypoint-Ansatz, aber als zweite, klar benannte Zahl neben dem laufenden Saldo — nicht als einzige Anzeige.

Soll individuell pro Rolle berechnen

Jahressoll = Wochensoll_Vertrag × Pensum% × Wochen/Jahr − Ferienanspruch(Std) − Feiertage_auf_Arbeitstage(Std)

Arzt 100 %, 50h/Woche und Admin 100 %, 42h/Woche unterscheiden sich also nur im konfigurierten Wochensoll — das sollte pro Personalkategorie/Vertrag hinterlegt sein, nicht hartkodiert, weil auch Ferienanspruch (oft altersabhängig) und ggf. Zuschlagsregeln je Kategorie variieren.

Abwesenheiten müssen Soll-neutral sein

Ferien, Feiertage, Krankheit etc. dürfen nicht als "nicht geleistet" in den Saldo einfliessen, sonst wird jemand für Krankheit "bestraft". Am saubersten: Diese Tage reduzieren direkt das Soll_kumuliert(t), statt im Ist gutgeschrieben zu werden.

Drei Ebenen sauber trennen — hier entsteht in der Praxis die meiste Verwirrung:

Ebene	Was gemessen wird
- Tagesdifferenz:	geplante vs. effektiv geleistete Stunden EINES Dienstes
- Laufender Saldo:	kumulierte Ist/Soll-Differenz über die Zeit
- Überzeit (offiziell):	Saldo-Anteil über einem Schwellenwert, explizit genehmigt/zuschlagsberechtigt

Gerade bei Ärzten ist das relevant, weil dort oft eigene Regeln gelten (Nacht-/Wochenendzuschläge, Ruhezeiten, kantonale GAV wie VSAO). Diese Regeln gehören als konfigurierbares Regelwerk pro Personalkategorie ins System, nicht fix im Code.

Für die Oberfläche:

Saldo prominent mit Farbcodierung (blau/positiv = vor Plan, rot/negativ = hinter Plan) statt nackter grosser Zahl
Fortschrittsbalken zum Jahressoll als visuelle Ergänzung
Prognose statt nur Ist-Zustand: "bei aktuellem Pensum Jahresziel voraussichtlich am 3. Dezember erreicht"
Nie am 1. Januar ein rohes "-2267h" ohne Kontext zeigen — rechnerisch korrekt, aber für neue Mitarbeitende eher beängstigend als informativ

### 8. Öffentliche/Partner-API für externe Integrationen

Vorsorglich (2026-08, Nutzer-Vorgabe: "ich will das meine applikation so modern wie möglich
anbindbar ist"), nicht durch einen konkreten Integrationspartner ausgelöst. Ausgangspunkt war ein
Audit des aktuellen `/api/`-Surface (siehe unten je Punkt): die App wurde bisher konsequent für
**einen** selbstkontrollierten Client (das eigene Frontend, dessen Deploy-Zyklus man selbst
kontrolliert -- eine Breaking Change und der angepasste Client landen im selben Release) gebaut --
das war für diesen Zweck richtig bemessen (kein Overengineering), reicht aber nicht, sobald
externe Middlewares ohne koordinierten Deploy dagegen entwickeln sollen. Wichtig: nichts davon ist
"schlecht gebaut" -- DRFs Standardverhalten ohne Zusatzarbeit sieht exakt so aus (uneinheitliches
Fehlerformat, Page-Number- statt Cursor-Pagination, unbegrenzt gültige Tokens) wie ein Grossteil
aller DRF-Apps beim Start.

**Scope: voller Lese- UND Schreibzugriff (Nutzer-Vorgabe 2026-08).** Externe Middlewares sollen
über die API nicht nur Daten ziehen, sondern auch aktualisieren/erstellen/löschen können --
also dieselben Operationen, die heute das eigene Frontend über die bestehenden ViewSets ausführt,
nur eben von einem Client, dessen Deploy-Zyklus man nicht kontrolliert. Die bestehende
Regel-Engine (`ShiftAssignment.clean()`, Ruhezeit/Höchstarbeitszeit/Jugendschutz/
Mutterschutz-Prüfungen etc.) greift dabei bereits unabhängig vom Aufrufer -- ein Schreibzugriff
über eine externe Middleware unterliegt denselben Validierungen wie einer über das eigene
Frontend, das muss nicht neu gebaut werden. Was fehlt, ist die sichere **Einfassung** dieses
Zugriffs für einen Client, den man nicht kontrolliert:

**Zuerst (Eintrittskarte für jede sichere Fremdintegration mit Schreibzugriff):**

1. **Eigene, granular scoped API-Credentials statt User-Token**: aktuell genau ein nicht
   ablaufender, nicht scoped DRF-Token pro `User` (`rest_framework.authtoken`,
   `POST /api/auth/token/`) -- ein Token ist immer der volle Lese-/Schreibzugriff eines
   menschlichen Logins. Für externe Middleware braucht es ein Service-Account-/API-Key-Konzept,
   unabhängig von einem Login, mit granularen Scopes pro Ressource/Aktion (z. B. "Dienstplan
   lesen + schreiben", "Lohn-Export nur lesen"), individuell widerrufbar/rotierbar -- gerade WEIL
   Schreibzugriff möglich ist, ist das feingranulare Scope-Modell hier keine Nebensache, sondern
   der Kern der Absicherung.
2. **Rate-Limiting**: `REST_FRAMEWORK.DEFAULT_THROTTLE_CLASSES` ist aktuell leer -- keinerlei
   Schutz vor einer fehlerhaften oder zu aggressiven externen Integration, bei Schreibzugriffen
   potenziell mit echten Datenfolgen statt nur Serverlast.
3. **Idempotency-Key** auf den schreibenden Endpunkten, die eine Middleware typischerweise
   aufruft (`ShiftAssignment`, `TimeRecord`, `Absence`) -- ein Netzwerk-Retry von aussen kann sonst
   Duplikate erzeugen (z. B. doppelt angelegte Zuweisungen).
4. **OpenAPI-Schema** (z. B. `drf-spectacular`): aktuell keine maschinenlesbare API-Doku, kein
   Schema-Endpoint -- ein externer Entwickler müsste den Quellcode lesen statt gegen einen
   Vertrag zu bauen. Bei Schreibzugriff umso wichtiger, damit Partner die Validierungsregeln
   (Pflichtfelder, erlaubte Werte) nicht durch Trial-and-Error herausfinden müssen.

**Danach (Robustheit im laufenden Betrieb mit externen Clients):**

5. **Einheitliches Fehlerformat**: DRF liefert je nach Fehlerart `{"detail": ...}`,
   `{"feld": [...]}` oder `{"non_field_errors": [...]}` -- das eigene Frontend patcht das bereits
   clientseitig zusammen (`dienstplan_frontend/src/api.js: parseErrorResponse`), ein externer
   Integrator müsste dasselbe nachbauen. Bei Schreibzugriff besonders relevant, da
   Validierungsfehler der Regel-Engine (z. B. Ruhezeit-Verletzung) strukturiert zurückkommen
   müssen, damit eine Middleware den Grund einer abgelehnten Schreibung programmatisch auswerten
   kann. Ein `EXCEPTION_HANDLER` mit festem Envelope behebt das zentral.
6. **Konflikt-sicheres Schreiben** (`ETag`/`If-Match` oder ein Versionsfeld): wenn sowohl das
   eigene Frontend als auch eine externe Middleware denselben Datensatz ändern können, drohen
   verlorene Änderungen ("lost update") ohne eine Form von optimistischem Locking beim Schreiben.
7. **Cursor- statt Page-Number-Pagination** für Listen-Endpunkte, die eine Middleware vollständig
   durchpaginiert (aktuell `PageNumberPagination`, `PAGE_SIZE=50`, `requestAllPages()` im
   Frontend folgt den `next`-Links) -- bei parallelen Schreibvorgängen während des Durchblätterns
   können Einträge übersprungen oder doppelt geliefert werden.
8. **API-Versionierung** (`/api/v1/...`): aktuell keine, jede künftige Breaking Change trifft
   sofort alle Clients gleichzeitig. Am einfachsten VOR dem ersten produktiven externen Client
   einführen, danach wird das Nachziehen aufwendiger.

**Später, sobald erste Partner produktiv sind:**

9. **Webhooks** (z. B. "Absenz genehmigt", "Plan veröffentlicht") statt Polling -- aktuell gibt
   es ausser synchronen, best-effort E-Mails (`core/notifications.py`) keinen Event-/
   Push-Mechanismus für Dritte.
10. **Sandbox-Tenant** zum Testen von Schreibzugriffen ohne Produktivdaten zu gefährden.
11. **API-Zugriffs-Audit-Log** (wer/welches Credential hat wann welchen Endpoint mit welchem
    Ergebnis aufgerufen) -- heute gibt es nur modellbezogene Änderungshistorie
    (`django-simple-history`), kein Log der rohen API-Aufrufe selbst. Bei Schreibzugriff Dritter
    für die Nachvollziehbarkeit ("wer hat diese Schicht wirklich verändert") relevanter als bei
    reinem Lesezugriff.
12. **Sparse-Fieldsets** (`?fields=...`) für Partner, die nur wenige Felder pro Objekt brauchen.

Bewusst zurückgestellt, bis relevant: ETag/Caching-Header für reine Lese-Performance (Punkt 6 oben
deckt den sicherheitsrelevanten Teil ab), Mehrfach-Mandantschaft pro User (aktuell exakt ein
`Membership` pro `User` vorausgesetzt, siehe `core/tenancy.py: resolve_membership_for_user`),
Bulk-Endpunkte (aktuell ein Datensatz pro Schreibrequest wie im übrigen ViewSet-Design -- erst
nachziehen, falls ein Partner tatsächlich grosse Batches schreiben muss). Teilweise Überschneidung
mit Block 4 (Produktionsreife) Punkt 2 ("Auth härten") -- dort ist der Fokus jedoch der eigene
Frontend-Client (Token-Ablauf), hier der fremde Client (eigene, granular scoped Credentials);
beide Punkte sind bei der Umsetzung gemeinsam zu betrachten, aber unterschiedlich motiviert.

### 9. Polishing (Styling-Konsistenz & Feinschliff) ✅

Nutzer-Feedback (2026-08): "Ich denke es ist noch zu sehr ein Mix von Schriftarten und genereller
Optik. Das sollte einheitlich aussehen und wie aus einem Guss." -- berechtigt: die App ist über
~280 einzelne Feature-Runden gewachsen, jede Runde hat ihr eigenes kleines Stück Design
mitgebracht, ohne dass je ein bewusster Konsistenz-Durchgang über das Ganze gemacht wurde. Ein
kurzer CSS-Audit (2026-08) hat das konkret bestätigt, alle sechs Funde sind behoben. Ausdrücklich
als reiner Styling-Refactor umgesetzt (Nutzer-Vorgabe: "ohne die Funktionalität zu berühren") --
jeder Schritt wurde per Playwright gegen den laufenden Dev-Server verifiziert (Font-Familie/Farben
per `getComputedStyle`, Screenshots für Layout), bevor committet wurde.

1. ✅ **Drei Schriftfamilien statt zwei**: Landing-Page-Überschriften/Kennzahlen nutzten
   `ui-serif, Georgia, "Times New Roman", serif` (`landing-hero h1`, `landing-stat-value`,
   `landing-showcase-copy h2`, `landing-checklist h2`, `landing-cta-band-inner h2`), der Rest der
   App durchgängig `--font-display: "Space Grotesk"`. Nutzer-Entscheidung (Frage: Serif bewusst
   behalten oder vereinheitlichen): **vereinheitlichen** -- alle 5 Stellen nutzen jetzt
   `var(--font-display)`, kein `ui-serif` mehr im gesamten Stylesheet.
2. ✅ **Farb-Tokens statt hartkodierter Hex-Werte**: 5 wiederkehrende Status-Farbpaare (positiv/in
   Bearbeitung/neutral, je Text- und Hintergrundfarbe) waren an 18 Stellen als roher Hex-Wert
   wiederholt, v. a. die `.status-badge--*`-Modifier. Jetzt als `--status-positive[-soft]`,
   `--status-progress[-soft]`, `--status-neutral-soft` in `:root` definiert und an allen 18
   Stellen per `var(...)` referenziert -- byte-identische Farben (per Playwright verifiziert:
   exakt dieselben RGB-Werte wie vorher).
3. ✅ **Ein Button-System statt vier parallelen**: `.plan-export-bar button` war die einzige an
   einen Container statt an eine eigene Klasse gebundene Variante -- jetzt `.btn-pill` als
   dritte, klar benannte Variante neben `.btn-primary` (Hauptaktion) und `.btn-ghost`
   (Nebenaktion), dokumentiert und wiederverwendbar. `.settings-module-card` bleibt bewusst
   eigenständig (Klick-Kachel, keine Aktions-Zeile) statt in ein unpassendes Aktions-Button-Muster
   gepresst zu werden -- entsprechend kommentiert, damit das keine vierte stille Variante bleibt.
4. ✅ **Stat-Kacheln/Detail-Zeilen vereinheitlicht**: Nutzer-Entscheidung (Frage: nur
   dokumentieren oder echtes visuelles Redesign) -- **echtes Redesign**. `.billing-stat*` wurde
   zur generischen `.stat-tile`-Familie (`.stat-tile-grid`/`.stat-tile`/`.stat-tile-label`/
   `.stat-tile-value`), die jetzt auch `MonthlySummaryPanel.jsx` nutzt (vorher eine `<table>` für
   reine Label/Wert-Paare ohne echte Tabellen-Semantik, jetzt derselbe Kachel-Look wie
   Abrechnung). Die echte mehrspaltige Tabelle (Spezialitäten-Aufschlüsselung) bleibt bewusst eine
   Tabelle -- eine Kachel pro Zeile wäre bei mehreren Spalten pro Datensatz schlechter lesbar.
   `BalanceBadge`/`FairnessBadge` nutzen jetzt ebenfalls `--font-display` für den Zahlenwert,
   damit kompakte Pills und grosse Kacheln dieselbe Typografie-Sprache sprechen -- die
   Saldo-Farbsemantik (`--primary`/`--warn`, bewusst getrennt von den Status-Tokens aus Punkt 2)
   bleibt unverändert.
5. ✅ **Inline-Styles reduziert**: `style={{ margin: 0 }}` auf der Abrechnung-Überschrift
   (`BillingSettings.jsx`) war der einzige statische Inline-Style im ganzen Frontend -- jetzt eine
   `.billing-header h2`-Regel. Alle anderen `style={{...}}`-Vorkommen (Chip-Farbe aus Nutzerdaten,
   Fortschrittsbalken-Breite, gestaffelte Animation-Delays, dynamische Einrücktiefe) sind echte
   Laufzeitwerte und bleiben bewusst inline.
6. ✅ **Automatisierter Schutz gegen erneutes Auseinanderdriften**: `stylelint` (neue
   `devDependency`, `dienstplan_frontend/.stylelintrc.json`) mit der Regel `color-no-hex`
   verbietet jetzt rohe Hex-Farbwerte ausserhalb von `:root` (`npm run lint:css`). Token-
   Definitionen selbst sind per `stylelint-disable`/`-enable`-Kommentar um den `:root`-Block
   ausgenommen. 5 vereinzelte, vor diesem Block bereits bestehende Hex-Werte (z. B.
   Feiertags-/Wochenend-Hintergründe im Planblatt) wurden nicht rückwirkend tokenisiert (kein Teil
   des ursprünglichen Funds), sondern per `stylelint-disable-next-line`-Kommentar dokumentiert,
   damit die Baseline sauber grün ist, ohne stillschweigend Bestandsschulden zu verstecken.

Bewusst nicht Teil dieses Blocks (siehe eigene Blöcke): fehlende automatisierte Frontend-Tests
(Block 4 Punkt 5), fehlende i18n für französisch-/italienischsprachige Kantone (aktuell nirgends
festgehalten, hier nur als Randnotiz: falls relevant, eigener Block wert), fehlende
Rate-Limits auf Login/Signup (Überschneidung mit Block 4 Punkt 2 und Block 8 Punkt 2).

### 10. Code-Polishing (DRY & Wartbarkeit)

Nutzer-Feedback (2026-08): "Durch das Wachsen gibt es diverse unschöne Stellen. Vor allem DRY ist
mir wichtig und dass der Code so einfach wartbar wie möglich ist." -- analog zu Block 9
(Styling-Konsistenz) berechtigt: über ~280 Feature-Runden ist Duplikation entstanden, wo
wiederkehrende Probleme (Query-Param-Parsing, Fetch-Boilerplate, Statustransitions,
Listen-Tabellen mit Suche/Sortierung/Pagination) jedes Mal einzeln statt über eine gemeinsame
Abstraktion gelöst wurden. Zwei unabhängige Vollaudits (2026-08, je ein Recherche-Agent für
Backend `core/`+`scheduling/` und Frontend `dienstplan_frontend/src/`) haben die Funde unten
mit konkreten Datei:Zeilen-Belegen bestätigt. Reine Planung/Dokumentation, keine Implementierung
-- Prioritäten sind absteigend nach Hebelwirkung sortiert, die Nummerierung ist kein
Abarbeitungszwang.

**Backend (`core/`, `scheduling/`)**

1. ✅ **`initial()` dreifach fast identisch reimplementiert**: `TenantScopedViewSet.initial`
   (scheduling), `TenantScopedAPIMixin.initial` (core), `_TenantScopedNoBillingGateMixin.initial`
   (core.billing_views) bauten alle drei manuell dieselbe DRF-`APIView.initial()`-Sequenz nach
   (Content-Negotiation, Authentifizierung, Membership-/Tenant-Auflösung,
   `check_permissions`/`check_throttles`). Behoben durch `core.tenancy.
   apply_tenant_scoped_initial(view, request, *args, resolve_employee_profile=False,
   enforce_billing=True, **kwargs)` -- alle drei Klassen rufen ihn nur noch auf.
   `resolve_employee_profile=True` (nur `TenantScopedViewSet`) setzt zusätzlich
   `request.employee_profile` (lazy-importiert `scheduling.models.Employee`, damit `core`
   weiterhin nichts von `scheduling` auf Modulebene importiert). `enforce_billing=False` (nur
   `_TenantScopedNoBillingGateMixin`) lässt `enforce_billing_access()` aus. Sicherheitskritischer
   Code (läuft bei praktisch jedem API-Request) -- vor dem Refactor wurden gezielt zwei
   Test-Lücken geschlossen: ein Cross-Tenant-Isolationstest für die `TenantScopedAPIMixin`-
   Endpunkte (die bisher nur die `TenantScopedViewSet`-Endpunkte hatten) und ein Test, der
   beweist, dass `set_current_tenant()` während eines echten API-Requests wirkt (empirisch
   verifiziert: schlägt fehl, wenn der Aufruf entfernt wird). In vier einzeln committeten
   Schritten umgesetzt (Tests zuerst, dann Helper isoliert hinzugefügt, dann die drei Call-Sites
   nacheinander migriert, kleinster Blast-Radius zuerst), nach jedem Schritt gezielt und am Ende
   mit der vollen Suite verifiziert.

   Nachbesserung (Nutzer-Feedback: "fix das bitte das alles doppelt gesichert ist"): `initial()`
   setzte die Tenant-ContextVar (zweite Verteidigungslinie für `TenantScopedManager`) ursprünglich
   nur bei `bind_scheduling_context=True`, also nur für `TenantScopedViewSet`-Endpunkte --
   `TenantScopedAPIMixin`/`_TenantScopedNoBillingGateMixin`-Endpunkte (`TenantView`,
   `MembershipViewSet`, `PayrollExportView`, `BillingStatusView` u. a.) hatten dadurch nur die
   erste, explizite `request.tenant`-Filterung, keine zweite Absicherung. Vor der Änderung
   verifiziert, dass keine dieser Views absichtlich auf ungefiltertes Cross-Tenant-Lesen über den
   Standard-Manager angewiesen ist (`User.objects`/`Tenant.objects`/`Membership.objects` sind
   ohnehin nicht ContextVar-gefiltert, da diese Modelle nicht von `TenantScopedModel` erben;
   `MembershipSerializer.scoped_nodes` bekommt durch die ContextVar sogar eine zusätzliche,
   frühere Absicherung, ohne die bestehende `validate_scoped_nodes()`-Prüfung überflüssig zu
   machen). `set_current_tenant()` wird jetzt in `apply_tenant_scoped_initial()` unbedingt für
   alle drei Basisklassen aufgerufen, das `resolve_employee_profile`-Flag steuert nur noch die
   `employee_profile`-Auflösung. Neuer Regressionstest (analog zum bestehenden, über
   `PayrollExportView` statt `TenantScopedViewSet`) beweist die Wirkung während eines echten
   Requests, ebenfalls empirisch verifiziert (schlägt fehl, wenn der Aufruf entfernt wird).
2. ✅ **Stripe-Statusmapping-Dict verdoppelt**: identisches 7-Einträge-Dict in
   `core/billing.py:282-290` (`handle_webhook_event`) und
   `core/management/commands/sync_stripe_subscriptions.py:60-68`. Behoben durch
   `core.billing.stripe_status_to_subscription_status()`, von beiden Stellen genutzt.
3. ✅ **`DjangoValidationError`→DRF-`ValidationError`-Übersetzung sechsfach kopiert**: exakt
   dieselbe Zeile in `scheduling/views.py:1088,1183,1363,1374,1385,1485`. Behoben durch den
   Context-Manager `scheduling.views.translate_model_validation_error()`, alle sechs Stellen
   nutzen ihn jetzt statt eigenem try/except.
4. ✅ **Query-Param-Parsing (Datum/Jahr/Monat) 8+ fach dupliziert**: `try: date.fromisoformat(...)`/
   `int(...)` + `except ValueError: raise ValidationError(...)` wiederholte sich über
   `EmployeeViewSet.weekly_overtime/night_work/fairness/balance/sick_pay/monthly_summary`
   sowie identisch zwischen `PayrollExportView.get` und `PlanExportView.get`. Behoben durch
   `scheduling.views.parse_date_param()`/`parse_int_param()`/`parse_year_month_param()`, alle
   Stellen nutzen sie jetzt (Ausnahme: `settle_overtime` liest aus dem POST-Body statt
   Query-Params und bleibt bewusst unangetastet -- anderes Muster, keine Duplikation).
5. ✅ **`has_permission`-Rumpf dreifach identisch**: `OwnEmployeeRecordPermission`,
   `ShiftTradeRequestPermission`, `TimeRecordPermission` in `core/permissions.py:78-84,116-122,
   154-160` haben denselben Methodenkörper. Behoben durch
   `_ManagerOrEmployeeCanWriteMixin`, alle drei subclassen jetzt nur noch
   `has_object_permission`.
6. ✅ **Statusvergleich gegen rohe String-Literale statt Model-Enum**:
   `OwnEmployeeRecordPermission.has_object_permission`/`TimeRecordPermission.has_object_permission`
   (`core/permissions.py:97,173`) vergleichen `obj.status` gegen `"pending"`/`"submitted"` samt
   erklärendem Kommentar. Behoben: `obj.status == obj.Status.PENDING`/
   `obj.Status.SUBMITTED` (kein Zusatzimport nötig, `obj` ist bereits die Model-Instanz).
7. ✅ **`username`-Eindeutigkeitsvalidator dreifach kopiert**: identische Prüfung + Fehlermeldung
   "Dieser Benutzername ist bereits vergeben." in `core/serializers.py:140-143` (
   `MembershipCreateSerializer`), `:183-186` (`SignupSerializer`),
   `scheduling/serializers.py:306-310` (`EmployeeAccessSetupSerializer`). Behoben durch
   `core.serializers.validate_unique_username()`, von allen drei Stellen genutzt.
8. ✅ **"Instanz bauen + setattr-Schleife + `instance.clean()`"-Muster fünffach**:
   `ShiftAssignmentSerializer.validate`, `AbsenceSerializer.validate`,
   `ShiftPreferenceSerializer.validate`, `TimeRecordSerializer.validate`,
   `ShiftTradeRequestSerializer.validate` wiederholten dasselbe Gerüst mit unterschiedlicher
   Feldliste. Behoben durch `build_instance_for_clean(serializer, attrs, model_cls, fields,
   set_tenant=False)`, alle fünf `validate()`-Methoden nutzen ihn jetzt -- ruft bewusst NICHT
   selbst `instance.clean()` auf, damit Aufrufer mit zusätzlicher Logik vor/nach dem Clean
   (Status-Vorbelegung, Unique-Check) das weiterhin selbst steuern.
9. ✅ **Nested-Child-Sync-create()/update()-Paar dreifach dupliziert**: `EmployeeSerializer`
   (employments), `TimeTemplateSerializer` (segments), `TimeRecordSerializer` (segments)
   implementierten praktisch identische create()/update()-Logik (m2m-Felder poppen, Nested-Liste
   poppen, Objekt anlegen/aktualisieren, `_sync_*`-Helper aufrufen). Behoben durch
   `NestedWritableSerializerMixin` (create()/update() generisch über `self.Meta.model` +
   `_nested_field`), alle drei Serializer nutzen ihn jetzt und definieren nur noch
   `_nested_field` + `_sync_nested(instance, nested_data)` mit ihrer model-eigenen Sync-Logik.
10. ✅ **`save_formset` byte-identisch dupliziert**: `TimeTemplateAdmin.save_formset` und
    `TimeRecordAdmin.save_formset` (`scheduling/admin.py`) waren wortgleich (Tenant auf
    Inline-Instanzen stempeln). Behoben durch Verschieben nach `TenantScopedAdminMixin`
    (`core/admin.py`), beide Kopien entfernt.
11. ✅ **Statustransitions inkonsistent implementiert**: `ShiftTradeRequest.accept()/approve()/
    reject()` waren Model-Methoden, aber `ShiftTradeRequestViewSet.decline`/`cancel` und
    `AbsenceViewSet.approve`/`reject` bauten denselben Statuswechsel direkt im View statt als
    Model-Methode. Behoben durch `ShiftTradeRequest.decline()`/`cancel()` sowie
    `Absence.approve()`/`reject()` als neue Model-Methoden (Status-Check + `ValidationError` bei
    ungültigem Übergang + Statusmutation + `save(update_fields=[...])`, gleiches Muster wie
    `accept()`/`approve()`/`reject()`); die Views rufen die Methode nur noch auf (gewrappt in
    `translate_model_validation_error()`, Punkt 3) und behalten die Notification-Aufrufe.
12. ✅ **`EmployeeViewSet` als "God Class" (~520 Zeilen, 14+ Actions)**: mischte CRUD,
    CSV-Import/-Export, Zugangsverwaltung (`setup_access`/`deactivate`/`reactivate`) und
    7 reine Reporting-Actions in einer Klasse. Behoben durch Aufteilung in
    `EmployeeReportingMixin` (Saldo/Fairness/Nacht-/Sonntagsarbeit/Lohnfortzahlung/
    Monatsauswertung/Gleitzeit-Abrechnung) und `EmployeeAccessManagementMixin`
    (`setup_access`/`deactivate`/`reactivate`) -- `EmployeeViewSet` selbst enthält nur noch
    CRUD + CSV-Import und erbt von beiden Mixins. DRF sammelt `@action`-Methoden über die
    komplette MRO ein, URLs/Verhalten bleiben identisch.
13. ✅ **`PayrollExportView.get()` mischte vier Verantwortlichkeiten in ~110 Zeilen**:
    Monats-Parsing (bereits durch Punkt 4 gelöst), Mapping-Lookups, Pro-Mitarbeiter-Zeilenbau,
    CSV/JSON-Verzweigung inline. Behoben durch `_build_category_lookup`/`_build_employee_lines`-
    Helper, `get()` ist jetzt reiner Orchestrator.
14. ✅ **"Spezialitäten sind ausgenommen"-Guard fünffach wiederholt**: `_check_rest_period`,
    `_check_maximum_weekly_hours`, `_check_break_minutes`, `_check_daily_span`,
    `_check_weekly_rest_day` in `scheduling/models.py` begannen alle mit derselben 2-Zeilen-
    Bedingung (Audit fand 3, tatsächlich waren es 5). Behoben durch den
    `@skip_for_specialties`-Decorator, alle fünf Methoden nutzen ihn jetzt.
15. ✅ **Node-Scope-Filter-Wiring siebenfach copy-paste**: `NodeViewSet`, `TimeTemplateViewSet`,
    `ShiftAssignmentViewSet`, `AbsenceViewSet`, `ShiftTradeRequestViewSet`, `TimeRecordViewSet`,
    `MissingTimeRecordViewSet` riefen `_employee_scoped_node_ids(...)` und branchten jeweils
    manuell auf `if node_ids is not None: qs = qs.filter(...)`. Behoben durch
    `apply_node_scope(qs, node_ids, field_lookup)` für die 5 Stellen mit reinem Feld-Filter
    (NodeViewSet, TimeTemplateViewSet, ShiftAssignmentViewSet, TimeRecordViewSet,
    MissingTimeRecordViewSet) -- AbsenceViewSet/ShiftTradeRequestViewSet bleiben bewusst
    unangetastet, deren Node-Scoping ist mit einer Q()-Sonderregel für eigene Absenzen/
    Tauschangebote der Mitarbeiter-Rolle verknüpft, kein reiner Feld-Filter.
16. ✅ **CSV-Export-Boilerplate dreifach dupliziert**: `EmployeeViewSet.import_csv_template`
    (`scheduling/views.py:875-877`), `PayrollExportView._csv_response` (`:1752-1754`) und
    `PlanExportView._csv_response` (`:1914-1916`) bauen je separat `HttpResponse(content_type=
    "text/csv")` + `Content-Disposition`-Header + `csv.writer(response)`. Behoben durch
    `scheduling.views.csv_response(filename) -> (response, writer)`, alle drei Stellen nutzen ihn.
17. **`Employee`-Modell als "God Class"**: `scheduling/models.py:243-1515` (~1270 Zeilen,
    29 Methoden) vereint Alters-/Jugendschutz, Mutterschutz, Wochenstunden, Nachtarbeit,
    Fairness-Punkte, Zeitkonto/-saldo, Ferien, Lohnfortzahlung, Monatszusammenfassung und
    Lohn-Rohdaten-/Gleitzeit-Abrechnung in einer einzigen Klasse. Aufteilung in
    fachlich getrennte Mixins oder Service-Module (z. B. `JugendschutzMixin`,
    `ZeitkontoMixin`, `PayrollMixin`) würde die Datei deutlich wartbarer machen, ohne die
    öffentliche API des Modells zu ändern.
18. **`monthly_summary()` extrem lang**: `scheduling/models.py:1102-1307` (~200 Zeilen) mischt
    Soll/Ist-Berechnung, Zuschlags-Aufschlüsselung pro Zeitvorlage, Überstunden-/Korridor-Logik
    und Nacht-/Sonntagszuschläge in einer Methode. Extraktion benannter Teilschritte (z. B.
    `_calc_soll_ist`, `_calc_surcharges`, `_calc_overtime_corridor`) würde die Methode lesbar
    strukturieren, ohne das Ergebnis zu verändern.
19. **Datei-/Klassengrössen insgesamt unverhältnismässig**: `scheduling/models.py` (3017 Zeilen,
    16 Modelle) und `scheduling/views.py` (2009 Zeilen, 12 ViewSets + 3 APIViews) sind deutlich
    grösser als vergleichbare Module wie `core/models.py` (505 Zeilen). Eine Aufteilung nach
    fachlichem Bereich (z. B. `models/node_employee.py`, `models/scheduling.py`,
    `models/time_tracking.py`; `scheduling/export_views.py` für die Export-Views) würde die
    Navigierbarkeit deutlich verbessern -- reine Struktur-Massnahme, kein Verhaltensrisiko, aber
    grösster Einzelaufwand in dieser Liste.

**Frontend (`dienstplan_frontend/src/`)**

1. **Server-Tabellen-Logik (Suche/Debounce/Sortierung/Pagination/Bulk-Auswahl) fünffach separat**:
   `AbsenceOverview.jsx`, `TradeRequestOverview.jsx`, `TimeRecordOverview.jsx` (zweimal intern,
   "confirm" und "missing"), `EmployeeSettings.jsx`, `TimeTemplateSettings.jsx` implementieren
   je ihr eigenes Debounce-`useEffect`, Ordering-Toggle, Page-State, `emptyPage`-Konstante und
   Pager-Markup. Gemeinsamer Hook `useServerTable({ fetcher, deps })`, der Debounce, Ordering,
   Page-Reset, Loading/Data-State und Pager-Berechnung kapselt.
2. **`loadPage()` + `useEffect` doppelt denselben Request**: in `AbsenceOverview.jsx:80-115`,
   `TradeRequestOverview.jsx:59-80`, zweimal in `TimeRecordOverview.jsx:122-189` steht derselbe
   `api.searchX(...)`-Aufruf wortwörtlich zweimal (einmal als `loadPage()` fürs Neuladen nach
   einer Aktion, einmal inline im datenabhängigen Effekt) -- muss synchron gehalten werden. Löst
   sich mit Punkt 1: `useServerTable` liefert ein stabiles `reload()`.
3. **`fieldError(key)`-Helper wortidentisch dreifach**: `EmployeeSettings.jsx:495-498`,
   `PregnancyEditor.jsx:66-69`, `TenantSettings.jsx:209-212`, dazu dasselbe
   `catch (e) { if (e.fields...) setFieldErrors(e.fields); }`-Muster, das `SignupForm.jsx:18,
   59-113` ein viertes Mal manuell ausschreibt. `useFieldErrors()`-Hook
   (`{fieldErrors, setFromApiError, clearField, FieldError}`).
4. **`employeeName(id)`/`nodeName(id)`-Lookup fünf- bzw. dreifach separat**: identische
   `find`/`Map.get`+Fallback-Logik in `AbsencePanel.jsx:72-75`, `Dashboard.jsx:91-94`,
   `TimeRecordOverview.jsx:205-208,210-212`, `TimeRecordPanel.jsx:84-87`,
   `TradeRequestPanel.jsx:64-67`, `MembershipAccessSettings.jsx:136`,
   `TimeTemplateSettings.jsx:191`. Neue `lookups.js`-Utility (Muster existiert schon für
   `chipGlyph.js`/`timeRecordSegments.js`).
5. **Datums-/Kalenderfunktionen komplett dupliziert**: `pad`, `daysInMonth`, `isoDate`,
   `addDays`, `groupConsecutiveDates`, `WEEKDAYS_SHORT` sind in `PlanGrid.jsx:83-129` und
   `YearPlan.jsx:8-71` wortgleich vorhanden -- ein Kommentar in `PlanGrid.jsx:107-108` verweist
   sogar explizit auf "gleiches Muster wie YearPlan.jsx", ohne dass je extrahiert wurde. Neue
   `dateUtils.js` mit allen sieben Funktionen/Konstanten.
6. **`MONTH_NAMES`/`STATUS_LABELS` dupliziert UND dabei inkonsistent**: `MONTH_NAMES` identisch
   in `YearPlan.jsx:9-12`/`MonthlySummaryPanel.jsx:4-7`. Absenz-`STATUS_LABELS` identisch in
   `AbsenceOverview.jsx:16-20`/`AbsencePanel.jsx:5-9` ("Genehmigt"), aber in `YearPlan.jsx:14`
   mit denselben Keys kleingeschrieben ("genehmigt") -- eine echte UI-Inkonsistenz, nicht nur
   Codeduplikation. Trade-Request-`STATUS_LABELS` identisch in
   `TradeRequestOverview.jsx:13-20`/`TradeRequestPanel.jsx:5-12`. Zentrale `labels.js`.
7. **`PlanGrid.jsx` ist ein 1350-Zeilen-Monolith mit 15+ Verantwortlichkeiten** (Zeile 147-1504,
   34 `useState`/`useEffect`-Hooks in einer Funktion): Drag&Drop, Wunschdienst-Editor
   (`:471-494`), Ist-Zeit-Erfassung (`:495-528`), Absenz-Handling (`:566-989`), Sonderdienste
   (`:589-615`), Schicht-Verschieben (`:990-1027`), Wochenmuster-Kopieren (`:1028-1145`), Export
   (`:1146-1157`), Tauschangebot (`:1158+`) -- alles in einer Komponente. Grösster Hebel im
   Frontend: mind. `usePlanGridAssignments`-Hook für die CRUD-Handler + separate
   Unterkomponenten für Wunschdienst-Popover/Ist-Zeit-Modal/Wochenmuster-Kopieren.
8. **`YearPlan.jsx` (915 Zeilen) und `EmployeeSettings.jsx` (997 Zeilen) ebenfalls faktisch
   unaufgeteilte Monolithen**: `YearPlan.jsx:73`ff ist eine einzige Exportfunktion
   (Kalendergitter + Absenz-Gruppierung + Platzierungslogik zusammen).
   `EmployeeSettings.jsx:126`ff vereint Tabellen-Liste, Formular (Stammdaten+Anstellung+Zugang),
   CSV-Import und Deaktivieren/Reaktivieren. `EmployeeSettings` in `EmployeeTable` +
   `EmployeeForm` aufteilen (deckt sich mit Punkt 1), `YearPlan`-Zellenrendering in eigene
   Zellen-Komponente auslagern.
9. **Fetch/Loading/Error-Boilerplate ohne gemeinsamen Hook in 17 Dateien**: das
   `useState(loading)`+`useEffect`+`.then/.catch/.finally`-Muster (teils mit `cancelled`-Flag,
   teils ohne -- uneinheitlich) einzeln u. a. in `AbsenceTypeSettings.jsx:29-40`,
   `BillingSettings.jsx`, `Dashboard.jsx`, `MembershipAccessSettings.jsx`, `PayrollSettings.jsx`,
   `PregnancyEditor.jsx`, `TenantSettings.jsx` (2×), `SettingsPanel.jsx:109-124`. Einfacher
   `useApiData(fetcher, deps)`-Hook, der Loading/Error/Cancel einmal kapselt.
10. **api.js: Blob-Download-Muster dreifach kopiert**: `downloadEmployeeCsvTemplate`
    (`:282-297`), `downloadPayrollExportCsv` (`:529-544`), `downloadPlanExport` (`:549-564`)
    wiederholen wortgleich Token holen → fetchen → Blob → `<a>`-Element bauen/klicken/entfernen
    → `revokeObjectURL`. Gemeinsamer privater Helper `downloadFile(url, filename)`.
11. **api.js: `searchX({...})`-Query-Builder fünffach mit identischem Gerüst**:
    `searchEmployees` (`:198-221`), `searchTimeTemplates` (`:305-314`), `searchAbsences`
    (`:379-388`), `searchShiftTradeRequests` (`:412-421`), `searchTimeRecords`/
    `getMissingTimeRecords` (`:460-500`) bauen alle `new URLSearchParams()` +
    `if (x) params.set(...)` manuell nach. Kleiner Helper `buildQuery(params)`, der leere/
    undefined Werte automatisch überspringt -- ansonsten ist `api.js` bereits ordentlich
    generisch gehalten, hier keine grösseren Probleme gefunden.
12. **`emptyPage`-Konstante identisch dreifach definiert**: `{ count: 0, next: null,
    previous: null, results: [] }` wortgleich in `AbsenceOverview.jsx:22`,
    `TimeRecordOverview.jsx:23`, `TradeRequestOverview.jsx:22`. Löst sich mit Punkt 1 (Default-
    State wandert in den `useServerTable`-Hook).
13. **`onError`-Prop wird bis zu 3 Ebenen tief durchgereicht statt über Context**: `App.jsx`
    reicht `onError` unverändert an 9 Top-Level-Panels weiter, jedes davon weiter an
    Unterformulare (z. B. `SettingsPanel.jsx:177-203` an 10 Module) -- nur `App.jsx:74,299-306`
    rendert den globalen Fehlerbanner tatsächlich. Kein funktionaler Bug, niedrige Priorität,
    aber ein `ErrorContext`/`useError()`-Hook würde das Prop-Drilling auflösen.

Methodik-Hinweis: die Funde stammen aus zwei unabhängigen Code-Audits (je ein Recherche-Agent
für Backend/Frontend, 2026-08) gegen den damaligen Stand von `claude/deutsch-understanding-m7kmam`
-- beide Audits bestätigen ausdrücklich, dass die Codebase bereits mehrere gute gemeinsame
Abstraktionen hat (`TenantScopedManager`, `TenantScopedAdminMixin`, `pop_m2m_fields`/
`set_m2m_fields`, `_employee_scoped_node_ids`, `chipGlyph.js`/`timeRecordSegments.js`) -- die
Funde oben sind die konkreten Lücken in einer sonst konsistenten Architektur, kein Zeichen
grundsätzlicher Unordnung. Kein toter Code/keine ungenutzten Importe von Bedeutung gefunden.
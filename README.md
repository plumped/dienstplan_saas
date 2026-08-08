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
   andere Gruppen). Inzwischen gelöst, siehe Punkt 12 (Employee-Override-Felder).
2. ✅ **Pausenregelung** (Art. 15 ArG): > 5.5h Netto-Arbeitszeit → 15 Min., > 7h → 30 Min.,
   > 9h → 1h Pause, automatisch gegen `TimeTemplate.break_minutes` geprüft statt nur erfasst.
3. ✅ **Tägliche Höchstarbeitszeit inkl. Pausen** (Art. 10 ArG: Tagesspanne max.
   `maximum_daily_span_hours`, Default 14h).
4. ✅ **Wöchentlicher freier Tag**: mindestens ein ganzer freier Tag pro Kalenderwoche
   (Art. 21 ArG) wird geprüft. *Noch offen*: die zusätzliche Anforderung "im Schnitt einmal
   monatlich ein Sonntag frei" ist nicht automatisiert.
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
   App nicht prüfen, nur an sie erinnern).
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

**Noch offen**:

14. **Aufbewahrung**: Ist-Daten (`TimeRecord`) fallen unter dieselbe Aufbewahrungspflicht wie
    Lohnunterlagen (siehe Block 5.3) — beim Löschkonzept mitdenken.

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
5. **Export** (PDF/Excel) des Monatsplans — für Aushang in der Praxis und Übergabe an externe
   Lohnbuchhaltung, die selten direkt an die API angebunden ist.
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
     Löschkonzept, sobald ein Übertragsmechanismus feststeht.
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

19. **Automatisierte Planung (One-Click Planning)** (noch nicht umgesetzt). Ziel: Admin/Planer
    wählen eine Station/einen Zeitraum und lassen das System selbständig einen vollständigen,
    regelkonformen Dienstplan-Entwurf erzeugen -- unter Einhaltung sämtlicher bereits vorhandener
    Einschränkungen (Regel-Engine: Ruhezeit, Höchstarbeitszeit, Qualifikation/`required_skill`,
    Jugendschutz, Absenz-Konflikte; Mindestbesetzung pro Schichttyp, Block 9; künftig
    Split-Shift-Überlappung, Punkt 18 oben) statt jede Schicht manuell zu ziehen.

    - **State-of-the-art-Einordnung**: das ist im Kern ein klassisches "Nurse/Staff Rostering
      Problem" aus der Operations-Research-Literatur -- ein Constraint-Satisfaction- bzw.
      Optimierungsproblem, kein Heuristik-Hack. Empfehlung: ein dedizierter CP-Solver
      (z. B. Google OR-Tools CP-SAT, reine Python-Abhängigkeit, keine externe Service-Anbindung
      nötig) statt einer selbstgeschriebenen Greedy-Heuristik -- letztere findet bei mehreren
      gleichzeitig wirkenden ArG-Regeln + Mindestbesetzung + Fairness (Punkt 20 unten) schnell
      keine gültige Lösung mehr oder erzeugt unfaire, schwer nachvollziehbare Ergebnisse, während
      ein CP-Solver Machbarkeit *beweist* oder explizit meldet, welche Nebenbedingung(en) eine
      Lösung verhindern.
    - **Modellierung als Entscheidungsproblem**: eine Binärvariable `x[employee, date, template]`
      pro möglicher Zuweisung; harte Nebenbedingungen (müssen gelten, sonst keine gültige Lösung) =
      identisch zu den bestehenden `ShiftAssignment.clean()`-Prüfungen (Ruhezeit, Höchstarbeitszeit,
      `required_skill`, Jugendschutz, kein Absenz-Konflikt, Mindestbesetzung als Untergrenze der
      Summe über alle passenden `x`); weiche Ziele (sollen möglichst gut erfüllt werden, blockieren
      aber nichts) = `ShiftPreference`-Wünsche (Block 2.13, Wunschfrei/Wunschdienst) und
      Fairness-Ausgleich über die Bonus-Punkte aus Punkt 20 unten (unpopuläre Schichten bevorzugt an
      Mitarbeitende mit aktuell niedrigem Punktestand vergeben) als gewichtete Terme in der
      Zielfunktion.
    - **Vorschau statt Blindautomatik (Vertrauen vor Bequemlichkeit)**: das Ergebnis wird
      grundsätzlich als **Entwurf/Vorschlag** erzeugt, nicht direkt gespeichert -- eine
      Diff-Ansicht im Planblatt (neue Zuweisungen optisch hervorgehoben, z. B. gestrichelter Rand)
      erlaubt Durchsicht, punktuelle manuelle Korrektur und erst dann bewusstes Übernehmen. Analog
      zur bereits etablierten Begründung, warum ArG-Prüfungen informativ statt blockierend sind
      (Nachtarbeit/Sonntagsarbeit, Block 1.5/1.6): ein Algorithmus, der ohne Bestätigung einen
      ganzen Monatsplan überschreibt, wäre für die Akzeptanz in der Praxis riskanter als ein
      spürbar geringerer Automatisierungsgrad mit echtem Vertrauen der Planenden.
    - **Umgang mit Unlösbarkeit**: liefert der Solver keine vollständige Lösung (z. B. zu wenig
      Personal mit der nötigen Qualifikation für die gewählte Mindestbesetzung), soll das Ergebnis
      trotzdem der bestmögliche **Teilentwurf** sein plus eine für Menschen lesbare Liste der
      verletzten/nicht erfüllbaren Stellen ("Nachtwache am 14.6.: nur 1 von 2 Personen mit Skill
      'Reanimation' verfügbar") -- kein reines Scheitern ohne Diagnose.
    - **Umfang MVP vs. später**: ein erster Wurf beschränkt sich sinnvollerweise auf eine Station
      (nicht tenant-weit) und einen Monat (nicht beliebige Zeiträume) -- CP-SAT-Laufzeit wächst mit
      der Anzahl Variablen (Mitarbeitende × Tage × Schichttypen), ein monatlicher Stations-Lauf
      bleibt performant, ein tenant-weiter Jahres-Lauf müsste erst als eigener, potenziell
      asynchroner Hintergrund-Task (nicht im Request-Response-Zyklus) konzipiert werden.

20. **Bonus-/Fairness-Punktesystem für unpopuläre Schichten** (noch nicht umgesetzt). Ziel: sichtbar
    und nachvollziehbar machen, wer wie oft unpopuläre Schichten (Sonntag, Nacht) übernommen hat --
    sowohl als Transparenz-/Motivationsinstrument für Mitarbeitende als auch als Fairness-Eingabe
    für die automatisierte Planung (Punkt 19 oben), damit nicht dieselbe Person systematisch
    überproportional oft Sonntagsdienst leistet.

    - **Bewusst keine manuelle Punktevergabe pro Schichttyp**: statt eines neuen, separat zu
      pflegenden Felds (z. B. "Bonuspunkte" pro `TimeTemplate`) auf den bereits vorhandenen,
      automatisch berechneten Signalen aufbauen, die die Regel-Engine ohnehin schon liefert --
      `ShiftAssignment.is_sunday`/`night_hours` (Block 1.5/1.6, informativ, bereits pro Zuweisung
      berechnet). Zwei neue, konfigurierbare `Tenant`-Felder nach demselben Muster wie
      `overtime_surcharge_pct`/`sunday_work_surcharge_pct`: `sunday_shift_bonus_points` und
      `night_shift_bonus_points_per_hour` -- eine Klinik kann damit z. B. "1 Punkt pro
      Sonntagsdienst" oder "0.5 Punkte pro Nachtstunde" festlegen, ohne jedes `TimeTemplate`
      manuell zu pflegen. Neue Schichttypen sind dadurch automatisch korrekt eingebunden, sobald sie
      auf einen Sonntag fallen oder Nachtstunden enthalten -- kein Vergessen möglich.
    - **Aggregation nach demselben, bereits etablierten Muster** wie `night_work_summary()`/
      `weekly_hours_summary()`: neue Methode `Employee.fairness_summary(year)` (kalenderjahresweise,
      analog zu `annual_target_hours`) liefert die kumulierten Punkte der Person sowie -- für die
      Einordnung "bin ich fair dran" -- den Punktedurchschnitt aller Mitarbeitenden derselben
      Station/desselben Teams im selben Zeitraum.
    - **Frontend**: kleines Badge analog zu `BalanceBadge.jsx` (gleiches Pub/Sub-Muster über
      `onBalanceChanged`, da eine neue Zuweisung sowohl den Saldo als auch die Fairness-Punkte
      beeinflusst), sichtbar für Admin/Planer in der Mitarbeitendenliste (Übersicht "wer ist
      wann dran") sowie optional für Mitarbeitende selbst in der Topbar (**zur Diskussion**, wenn
      umgesetzt: Transparenz kann Fairness-Vertrauen stärken, aber ein sichtbarer
      "Punkte-Vergleich mit Kolleg:innen" könnte in manchen Teams auch unerwünschten Konkurrenzdruck
      erzeugen -- anders als der bestehende Saldo, der bewusst rein personenbezogen ist und nie mit
      anderen verglichen wird).
    - **Verzahnung mit Punkt 19**: der Solver berücksichtigt beim Verteilen einer unpopulären
      Schicht neben den harten Regeln (Ruhezeit, Qualifikation etc.) den *aktuellen* Punktestand
      aller in Frage kommenden Mitarbeitenden als weichen Zielfunktions-Term -- wer zuletzt
      überdurchschnittlich oft Sonntag/Nacht gemacht hat, wird bei der nächsten automatischen
      Zuteilung tendenziell übersprungen, ohne dass das je hart erzwungen würde (eine einzelne
      unpopuläre Schicht bei objektiv fehlenden Alternativen -- z. B. nur eine Person mit dem
      nötigen Skill verfügbar -- darf die Planung nicht blockieren).

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

### 3. Onboarding & Mandantenfähigkeit für Self-Signup

**Grundsatzentscheid (2026-08)**: kein reines Consumer-Self-Signup, sondern ein Hybrid — passend
zu einem B2B-Vertical-SaaS für Heime/Kliniken, wo Datenschutz (revDSG) und ArG-Konformität eine
höhere Vertrauenshürde als bei einem generischen Tool bedeuten. Landing Page mit zwei
gleichwertigen CTAs ("Kostenlos testen" für Self-Serve, "Demo buchen" für Ketten/grössere Häuser,
die vor dem Hochladen echter Mitarbeiterdaten mit jemandem sprechen wollen), Self-Serve-Pfad führt
über einen vorbefüllten Demo-Tenant (Aha-Moment vor der Commitment-Hürde) in einen geführten
Setup-Wizard statt direkt in den Django-Admin.

1. **Landing Page** (eigenständige Marketing-Seite ausserhalb der App, kein Login nötig):
   Nutzenversprechen konkret statt generisch (z. B. "ArG-konforme Planung ohne Excel-Chaos" statt
   "Software für Dienstpläne"). Zwei CTAs nebeneinander:
   - **"Kostenlos testen"** → Self-Serve-Signup-Flow (Punkt 2).
   - **"Demo buchen"** → Kontaktformular/Kalender-Link, sales-assistiertes Onboarding für grössere
     Institutionen (kein Code-Task, aber als bewusster zweiter Pfad einzuplanen, nicht nachträglich
     anzuflicken).
2. **Self-Serve-Signup-Flow**: E-Mail-basiert, Magic Link statt Passwort-Ping-Pong beim ersten
   Login (weniger Reibung als klassisches Passwort-Setzen). Landet nach dem Signup sofort in einem
   vorbefüllten **Demo-Tenant** (Beispiel-Stationen/-Mitarbeitende/-Dienstplan zum Anfassen), bevor
   der eigene, echte Tenant angelegt wird — senkt die Hürde, weil man das Produkt fühlt, bevor man
   sich für echte Personendaten committen muss.
3. **Geführter Setup-Wizard** für den eigenen Tenant (ersetzt die heutige Django-Admin-Pflicht):
   Tenant-Name → Kanton (für Feiertagskalender, Block 1.4) → erste Station(en)/Teams → Schichttypen
   (mit sinnvollen Vorlagen zur Auswahl statt Leerformular) → Mitarbeitende (CSV-Import statt
   Einzelanlage). Mit sichtbarer Fortschritts-Checkliste (Muster: Linear/Notion-Onboarding) statt
   alles auf einer langen Formularseite abzufragen.
4. **Einladungs-Flow** für weitere Mitarbeitende (E-Mail-Einladung statt manuellem Anlegen im
   Admin) — Folgeschritt nach dem Setup-Wizard, für den laufenden Betrieb.
5. **Passwort-Reset/Magic-Link-Login** — aktuell nicht vorhanden, nur `POST /api/auth/token/` mit
   bekanntem Passwort. Voraussetzung für Punkt 2 (Self-Serve-Signup ohne Passwort-Vergabe durch
   einen Admin).
6. **Rollenverwaltung im Frontend**, sobald Block 2.1 (rollenbasierte Berechtigungen) steht —
   damit der Setup-Wizard (Punkt 3) dem ersten Account direkt die Admin-Rolle zuweisen kann, ohne
   Django-Admin-Umweg.

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
# Dienstplanungs-SaaS — Backend-Skelett

Django + DRF Skelett gemäss `funktionsumfang-dienstplanung-saas.md` (Abschnitte 1–4, 6, 7, 8, 10).
Multi-Tenancy per `tenant_id` (shared database), getestet inkl. Cross-Tenant-Sicherheitschecks
(`core/tests.py`, `scheduling/tests.py`).

**Zielgruppe**: kleine Kliniken, Arztpraxen und ähnliche Gesundheitsbetriebe in der Schweiz
(typischerweise 5–50 Mitarbeitende, eine bis wenige Stationen/Standorte). Die Regel-Engine bildet
bereits mehrere Kernpunkte des Schweizer Arbeitsgesetzes (ArG) ab (siehe nächster Abschnitt) --
was für einen rechtssicheren Praxiseinsatz noch fehlt (u. a. automatische
Ersatzruhetag-Kontrolle), steht im
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
    Wochenübersicht ausserhalb dieses Feedbacks, keine Monats-/Jahres-Kumulierung (nur pro
    Kalenderwoche einzeln abrufbar).
12. ✅ **Wochenstunden-Grenzwerte pro Personalkategorie statt nur pro Tenant**: ein einzelner
    Tenant-Wert reicht nicht, wenn z. B. Ärzteschaft vertraglich 50h und Büropersonal 42h hat.
    `Employee.maximum_weekly_hours`/`standard_weekly_hours` sind jetzt optionale
    Override-Felder (`null` = Tenant-Default gilt) -- `ShiftAssignment._check_maximum_weekly_hours`
    und `Employee.weekly_hours_summary` verwenden `self.employee.<feld> or self.tenant.<feld>`.
    Bewusst als Override direkt auf `Employee` (nicht auf `Node`/als eigenes
    "Personalkategorie"-Modell), da die Grenze eine Eigenschaft der einzelnen Anstellung ist.
    *Noch offen*: keine Frontend-Oberfläche zum Setzen (aktuell nur im Django-Admin unter
    Employee editierbar, siehe Block 2.10); gleiche Überlegung gilt potenziell auch für
    `minimum_rest_hours`, hier aber erst nachziehen, falls in der Praxis tatsächlich gebraucht.

**Noch offen**:

13. **Anschluss der Ist-Arbeitszeiterfassung an Block 2.6**: die geplante Monatsauswertung
    (Soll/Ist-Stunden, Überzeit, Nacht-/Sonntagszuschläge) sollte, sobald sie existiert, auf
    `TimeRecord` statt nur auf der Planung (`ShiftAssignment`) basieren.
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
5. **Export** (PDF/Excel) des Monatsplans — für Aushang in der Praxis und Übergabe an externe
   Lohnbuchhaltung, die selten direkt an die API angebunden ist.
6. **Monatsauswertung Soll/Ist-Stunden pro Mitarbeiter** (inkl. Nacht-/Sonntagszuschläge,
   Überzeit) als Basis für den Lohnlauf.
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
     wie beim übrigen Planblatt. Response-Felder: `saldo_hours`, `annual_target_hours`,
     `annual_remaining_hours`, `is_provisional`, `vacation_*`.
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
     eigene Workflow-Funktion, keine reine Rechenkorrektur); Prognose ("Jahresziel voraussichtlich
     am 3. Dezember erreicht") -- die Datenbasis (`annual_remaining_hours` + geplante künftige
     Zuweisungen) ist vorhanden, die Extrapolation selbst ist ein separates UI-Feature.
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
8. **Diensttausch als echter Swap** auch im Drag & Drop des Planblatt-Grids (aktuell: Ziehen auf
   eine belegte Zelle wird abgelehnt statt getauscht).
9. **Mindestbesetzung pro Schicht/Node** definierbar machen und in der Regel-Engine warnen, wenn
   sie unterschritten wird.
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
    Wochenmuster-Kopieren pro Zelle übersprungen und summarisch gemeldet). Absenz-Tage lassen sich
    gar nicht erst markieren (Regel-Engine würde die Zuweisung ohnehin ablehnen). Der bestehende
    Einzel-Dropdown bleibt für gezielte Korrekturen einer einzelnen Zelle erhalten -- Ergänzung,
    kein Ersatz.
    - ✅ **Markieren durch Ziehen** (statt jede Zelle einzeln anklicken zu müssen): `onMouseDown`
      entscheidet anhand des Zustands der zuerst berührten Zelle, ob markiert oder entmarkiert
      wird, und startet damit den Ziehen-Modus; `onMouseEnter` wendet denselben Modus beim
      Drüberziehen mit gedrückter Maustaste auf weitere Zellen an; ein globaler `mouseup`-Listener
      am `window` beendet den Ziehvorgang auch dann, wenn die Maustaste ausserhalb einer Zelle
      losgelassen wird. `onClick` bleibt als Tastatur-Fallback (Enter/Leertaste lösen `click` ohne
      vorheriges `mousedown` aus) -- ein bereits per `mousedown` verarbeiteter Klick unterdrückt das
      nachfolgende `click`, damit nicht doppelt (ent-)markiert wird. Gleiches Muster in
      `ShiftCell.jsx` (Planblatt) und `YearPlan.jsx` (Jahresplan, Punkt 12 unten).
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
17. **Teams pro Station + Mehrfachanstellungen -- Anstellung statt Person als Planungseinheit**
    (2026-08, Nutzer-Feedback + Neuentwurf; ersetzt eine erste, unvollständige Fassung dieses
    Punkts). Zwei Fakten, die zusammen betrachtet werden müssen, weil sie dieselbe Modell-Lücke
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

    - **Datenmodell (neu, nicht implementiert)**: `Employment` als eigenständiges Modell zwischen
      `Employee` (die reale Person, bleibt Login/Stammdaten-Träger) und `Node` (jetzt konsequent als
      Team-Ebene genutzt, dank `django-treebeard` bereits ein Baum -- eine Station wie "ICT" bekommt
      Kind-Knoten "ICT/Infrastruktur", "ICT/Applikationen", "ICT/Support"). Felder: `employee` (FK),
      `node` (FK, das konkrete Team), `pensum_pct` (statt des heutigen globalen
      `Employee.employment_pct`), `title` (Freitext-Bezeichnung der Rolle, z. B. "Arzt"/"Dozent" --
      bewusst getrennt von `Skill`, das weiterhin die schicht-relevante Qualifikation abbildet, nicht
      den Vertragstitel), `is_team_lead` (Boolean, **pro Anstellung**, nicht pro Person -- so kann
      dieselbe Person in Team A Teamleiterin sein und in Team B nicht), `active` (für Ein-/Austritt
      einzelner Anstellungen, unabhängig von den anderen). `ShiftAssignment` referenziert künftig
      `Employment` statt `Employee` -- eine Schicht ist damit von Anfang an eindeutig einer Rolle
      zugeordnet, ohne Sonderfall-Logik zur Laufzeit zu brauchen ("in welcher Rolle war die Person an
      diesem Tag tätig"). Grössere Migration (u. a. `TimeRecord`/`Absence`/`ShiftPreference` hängen
      aktuell an `Employee` und müssten geprüft werden, ob sie an `Employment` oder weiterhin an
      `Employee` bleiben -- z. B. Ferienanspruch/Absenzen sind vermutlich personenweit sinnvoller
      als pro Anstellung), deshalb hier bewusst nur als Zielbild skizziert.

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

    - **Bewusst offen/zu klären, sobald das umgesetzt wird**: ob `TimeRecord`/`Absence`/
      `ShiftPreference` an `Employee` (personenweit) oder `Employment` (pro Anstellung) hängen
      sollten -- vermutlich meist personenweit, ausser ggf. Zeiterfassung; ob der Jahresplan
      (`YearPlan.jsx`, aktuell Ein-Knoten-Filter) dieselbe Team-Gruppierung und Mehrzeilen-Darstellung
      pro Anstellung braucht; wie viele Ebenen tief Teams verschachtelt werden dürfen (nur Station →
      Team, oder beliebig tief wie der `Node`-Baum es technisch zuliesse); ob `title` frei bleibt
      oder an eine kontrollierte Liste/den bestehenden `Skill`-Katalog gekoppelt wird.

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
5. **Automatisierte Frontend-Tests**: `dienstplan_frontend` hat aktuell keine persistierte
   Testsuite -- jedes Feature wurde bei der Entwicklung manuell per Playwright im Browser
   verifiziert, aber nichts davon liegt als wiederholbarer Test im Repo. Regressionen im
   Frontend fallen damit nicht automatisch auf, anders als im Backend (155 Tests, `python
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
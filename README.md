# Dienstplanungs-SaaS — Backend-Skelett

Django + DRF Skelett gemäss `funktionsumfang-dienstplanung-saas.md` (Abschnitte 1–4, 6, 7, 8, 10).
Multi-Tenancy per `tenant_id` (shared database), getestet inkl. Cross-Tenant-Sicherheitschecks
(`core/tests.py`, `scheduling/tests.py`).

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
  Genehmigungs-Workflow durch Vorgesetzte (siehe "Nächste Schritte").

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

## Nächste sinnvolle Schritte

1. Regel-Engine-Grenzwerte (`MINIMUM_REST_HOURS`, `MAXIMUM_WEEKLY_HOURS`) pro Tenant/Branche
   konfigurierbar machen statt globaler Konstanten; Mindestbesetzung pro Schicht ergänzen.
2. Genehmigungs-Workflow für Diensttausch durch Vorgesetzte (aktuell: direktes `accept` durch
   den Zielmitarbeiter, ohne Planer-Freigabe).
3. Benachrichtigungen (E-Mail/Push) bei neuer Diensttausch-Anfrage bzw. deren Annahme/Ablehnung.
4. Rollenbasierte Berechtigungen: `Membership.role` (Admin/Planer/Mitarbeiter/HR) existiert
   bereits als Datenmodell, wird aber von den ViewSets noch nicht ausgewertet — aktuell darf
   jeder authentifizierte Tenant-Angehörige alles innerhalb seines Tenants.
5. Diensttausch als echten Swap statt Move auch im Drag & Drop des Planblatt-Grids anbieten
   (aktuell: Ziehen auf eine belegte Zelle wird abgelehnt statt getauscht).

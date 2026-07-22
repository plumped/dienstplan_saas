# Dienstplanungs-SaaS — Backend-Skelett

Django + DRF Skelett gemäss `funktionsumfang-dienstplanung-saas.md` (Abschnitte 1–3, 6, 8, 10).
Multi-Tenancy per `tenant_id` (shared database), getestet inkl. Cross-Tenant-Sicherheitschecks.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Admin unter `/admin/`, API unter `/api/` (Browsable API inkl. Login unter `/api-auth/login/`).

## Architektur-Entscheidungen, die wichtig zu kennen sind

- **Multi-Tenancy**: `core.models.TenantScopedModel` (abstract) gibt jedem fachlichen Modell ein
  `tenant`-Feld. Zwei Manager: `objects` (ContextVar-gefiltert, praktisch für Shell/Celery),
  `all_objects` (ungefiltert). Die DRF-ViewSets filtern zusätzlich **explizit** über
  `request.tenant` in `get_queryset()` — das ist die eigentliche Sicherheitsgrenze, nicht die
  ContextVar. Grund: eine `queryset=Model.objects.all()` als Klassenattribut (z. B. in einem
  Serializer-Feld) wird beim Modul-Import ausgewertet, **bevor** ein Request-Tenant existiert,
  und wäre dauerhaft ungefiltert. Deshalb nutzt `NodeSerializer.parent` bewusst kein
  `PrimaryKeyRelatedField` mit `Node.objects`, sondern ein rohes `IntegerField` mit manueller
  Prüfung in der View. Alle Cross-Tenant-Zugriffe sind end-to-end getestet (siehe Verlauf).

  `request.tenant` wird in `TenantScopedViewSet.initial()` gesetzt — **nicht** in einer
  Django-Middleware. Eine frühere Version tat genau das und funktionierte in Tests mit
  Session-Login, lieferte aber leere Listen für das Frontend, das `TokenAuthentication`
  nutzt: Django-Middleware läuft VOR der DRF-Authentifizierung, `request.user` ist an der
  Middleware-Stelle bei Token-Logins noch `AnonymousUser`. `initial()` ruft zuerst
  `super().initial()` auf (das führt die DRF-Auth aus) und liest `request.user` erst danach.
  Siehe `core/tenancy.py` für den ausführlichen Kommentar dazu.

- **Auth für die API**: `POST /api/auth/token/` (DRF's `obtain_auth_token`) nimmt
  `{"username", "password"}` entgegen und gibt `{"token": "..."}` zurück. Das
  Frontend-Template (`dienstplan_frontend`) nutzt genau das.

- **Node (Organisationsbaum)**: nutzt `django-treebeard` (Materialized Path). Wichtig:
  Knoten **müssen** über `Node.add_root(...)` / `parent.add_child(...)` erzeugt werden, nicht
  über `.objects.create()` — sonst bleiben `path`/`depth` leer und es gibt einen
  `IntegrityError`. `NodeViewSet.perform_create` kapselt das bereits für die API
  (`POST /api/nodes/` mit optionalem `parent`-Feld).

- **Ruhezeit-Check**: `ShiftAssignment.clean()` ist ein bewusst einfacher Platzhalter für die
  volle Regel-Engine aus Abschnitt 4 des Funktionsumfangs (dort fehlen noch
  Höchstarbeitszeit- und Qualifikationsprüfung). Er greift bereits über die API, weil der
  Serializer `clean()` in `validate()` aufruft.

## Frontend

Ein kleines React/Vite-Template liegt separat unter `dienstplan_frontend/` (eigenes README dort).
Es spricht genau die hier beschriebene API an (`/api/auth/token/`, `/api/nodes/`,
`/api/employees/`, `/api/time-templates/`, `/api/shift-assignments/`).

## Nächste sinnvolle Schritte

1. Höchstarbeitszeit- und Qualifikationsprüfung in die Regel-Engine ergänzen (Abschnitt 4)
2. Absenzen-/Ferienmodell (Abschnitt 6) + Diensttausch (Abschnitt 7)
3. Drag & Drop und Wochenmuster-Kopieren im Grid (aktuell: Klick + Dropdown)
4. Custom User Model einführen, bevor die erste echte Migration mit Produktivdaten läuft
   (nachträglicher Wechsel von `django.contrib.auth.User` ist schmerzhaft)

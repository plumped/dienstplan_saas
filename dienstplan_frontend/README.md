# Dienstplan — Frontend-Template

Kleines React/Vite-Grid für das Planblatt aus dem Backend (`dienstplan_saas`).
Zeigt Mitarbeiter × Tage für eine Station/einen Monat, Klick auf eine Zelle
weist eine Schicht zu (oder entfernt sie).

## Setup

```bash
npm install
npm run dev
```

Läuft auf `http://localhost:5173` — das Backend muss parallel unter
`http://localhost:8000` laufen (`CORS_ALLOWED_ORIGINS` im Backend ist
bereits auf Port 5173 eingestellt). Login mit einem User, der eine
`Membership` in `core` hat (siehe Backend-README, Abschnitt Setup).

Andere Backend-URL? `.env.example` nach `.env.local` kopieren und anpassen.

## Was hier bewusst NICHT drin ist (Template, kein fertiges Produkt)

- Kein Drag & Drop im Grid — Zuweisung per Klick + Dropdown, das reicht für
  den MVP-Scope aus Abschnitt 3 des Funktionsumfangs, Drag & Drop wäre ein
  sinnvoller nächster Ausbauschritt.
- Keine Diensttausch-/Absenzen-Ansicht (Abschnitte 6/7) — noch nicht gebaut.
- Ein User = ein Tenant (Backend-Annahme). Für Mitarbeitende an mehreren
  Einrichtungen bräuchte es eine Tenant-Auswahl nach dem Login.
- `localStorage` fürs Token ist für einen echten Dev-Server/eigenes Deployment
  völlig in Ordnung (anders als in Claude-Artefakten, wo es nicht
  unterstützt wird).

## Struktur

```
src/
  api.js                 Fetch-Wrapper inkl. Token-Auth
  App.jsx                Login-Gate, Kopfzeile, Fehlerbanner
  components/
    LoginForm.jsx
    NodeSelector.jsx      Stations-/Standortwahl (Baumtiefe eingerückt)
    MonthNav.jsx
    PlanGrid.jsx           Kernstück: lädt Employees/Templates/Assignments
    ShiftCell.jsx          Eine Grid-Zelle (Chip + Klick-Dropdown)
  styles.css
```

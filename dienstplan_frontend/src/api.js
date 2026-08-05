const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000/api";
const AUTH_BASE = import.meta.env.VITE_AUTH_BASE || "http://localhost:8000/api/auth";
const TOKEN_KEY = "dienstplan_token";

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// UX-Nachbesserung Block 2.7: der Saldo (Employee.overtime_summary/
// vacation_balance) ist im Backend schon nach jeder Mutation aktuell --
// BalanceBadge.jsx hat ihn bisher aber nur einmal beim Mounten geladen und
// nie neu abgefragt, wenn währenddessen z. B. eine Schicht eingeplant oder
// eine Zeiterfassung gespeichert wurde. Statt jede Komponente einzeln zu
// verdrahten: ein simpler Pub/Sub, den `request()` unten selbst auslöst,
// sobald ein als `affectsBalance` markierter Aufruf erfolgreich war.
const balanceListeners = new Set();
export function onBalanceChanged(listener) {
  balanceListeners.add(listener);
  return () => balanceListeners.delete(listener);
}
function notifyBalanceChanged() {
  balanceListeners.forEach((listener) => listener());
}

// Block 2.4: analog zu onBalanceChanged, aber für die "offene Tasks"-Zähler
// (GET /api/me/: task_counts) hinter den Header-Badges bei Abwesenheiten/
// Diensttausch/Zeiterfassung -- eigener Kanal statt onBalanceChanged
// wiederzuverwenden, weil beide Konzepte zwar oft gleichzeitig, aber nicht
// zwingend zusammen ausgelöst werden (z. B. accept/decline/cancel einer
// Tauschanfrage ändert nur den Task-Zähler, nicht den Saldo).
const taskListeners = new Set();
export function onTasksChanged(listener) {
  taskListeners.add(listener);
  return () => taskListeners.delete(listener);
}
function notifyTasksChanged() {
  taskListeners.forEach((listener) => listener());
}

async function parseErrorResponse(res) {
  const detail = await res.json().catch(() => ({}));
  const message =
    detail.non_field_errors?.[0] ||
    detail.detail ||
    Object.values(detail)[0]?.[0] ||
    `Fehler (HTTP ${res.status})`;
  const error = new Error(message);
  // Block 2.16: DRF liefert Validierungsfehler strukturiert pro Feld
  // (z. B. {"minimum_rest_hours": ["..."]}) -- .message bleibt wie bisher
  // ein einzelner String für Aufrufer, die nur den globalen Fehlerbanner
  // füllen; .fields gibt Formularen mit vielen Feldern (EmployeeSettings,
  // TenantSettings) die Möglichkeit, Fehler direkt am betroffenen Feld
  // statt nur global anzuzeigen.
  error.fields = detail;
  return error;
}

async function request(
  path,
  { method = "GET", body, base = API_BASE, affectsBalance = false, affectsTasks = false } = {}
) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Token ${token}`;

  const res = await fetch(`${base}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    setToken(null);
    throw new Error("unauthorized");
  }
  if (!res.ok) throw await parseErrorResponse(res);
  if (affectsBalance) notifyBalanceChanged();
  if (affectsTasks) notifyTasksChanged();
  if (res.status === 204) return null;
  return res.json();
}

// Bugfix: Listen-Endpoints sind DRF-paginiert (PAGE_SIZE=50, siehe
// config/settings.py) -- request() allein liefert nur die erste Seite.
// Für Aufrufer, die z. B. ein ganzes Jahr an Zuweisungen für eine Station
// abfragen (YearPlan.jsx), reicht eine Seite schnell nicht mehr (bereits ab
// ca. 10 Wochen Mo-Fr-Diensten), und weiter zurückliegende Einträge fehlen
// dann kommentarlos. requestAllPages() folgt die `next`-URLs der DRF-
// Pagination und gibt alle Seiten zusammengeführt zurück, im selben
// {results: [...]}-Format wie eine einzelne Seite -- bestehende Aufrufer
// (`data.results ?? data`) müssen dafür nicht angepasst werden.
async function requestAllPages(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Token ${token}`;

  let url = `${API_BASE}${path}`;
  const results = [];
  while (url) {
    const res = await fetch(url, { headers });
    if (res.status === 401) {
      setToken(null);
      throw new Error("unauthorized");
    }
    if (!res.ok) throw await parseErrorResponse(res);
    const data = await res.json();
    if (Array.isArray(data)) return data; // unpaginierter Endpoint -- unverändert durchreichen
    results.push(...(data.results ?? []));
    url = data.next;
  }
  return { results };
}

export const api = {
  isLoggedIn: () => Boolean(getToken()),
  logout: () => setToken(null),

  login: async (username, password) => {
    // DRF's obtain_auth_token (core/urls.py: /api/auth/token/) -- kein
    // eigener Login-Endpoint nötig, kommt aus rest_framework.authtoken.
    const data = await request("/token/", {
      method: "POST",
      body: { username, password },
      base: AUTH_BASE,
    });
    setToken(data.token);
    return data;
  },

  getMe: () => request("/me/"),

  // Tenant-Konfiguration (Block 2.14): Single-Object-Endpoint
  // (core.views.TenantView), keine Liste -- genau ein Tenant pro Account.
  getTenant: () => request("/tenant/"),
  updateTenant: (payload) => request("/tenant/", { method: "PATCH", body: payload }),

  // Feiertags-Overrides (Arbeitszeitmodell, Block 2.7 Punkt 7): Ausnahmen
  // zum kantonalen Kalender, siehe core.views.TenantHolidayOverrideViewSet.
  getTenantHolidayOverrides: () => requestAllPages("/tenant-holiday-overrides/"),
  // Aufgelöste Feiertagsdaten fürs Planblatt/Jahresplan (siehe
  // core.views.TenantHolidaysView) -- {year, dates: ["2026-01-01", ...]}.
  getTenantHolidays: (year) => request(`/tenant/holidays/?year=${year}`),
  createTenantHolidayOverride: (payload) =>
    request("/tenant-holiday-overrides/", { method: "POST", body: payload }),
  deleteTenantHolidayOverride: (id) => request(`/tenant-holiday-overrides/${id}/`, { method: "DELETE" }),

  getNodes: () => requestAllPages("/nodes/"),
  createNode: (payload) => request("/nodes/", { method: "POST", body: payload }),
  updateNode: (id, payload) => request(`/nodes/${id}/`, { method: "PATCH", body: payload }),
  deleteNode: (id) => request(`/nodes/${id}/`, { method: "DELETE" }),

  getSkills: () => requestAllPages("/skills/"),
  createSkill: (payload) => request("/skills/", { method: "POST", body: payload }),
  updateSkill: (id, payload) => request(`/skills/${id}/`, { method: "PATCH", body: payload }),
  deleteSkill: (id) => request(`/skills/${id}/`, { method: "DELETE" }),

  getEmployees: () => requestAllPages("/employees/"),
  createEmployee: (payload) => request("/employees/", { method: "POST", body: payload }),
  updateEmployee: (id, payload) =>
    request(`/employees/${id}/`, { method: "PATCH", body: payload, affectsBalance: true }),
  getEmployeeBalance: (id) => request(`/employees/${id}/balance/`),
  getEmployeeWeeklyOvertime: (id, week) => request(`/employees/${id}/weekly-overtime/?week=${week}`),

  getTimeTemplates: () => requestAllPages("/time-templates/"),
  createTimeTemplate: (payload) => request("/time-templates/", { method: "POST", body: payload }),
  updateTimeTemplate: (id, payload) =>
    request(`/time-templates/${id}/`, { method: "PATCH", body: payload }),
  deleteTimeTemplate: (id) => request(`/time-templates/${id}/`, { method: "DELETE" }),
  getShiftAssignments: (nodeId, dateFrom, dateTo) =>
    requestAllPages(`/shift-assignments/?node=${nodeId}&date_from=${dateFrom}&date_to=${dateTo}`),
  getShiftAssignment: (id) => request(`/shift-assignments/${id}/`),
  createShiftAssignment: (payload) =>
    request("/shift-assignments/", { method: "POST", body: payload, affectsBalance: true }),
  updateShiftAssignment: (id, payload) =>
    request(`/shift-assignments/${id}/`, { method: "PATCH", body: payload, affectsBalance: true }),
  deleteShiftAssignment: (id) =>
    request(`/shift-assignments/${id}/`, { method: "DELETE", affectsBalance: true }),
  // README Block 2.8: echter Swap im Drag & Drop (Ziehen auf eine belegte
  // Zelle) -- tauscht employee/date/node zwischen zwei Zuweisungen, siehe
  // ShiftAssignment.swap() im Backend.
  swapShiftAssignments: (firstId, secondId) =>
    request("/shift-assignments/swap/", {
      method: "POST",
      body: { first: firstId, second: secondId },
      affectsBalance: true,
    }),

  getAbsences: (employeeId) =>
    requestAllPages(employeeId ? `/absences/?employee=${employeeId}` : "/absences/"),
  createAbsence: (payload) =>
    request("/absences/", { method: "POST", body: payload, affectsBalance: true, affectsTasks: true }),
  deleteAbsence: (id) => request(`/absences/${id}/`, { method: "DELETE", affectsBalance: true }),
  approveAbsence: (id) =>
    request(`/absences/${id}/approve/`, { method: "POST", affectsBalance: true, affectsTasks: true }),
  rejectAbsence: (id) =>
    request(`/absences/${id}/reject/`, { method: "POST", affectsBalance: true, affectsTasks: true }),

  getShiftTradeRequests: () => requestAllPages("/shift-trade-requests/"),
  createShiftTradeRequest: (payload) =>
    request("/shift-trade-requests/", { method: "POST", body: payload, affectsTasks: true }),
  acceptShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/accept/`, { method: "POST", affectsTasks: true }),
  declineShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/decline/`, { method: "POST", affectsTasks: true }),
  cancelShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/cancel/`, { method: "POST", affectsTasks: true }),
  // approve() vollzieht den eigentlichen Tausch (siehe ShiftTradeRequest.approve
  // im Backend) -- verschiebt Zuweisungen zwischen zwei Mitarbeitenden und
  // beeinflusst damit den Saldo beider. accept/decline/cancel tun das nicht.
  approveShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/approve/`, {
      method: "POST",
      affectsBalance: true,
      affectsTasks: true,
    }),
  rejectShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/reject/`, { method: "POST", affectsTasks: true }),

  // Wunschfrei/Wunschdienst (Block 2.13): reine Selbstauskunft, kein Effekt
  // auf den Saldo -- deshalb kein affectsBalance.
  getShiftPreferences: (employeeId) =>
    requestAllPages(employeeId ? `/shift-preferences/?employee=${employeeId}` : "/shift-preferences/"),
  createShiftPreference: (payload) => request("/shift-preferences/", { method: "POST", body: payload }),
  updateShiftPreference: (id, payload) =>
    request(`/shift-preferences/${id}/`, { method: "PATCH", body: payload }),
  deleteShiftPreference: (id) => request(`/shift-preferences/${id}/`, { method: "DELETE" }),

  getTimeRecords: (dateFrom, dateTo) =>
    requestAllPages(
      dateFrom && dateTo ? `/time-records/?date_from=${dateFrom}&date_to=${dateTo}` : "/time-records/"
    ),
  createTimeRecord: (payload) =>
    request("/time-records/", { method: "POST", body: payload, affectsBalance: true, affectsTasks: true }),
  updateTimeRecord: (id, payload) =>
    request(`/time-records/${id}/`, {
      method: "PATCH",
      body: payload,
      affectsBalance: true,
      affectsTasks: true,
    }),
  deleteTimeRecord: (id) =>
    request(`/time-records/${id}/`, { method: "DELETE", affectsBalance: true, affectsTasks: true }),
  confirmTimeRecord: (id) =>
    request(`/time-records/${id}/confirm/`, { method: "POST", affectsBalance: true, affectsTasks: true }),
};

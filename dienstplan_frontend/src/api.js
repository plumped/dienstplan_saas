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
  // Erzwungener Passwortwechsel nach admin-seitiger Direktanlage (Nutzer-
  // Feedback 2026-08, siehe core.views.ChangePasswordView).
  changePassword: (currentPassword, newPassword) =>
    request("/me/change-password/", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    }),

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
  // Nutzer-Feedback (2026-08): Stationen per Drag & Drop verschieben --
  // parent=null verschiebt auf die oberste Ebene (Wurzelknoten).
  moveNode: (id, parentId) => request(`/nodes/${id}/move/`, { method: "POST", body: { parent: parentId } }),

  getSkills: () => requestAllPages("/skills/"),
  createSkill: (payload) => request("/skills/", { method: "POST", body: payload }),
  updateSkill: (id, payload) => request(`/skills/${id}/`, { method: "PATCH", body: payload }),
  deleteSkill: (id) => request(`/skills/${id}/`, { method: "DELETE" }),

  getEmployees: () => requestAllPages("/employees/"),
  // Stammdatenpflege (Nutzer-Feedback 2026-08): eigene, NICHT paginierend
  // durchgereichte Variante für die Mitarbeitenden-Tabelle in
  // EmployeeSettings.jsx -- bei mehreren hundert Mitarbeitenden lädt
  // getEmployees() (requestAllPages, alle Seiten auf einmal) viel zu viel für
  // eine einzelne Tabellenansicht. searchEmployees() gibt die rohe
  // DRF-Pagination-Envelope ({count, next, previous, results}) einer EINEN
  // Seite zurück, gefiltert/sortiert serverseitig (EmployeeViewSet:
  // ?search=/?ordering=/?node=/?is_active=/?page=). Alle anderen Stellen der
  // App (Planblatt, Absenzen, Diensttausch, Dashboard) brauchen weiterhin den
  // kompletten Bestand und bleiben bei getEmployees().
  searchEmployees: ({ search, ordering, node, isActive, page } = {}) => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (ordering) params.set("ordering", ordering);
    if (node) params.set("node", node);
    if (isActive !== undefined && isActive !== "") params.set("is_active", isActive);
    if (page) params.set("page", page);
    const qs = params.toString();
    return request(`/employees/${qs ? `?${qs}` : ""}`);
  },
  createEmployee: (payload) => request("/employees/", { method: "POST", body: payload }),
  updateEmployee: (id, payload) =>
    request(`/employees/${id}/`, { method: "PATCH", body: payload, affectsBalance: true }),
  getEmployeeBalance: (id) => request(`/employees/${id}/balance/`),
  getEmployeeSickPay: (id) => request(`/employees/${id}/sick-pay/`),
  // MVP-Fahrplan Block 2, Punkt 20: Fairness-Punkte für unpopuläre Schichten.
  getEmployeeFairness: (id) => request(`/employees/${id}/fairness/`),
  // Nutzer-Feedback (2026-08, Performance): Bulk-Variante für Saldo+Fairness
  // -- ein Request für eine ganze Mitarbeitendenliste statt 2xN
  // Einzelrequests (siehe EmployeeSettings.jsx, BalanceBadge.jsx/
  // FairnessBadge.jsx im Bulk-Modus).
  getEmployeesBalanceFairnessBulk: (ids) =>
    ids.length ? request(`/employees/balance-fairness-bulk/?ids=${ids.join(",")}`) : Promise.resolve([]),
  getEmployeeWeeklyOvertime: (id, week) => request(`/employees/${id}/weekly-overtime/?week=${week}`),
  getEmployeeMonthlySummary: (id, year, month) =>
    request(`/employees/${id}/monthly-summary/?year=${year}&month=${month}`),
  settleOvertime: (id, year, month) =>
    request(`/employees/${id}/settle-overtime/`, { method: "POST", body: { year, month } }),
  // Nutzer-Feedback (2026-08): "Es gibt nun Tab Mitarbeitende, Tab Mitglieder
  // und Zugriff [...] Das muss doch intuitiver gelöst werden?" -- Login-
  // Zugang wird direkt am Mitarbeitenden-Datensatz eingerichtet statt in
  // einem separaten Tab, siehe scheduling.views.EmployeeViewSet.setup_access.
  // Response enthält `temporary_password` EINMALIG (danach nicht mehr
  // abrufbar), analog zu createMembership() unten.
  setupEmployeeAccess: (id, payload) => request(`/employees/${id}/setup-access/`, { method: "POST", body: payload }),

  getTimeTemplates: () => requestAllPages("/time-templates/"),
  // Stammdatenpflege (Nutzer-Feedback 2026-08): gleiches Muster wie
  // searchEmployees() oben -- eigene, serverseitig gefilterte/sortierte
  // Einzelseiten-Variante für die Schichttyp-Tabelle in
  // TimeTemplateSettings.jsx. Planblatt/Jahresplan/Stempelleisten brauchen
  // weiterhin den kompletten Bestand und bleiben bei getTimeTemplates().
  searchTimeTemplates: ({ search, ordering, node, category, page } = {}) => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (ordering) params.set("ordering", ordering);
    if (node) params.set("node", node);
    if (category) params.set("category", category);
    if (page) params.set("page", page);
    const qs = params.toString();
    return request(`/time-templates/${qs ? `?${qs}` : ""}`);
  },
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
  // Nutzer-Feedback ("massiver Bug"): eine Mehrfachanstellung kann in einem
  // ANDEREN Team/Station bereits einen Dienst haben, der eine neue Zuweisung
  // hier blockiert -- ohne dass das Grid (nur das aktuelle Team geladen)
  // das je zeigen würde. Liefert genau die Info für eine proaktive Warnung
  // auf der sonst leeren Zelle, siehe ShiftAssignmentViewSet.other_team_conflicts.
  getOtherTeamConflicts: (employeeIds, dateFrom, dateTo, excludeNodeId) =>
    requestAllPages(
      `/shift-assignments/other-team-conflicts/?employees=${employeeIds.join(",")}` +
        `&date_from=${dateFrom}&date_to=${dateTo}&exclude_node=${excludeNodeId}`
    ),

  // README Punkt 21 (Dashboard): einziger neuer Endpoint fürs Dashboard --
  // die stationsübergreifende Mindestbesetzungs-Auswertung lässt sich (anders
  // als offene Absenzen/Tauschanfragen, die aus den bereits bestehenden
  // Endpoints unten kommen) nicht ohne N Requests pro Station clientseitig
  // bilden, siehe scheduling.views.UnderstaffedShiftsView. Plain-Liste, kein
  // DRF-Pagination-Envelope -- request() statt requestAllPages() reicht.
  getUnderstaffedShifts: () => request("/understaffed-shifts/"),

  // Nutzer-Feedback (2026-08): Absenzarten sind ein tenant-eigener Katalog
  // (analog Schichttypen), keine hartcodierten Ferien/Krankheit/Sonstiges mehr.
  getAbsenceTypes: () => requestAllPages("/absence-types/"),
  createAbsenceType: (payload) => request("/absence-types/", { method: "POST", body: payload }),
  updateAbsenceType: (id, payload) =>
    request(`/absence-types/${id}/`, { method: "PATCH", body: payload }),
  deleteAbsenceType: (id) => request(`/absence-types/${id}/`, { method: "DELETE" }),

  // MVP-Fahrplan Block 2, Punkt 30: Lohnart-Zuordnung, Admin-only fürs
  // Schreiben (siehe scheduling.views.PayrollCategoryMappingViewSet).
  getPayrollCategoryMappings: () => requestAllPages("/payroll-category-mappings/"),
  createPayrollCategoryMapping: (payload) =>
    request("/payroll-category-mappings/", { method: "POST", body: payload }),
  updatePayrollCategoryMapping: (id, payload) =>
    request(`/payroll-category-mappings/${id}/`, { method: "PATCH", body: payload }),

  getAbsences: (employeeId) =>
    requestAllPages(employeeId ? `/absences/?employee=${employeeId}` : "/absences/"),
  // Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
  // aufgebaut sein wie Zeiterfassung" -- gleiches Muster wie
  // searchTimeRecords() oben: eine gefilterte/sortierte Einzelseite
  // (DRF-Envelope), stationsübergreifend (Scoping passiert serverseitig
  // über AbsenceViewSet), für AbsenceOverview.jsx.
  searchAbsences: ({ status, node, search, ordering, page } = {}) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (node) params.set("node", node);
    if (search) params.set("search", search);
    if (ordering) params.set("ordering", ordering);
    if (page) params.set("page", page);
    const qs = params.toString();
    return request(`/absences/${qs ? `?${qs}` : ""}`);
  },
  createAbsence: (payload) =>
    request("/absences/", { method: "POST", body: payload, affectsBalance: true, affectsTasks: true }),
  deleteAbsence: (id) => request(`/absences/${id}/`, { method: "DELETE", affectsBalance: true }),
  approveAbsence: (id) =>
    request(`/absences/${id}/approve/`, { method: "POST", affectsBalance: true, affectsTasks: true }),
  rejectAbsence: (id) =>
    request(`/absences/${id}/reject/`, { method: "POST", affectsBalance: true, affectsTasks: true }),

  // Mutterschutz (Block 1.15): eigenes Ereignis-Modell (mehrere
  // Schwangerschaften pro Mitarbeiterin möglich) -- anders als bei den
  // übrigen Listen hier ist die Sichtbarkeit schon serverseitig
  // eingeschränkt (Admin sieht alle, sonst nur die eigenen), das Frontend
  // muss dafür nichts extra filtern.
  getPregnancies: (employeeId) =>
    requestAllPages(employeeId ? `/pregnancies/?employee=${employeeId}` : "/pregnancies/"),
  createPregnancy: (payload) => request("/pregnancies/", { method: "POST", body: payload }),
  updatePregnancy: (id, payload) => request(`/pregnancies/${id}/`, { method: "PATCH", body: payload }),
  deletePregnancy: (id) => request(`/pregnancies/${id}/`, { method: "DELETE" }),

  getShiftTradeRequests: () => requestAllPages("/shift-trade-requests/"),
  // Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
  // aufgebaut sein wie Zeiterfassung" -- gleiches Muster wie
  // searchTimeRecords()/searchAbsences() oben, für TradeRequestOverview.jsx.
  searchShiftTradeRequests: ({ open, node, search, ordering, page } = {}) => {
    const params = new URLSearchParams();
    if (open) params.set("open", "true");
    if (node) params.set("node", node);
    if (search) params.set("search", search);
    if (ordering) params.set("ordering", ordering);
    if (page) params.set("page", page);
    const qs = params.toString();
    return request(`/shift-trade-requests/${qs ? `?${qs}` : ""}`);
  },
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
  // Nutzer-Feedback (2026-08): "ich muss die Stationen durchsuchen, bis ich
  // die zu bestätigende Erfassung finde" -- gleiches Muster wie
  // searchEmployees()/searchTimeTemplates() oben: eine gefilterte/sortierte
  // Einzelseite (DRF-Envelope), stationsübergreifend (Scoping passiert
  // serverseitig über TimeRecordViewSet), für TimeRecordOverview.jsx.
  searchTimeRecords: ({ status, node, search, ordering, page, dateFrom, dateTo } = {}) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (node) params.set("node", node);
    if (search) params.set("search", search);
    if (ordering) params.set("ordering", ordering);
    if (page) params.set("page", page);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const qs = params.toString();
    return request(`/time-records/${qs ? `?${qs}` : ""}`);
  },
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

  // Nutzer-Feedback (2026-08): "Noch nicht erfasst" -- Gegenstück zu
  // searchTimeRecords() oben, aber für vergangene ShiftAssignments OHNE
  // TimeRecord (siehe scheduling.views.MissingTimeRecordViewSet). Rein
  // lesend, deshalb kein create/update/delete hier.
  getMissingTimeRecords: ({ node, search, ordering, page, dateFrom, dateTo } = {}) => {
    const params = new URLSearchParams();
    if (node) params.set("node", node);
    if (search) params.set("search", search);
    if (ordering) params.set("ordering", ordering);
    if (page) params.set("page", page);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const qs = params.toString();
    return request(`/missing-time-records/${qs ? `?${qs}` : ""}`);
  },

  // Nutzer-Feedback (2026-08): "kann man [Planer] Stationen zuweisen?" --
  // Admin-only Verwaltung von Membership.scoped_nodes, siehe
  // MembershipViewSet/MembershipAccessSettings.jsx.
  getMemberships: () => requestAllPages("/memberships/"),
  updateMembershipScopedNodes: (id, nodeIds) =>
    request(`/memberships/${id}/`, { method: "PATCH", body: { scoped_nodes: nodeIds } }),
  // Nutzer-Feedback (2026-08): Direktanlage statt E-Mail-Einladung, siehe
  // core.serializers.MembershipCreateSerializer -- Response enthält
  // `temporary_password` EINMALIG (danach nicht mehr abrufbar).
  createMembership: (payload) => request("/memberships/", { method: "POST", body: payload }),
  updateMembershipRole: (id, role) => request(`/memberships/${id}/`, { method: "PATCH", body: { role } }),

  // MVP-Fahrplan Block 2, Punkt 31: Lohn-Rohdaten-Export. getPayrollExport
  // liefert die JSON-Vorschau (inkl. Warnliste bei fehlendem Mapping) für
  // die Einstellungen-Seite; downloadPayrollExportCsv löst stattdessen
  // einen Datei-Download aus -- erster CSV-Export der App, request() liefert
  // nur JSON, daher ein eigener fetch-Aufruf mit demselben Auth-Header.
  getPayrollExport: (month) => request(`/payroll-export/?month=${month}`),
  downloadPayrollExportCsv: async (month) => {
    const token = getToken();
    const res = await fetch(`${API_BASE}/payroll-export/?month=${month}&output=csv`, {
      headers: token ? { Authorization: `Token ${token}` } : {},
    });
    if (!res.ok) throw await parseErrorResponse(res);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `lohn-export-${month}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },

  // MVP-Fahrplan Block 2, Punkt 5: Planblatt-Export (PDF für Aushang, CSV
  // für nicht API-angebundene Lohnbuchhaltung) -- gleiches Download-Muster
  // wie downloadPayrollExportCsv oben, nur mit variablem output/Dateityp.
  downloadPlanExport: async (nodeId, month, outputFormat) => {
    const token = getToken();
    const res = await fetch(`${API_BASE}/plan-export/?node=${nodeId}&month=${month}&output=${outputFormat}`, {
      headers: token ? { Authorization: `Token ${token}` } : {},
    });
    if (!res.ok) throw await parseErrorResponse(res);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `plan-export-${month}.${outputFormat}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
};

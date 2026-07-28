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

async function request(path, { method = "GET", body, base = API_BASE } = {}) {
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
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const message =
      detail.non_field_errors?.[0] ||
      detail.detail ||
      Object.values(detail)[0]?.[0] ||
      `Fehler (HTTP ${res.status})`;
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
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

  getNodes: () => request("/nodes/"),
  createNode: (payload) => request("/nodes/", { method: "POST", body: payload }),
  updateNode: (id, payload) => request(`/nodes/${id}/`, { method: "PATCH", body: payload }),
  deleteNode: (id) => request(`/nodes/${id}/`, { method: "DELETE" }),

  getSkills: () => request("/skills/"),
  createSkill: (payload) => request("/skills/", { method: "POST", body: payload }),
  updateSkill: (id, payload) => request(`/skills/${id}/`, { method: "PATCH", body: payload }),
  deleteSkill: (id) => request(`/skills/${id}/`, { method: "DELETE" }),

  getEmployees: () => request("/employees/"),
  createEmployee: (payload) => request("/employees/", { method: "POST", body: payload }),
  updateEmployee: (id, payload) => request(`/employees/${id}/`, { method: "PATCH", body: payload }),
  getEmployeeBalance: (id) => request(`/employees/${id}/balance/`),

  getTimeTemplates: () => request("/time-templates/"),
  createTimeTemplate: (payload) => request("/time-templates/", { method: "POST", body: payload }),
  updateTimeTemplate: (id, payload) =>
    request(`/time-templates/${id}/`, { method: "PATCH", body: payload }),
  deleteTimeTemplate: (id) => request(`/time-templates/${id}/`, { method: "DELETE" }),
  getShiftAssignments: (nodeId, dateFrom, dateTo) =>
    request(`/shift-assignments/?node=${nodeId}&date_from=${dateFrom}&date_to=${dateTo}`),
  getShiftAssignment: (id) => request(`/shift-assignments/${id}/`),
  createShiftAssignment: (payload) =>
    request("/shift-assignments/", { method: "POST", body: payload }),
  updateShiftAssignment: (id, payload) =>
    request(`/shift-assignments/${id}/`, { method: "PATCH", body: payload }),
  deleteShiftAssignment: (id) =>
    request(`/shift-assignments/${id}/`, { method: "DELETE" }),

  getAbsences: (employeeId) =>
    request(employeeId ? `/absences/?employee=${employeeId}` : "/absences/"),
  createAbsence: (payload) => request("/absences/", { method: "POST", body: payload }),
  deleteAbsence: (id) => request(`/absences/${id}/`, { method: "DELETE" }),
  approveAbsence: (id) => request(`/absences/${id}/approve/`, { method: "POST" }),
  rejectAbsence: (id) => request(`/absences/${id}/reject/`, { method: "POST" }),

  getShiftTradeRequests: () => request("/shift-trade-requests/"),
  createShiftTradeRequest: (payload) =>
    request("/shift-trade-requests/", { method: "POST", body: payload }),
  acceptShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/accept/`, { method: "POST" }),
  declineShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/decline/`, { method: "POST" }),
  cancelShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/cancel/`, { method: "POST" }),
  approveShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/approve/`, { method: "POST" }),
  rejectShiftTradeRequest: (id) =>
    request(`/shift-trade-requests/${id}/reject/`, { method: "POST" }),

  getTimeRecords: (dateFrom, dateTo) =>
    request(
      dateFrom && dateTo ? `/time-records/?date_from=${dateFrom}&date_to=${dateTo}` : "/time-records/"
    ),
  createTimeRecord: (payload) => request("/time-records/", { method: "POST", body: payload }),
  updateTimeRecord: (id, payload) =>
    request(`/time-records/${id}/`, { method: "PATCH", body: payload }),
  deleteTimeRecord: (id) => request(`/time-records/${id}/`, { method: "DELETE" }),
  confirmTimeRecord: (id) => request(`/time-records/${id}/confirm/`, { method: "POST" }),
};

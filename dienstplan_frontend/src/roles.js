export const ROLE_LABELS = {
  admin: "Admin",
  planner: "Planer",
  employee: "Mitarbeiter",
  hr: "HR",
};

// Deckt sich mit core.permissions.MANAGER_ROLES im Backend -- das Frontend
// blendet damit nur Bedienelemente aus, die der Server ohnehin mit 403
// ablehnen würde (siehe core/permissions.py).
export function canManageSchedule(me) {
  return me?.role === "admin" || me?.role === "planner";
}

// Deckt sich mit core.permissions.IsTenantAdmin (Block 2, Punkt 14) --
// strenger als canManageSchedule: nur Admin, nicht Planer, darf die
// Tenant-Konfiguration (ArG-/Zuschlags-Grenzwerte) schreiben.
export function isTenantAdmin(me) {
  return me?.role === "admin";
}

// Deckt sich mit core.permissions.MANAGER_AND_HR_ROLES -- weiter als
// canManageSchedule (schliesst HR ein, "nur Reporting", aber genau dafür
// ist die stationsübergreifende Zeiterfassungs-Übersicht gedacht, siehe
// TimeRecordOverview.jsx).
export function canViewScheduleReports(me) {
  return me?.role === "admin" || me?.role === "planner" || me?.role === "hr";
}

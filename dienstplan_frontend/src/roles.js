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

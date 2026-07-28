import { useEffect, useState } from "react";
import { api } from "../api.js";

// Block 2.7: kompakte Saldo-Anzeige (Überstunden ± X h, Ferienguthaben) --
// im Topbar für den eigenen Account, in EmployeeSettings.jsx zusätzlich pro
// Mitarbeiter für Admin/Planer.
export default function BalanceBadge({ employeeId }) {
  const [balance, setBalance] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setBalance(null);
    api
      .getEmployeeBalance(employeeId)
      .then((data) => !cancelled && setBalance(data))
      .catch(() => {
        // Saldo ist eine Zusatzinfo -- ein Fehler hier soll die restliche
        // Seite nicht mit einer globalen Fehlermeldung stören.
      });
    return () => {
      cancelled = true;
    };
  }, [employeeId]);

  if (!balance) return null;

  const hours = balance.overtime_balance_hours;
  const sign = hours > 0 ? "+" : "";
  const overtimeClass = hours < 0 ? "is-negative" : hours > 0 ? "is-positive" : "";

  return (
    <span
      className="balance-badge"
      title={`Überstunden-Saldo seit der ersten erfassten Schicht, Feriensaldo ${balance.vacation_year} -- Stand ${balance.as_of}`}
    >
      <span className={overtimeClass}>
        {sign}
        {hours} h
      </span>
      <span className="balance-badge-sep">·</span>
      <span>{balance.vacation_remaining_days} Ferientage</span>
    </span>
  );
}

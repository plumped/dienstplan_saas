import { useEffect, useState } from "react";
import { api } from "../api.js";

// Block 2.7: Saldo-Anzeige (Überstunden ± X h, Ferienguthaben). Zwei
// Varianten je nach Einsatzort:
// - "pill" (Default): abgerundetes Badge für Topbar und Mitarbeitenden-
//   Verwaltung (Block 2.10), dort ist genug Platz für eine eigene Zeile.
// - "cell": schlichter Zelleninhalt für die Saldo-Spalte im Planblatt-Grid
//   (Block 2.7) -- kein Badge-Rahmen, passt sich in eine normale Tabellenzelle
//   ein statt wie ein Fremdkörper darin zu sitzen.
export default function BalanceBadge({ employeeId, variant = "pill" }) {
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
  const title = `Überstunden-Saldo seit der ersten erfassten Schicht, Feriensaldo ${balance.vacation_year} -- Stand ${balance.as_of}`;

  if (variant === "cell") {
    return (
      <span className="balance-cell" title={title}>
        <span className={overtimeClass}>
          {sign}
          {hours} h
        </span>
        <span className="balance-cell-sep">/</span>
        <span>{balance.vacation_remaining_days} Ferientage</span>
      </span>
    );
  }

  return (
    <span className="balance-badge" title={title}>
      <span className={overtimeClass}>
        {sign}
        {hours} h
      </span>
      <span className="balance-badge-sep">·</span>
      <span>{balance.vacation_remaining_days} Ferientage</span>
    </span>
  );
}

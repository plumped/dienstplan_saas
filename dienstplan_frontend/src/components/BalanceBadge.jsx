import { useEffect, useState } from "react";
import { api, onBalanceChanged } from "../api.js";

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

    function load() {
      api
        .getEmployeeBalance(employeeId)
        .then((data) => !cancelled && setBalance(data))
        .catch(() => {
          // Saldo ist eine Zusatzinfo -- ein Fehler hier soll die restliche
          // Seite nicht mit einer globalen Fehlermeldung stören.
        });
    }

    setBalance(null);
    load();
    // UX-Nachbesserung: der Saldo ist im Backend nach jeder Mutation sofort
    // aktuell (auch aus geplanten, noch ungeprüften Schichten) -- ohne
    // dieses Abo würde die Badge das aber nur einmal beim Mounten laden und
    // erst nach einem Seiten-Reload nachziehen. onBalanceChanged() feuert,
    // sobald irgendwo eine Zuweisung/Zeiterfassung/Absenz gespeichert wurde
    // (siehe affectsBalance in api.js).
    const unsubscribe = onBalanceChanged(load);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [employeeId]);

  if (!balance) return null;

  const hours = balance.overtime_balance_hours;
  const sign = hours > 0 ? "+" : "";
  const overtimeClass = hours < 0 ? "is-negative" : hours > 0 ? "is-positive" : "";
  const provisional = balance.overtime_is_provisional;
  const title =
    `Überstunden-Saldo seit der ersten erfassten Schicht, Feriensaldo ${balance.vacation_year} -- Stand ${balance.as_of}. ` +
    (provisional
      ? "Enthält geplante und/oder noch nicht geprüfte Schichten -- die Zahl ist bereits aktuell, kann sich aber noch leicht ändern, bis alle Schichten erfasst und geprüft sind."
      : "Beruht vollständig auf geprüften Zeiterfassungen.");

  const overtimeValue = (
    <span className={overtimeClass}>
      {provisional && <span className="balance-provisional-marker">~</span>}
      {sign}
      {hours} h
    </span>
  );

  if (variant === "cell") {
    return (
      <span className="balance-cell" title={title}>
        {overtimeValue}
        <span className="balance-cell-sep">/</span>
        <span>{balance.vacation_remaining_days} Ferientage</span>
      </span>
    );
  }

  return (
    <span className="balance-badge" title={title}>
      {overtimeValue}
      <span className="balance-badge-sep">·</span>
      <span>{balance.vacation_remaining_days} Ferientage</span>
    </span>
  );
}

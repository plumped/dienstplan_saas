import { useEffect, useState } from "react";
import { api, onBalanceChanged } from "../api.js";

// Arbeitszeitmodell (README Block 2.7 Punkt 7): Saldo-Anzeige mit zwei
// Kennzahlen -- laufender Saldo (± X h, Farbcodierung blau/vor Plan vs.
// rot/hinter Plan) und Jahresrestsoll (als Fortschrittsbalken zum
// Jahressoll, nicht als rohe grosse Zahl -- die wäre v. a. am Jahresanfang
// erschreckend). Zwei Varianten je nach Einsatzort:
// - "pill" (Default): abgerundetes Badge für Topbar und Mitarbeitenden-
//   Verwaltung (Block 2.10) inkl. Fortschrittsbalken, dort ist Platz dafür.
// - "cell": schlichter Zelleninhalt für die Saldo-Spalte im Planblatt-Grid
//   (Block 2.7) -- kein Badge-Rahmen, kein Balken (zu wenig Platz in der
//   Tabellenzeile), nur Saldo + Ferientage.
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
    // aktuell -- ohne dieses Abo würde die Badge das aber nur einmal beim
    // Mounten laden und erst nach einem Seiten-Reload nachziehen.
    // onBalanceChanged() feuert, sobald irgendwo eine Zuweisung/
    // Zeiterfassung/Absenz gespeichert wurde (siehe affectsBalance in api.js).
    const unsubscribe = onBalanceChanged(load);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [employeeId]);

  if (!balance) return null;

  const hours = balance.saldo_hours;
  const sign = hours > 0 ? "+" : "";
  const saldoClass = hours < 0 ? "is-negative" : hours > 0 ? "is-positive" : "";
  const provisional = balance.is_provisional;
  const target = balance.annual_target_hours;
  const worked = target - balance.annual_remaining_hours;
  const progressPct = target > 0 ? Math.max(0, Math.min(100, Math.round((worked / target) * 100))) : 0;
  const title =
    `Laufender Saldo (Ist minus Soll seit Jahresbeginn bzw. Eintritt, Stand ${balance.as_of}): ` +
    `${sign}${hours} h. Jahressoll ${target} h, davon ${progressPct}% erreicht -- ` +
    `noch ${balance.annual_remaining_hours} h bis Jahresende. Feriensaldo ${balance.vacation_year}. ` +
    (provisional
      ? "Enthält Schichten ohne geprüfte Zeiterfassung -- die Zahl ist bereits aktuell, kann sich aber noch leicht ändern."
      : "Beruht vollständig auf geprüften Zeiterfassungen.");

  const saldoValue = (
    <span className={saldoClass}>
      {provisional && <span className="balance-provisional-marker">~</span>}
      {sign}
      {hours} h
    </span>
  );

  if (variant === "cell") {
    return (
      <span className="balance-cell" title={title}>
        {saldoValue}
        <span className="balance-cell-sep">/</span>
        <span>{balance.vacation_remaining_days} Ferientage</span>
      </span>
    );
  }

  return (
    <span className="balance-badge" title={title}>
      {saldoValue}
      <span className="balance-progress" aria-hidden="true">
        <span className="balance-progress-fill" style={{ width: `${progressPct}%` }} />
      </span>
      <span className="balance-badge-sep">·</span>
      <span>{balance.vacation_remaining_days} Ferientage</span>
    </span>
  );
}

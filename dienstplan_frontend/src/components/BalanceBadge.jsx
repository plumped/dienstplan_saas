import { useEffect, useState } from "react";
import { api, onBalanceChanged } from "../api.js";

// Arbeitszeitmodell (README Block 2.7 Punkt 7, redesignt 2026-08 nach
// Nutzer-Feedback): primäre Anzeige ist jetzt plan_saldo_hours statt
// saldo_hours -- bei festem Pensum entscheidet der Planer, WANN die
// Stunden anfallen, nicht die Mitarbeitenden. Die strenge "nur bereits
// erfolgte Schichten"-Zahl (saldo_hours) hätte als Hauptanzeige einen
// grossen, irreführenden Minus-Wert gezeigt, solange noch nicht das ganze
// Jahr "eingetreten" ist, obwohl der Jahresplan das Vertragssoll längst
// erfüllt. plan_saldo_hours bezieht bereits eingeplante künftige
// Zuweisungen mit ein und liegt bei einem sauber durchgeplanten Jahr nahe
// 0, unabhängig vom aktuellen Datum -- das ist die aussagekräftige Zahl,
// nicht nur "wie viel wurde bisher gearbeitet". Die strenge Zahl bleibt im
// Tooltip als Detail erhalten (u. a. für Lohn-/Überzeit-Zwecke relevant).
// Zwei Varianten je nach Einsatzort:
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

  const hours = balance.plan_saldo_hours;
  const sign = hours > 0 ? "+" : "";
  const saldoClass = hours < 0 ? "is-negative" : hours > 0 ? "is-positive" : "";
  const provisional = balance.is_provisional;
  const target = balance.annual_target_hours;
  // annual_remaining_hours ist seit dem Redesign "noch nicht verplant"
  // (weder geleistet noch bereits eingeteilt) -- worked ist entsprechend
  // "bereits verplant" (Vergangenheit + Zukunft), nicht mehr nur "bereits
  // gearbeitet". Der Fortschrittsbalken zeigt dadurch jetzt sinnvollerweise
  // den Planungsfortschritt fürs Jahr, nicht nur den Arbeitsfortschritt.
  const worked = target - balance.annual_remaining_hours;
  const progressPct = target > 0 ? Math.max(0, Math.min(100, Math.round((worked / target) * 100))) : 0;
  const strictSign = balance.saldo_hours > 0 ? "+" : "";
  const title =
    `Saldo gemäss Jahresplan (bereits geleistete + bereits eingeplante Stunden minus Jahressoll, ` +
    `Stand ${balance.as_of}): ${sign}${hours} h. Jahressoll ${target} h, davon ${progressPct}% bereits ` +
    `verplant -- noch ${balance.annual_remaining_hours} h zu verplanen. Feriensaldo ${balance.vacation_year}. ` +
    `Nur bereits erfolgte Schichten (Stand heute, ohne Planung): ${strictSign}${balance.saldo_hours} h. ` +
    (provisional
      ? "Enthält Schichten ohne geprüfte Zeiterfassung bzw. noch nicht erfolgte, eingeplante Schichten -- die Zahl ist bereits aktuell, kann sich aber noch leicht ändern."
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

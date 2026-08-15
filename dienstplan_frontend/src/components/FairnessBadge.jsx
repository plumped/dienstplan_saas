import { useEffect, useState } from "react";
import { api, onBalanceChanged } from "../api.js";

// MVP-Fahrplan Block 2, Punkt 20: Fairness-Punkte für unpopuläre Schichten
// (Sonntag, Nacht) -- nur für Admin/Planer in der Mitarbeitendenliste
// sichtbar (Nutzer-Entscheidung: ein Punkte-Vergleich mit Kolleg:innen in
// der Topbar könnte in manchen Teams Konkurrenzdruck erzeugen, anders als
// der rein personenbezogene Saldo). Bewusst denselben onBalanceChanged-
// Kanal wie BalanceBadge.jsx abonniert statt einen eigenen einzuführen --
// dieselbe Zuweisungs-Mutation beeinflusst sowohl Saldo als auch
// Fairness-Punkte (siehe affectsBalance in api.js).
//
// Bulk-Modus (Nutzer-Feedback 2026-08, Performance): wird ein `data`-Prop
// übergeben, holt die Badge sich die Fairness-Punkte NICHT mehr selbst --
// siehe BalanceBadge.jsx für dasselbe Muster und die Begründung
// (EmployeeSettings.jsx lädt dann Saldo+Fairness für die ganze Liste in
// einem einzigen Bulk-Request statt 2xN Einzelrequests).
export default function FairnessBadge({ employeeId, data: providedFairness }) {
  const [fetchedFairness, setFetchedFairness] = useState(null);
  const bulkMode = providedFairness !== undefined;
  const fairness = bulkMode ? providedFairness : fetchedFairness;

  useEffect(() => {
    if (bulkMode) return;
    let cancelled = false;

    function load() {
      api
        .getEmployeeFairness(employeeId)
        .then((data) => !cancelled && setFetchedFairness(data))
        .catch(() => {
          // Fairness-Punkte sind eine Zusatzinfo -- ein Fehler hier soll die
          // restliche Seite nicht mit einer globalen Fehlermeldung stören.
        });
    }

    setFetchedFairness(null);
    load();
    const unsubscribe = onBalanceChanged(load);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [employeeId, bulkMode]);

  if (!fairness) return null;

  const { points, sunday_hours, sunday_points, night_hours, night_points, team_average_points } = fairness;
  const title =
    `Fairness-Punkte für unpopuläre Schichten, letzte 365 Tage: ${points} Pkt. ` +
    `${sunday_hours} Sonntagsstunden (${sunday_points} Pkt) + ${night_hours} Nachtstunden ` +
    `(${night_points} Pkt). ` +
    (team_average_points === null
      ? "Kein Team-Durchschnitt verfügbar (keine Station zugeordnet)."
      : `Team-Durchschnitt (auf dein Pensum umgerechnet): ${team_average_points} Pkt. `) +
    "Wunschdienste zählen nicht als Belastung. Rein informativ, kein Lohnbestandteil.";

  return (
    <span className="fairness-badge" title={title}>
      <span className="fairness-badge-value">{points} Pkt</span>
      {team_average_points !== null && <span className="fairness-badge-avg"> (Ø {team_average_points})</span>}
    </span>
  );
}

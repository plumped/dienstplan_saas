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
export default function FairnessBadge({ employeeId }) {
  const [fairness, setFairness] = useState(null);

  useEffect(() => {
    let cancelled = false;

    function load() {
      api
        .getEmployeeFairness(employeeId)
        .then((data) => !cancelled && setFairness(data))
        .catch(() => {
          // Fairness-Punkte sind eine Zusatzinfo -- ein Fehler hier soll die
          // restliche Seite nicht mit einer globalen Fehlermeldung stören.
        });
    }

    setFairness(null);
    load();
    const unsubscribe = onBalanceChanged(load);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [employeeId]);

  if (!fairness) return null;

  const { points, sunday_hours, sunday_points, night_hours, night_points, team_average_points } = fairness;
  const title =
    `Fairness-Punkte für unpopuläre Schichten, letzte 365 Tage: ${points} Pkt. ` +
    `${sunday_hours} Sonntagsstunden (${sunday_points} Pkt) + ${night_hours} Nachtstunden ` +
    `(${night_points} Pkt). ` +
    (team_average_points === null
      ? "Kein Team-Durchschnitt verfügbar (keine Station zugeordnet)."
      : `Team-Durchschnitt: ${team_average_points} Pkt. `) +
    "Wunschdienste zählen nicht als Belastung. Rein informativ, kein Lohnbestandteil.";

  return (
    <span className="fairness-badge" title={title}>
      {points} Pkt
      {team_average_points !== null && <span className="fairness-badge-avg"> (Ø {team_average_points})</span>}
    </span>
  );
}

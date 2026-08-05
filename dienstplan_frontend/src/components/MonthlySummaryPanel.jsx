import { useEffect, useState } from "react";
import { api } from "../api.js";

const MONTH_NAMES = [
  "Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember",
];

// Block 2.6, "Basis für den Lohnlauf": Monatsauswertung Soll/Ist-Stunden
// pro Mitarbeiter inkl. Überzeit- sowie Nacht-/Sonntagszuschlag
// (Employee.monthly_summary). Anders als die Saldo-Badges in Topbar/
// Mitarbeiterliste (README Block 2.7, Mitarbeiter-Selbstauskunft) bewusst
// ein eigenes, separates Settings-Modul nur für Admin/Planer (siehe
// EmployeeViewSet.monthly_summary-Berechtigung) -- die Zahlen hier dienen
// der externen Lohnbuchhaltung, nicht der Selbstauskunft.
export default function MonthlySummaryPanel({ onError }) {
  const today = new Date();
  const [employees, setEmployees] = useState([]);
  const [employeeId, setEmployeeId] = useState(null);
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .getEmployees()
      .then((res) => {
        if (cancelled) return;
        const list = res.results ?? res;
        setEmployees(list);
        setEmployeeId((current) => current ?? list[0]?.id ?? null);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!employeeId) return;
    let cancelled = false;
    setSummary(null);
    api
      .getEmployeeMonthlySummary(employeeId, year, month)
      .then((data) => !cancelled && setSummary(data))
      .catch((e) => !cancelled && onError(e.message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employeeId, year, month]);

  if (loading) return <p className="loading-state">Mitarbeitende werden geladen …</p>;

  const rows = summary
    ? [
        { key: "soll", label: "Soll-Stunden", value: summary.soll_hours },
        { key: "ist", label: "Ist-Stunden", value: summary.ist_hours },
        { key: "overtime", label: "Überzeitstunden", value: summary.overtime_hours },
        { key: "overtime-surcharge", label: "davon Zuschlag", value: summary.overtime_surcharge_hours },
        { key: "night", label: "Nachtstunden", value: summary.night_hours },
        { key: "night-surcharge", label: "davon Zeitgutschrift", value: summary.night_surcharge_hours },
        { key: "sunday", label: "Sonntagsstunden", value: summary.sunday_hours },
        { key: "sunday-surcharge", label: "davon Zuschlag", value: summary.sunday_surcharge_hours },
      ]
    : [];

  return (
    <div className="panel-form">
      <h2>Monatsauswertung</h2>
      <p className="panel-hint">
        Soll/Ist-Stunden, Überzeit sowie Nacht-/Sonntagszuschlag für einen Kalendermonat -- Basis für
        den Lohnlauf. Ist-Stunden kommen pro Schicht bevorzugt aus der geprüften Zeiterfassung,
        sonst aus der Planung als Schätzwert (siehe "voraussichtlich"-Hinweis unten).
      </p>

      <div className="panel-form-row">
        <label>
          Mitarbeiter
          <select
            value={employeeId ?? ""}
            onChange={(e) => setEmployeeId(Number(e.target.value))}
          >
            {employees.map((emp) => (
              <option key={emp.id} value={emp.id}>
                {emp.first_name} {emp.last_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Monat
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
            {MONTH_NAMES.map((name, index) => (
              <option key={name} value={index + 1}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Jahr
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
          />
        </label>
      </div>

      {!summary ? (
        <p className="loading-state">Auswertung wird geladen …</p>
      ) : (
        <>
          <table className="monthly-summary-table">
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <th>{row.label}</th>
                  <td>{row.value} h</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="panel-hint">
            Zeitraum {summary.month_start} – {summary.month_end}.{" "}
            {summary.is_provisional
              ? "Enthält Schichten ohne geprüfte Zeiterfassung -- die Zahlen sind bereits aktuell, können sich aber noch leicht ändern."
              : "Beruht vollständig auf geprüften Zeiterfassungen."}
          </p>
        </>
      )}
    </div>
  );
}

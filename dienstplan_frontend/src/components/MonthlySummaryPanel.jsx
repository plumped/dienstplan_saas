import { useEffect, useState } from "react";
import { api } from "../api.js";
import { IconBarChart } from "../icons.jsx";

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
//
// Nutzer-Feedback (2026-08): "bei uns gilt Gleitzeit, nur angeordnete
// Überstunden werden effektiv abgerechnet" -- overtime_surcharge_hours wird
// deshalb NICHT mehr automatisch aus jeder Soll/Ist-Differenz berechnet,
// sondern erst, sobald ein Saldo-Überschuss ausserhalb der Gleitzeit-
// Bandbreite (Tenant.flextime_corridor_hours) hier bestätigt wurde (siehe
// flextime_corridor_excess_hours/handleSettle unten).
export default function MonthlySummaryPanel({ onError }) {
  const today = new Date();
  const [employees, setEmployees] = useState([]);
  const [employeeId, setEmployeeId] = useState(null);
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [settling, setSettling] = useState(false);

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

  async function handleSettle() {
    setSettling(true);
    try {
      const data = await api.settleOvertime(employeeId, year, month);
      setSummary(data);
    } catch (e) {
      onError(e.message);
    } finally {
      setSettling(false);
    }
  }

  const rows = summary
    ? [
        { key: "soll", label: "Soll-Stunden", value: summary.soll_hours },
        { key: "ist", label: "Ist-Stunden", value: summary.ist_hours },
        { key: "overtime", label: "Überzeitstunden (Monat)", value: summary.overtime_hours },
        { key: "saldo", label: "Gleitzeitsaldo (Jahr, kumuliert)", value: summary.saldo_hours },
        { key: "overtime-surcharge", label: "davon abgerechnet (Zuschlag)", value: summary.overtime_surcharge_hours },
        { key: "night", label: "Nachtstunden", value: summary.night_hours },
        { key: "night-surcharge", label: "davon Zeitgutschrift", value: summary.night_surcharge_hours },
        { key: "sunday", label: "Sonntagsstunden", value: summary.sunday_hours },
        { key: "sunday-surcharge", label: "davon Zuschlag", value: summary.sunday_surcharge_hours },
        { key: "special-surcharge", label: "Spezialitäten-Zuschlag total", value: summary.special_surcharge_hours },
      ]
    : [];

  return (
    <div className="panel-form">
      <div className="settings-form-header">
        <span className="settings-form-icon">
          <IconBarChart />
        </span>
        <div>
          <h2>Monatsauswertung</h2>
          <p className="settings-form-subtitle">Soll/Ist-Stunden und Zuschläge pro Mitarbeiter und Monat.</p>
        </div>
      </div>
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
          <div className="stat-tile-grid">
            {rows.map((row) => (
              <div className="stat-tile" key={row.key}>
                <p className="stat-tile-label">{row.label}</p>
                <p className="stat-tile-value">{row.value} h</p>
              </div>
            ))}
          </div>

          {summary.flextime_corridor_excess_hours > 0 && (
            <div className="corridor-callout">
              <p>
                <strong>{summary.flextime_corridor_excess_hours} h</strong> liegen über der
                Gleitzeit-Bandbreite von {summary.flextime_corridor_hours} h (Einstellungen →
                Regel-Engine &amp; Zuschläge) und sind noch nicht abgerechnet. Nutzer-Feedback (2026-08):
                "bei uns gilt Gleitzeit, nur angeordnete Überstunden werden effektiv abgerechnet" -- erst
                nach Bestätigung fliesst dieser Betrag in den Lohnlauf ein.
              </p>
              <button type="button" className="btn-primary" onClick={handleSettle} disabled={settling}>
                {settling ? "Bestätigt …" : "Überschuss bestätigen"}
              </button>
            </div>
          )}
          {summary.is_overtime_settled && (
            <p className="panel-hint">
              Für diesen Monat bereits bestätigt: {summary.overtime_surcharge_hours} h Zuschlag fliessen in
              den Lohnlauf ein.
            </p>
          )}

          {summary.special_surcharge_breakdown.length > 0 && (
            <>
              <h3>Spezialitäten-Zuschlag nach Schichttyp</h3>
              <p className="panel-hint">
                Nutzer-Feedback (2026-08): "wenn jemand Pikett macht, ist dieser zuschlagsberechtigt"
                -- pro Spezialität einzeln statt als eine Summe, damit später jede ihren eigenen
                Lohnart-Code im Lohnsystem bekommen kann (README Punkt 30).
              </p>
              <table className="monthly-summary-table">
                <thead>
                  <tr>
                    <th>Spezialität</th>
                    <th>Stunden</th>
                    <th>Zuschlag %</th>
                    <th>Zuschlagsstunden</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.special_surcharge_breakdown.map((entry) => (
                    <tr key={entry.template_id}>
                      <td>{entry.template_name}</td>
                      <td>{entry.hours} h</td>
                      <td>{entry.surcharge_pct}%</td>
                      <td>{entry.surcharge_hours} h</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

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

import { useEffect, useState } from "react";
import { api } from "../api.js";
import AbsenceForm, { DAY_PORTIONS } from "./AbsenceForm.jsx";

const STATUS_LABELS = {
  pending: "Offen",
  approved: "Genehmigt",
  rejected: "Abgelehnt",
};

// Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen gleich
// aufgebaut sein wie Zeiterfassung" -- Admin/Planer/HR nutzen dafür jetzt
// die stationsübergreifende AbsenceOverview.jsx (siehe App.jsx-Routing).
// Dieses Panel wird dadurch nur noch von der Mitarbeiter-Rolle erreicht:
// eigene, stationsgebundene Selbstauskunft (eigene Absenz erfassen,
// eigene/Team-Absenzen einsehen, eigene offene Absenz zurückziehen) --
// keine canManage-Verzweigung mehr nötig, kein tenant-weiter
// allEmployeesById-Workaround mehr (der existierte nur, weil AbsenceViewSet
// bisher kein Stations-Scoping kannte, siehe AbsenceOverview.jsx-Docstring).
export default function AbsencePanel({ employees, me, onError }) {
  const ownEmployeeId = me?.employee?.id ?? null;

  const [absenceTypes, setAbsenceTypes] = useState([]);
  const absenceTypesById = new Map(absenceTypes.map((t) => [t.id, t]));
  const [absences, setAbsences] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .getAbsenceTypes()
      .then((data) => !cancelled && setAbsenceTypes(data.results ?? data))
      .catch((e) => onError(e.message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getAbsences()
      .then((data) => {
        if (cancelled) return;
        const list = data.results ?? data;
        const employeeIds = new Set(employees.map((e) => e.id));
        setAbsences(list.filter((a) => employeeIds.has(a.employee)));
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees]);

  function handleCreated(created) {
    setAbsences((prev) => [created, ...prev]);
  }

  async function handleDelete(id) {
    try {
      await api.deleteAbsence(id);
      setAbsences((prev) => prev.filter((a) => a.id !== id));
    } catch (e) {
      onError(e.message);
    }
  }

  function employeeName(id) {
    const employee = employees.find((e) => e.id === id);
    return employee ? `${employee.first_name} ${employee.last_name}` : `#${id}`;
  }

  if (!employees.length) {
    return (
      <p className="empty-state">
        Keine Mitarbeiter dieser Station zugeordnet. Im Admin unter „Employees“ ergänzen.
      </p>
    );
  }

  return (
    <div className="side-panel">
      <AbsenceForm
        employees={employees}
        canManage={false}
        me={me}
        absenceTypes={absenceTypes}
        onCreated={handleCreated}
        onError={onError}
      />

      <div className="panel-list">
        <h2>Erfasste Abwesenheiten</h2>
        {loading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : absences.length === 0 ? (
          <p className="empty-state">Keine Abwesenheiten für diese Station erfasst.</p>
        ) : (
          <ul className="entry-list">
            {absences.map((a) => {
              const isOwnPending = a.status === "pending" && a.employee === ownEmployeeId && ownEmployeeId !== null;
              return (
                <li key={a.id} className="entry-list-item">
                  <span className="type-badge" style={{ "--chip-color": absenceTypesById.get(a.type)?.color }}>
                    {absenceTypesById.get(a.type)?.name ?? a.type}
                  </span>
                  <span className={`status-badge status-badge--${a.status}`}>{STATUS_LABELS[a.status] ?? a.status}</span>
                  <span className="entry-main">
                    <strong>{employeeName(a.employee)}</strong> · {a.start_date} – {a.end_date}
                    {a.day_portion && a.day_portion !== "full" && (
                      <span className="entry-day-portion">
                        {" "}
                        · {DAY_PORTIONS.find((p) => p.value === a.day_portion)?.label ?? a.day_portion}
                      </span>
                    )}
                    {a.note && <span className="entry-note"> · {a.note}</span>}
                  </span>
                  <span className="entry-actions">
                    {isOwnPending && (
                      <button
                        type="button"
                        className="btn-ghost btn-danger-ghost"
                        onClick={() => handleDelete(a.id)}
                        aria-label="Abwesenheit löschen"
                      >
                        Löschen
                      </button>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

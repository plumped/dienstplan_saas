import { useEffect, useState } from "react";
import { api } from "../api.js";

// Nutzer-Feedback (2026-08): "ich kann auch einen Nachmittag frei nehmen" --
// nur bei einem einzelnen Tag sinnvoll (Backend erzwingt das in
// Absence.clean(), siehe Kommentar dort), daher gilt DAY_PORTIONS[1]/[2] nur
// für start_date === end_date; das Select wird sonst deaktiviert.
export const DAY_PORTIONS = [
  { value: "full", label: "Ganzer Tag" },
  { value: "morning", label: "Nur vormittags" },
  { value: "afternoon", label: "Nur nachmittags" },
];

const SICK_PAY_SCALE_LABELS = { basel: "Basler", bern: "Berner", zuerich: "Zürcher" };

function emptyForm(defaultEmployeeId, defaultTypeId) {
  return {
    employee: defaultEmployeeId ?? "",
    start_date: "",
    end_date: "",
    day_portion: "full",
    type: defaultTypeId ?? "",
    note: "",
  };
}

// Aus AbsencePanel.jsx extrahiert (Nutzer-Feedback 2026-08: "Abwesenheiten/
// Diensttausch sollen gleich aufgebaut sein wie Zeiterfassung"), damit
// dasselbe Formular sowohl im Self-Service-Panel (AbsencePanel.jsx,
// canManage=false, fest eingeblendet) als auch in der stationsübergreifenden
// Übersicht (AbsenceOverview.jsx, canManage=true, in einem Modal, Mitarbeiter
// per Dropdown wählbar) läuft, statt zweimal gepflegt zu werden.
export default function AbsenceForm({ employees, canManage, me, absenceTypes, onCreated, onError }) {
  const ownEmployeeId = me?.employee?.id ?? null;
  const defaultEmployeeId = canManage ? employees[0]?.id ?? "" : ownEmployeeId ?? "";

  const [form, setForm] = useState(() => emptyForm(defaultEmployeeId, absenceTypes[0]?.id));
  const [saving, setSaving] = useState(false);
  // MVP-Fahrplan Block 1 Punkt 16 (Art. 324a OR): Lohnfortzahlungs-Anspruch
  // des gerade gewählten Mitarbeiters, nur geladen wenn im Formular
  // tatsächlich eine Absenzart mit counts_as_sick_leave ausgewählt ist (kein
  // unnötiger Roundtrip für Ferien/Sonstiges).
  const [sickPaySummary, setSickPaySummary] = useState(null);

  useEffect(() => {
    setForm((prev) => ({
      ...emptyForm(defaultEmployeeId, absenceTypes[0]?.id),
      type: prev.type || absenceTypes[0]?.id || "",
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employees, canManage, ownEmployeeId, absenceTypes]);

  const selectedAbsenceType = absenceTypes.find((t) => t.id === Number(form.type));

  useEffect(() => {
    if (!form.employee || !selectedAbsenceType?.counts_as_sick_leave) {
      setSickPaySummary(null);
      return;
    }
    let cancelled = false;
    api
      .getEmployeeSickPay(form.employee)
      .then((data) => !cancelled && setSickPaySummary(data))
      .catch(() => !cancelled && setSickPaySummary(null));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.employee, selectedAbsenceType?.counts_as_sick_leave]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.employee || !form.start_date || !form.end_date) {
      onError("Mitarbeiter, Start- und Enddatum sind Pflichtfelder.");
      return;
    }
    setSaving(true);
    try {
      const created = await api.createAbsence({
        employee: Number(form.employee),
        start_date: form.start_date,
        end_date: form.end_date,
        day_portion: form.start_date === form.end_date ? form.day_portion : "full",
        type: Number(form.type),
        note: form.note,
      });
      onCreated(created);
      setForm((prev) => ({ ...emptyForm(defaultEmployeeId, absenceTypes[0]?.id), employee: prev.employee }));
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="panel-form" onSubmit={handleSubmit}>
      <h2>Abwesenheit erfassen</h2>
      <div className="panel-form-row">
        <label>
          Mitarbeiter
          {canManage ? (
            <select value={form.employee} onChange={(e) => setForm((prev) => ({ ...prev, employee: e.target.value }))}>
              {employees.map((emp) => (
                <option key={emp.id} value={emp.id}>
                  {emp.first_name} {emp.last_name}
                </option>
              ))}
            </select>
          ) : (
            <input type="text" value={me?.employee ? `${me.employee.first_name} ${me.employee.last_name}` : ""} disabled />
          )}
        </label>
        <label>
          Art
          <select value={form.type} onChange={(e) => setForm((prev) => ({ ...prev, type: e.target.value }))}>
            {absenceTypes.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="panel-form-row">
        <label>
          Von
          <input
            type="date"
            value={form.start_date}
            onChange={(e) => setForm((prev) => ({ ...prev, start_date: e.target.value }))}
            required
          />
        </label>
        <label>
          Bis
          <input
            type="date"
            value={form.end_date}
            onChange={(e) => setForm((prev) => ({ ...prev, end_date: e.target.value }))}
            required
          />
        </label>
      </div>
      {/* Nutzer-Feedback (2026-08): Halbtags-Absenzen -- nur bei einem
          einzelnen Tag wählbar (Backend erzwingt das), sonst deaktiviert
          und automatisch auf "Ganzer Tag" zurückgesetzt beim Absenden. */}
      <label>
        Tagesanteil
        <select
          value={form.day_portion}
          disabled={!form.start_date || form.start_date !== form.end_date}
          onChange={(e) => setForm((prev) => ({ ...prev, day_portion: e.target.value }))}
          title={
            form.start_date && form.start_date !== form.end_date
              ? "Nur bei einem einzelnen Tag wählbar (Von = Bis)"
              : undefined
          }
        >
          {DAY_PORTIONS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Notiz (optional)
        <input type="text" value={form.note} onChange={(e) => setForm((prev) => ({ ...prev, note: e.target.value }))} />
      </label>
      {sickPaySummary && sickPaySummary.model === "scale" && (
        <div className={sickPaySummary.remaining_days <= 0 ? "corridor-callout" : "panel-hint"}>
          {sickPaySummary.remaining_days <= 0 ? (
            <p>
              <strong>Lohnfortzahlungs-Anspruch bereits ausgeschöpft</strong> im laufenden Dienstjahr (
              {sickPaySummary.service_year_start} – {sickPaySummary.service_year_end}): {sickPaySummary.used_days} von{" "}
              {sickPaySummary.entitlement_days} Tagen verbraucht (Art. 324a OR,{" "}
              {SICK_PAY_SCALE_LABELS[sickPaySummary.scale]} Skala).
            </p>
          ) : (
            <>
              Lohnfortzahlungs-Anspruch im laufenden Dienstjahr ({sickPaySummary.service_year_start} –{" "}
              {sickPaySummary.service_year_end}): {sickPaySummary.remaining_days} von {sickPaySummary.entitlement_days}{" "}
              Tagen verbleibend (Art. 324a OR, {SICK_PAY_SCALE_LABELS[sickPaySummary.scale]} Skala).
            </>
          )}
        </div>
      )}
      {sickPaySummary && sickPaySummary.model === "daily_allowance_insurance" && (
        <p className="panel-hint">
          Krankentaggeldversicherung: Wartefrist {sickPaySummary.waiting_days} Tage, bisher {sickPaySummary.used_days}{" "}
          Tage im laufenden Dienstjahr erfasst.
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={saving || !form.employee}>
        {saving ? "Speichert …" : "Anlegen"}
      </button>
    </form>
  );
}

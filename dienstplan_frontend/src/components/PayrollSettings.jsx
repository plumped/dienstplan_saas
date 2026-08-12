import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

// Dieselben Kategorien wie scheduling.models.PayrollCategoryMapping.Category
// (Backend) -- hier dupliziert, analog SWISS_CANTONS in TenantSettings.jsx.
// "Sonstige Absenztage" gibt es hier bewusst NICHT mehr als feste Kategorie
// (Nutzer-Feedback 2026-08: "sonstige Absenztage müssten aufgeschlüsselt
// werden") -- jede Absenzart ohne Ferien-/Krankheits-Flag bekommt stattdessen
// unten eine eigene Zeile, analog den Spezialitäten.
const CATEGORY_OPTIONS = [
  { value: "regular_hours", label: "Normalstunden" },
  { value: "overtime", label: "Überstunden" },
  { value: "night_credit", label: "Nacht-Zeitgutschrift (regelmässig)" },
  { value: "night_surcharge", label: "Nacht-Lohnzuschlag (gelegentlich)" },
  { value: "sunday_surcharge", label: "Sonntagszuschlag" },
  { value: "vacation_days", label: "Ferientage" },
  { value: "sick_days", label: "Krankheitstage (mit Lohnfortzahlung)" },
  { value: "sick_days_exhausted", label: "Krankheitstage (Anspruch erschöpft)" },
  { value: "holidays", label: "Feiertage" },
];

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

// MVP-Fahrplan Block 2, Punkt 30/31 ("näher am Verkaufsargument" als der
// reine Plan-Export, Nutzer-Entscheidung 2026-08): Lohnart-Zuordnung + CSV-
// Export der Lohn-Rohdaten. Zwei unabhängige Abschnitte -- die Tabelle
// bearbeitet PayrollCategoryMapping (fixe Kategorien + eine Zeile pro
// zuschlagspflichtiger Spezialität), der Export darunter ist rein lesend
// und unabhängig von ungespeicherten Tabellenänderungen.
export default function PayrollSettings({ onError }) {
  const [mappings, setMappings] = useState([]);
  const [specialTemplates, setSpecialTemplates] = useState([]);
  const [otherAbsenceTypes, setOtherAbsenceTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const [month, setMonth] = useState(currentMonth());
  const [exporting, setExporting] = useState(false);
  const [warnings, setWarnings] = useState(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getPayrollCategoryMappings(), api.getTimeTemplates(), api.getAbsenceTypes()])
      .then(([mappingsRes, templatesRes, absenceTypesRes]) => {
        if (cancelled) return;
        setMappings(mappingsRes.results ?? mappingsRes);
        const templates = templatesRes.results ?? templatesRes;
        setSpecialTemplates(templates.filter((t) => t.category === "special" && t.surcharge_pct > 0));
        const absenceTypes = absenceTypesRes.results ?? absenceTypesRes;
        // Nur Absenzarten ohne Ferien-/Krankheits-Flag sind hier relevant --
        // die beiden anderen fliessen fest gebündelt in vacation_days/
        // sick_days (siehe Employee.payroll_raw_lines()).
        setOtherAbsenceTypes(
          absenceTypes.filter((t) => !t.deducts_vacation_days && !t.counts_as_sick_leave)
        );
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mappingByCategory = useMemo(() => {
    const map = new Map();
    for (const m of mappings) if (m.category) map.set(m.category, m);
    return map;
  }, [mappings]);
  const mappingByTemplate = useMemo(() => {
    const map = new Map();
    for (const m of mappings) if (m.special_template) map.set(m.special_template, m);
    return map;
  }, [mappings]);
  const mappingByAbsenceType = useMemo(() => {
    const map = new Map();
    for (const m of mappings) if (m.absence_type) map.set(m.absence_type, m);
    return map;
  }, [mappings]);

  function fieldValue(key, field, existing) {
    if (form[key]?.[field] !== undefined) return form[key][field];
    return existing?.[field] ?? "";
  }

  function updateField(key, field, value) {
    setForm((prev) => ({ ...prev, [key]: { ...prev[key], [field]: value } }));
  }

  async function handleSave(key, identity, existing) {
    const payload = {
      category: identity.category ?? null,
      special_template: identity.specialTemplateId ?? null,
      absence_type: identity.absenceTypeId ?? null,
      payroll_code: fieldValue(key, "payroll_code", existing),
      payroll_label: fieldValue(key, "payroll_label", existing),
    };
    if (!payload.payroll_code.trim()) {
      onError("Lohnart-Code ist ein Pflichtfeld.");
      return;
    }
    setSavingKey(key);
    try {
      if (existing) {
        const updated = await api.updatePayrollCategoryMapping(existing.id, payload);
        setMappings((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
      } else {
        const created = await api.createPayrollCategoryMapping(payload);
        setMappings((prev) => [...prev, created]);
      }
    } catch (e) {
      onError(e.message);
    } finally {
      setSavingKey(null);
    }
  }

  async function handleExport() {
    setExporting(true);
    setWarnings(null);
    try {
      const preview = await api.getPayrollExport(month);
      setWarnings(preview.warnings);
      await api.downloadPayrollExportCsv(month);
    } catch (e) {
      onError(e.message);
    } finally {
      setExporting(false);
    }
  }

  if (loading) return <p className="loading-state">Wird geladen …</p>;

  return (
    <div className="panel-form">
      <div className="panel-form-group">
        <h2>Lohnarten</h2>
        <p className="panel-hint">
          Ordnet die intern berechneten Zuschlagskategorien den Lohnart-Codes deines Lohnsystems zu --
          kein einziger verpflichtender CH-Standard dafür, jedes Lohnsystem vergibt eigene Codes. Ohne
          Zuordnung erscheint die Kategorie im Export unten als Warnung statt stillschweigend zu fehlen.
        </p>
        <div className="payroll-mapping-table-wrap">
          <table className="payroll-mapping-table">
            <thead>
              <tr>
                <th>Kategorie</th>
                <th>Lohnart-Code</th>
                <th>Bezeichnung</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {CATEGORY_OPTIONS.map((opt) => {
                const existing = mappingByCategory.get(opt.value);
                const key = `category:${opt.value}`;
                return (
                  <tr key={key}>
                    <td>{opt.label}</td>
                    <td>
                      <input
                        type="text"
                        value={fieldValue(key, "payroll_code", existing)}
                        onChange={(e) => updateField(key, "payroll_code", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        value={fieldValue(key, "payroll_label", existing)}
                        onChange={(e) => updateField(key, "payroll_label", e.target.value)}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => handleSave(key, { category: opt.value }, existing)}
                        disabled={savingKey === key}
                      >
                        {savingKey === key ? "…" : "Speichern"}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {specialTemplates.map((t) => {
                const existing = mappingByTemplate.get(t.id);
                const key = `template:${t.id}`;
                return (
                  <tr key={key}>
                    <td>
                      {t.name} <span className="entry-note">({t.surcharge_pct}% Zuschlag)</span>
                    </td>
                    <td>
                      <input
                        type="text"
                        value={fieldValue(key, "payroll_code", existing)}
                        onChange={(e) => updateField(key, "payroll_code", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        value={fieldValue(key, "payroll_label", existing)}
                        onChange={(e) => updateField(key, "payroll_label", e.target.value)}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => handleSave(key, { specialTemplateId: t.id }, existing)}
                        disabled={savingKey === key}
                      >
                        {savingKey === key ? "…" : "Speichern"}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {otherAbsenceTypes.map((t) => {
                const existing = mappingByAbsenceType.get(t.id);
                const key = `absence:${t.id}`;
                return (
                  <tr key={key}>
                    <td>
                      {t.name} <span className="entry-note">(Absenzart)</span>
                    </td>
                    <td>
                      <input
                        type="text"
                        value={fieldValue(key, "payroll_code", existing)}
                        onChange={(e) => updateField(key, "payroll_code", e.target.value)}
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        value={fieldValue(key, "payroll_label", existing)}
                        onChange={(e) => updateField(key, "payroll_label", e.target.value)}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => handleSave(key, { absenceTypeId: t.id }, existing)}
                        disabled={savingKey === key}
                      >
                        {savingKey === key ? "…" : "Speichern"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel-form-group">
        <h2>Export</h2>
        <p className="panel-hint">
          Lohn-Rohdaten eines Kalendermonats als CSV, pro Mitarbeiter eine Zeile je Kategorie
          (Personalnummer, Name, Kostenstelle, Lohnart-Code, Bezeichnung, Menge, Einheit, Periode).
          Die Kostenstelle kommt von den Stationen des Mitarbeitenden (Einstellungen → Stationen) --
          leer, wenn keine konfiguriert ist oder mehrere unterschiedliche Kostenstellen zutreffen.
          Absenzarten ohne Ferien-/Krankheits-Flag (z. B. Militärdienst, unbezahlter Urlaub) werden
          einzeln exportiert, siehe Tabelle oben -- kein gemeinsamer "Sonstiges"-Topf mehr. Bei
          gerichtlicher Skala (Einstellungen → Tenant) werden Krankheitstage ausserdem automatisch in
          "mit Lohnfortzahlung" und "Anspruch erschöpft" aufgeteilt, je nach bereits verbrauchtem
          Anspruch im laufenden Dienstjahr.
        </p>
        <div className="panel-form-row">
          <label>
            Monat
            <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
          </label>
          <button type="button" className="btn-primary" onClick={handleExport} disabled={exporting}>
            {exporting ? "Wird erstellt …" : "CSV herunterladen"}
          </button>
        </div>
        {warnings && warnings.length > 0 && (
          <div className="corridor-callout">
            <p>
              <strong>Ohne Lohnart-Code, nicht im Export enthalten:</strong> {warnings.join("; ")}
            </p>
          </div>
        )}
        {warnings && warnings.length === 0 && (
          <p className="panel-hint">Alle Kategorien mit Daten in diesem Monat sind zugeordnet.</p>
        )}
      </div>
    </div>
  );
}

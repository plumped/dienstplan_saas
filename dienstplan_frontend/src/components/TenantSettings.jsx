import { useEffect, useState } from "react";
import { api } from "../api.js";

const FIELD_GROUPS = [
  {
    title: "Ruhezeit & Höchstarbeitszeit",
    fields: [
      {
        key: "minimum_rest_hours",
        label: "Mindestruhezeit (h)",
        hint: "Zwischen zwei Schichten. Art. 15a ArG: 11h Minimum.",
      },
      {
        key: "maximum_weekly_hours",
        label: "Wöchentliche Höchstarbeitszeit (h)",
        hint: "Art. 9 ArG: 45h für Gesundheits-/Büropersonal, 50h für übrige Betriebe.",
      },
      {
        key: "maximum_daily_span_hours",
        label: "Maximale Tagesspanne (h)",
        hint: "Arbeitsbeginn bis Arbeitsende inkl. Pausen. Art. 10 Abs. 3 ArG.",
      },
    ],
  },
  {
    title: "Zeiterfassung",
    fields: [
      {
        key: "time_record_deviation_tolerance_minutes",
        label: "Toleranz Soll/Ist-Abweichung (Min.)",
        hint: "Ab dieser Abweichung verlangt die Ist-Zeiterfassung eine Begründung.",
      },
    ],
  },
  {
    title: "Überzeit",
    fields: [
      {
        key: "standard_weekly_hours",
        label: "Normalarbeitszeit 100%-Pensum (h)",
        hint: "Soll-Basis für die Überzeitberechnung (Art. 13 ArG), nicht die gesetzliche Höchstgrenze oben.",
      },
      {
        key: "overtime_surcharge_pct",
        label: "Überzeitzuschlag (%)",
        hint: "Art. 13 Abs. 1 ArG: i. d. R. 25%, GAV-abhängig anpassbar.",
      },
    ],
  },
  {
    title: "Ferien",
    fields: [
      {
        key: "default_vacation_days_per_year",
        label: "Ferienanspruch (Arbeitstage/Jahr)",
        hint: "Art. 329a Abs. 1 OR: 20 Tage Minimum für Erwachsene.",
      },
    ],
  },
  {
    title: "Nacht- & Sonntagsarbeit",
    fields: [
      {
        key: "night_work_surcharge_pct",
        label: "Zeitgutschrift Nachtarbeit (%)",
        hint: "Art. 17b Abs. 1 ArG: i. d. R. 10% bei regelmässiger Nachtarbeit.",
      },
      {
        key: "night_work_regular_threshold_nights",
        label: "Schwelle „regelmässige“ Nachtarbeit (Nächte/Jahr)",
        hint: "ArGV 1 Art. 31 -- ab dieser Anzahl Nächte pro Kalenderjahr gilt Nachtarbeit als regelmässig.",
      },
      {
        key: "night_work_permit_confirmed",
        label: "Bewilligung für regelmässige Nachtarbeit liegt vor",
        hint: "Art. 17 ArG. Ohne Bestätigung erscheint ein Warnhinweis im Saldo, sobald jemand regelmässig nachts arbeitet.",
        type: "checkbox",
      },
      {
        key: "sunday_work_surcharge_pct",
        label: "Lohnzuschlag Sonntagsarbeit (%)",
        hint: "Art. 19 Abs. 3 ArG: i. d. R. 50%. Für Dauerbetriebe können Ausnahmen gelten -- ggf. auf 0 setzen.",
      },
    ],
  },
];

// Block 2.14: bislang waren diese Werte nur im Django-Admin editierbar --
// ein Planer erreicht den ohnehin nicht (separates Berechtigungssystem via
// User.is_staff, siehe README), und selbst ein Kunden-Admin sollte ihn nie
// bekommen (siehe README, Architektur-Abschnitt). Schreiben ist hier
// zusätzlich serverseitig Admin-only (core.permissions.IsTenantAdmin,
// strenger als IsTenantManager) -- SettingsPanel blendet das Modul für
// Planer entsprechend erst gar nicht ein (siehe dort).
export default function TenantSettings({ onError }) {
  const [tenant, setTenant] = useState(null);
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});

  useEffect(() => {
    let cancelled = false;
    api
      .getTenant()
      .then((data) => {
        if (cancelled) return;
        setTenant(data);
        setForm(data);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleChange(key, type) {
    return (event) => {
      const value = type === "checkbox" ? event.target.checked : event.target.value;
      setForm((prev) => ({ ...prev, [key]: value }));
      setSaved(false);
      setFieldErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
    };
  }

  function fieldError(key) {
    const message = fieldErrors[key]?.[0];
    return message ? <span className="field-error">{message}</span> : null;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setFieldErrors({});
    try {
      const payload = {};
      for (const group of FIELD_GROUPS) {
        for (const field of group.fields) {
          payload[field.key] =
            field.type === "checkbox" ? Boolean(form[field.key]) : Number(form[field.key]);
        }
      }
      const updated = await api.updateTenant(payload);
      setTenant(updated);
      setForm(updated);
      setSaved(true);
    } catch (e) {
      if (e.fields && typeof e.fields === "object") setFieldErrors(e.fields);
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading || !form) return <p className="loading-state">Einstellungen werden geladen …</p>;

  return (
    <form className="panel-form" onSubmit={handleSubmit}>
      <h2>Regel-Engine & Zuschläge -- {tenant.name}</h2>
      <p className="panel-hint">
        Diese Werte gelten für den gesamten Tenant und steuern die Regel-Engine sowie Saldo-/
        Zuschlagsberechnungen. Standardwerte, kein Rechtsrat -- im Zweifel arbeitsrechtlich prüfen
        lassen.
      </p>

      {FIELD_GROUPS.map((group) => (
        <fieldset key={group.title} className="panel-form-group">
          <h3>{group.title}</h3>
          {group.fields.map((field) =>
            field.type === "checkbox" ? (
              <div key={field.key}>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={Boolean(form[field.key])}
                    onChange={handleChange(field.key, "checkbox")}
                  />
                  {field.label}
                </label>
                <span className="panel-hint">{field.hint}</span>
                {fieldError(field.key)}
              </div>
            ) : (
              <label key={field.key}>
                {field.label}
                <input
                  type="number"
                  min="0"
                  value={form[field.key]}
                  onChange={handleChange(field.key)}
                  required
                />
                <span className="panel-hint">{field.hint}</span>
                {fieldError(field.key)}
              </label>
            )
          )}
        </fieldset>
      ))}

      <button type="submit" disabled={saving}>
        {saving ? "Speichert …" : saved ? "Gespeichert ✓" : "Speichern"}
      </button>
    </form>
  );
}

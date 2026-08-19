import { useEffect, useState } from "react";
import { api } from "../api.js";
import { SWISS_CANTONS } from "../cantons.js";
import { IconPlus, IconScale, IconTrash } from "../icons.jsx";

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
      {
        key: "flextime_corridor_hours",
        label: "Gleitzeit-Bandbreite (h)",
        hint: "Nutzer-Feedback: \"bei uns gilt Gleitzeit, nur angeordnete Überstunden werden effektiv "
          + "abgerechnet\". Der laufende Saldo darf sich innerhalb dieser Bandbreite frei bewegen, ohne "
          + "Zuschlag -- erst der Anteil darüber hinaus wird in der Monatsauswertung zur Bestätigung "
          + "vorgeschlagen.",
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
        key: "occasional_night_work_surcharge_pct",
        label: "Lohnzuschlag GELEGENTLICHE Nachtarbeit (%)",
        hint: "Art. 17b Abs. 2 ArG: i. d. R. 25%. Gilt für Nachtstunden unterhalb der Regelmässigkeits-Schwelle "
          + "oben (Geld statt Zeitgutschrift) -- die App liefert nur Stunden + Prozentsatz, keinen CHF-Betrag.",
      },
      {
        key: "sunday_work_surcharge_pct",
        label: "Lohnzuschlag Sonntagsarbeit (%)",
        hint: "Art. 19 Abs. 3 ArG: i. d. R. 50%. Für Dauerbetriebe können Ausnahmen gelten -- ggf. auf 0 setzen.",
      },
    ],
  },
  {
    title: "Fairness-Punktesystem (unpopuläre Schichten)",
    fields: [
      {
        key: "sunday_shift_bonus_points_per_hour",
        label: "Punkte je Sonntagsstunde",
        hint: "Rein interne Transparenz-/Planungsgrösse (Mitarbeitendenliste, Admin/Planer), kein "
          + "Lohnbestandteil. Wunschdienste zählen nicht als Belastung.",
        step: "0.1",
      },
      {
        key: "night_shift_bonus_points_per_hour",
        label: "Punkte je Nachtstunde",
        hint: "Gleiches Prinzip wie oben, für Nachtstunden statt Sonntagsstunden.",
        step: "0.1",
      },
    ],
  },
  {
    title: "Lohnfortzahlung bei Krankheit (Art. 324a OR)",
    fields: [
      {
        key: "sick_pay_model",
        label: "Modell",
        hint: "Viele Betriebe versichern die Lohnfortzahlungspflicht über eine "
          + "Krankentaggeldversicherung (typischerweise 80% Lohn ab Wartefrist) statt sich auf die "
          + "gerichtliche Skala zu verlassen -- die Police ersetzt dann die Skala komplett.",
        type: "select",
        options: [
          ["scale", "Gerichtliche Skala (Basel/Bern/Zürich)"],
          ["daily_allowance_insurance", "Krankentaggeldversicherung"],
        ],
      },
      {
        key: "sick_pay_scale",
        label: "Skala",
        hint: "Nur relevant bei Modell \"Gerichtliche Skala\". Näherungswerte -- vor "
          + "Produktivnutzung mit einer Rechts-/Treuhandstelle verifizieren.",
        type: "select",
        options: [
          ["basel", "Basler Skala"],
          ["bern", "Berner Skala"],
          ["zuerich", "Zürcher Skala"],
        ],
      },
      {
        key: "sick_pay_waiting_days",
        label: "Wartefrist Krankentaggeldversicherung (Tage)",
        hint: "Nur relevant bei Modell \"Krankentaggeldversicherung\": Tage aus der Police, bis das "
          + "Taggeld einsetzt -- vorher zahlt der Betrieb selbst weiter.",
      },
    ],
  },
  {
    title: "Feiertagskalender (Arbeitszeitmodell)",
    fields: [
      {
        key: "canton",
        label: "Kanton",
        hint: "Grundlage für den automatischen Feiertagskalender im Saldo (Jahressoll/laufender Saldo). Leer = kein Feiertagsabzug. Lokale Sonderfälle unten als Ausnahme pflegen.",
        type: "select",
        options: [["", "-- kein Kanton --"], ...SWISS_CANTONS],
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
          if (field.type === "checkbox") payload[field.key] = Boolean(form[field.key]);
          else if (field.type === "select") payload[field.key] = form[field.key];
          else payload[field.key] = Number(form[field.key]);
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
      <div className="settings-form-header">
        <span className="settings-form-icon">
          <IconScale />
        </span>
        <div>
          <h2>Regel-Engine & Zuschläge</h2>
          <p className="settings-form-subtitle">{tenant.name} -- Ruhezeit, Höchstarbeitszeit, Zuschläge, Ferienanspruch.</p>
        </div>
      </div>
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
                  <span className="pretty-checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(form[field.key])}
                      onChange={handleChange(field.key, "checkbox")}
                    />
                    <span className="pretty-checkbox-box" aria-hidden="true" />
                  </span>
                  {field.label}
                </label>
                <span className="panel-hint">{field.hint}</span>
                {fieldError(field.key)}
              </div>
            ) : field.type === "select" ? (
              <label key={field.key}>
                {field.label}
                <select value={form[field.key] ?? ""} onChange={handleChange(field.key)}>
                  {field.options.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <span className="panel-hint">{field.hint}</span>
                {fieldError(field.key)}
              </label>
            ) : (
              <label key={field.key}>
                {field.label}
                <input
                  type="number"
                  min="0"
                  step={field.step ?? "1"}
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

      <HolidayOverridesEditor onError={onError} />

      <button type="submit" className="btn-primary" disabled={saving}>
        {saving ? "Speichert …" : saved ? "Gespeichert ✓" : "Speichern"}
      </button>
    </form>
  );
}

// Eigenständige Ausnahme-Liste zum kantonalen Kalender (Gemeinde-Sonderfälle
// wie Patrozinien in GR/LU/SZ/SO) -- bewusst NICHT Teil des generischen
// FIELD_GROUPS-Formulars oben, weil es eine eigene Liste mit Add/Delete ist,
// kein einzelnes Tenant-Feld. Eigener State/Save-Zyklus, unabhängig vom
// umgebenden <form onSubmit>, deshalb type="button" für "Hinzufügen".
function HolidayOverridesEditor({ onError }) {
  const [overrides, setOverrides] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newEntry, setNewEntry] = useState({ date: "", name: "", kind: "add" });
  const [adding, setAdding] = useState(false);

  function load() {
    api
      .getTenantHolidayOverrides()
      .then((data) => setOverrides(data.results ?? data))
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleAdd(event) {
    event.preventDefault();
    if (!newEntry.date) {
      onError("Datum ist ein Pflichtfeld.");
      return;
    }
    setAdding(true);
    try {
      await api.createTenantHolidayOverride(newEntry);
      setNewEntry({ date: "", name: "", kind: "add" });
      load();
    } catch (e) {
      onError(e.message);
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(id) {
    try {
      await api.deleteTenantHolidayOverride(id);
      setOverrides((prev) => prev.filter((o) => o.id !== id));
    } catch (e) {
      onError(e.message);
    }
  }

  return (
    <fieldset className="panel-form-group">
      <h3>Lokale Feiertags-Ausnahmen</h3>
      <p className="panel-hint">
        Ergänzt oder überschreibt den kantonalen Kalender oben für Gemeinde-Sonderfälle (z. B.
        Patrozinien in GR/LU/SZ/SO), die die automatische Zuordnung nicht kennt.
      </p>
      {loading ? (
        <p className="loading-state">Lädt …</p>
      ) : overrides.length === 0 ? (
        <p className="panel-hint">Keine Ausnahmen hinterlegt.</p>
      ) : (
        <ul className="entry-list">
          {overrides.map((o) => (
            <li key={o.id} className="entry-list-item">
              <span>
                {o.date} -- {o.kind === "add" ? "zusätzlicher Feiertag" : "kein Feiertag"}
                {o.name ? ` (${o.name})` : ""}
              </span>
              <button type="button" className="icon-btn icon-btn-danger" onClick={() => handleDelete(o.id)}>
                <IconTrash width={14} height={14} />
                Entfernen
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="panel-form-row">
        <label>
          Datum
          <input
            type="date"
            value={newEntry.date}
            onChange={(e) => setNewEntry((prev) => ({ ...prev, date: e.target.value }))}
          />
        </label>
        <label>
          Bezeichnung (optional)
          <input
            type="text"
            value={newEntry.name}
            onChange={(e) => setNewEntry((prev) => ({ ...prev, name: e.target.value }))}
          />
        </label>
        <label>
          Art
          <select
            value={newEntry.kind}
            onChange={(e) => setNewEntry((prev) => ({ ...prev, kind: e.target.value }))}
          >
            <option value="add">Zusätzlicher Feiertag</option>
            <option value="remove">Kein Feiertag (Ausnahme)</option>
          </select>
        </label>
      </div>
      <button type="button" className="btn-primary" onClick={handleAdd} disabled={adding}>
        <IconPlus width={15} height={15} />
        {adding ? "Speichert …" : "Hinzufügen"}
      </button>
    </fieldset>
  );
}

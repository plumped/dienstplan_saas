import { useState } from "react";
import { api } from "../api.js";

// Schritt 3 des OnboardingWizard: hartcodierte Presets zur Auswahl, statt
// die volle TimeTemplateSettings.jsx-Formularlogik (Icon/Farbe/Segmente/
// Kategorie) hier nachzubauen -- "Übernehmen" ruft api.createTimeTemplate()
// sequenziell für die ausgewählten Presets, kein neuer Backend-Endpoint.
const PRESETS = [
  { key: "fruehdienst", name: "Frühdienst", start_time: "07:00", end_time: "15:00", break_minutes: 30 },
  { key: "spaetdienst", name: "Spätdienst", start_time: "13:00", end_time: "21:00", break_minutes: 30 },
  { key: "tagdienst", name: "Tagdienst", start_time: "08:00", end_time: "17:00", break_minutes: 60 },
  { key: "nachtdienst", name: "Nachtdienst", start_time: "21:00", end_time: "07:00", break_minutes: 0 },
];

export default function OnboardingStepShiftTypes({ nodes, timeTemplates, onTimeTemplatesChange, onNext, onError }) {
  const [selected, setSelected] = useState({});
  const [nodeId, setNodeId] = useState(nodes[0]?.id ?? "");
  const [saving, setSaving] = useState(false);

  function toggle(key) {
    setSelected((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  async function handleApply() {
    const chosen = PRESETS.filter((p) => selected[p.key]);
    if (!chosen.length || !nodeId) return;
    setSaving(true);
    try {
      const created = [];
      for (const preset of chosen) {
        const template = await api.createTimeTemplate({
          name: preset.name,
          node: Number(nodeId),
          start_time: preset.start_time,
          end_time: preset.end_time,
          break_minutes: preset.break_minutes,
        });
        created.push(template);
      }
      onTimeTemplatesChange([...timeTemplates, ...created]);
      setSelected({});
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel-form onboarding-step-body">
      <h2>Schichttypen</h2>
      <p className="panel-hint">
        {timeTemplates.length > 0
          ? `${timeTemplates.length} Schichttypen sind bereits vorhanden (z. B. aus den Beispieldaten). `
          : ""}
        Wählen Sie weitere gängige Schichttypen aus und übernehmen Sie sie für eine Station.
      </p>

      <div className="onboarding-preset-list">
        {PRESETS.map((preset) => (
          <label key={preset.key} className="checkbox-row">
            <input type="checkbox" checked={!!selected[preset.key]} onChange={() => toggle(preset.key)} />
            {preset.name} ({preset.start_time}–{preset.end_time})
          </label>
        ))}
      </div>

      <label>
        Station
        <select value={nodeId} onChange={(e) => setNodeId(e.target.value)}>
          {nodes.map((node) => (
            <option key={node.id} value={node.id}>
              {node.name}
            </option>
          ))}
        </select>
      </label>

      <div className="panel-form-row">
        <button
          type="button"
          className="btn-ghost"
          onClick={handleApply}
          disabled={saving || !nodeId || !Object.values(selected).some(Boolean)}
        >
          {saving ? "Wird übernommen …" : "Übernehmen"}
        </button>
      </div>

      <button type="button" className="btn-primary" onClick={onNext}>
        Weiter
      </button>
    </div>
  );
}

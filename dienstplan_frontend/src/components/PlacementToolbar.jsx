import { chipGlyph } from "../chipGlyph.js";

const MODES = [
  { value: "ganz", label: "Ganz" },
  { value: "links", label: "Links" },
  { value: "rechts", label: "Rechts" },
  { value: "pikett", label: "Pikett" },
];

function isArmed(armedTool, kind, id) {
  return Boolean(armedTool) && armedTool.kind === kind && armedTool.id === id;
}

// Nutzer-Feedback (2026-08, "genau wie Polypoint"): ersetzt das bisherige
// Klick-auf-Zelle-Dropdown komplett. Werkzeug wählen (Modus + Icon), dann
// auf eine Zielzelle klicken -- sofortige Zuweisung (siehe
// PlanGrid.jsx: handleCellClick). Immer sichtbar für canManage, unabhängig
// von der bestehenden Mehrfachauswahl-Stempelleiste (bulk, viele Tage auf
// einmal), die als eigenständiges Feature unverändert bestehen bleibt.
export default function PlacementToolbar({
  placementMode,
  onPlacementModeChange,
  armedTool,
  onArmTool,
  regularTemplates,
  specialTemplates,
  absenceTypes,
}) {
  const isPikett = placementMode === "pikett";

  return (
    <div className="placement-toolbar">
      <span className="placement-mode-group" role="radiogroup" aria-label="Platzierungsmodus">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={placementMode === m.value}
            className={`placement-mode-btn${placementMode === m.value ? " is-active" : ""}`}
            onClick={() => onPlacementModeChange(m.value)}
          >
            {m.label}
          </button>
        ))}
      </span>
      <span className="placement-palette">
        {isPikett ? (
          specialTemplates.length === 0 ? (
            <span className="placement-hint">Keine Spezialitäten angelegt.</span>
          ) : (
            specialTemplates.map((t) => (
              <button
                key={`t-${t.id}`}
                type="button"
                className={`stamp-chip${isArmed(armedTool, "template", t.id) ? " is-armed" : ""}`}
                style={{ "--chip-color": t.color }}
                title={t.name}
                onClick={() => onArmTool({ kind: "template", id: t.id })}
              >
                {chipGlyph(t)}
              </button>
            ))
          )
        ) : (
          <>
            {regularTemplates.map((t) => (
              <button
                key={`t-${t.id}`}
                type="button"
                className={`stamp-chip${isArmed(armedTool, "template", t.id) ? " is-armed" : ""}`}
                style={{ "--chip-color": t.color }}
                title={t.name}
                onClick={() => onArmTool({ kind: "template", id: t.id })}
              >
                {chipGlyph(t)}
              </button>
            ))}
            {absenceTypes.map((t) => (
              <button
                key={`a-${t.id}`}
                type="button"
                className={`stamp-chip stamp-chip--absence${isArmed(armedTool, "absence", t.id) ? " is-armed" : ""}`}
                style={{ "--chip-color": t.color }}
                title={t.name}
                onClick={() => onArmTool({ kind: "absence", id: t.id })}
              >
                {chipGlyph(t)}
              </button>
            ))}
            <button
              type="button"
              className={`stamp-chip stamp-chip--empty${isArmed(armedTool, "empty", null) ? " is-armed" : ""}`}
              title="Radiergummi -- entfernt die Zuweisung beim Klick auf eine Zelle"
              onClick={() => onArmTool({ kind: "empty", id: null })}
            >
              ×
            </button>
          </>
        )}
      </span>
    </div>
  );
}

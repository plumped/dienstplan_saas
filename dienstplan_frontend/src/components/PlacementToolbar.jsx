import { chipGlyph } from "../chipGlyph.js";

const MODES = [
  { value: "ganz", label: "Ganz" },
  { value: "links", label: "Links" },
  { value: "rechts", label: "Rechts" },
  { value: "pikett", label: "Pikett" },
];

// Workflow-Redesign (2026-08, Nutzer-Feedback: "erst Tage markieren, dann
// beplanen -- nicht umgekehrt"): ersetzt das frühere Zwei-Werkzeuge-Modell
// (diese Toolbar zum sofortigen Platzieren per Klick auf eine Zelle, plus
// eine separate Mehrfachauswahl-Stempelleiste mit genau umgekehrter
// Reihenfolge). Jetzt EIN Modell: Klicken/Ziehen auf eine Zelle markiert
// immer (PlanGrid.jsx: startMark/continueMark), diese Toolbar ist der
// Stempel für die aktuelle Markierung -- ein Klick auf ein Dienst-Icon
// wendet es sofort auf ALLE markierten Tage an (onApplyTool), egal ob das
// einer oder zwanzig sind. Deaktiviert (disabled), solange nichts markiert
// ist, mit Hinweistext + "Auswahl aufheben"-Kontrolle.
export default function PlacementToolbar({
  placementMode,
  onPlacementModeChange,
  regularTemplates,
  specialTemplates,
  absenceTypes,
  markedCount,
  onApplyTool,
  onClearMarked,
}) {
  const isPikett = placementMode === "pikett";
  const disabled = markedCount === 0;

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
                className="stamp-chip"
                style={{ "--chip-color": t.color }}
                title={`${t.name} auf alle markierten Tage anwenden`}
                disabled={disabled}
                onClick={() => onApplyTool({ kind: "template", id: t.id })}
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
                className="stamp-chip"
                style={{ "--chip-color": t.color }}
                title={`${t.name} auf alle markierten Tage anwenden`}
                disabled={disabled}
                onClick={() => onApplyTool({ kind: "template", id: t.id })}
              >
                {chipGlyph(t)}
              </button>
            ))}
            {absenceTypes.map((t) => (
              <button
                key={`a-${t.id}`}
                type="button"
                className="stamp-chip stamp-chip--absence"
                style={{ "--chip-color": t.color }}
                title={`${t.name} für alle markierten Tage eintragen`}
                disabled={disabled}
                onClick={() => onApplyTool({ kind: "absence", id: t.id })}
              >
                {chipGlyph(t)}
              </button>
            ))}
            <button
              type="button"
              className="stamp-chip stamp-chip--empty"
              title="Radiergummi -- entfernt die Zuweisung der markierten Tage"
              disabled={disabled}
              onClick={() => onApplyTool({ kind: "empty", id: null })}
            >
              ×
            </button>
          </>
        )}
      </span>
      <span className="placement-selection">
        <span className="multi-select-hint">
          {markedCount === 0 ? "Tage anklicken oder ziehen, um sie zu markieren." : `${markedCount} markiert`}
        </span>
        {markedCount > 0 && (
          <button type="button" className="btn-ghost" onClick={onClearMarked}>
            Auswahl aufheben
          </button>
        )}
      </span>
    </div>
  );
}

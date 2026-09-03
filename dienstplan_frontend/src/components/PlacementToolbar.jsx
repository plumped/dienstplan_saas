import { chipGlyph } from "../chipGlyph.js";

const MODES = [
  { value: "full", label: "Alles" },
  { value: "top", label: "Oben" },
  { value: "bottom", label: "Unten" },
  { value: "special", label: "Spezialdienste" },
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
  const isPikett = placementMode === "special";
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
            <>
              {specialTemplates.map((t) => (
                <button
                  key={`t-${t.id}`}
                  type="button"
                  className="stamp-chip"
                  style={{ "--chip-color": t.color }}
                  title={`${t.name} auf allen markierten Tagen an-/ausschalten`}
                  disabled={disabled}
                  onClick={() => onApplyTool({ kind: "template", id: t.id })}
                >
                  {chipGlyph(t)}
                </button>
              ))}
              {/* Bugfix (Nutzer-Feedback): der Radiergummi fehlte im
                  Pikett-Modus komplett -- es gab keinen Weg, eine
                  Spezialität über die Toolbar wieder zu entfernen (nur
                  einzeln über das Popover in SpecialBadge.jsx). Entfernt
                  hier ALLE Spezialitäten der markierten Zellen auf einmal. */}
              <button
                type="button"
                className="stamp-chip stamp-chip--empty"
                title="Radiergummi -- entfernt alle Spezialitäten der markierten Tage"
                disabled={disabled}
                onClick={() => onApplyTool({ kind: "empty", id: null })}
              >
                ×
              </button>
            </>
          )
        ) : (
          <>
            {/* Nutzer-Feedback (2026-08): "Abwesenheiten klar trennen von
                normalen Diensten -- die Stempel in der oberen Zeile" -- vorher
                eine einzige, ununterschiedene Reihe aus Dienst- UND
                Absenz-Icons. Jetzt zwei sichtbar getrennte Gruppen (eigener
                Rahmen je Gruppe + vertikaler Trenner dazwischen), damit auf
                einen Blick klar ist, welche Icons einen Dienst eintragen und
                welche eine Abwesenheit. */}
            <span className="placement-palette-group">
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
            </span>
            <span className="placement-palette-divider" aria-hidden="true" />
            <span className="placement-palette-group">
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
            </span>
            <span className="placement-palette-divider" aria-hidden="true" />
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

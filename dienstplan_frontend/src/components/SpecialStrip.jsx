import { chipGlyph } from "../chipGlyph.js";

// Nutzer-Feedback (2026-08, Nachbesserung): Spezialitäten (z. B.
// Pikettdienst) sollen als eigene, dünne Chip-Zeile UNTER den Schicht-Slots
// stehen -- analog zu PEP. Vorher steckte das in einem Zähler-Badge in der
// ShiftCell-Ecke, das bei einem Split-Shift (Vormittag+Nachmittag, zwei
// gestapelte Slots) faktisch unsichtbar wurde. Rein tagesbezogen (nicht pro
// Slot), daher als eigenständige Komponente auf Ebene der Tageszelle statt
// in ShiftCell.jsx.
//
// Icon-Toolbar (2026-08, "genau wie Polypoint"): das eigene "+"-Popover zum
// Hinzufügen entfällt -- eine Spezialität wird jetzt zentral über die
// Placement-Toolbar im Pikett-Modus angelegt (siehe PlanGrid.jsx:
// handleCellClick). Diese Komponente ist damit reine Anzeige + Entfernen.
export default function SpecialStrip({ specialAssignments, templates, canEdit, onRemove }) {
  if (specialAssignments.length === 0) return null;

  return (
    <div className="special-strip">
      {specialAssignments.map((a) => {
        const t = templates.find((tt) => tt.id === a.template);
        return (
          <span key={a.id} className="special-chip" style={{ "--chip-color": t?.color }} title={t?.name}>
            {t ? chipGlyph(t) : "?"}
            {canEdit && (
              <button
                type="button"
                className="special-chip-remove"
                title={`${t?.name ?? "Spezialität"} entfernen`}
                onClick={() => onRemove(a.id)}
              >
                ×
              </button>
            )}
          </span>
        );
      })}
    </div>
  );
}

import { useRef, useState } from "react";
import FloatingPopover from "./FloatingPopover.jsx";

// Nutzer-Feedback (2026-08, Nachbesserung): Spezialitäten (z. B.
// Pikettdienst) sollen als eigene, dünne Chip-Zeile UNTER den Schicht-Slots
// stehen -- analog zu PEP. Vorher steckte das in einem Zähler-Badge in der
// ShiftCell-Ecke, das bei einem Split-Shift (Vormittag+Nachmittag, zwei
// gestapelte Slots) faktisch unsichtbar wurde. Rein tagesbezogen (nicht pro
// Slot), daher als eigenständige Komponente auf Ebene der Tageszelle statt
// in ShiftCell.jsx.
export default function SpecialStrip({ specialAssignments, templates, assignableTemplates, canEdit, onAdd, onRemove }) {
  const [adding, setAdding] = useState(false);
  const addBtnRef = useRef(null);
  const addableTemplates = assignableTemplates.filter(
    (t) => !specialAssignments.some((a) => a.template === t.id)
  );

  if (specialAssignments.length === 0 && (!canEdit || addableTemplates.length === 0)) return null;

  return (
    <div className="special-strip">
      {specialAssignments.map((a) => {
        const t = templates.find((tt) => tt.id === a.template);
        return (
          <span key={a.id} className="special-chip" style={{ "--chip-color": t?.color }} title={t?.name}>
            {t?.name.slice(0, 3).toUpperCase() ?? "?"}
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
      {canEdit && addableTemplates.length > 0 && (
        <>
          <button
            ref={addBtnRef}
            type="button"
            className="special-chip special-chip--add"
            title="Spezialität hinzufügen"
            onClick={() => setAdding((v) => !v)}
          >
            +
          </button>
          {adding && (
            <FloatingPopover anchorRef={addBtnRef} onClose={() => setAdding(false)} className="special-popover">
              <div className="special-popover-add">
                {addableTemplates.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="stamp-chip"
                    style={{ "--chip-color": t.color }}
                    onClick={() => {
                      onAdd(t.id);
                      setAdding(false);
                    }}
                  >
                    + {t.name}
                  </button>
                ))}
              </div>
            </FloatingPopover>
          )}
        </>
      )}
    </div>
  );
}

import { useRef, useState } from "react";
import FloatingPopover from "./FloatingPopover.jsx";

// Redesign (2026-08, "hand aufs Herz"-Nachbesserung): ersetzt die frühere
// SpecialStrip.jsx (eigene, dünne Chip-Zeile UNTER den Dienst-Slots) --
// die machte Tageszellen je nach Tag unterschiedlich hoch, was eine ganze
// Kaskade von Layout-Bugs nach sich zog, und wirkte bei den meist 0-1
// Spezialitäten pro Tag unnötig schwer. Jetzt ein kleiner, farbiger Punkt
// in der Zellecke (wie ein Benachrichtigungs-Badge) -- alle Tageszellen
// einer Zeile bleiben dadurch garantiert gleich hoch. Details/Entfernen
// per Klick im Popover, gleiches Muster wie DayStaffingBadge in
// PlanGrid.jsx.
export default function SpecialBadge({ specialAssignments, templates, canEdit, onRemove }) {
  const [open, setOpen] = useState(false);
  const badgeRef = useRef(null);
  if (specialAssignments.length === 0) return null;

  const first = templates.find((t) => t.id === specialAssignments[0].template);
  const label =
    specialAssignments.length === 1
      ? (first?.name ?? "Spezialität")
      : `${specialAssignments.length} Spezialitäten`;

  return (
    <>
      <button
        ref={badgeRef}
        type="button"
        className="special-day-badge"
        style={{ "--chip-color": first?.color ?? "var(--primary)" }}
        title={label}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="visually-hidden">{label} -- Details anzeigen</span>
      </button>
      {open && (
        <FloatingPopover anchorRef={badgeRef} onClose={() => setOpen(false)} className="special-day-popover">
          <ul className="special-day-popover-list">
            {specialAssignments.map((a) => {
              const t = templates.find((tt) => tt.id === a.template);
              return (
                <li key={a.id} className="special-day-popover-item">
                  <span>{t?.name ?? "Spezialität"}</span>
                  {canEdit && (
                    <button
                      type="button"
                      className="special-day-popover-remove"
                      title={`${t?.name ?? "Spezialität"} entfernen`}
                      onClick={() => onRemove(a.id)}
                    >
                      ×
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </FloatingPopover>
      )}
    </>
  );
}

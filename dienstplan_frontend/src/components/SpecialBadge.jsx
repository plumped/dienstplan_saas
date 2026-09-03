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
//
// Nutzer-Feedback (2026-08): bei mehr als einer Spezialität zeigte der
// EINE Punkt nur die Farbe der ersten und einen Zähler ("2 Spezialitäten")
// -- auf einen Blick war weder erkennbar WIE VIELE noch WELCHE (Farbe)
// ohne das Popover zu öffnen. Jetzt ein kleiner Punkt PRO Spezialität,
// nebeneinander in der jeweils eigenen Farbe -- ein Klick auf irgendeinen
// öffnet weiterhin dasselbe Popover mit der vollständigen Liste + Entfernen.
export default function SpecialBadge({ specialAssignments, templates, canEdit, onRemove }) {
  const [open, setOpen] = useState(false);
  const badgeRef = useRef(null);
  if (specialAssignments.length === 0) return null;

  const named = specialAssignments.map((a) => ({
    assignment: a,
    template: templates.find((t) => t.id === a.template),
  }));
  const label =
    named.length === 1
      ? (named[0].template?.name ?? "Spezialität")
      : named.map(({ template }) => template?.name ?? "Spezialität").join(", ");

  return (
    <>
      <span ref={badgeRef} className="special-day-badges" title={label}>
        {named.map(({ assignment, template }) => (
          <button
            key={assignment.id}
            type="button"
            className="special-day-dot"
            style={{ "--chip-color": template?.color ?? "var(--primary)" }}
            onClick={() => setOpen((v) => !v)}
          >
            <span className="visually-hidden">{label} -- Details anzeigen</span>
          </button>
        ))}
      </span>
      {open && (
        <FloatingPopover anchorRef={badgeRef} onClose={() => setOpen(false)} className="special-day-popover">
          <ul className="special-day-popover-list">
            {named.map(({ assignment: a, template: t }) => (
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
            ))}
          </ul>
        </FloatingPopover>
      )}
    </>
  );
}

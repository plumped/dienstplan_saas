import { chipGlyph } from "../chipGlyph.js";

// README Block 2 Punkt 19 (Automatisierte Planung): reiner Vorschlags-Chip
// für generate_draft_plan()-Ergebnisse -- bewusst NICHT über ShiftCell.jsx
// (das ist für persistierte Zuweisungen mit echter ID, samt Drag/Tausch/
// Zeiterfassung/Wunsch/Absenz-Handling, das ein Entwurfsvorschlag nicht
// hat). Gestrichelter Rand (Schwester zu .stamp-chip--wish in styles.css)
// signalisiert "noch nicht gespeichert"; das "×" ist -- anders als der
// hover-only .btn-offer-trade -- immer sichtbar, weil Entfernen hier die
// zentrale Interaktion der Entwurfsansicht ist (Nutzer-Vorgabe: "Alle
// Vorschläge sichtbar, einzeln entfernbar").
export default function DraftShiftChip({ template, onRemove }) {
  if (!template) return null;
  return (
    <span className="cell-wrap">
      <span className="shift-chip-btn is-readonly is-draft">
        <span className="shift-chip shift-chip--draft" style={{ "--chip-color": template.color }}>
          {chipGlyph(template)}
        </span>
      </span>
      <button
        type="button"
        className="draft-chip-remove"
        title={`Vorschlag "${template.name}" entfernen`}
        onClick={onRemove}
      >
        ×<span className="visually-hidden"> Vorschlag "{template.name}" entfernen</span>
      </button>
    </span>
  );
}

// Kleine Variante für additive Vorschläge einer Spezialität (z. B.
// Pikettdienst), analog zum Eck-Punkt-Muster in SpecialBadge.jsx -- aber
// ohne Popover: bei den erwartungsgemäss wenigen Pikett-Vorschlägen pro Tag
// genügt ein direkter Klick zum Entfernen.
export function DraftSpecialDot({ template, onRemove }) {
  if (!template) return null;
  return (
    <button
      type="button"
      className="special-day-dot special-day-dot--draft"
      style={{ "--chip-color": template.color ?? "var(--primary)" }}
      title={`Vorschlag "${template.name}" entfernen`}
      onClick={onRemove}
    >
      <span className="visually-hidden">Vorschlag "{template.name}" entfernen</span>
    </button>
  );
}

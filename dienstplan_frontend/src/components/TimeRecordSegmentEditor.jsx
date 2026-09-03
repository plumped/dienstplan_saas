import { useState } from "react";
import { formatDeviation, formatHoursMinutes, toMinutes } from "../timeRecordSegments.js";

// Nur ein UI-Hinweis (Richtwert) -- massgeblich ist die tenant-konfigurierte
// Toleranz, die der Server bei tatsächlicher Überschreitung ohne Notiz mit
// einer klaren Fehlermeldung ablehnt (Tenant.time_record_deviation_tolerance_minutes).
const TOLERANCE_HINT_MINUTES = 15;

function toHHMM(value) {
  return value ? value.slice(0, 5) : "";
}

// Block 1.12: die Blockstruktur (Anzahl/Reihenfolge der Segmente) ist durch
// plannedSegments (aus dem TimeTemplate) fest vorgegeben -- der Mitarbeiter
// darf hier nur die Uhrzeiten je Block anpassen, kein Hinzufügen/Entfernen.
export default function TimeRecordSegmentEditor({
  plannedSegments,
  initialSegments,
  initialNote = "",
  onSave,
  onCancel,
  onDelete,
  canDelete = false,
  saving = false,
}) {
  const [values, setValues] = useState(() =>
    plannedSegments.map((seg, i) => ({
      actual_start: toHHMM(initialSegments?.[i]?.actual_start) || toHHMM(seg.start_time),
      actual_end: toHHMM(initialSegments?.[i]?.actual_end) || toHHMM(seg.end_time),
    }))
  );
  const [note, setNote] = useState(initialNote);

  function updateValue(i, field, value) {
    setValues((prev) => prev.map((v, idx) => (idx === i ? { ...v, [field]: value } : v)));
  }

  const complete = values.every((v) => v.actual_start && v.actual_end);

  let workedMinutes = 0;
  let breakMinutes = 0;
  let overlap = false;
  if (complete) {
    values.forEach((v, i) => {
      workedMinutes += toMinutes(v.actual_end) - toMinutes(v.actual_start);
      if (i < values.length - 1) {
        const gap = toMinutes(values[i + 1].actual_start) - toMinutes(v.actual_end);
        if (gap < 0) overlap = true;
        else breakMinutes += gap;
      }
    });
  }

  const startDeviation = complete ? toMinutes(values[0].actual_start) - toMinutes(toHHMM(plannedSegments[0].start_time)) : 0;
  const endDeviation = complete
    ? toMinutes(values[values.length - 1].actual_end) -
      toMinutes(toHHMM(plannedSegments[plannedSegments.length - 1].end_time))
    : 0;
  const needsNote =
    complete && (Math.abs(startDeviation) > TOLERANCE_HINT_MINUTES || Math.abs(endDeviation) > TOLERANCE_HINT_MINUTES);

  function handleSave() {
    if (!complete || overlap) return;
    onSave({
      segments: values.map((v, i) => ({ order: i, actual_start: v.actual_start, actual_end: v.actual_end })),
      note,
    });
  }

  return (
    <div className="segment-editor">
      {values.map((v, i) => (
        <div key={i}>
          <div className="segment-editor-row">
            {plannedSegments.length > 1 && <span className="segment-editor-index">{i + 1}</span>}
            <input
              type="time"
              value={v.actual_start}
              onChange={(e) => updateValue(i, "actual_start", e.target.value)}
            />
            <span className="segment-editor-sep">bis</span>
            <input type="time" value={v.actual_end} onChange={(e) => updateValue(i, "actual_end", e.target.value)} />
          </div>
          {i < values.length - 1 && (
            <div className="segment-editor-gap">
              {overlap && toMinutes(v.actual_end) > toMinutes(values[i + 1].actual_start) ? (
                <span className="segment-editor-gap-warn">Blöcke überlappen sich</span>
              ) : (
                <span className="segment-editor-gap-chip">
                  Pause {formatHoursMinutes(toMinutes(values[i + 1].actual_start) - toMinutes(v.actual_end))}
                </span>
              )}
            </div>
          )}
        </div>
      ))}

      {complete && !overlap && (
        <div className="segment-editor-summary">
          <span>
            Arbeitszeit <strong>{formatHoursMinutes(workedMinutes)}</strong>
          </span>
          {plannedSegments.length > 1 && (
            <span>
              Pause <strong>{formatHoursMinutes(breakMinutes)}</strong>
            </span>
          )}
          <span>
            Start <strong>{formatDeviation(startDeviation)}</strong>
          </span>
          <span>
            Ende <strong>{formatDeviation(endDeviation)}</strong>
          </span>
        </div>
      )}

      <input
        type="text"
        className="segment-editor-note"
        placeholder={needsNote ? "Begründung für die Abweichung *" : "Notiz / Begründung (optional)"}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />

      <div className="entry-actions">
        <button type="button" className="btn-primary" disabled={saving || !complete || overlap} onClick={handleSave}>
          Speichern
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Abbrechen
        </button>
        {canDelete && onDelete && (
          <button type="button" className="btn-ghost btn-danger-ghost" onClick={onDelete}>
            Löschen
          </button>
        )}
      </div>
    </div>
  );
}

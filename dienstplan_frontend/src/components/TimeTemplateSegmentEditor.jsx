import { formatHoursMinutes, toMinutes } from "../timeRecordSegments.js";

// Block 1.9/2.10: der Planer definiert hier die Blockstruktur eines
// Schichttyps (z. B. Vormittag/Nachmittag mit fixer Mittagspause
// dazwischen) -- im Gegensatz zum TimeRecordSegmentEditor (fixe
// Blockanzahl, nur Uhrzeiten anpassbar) darf hier frei hinzugefügt/entfernt
// werden, weil das die Struktur ist, die spätere Ist-Erfassungen übernehmen.
export default function TimeTemplateSegmentEditor({ segments, onChange }) {
  function updateSegment(i, field, value) {
    onChange(segments.map((s, idx) => (idx === i ? { ...s, [field]: value } : s)));
  }

  function addSegment() {
    const last = segments[segments.length - 1];
    const lastEndMinutes = last ? toMinutes(last.end_time) : 8 * 60;
    const pad = (n) => String(n).padStart(2, "0");
    const nextStart = Math.min(lastEndMinutes + 45, 23 * 60);
    const nextEnd = Math.min(nextStart + 4 * 60, 23 * 60 + 59);
    const toHHMM = (m) => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
    onChange([...segments, { start_time: toHHMM(nextStart), end_time: toHHMM(nextEnd) }]);
  }

  function removeSegment(i) {
    onChange(segments.filter((_, idx) => idx !== i));
  }

  if (!segments.length) {
    return (
      <div className="segment-editor">
        <p className="panel-hint">
          Kein Segment definiert -- der Schichttyp nutzt ein einzelnes Zeitfenster mit pauschaler
          Pause (siehe Feld "Pause (Min.)" oben).
        </p>
        <button type="button" className="add-segment" onClick={addSegment}>
          + Segment hinzufügen
        </button>
      </div>
    );
  }

  return (
    <div className="segment-editor">
      {segments.map((seg, i) => (
        <div key={i}>
          <div className="segment-editor-row">
            <span className="segment-editor-index">{i + 1}</span>
            <input
              type="time"
              value={seg.start_time}
              onChange={(e) => updateSegment(i, "start_time", e.target.value)}
            />
            <span className="segment-editor-sep">bis</span>
            <input
              type="time"
              value={seg.end_time}
              onChange={(e) => updateSegment(i, "end_time", e.target.value)}
            />
            <button
              type="button"
              className="remove-segment"
              onClick={() => removeSegment(i)}
              aria-label={`Segment ${i + 1} entfernen`}
            >
              Entfernen
            </button>
          </div>
          {i < segments.length - 1 && (
            <div className="segment-editor-gap">
              {toMinutes(seg.end_time) > toMinutes(segments[i + 1].start_time) ? (
                <span className="segment-editor-gap-warn">Blöcke überlappen sich</span>
              ) : (
                <span className="segment-editor-gap-chip">
                  Pause {formatHoursMinutes(toMinutes(segments[i + 1].start_time) - toMinutes(seg.end_time))}
                </span>
              )}
            </div>
          )}
        </div>
      ))}
      <button type="button" className="add-segment" onClick={addSegment}>
        + Segment hinzufügen
      </button>
    </div>
  );
}

// Block 1.12: gemeinsame Helfer für TimeRecordPanel.jsx (Tab "Zeiterfassung")
// und den Inline-Popover im Planblatt-Grid (Block 1.13, ShiftCell.jsx) --
// beide zeigen/bearbeiten dieselbe Segment-Struktur.

export function effectiveTemplateSegments(template) {
  if (!template) return [];
  if (template.segments && template.segments.length) {
    return [...template.segments].sort((a, b) => a.order - b.order);
  }
  return [{ order: 0, start_time: template.start_time, end_time: template.end_time }];
}

// Vorhandene Ist-Zeiten eines TimeRecord als geordnete Segmentliste, oder
// null, wenn noch nichts erfasst ist.
export function effectiveRecordSegments(record) {
  if (!record) return null;
  if (record.segments && record.segments.length) {
    return [...record.segments].sort((a, b) => a.order - b.order);
  }
  if (record.actual_start && record.actual_end) {
    return [{ order: 0, actual_start: record.actual_start, actual_end: record.actual_end }];
  }
  return null;
}

export function toMinutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function formatHoursMinutes(totalMinutes) {
  const sign = totalMinutes < 0 ? "-" : "";
  const abs = Math.round(Math.abs(totalMinutes));
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  return `${sign}${h}h ${String(m).padStart(2, "0")}min`;
}

export function formatDeviation(mins) {
  if (mins === 0) return "±0 Min.";
  return `${mins > 0 ? "+" : ""}${mins} Min.`;
}

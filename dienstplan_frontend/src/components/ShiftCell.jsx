import { useEffect, useRef, useState } from "react";

const DRAG_MIME = "application/x-dienstplan-shift";

export default function ShiftCell({
  templates,
  selectedTemplateId,
  templateInfo,
  onChange,
  employeeId,
  date,
  onMove,
}) {
  const [editing, setEditing] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const selectRef = useRef(null);

  useEffect(() => {
    if (editing) selectRef.current?.focus();
  }, [editing]);

  if (editing) {
    return (
      <select
        ref={selectRef}
        className="cell-select"
        defaultValue={selectedTemplateId ?? ""}
        onBlur={() => setEditing(false)}
        onChange={(e) => {
          const val = e.target.value;
          onChange(val === "" ? null : Number(val));
          setEditing(false);
        }}
      >
        <option value="">— leer —</option>
        {templates.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name} ({t.start_time.slice(0, 5)}–{t.end_time.slice(0, 5)})
          </option>
        ))}
      </select>
    );
  }

  function handleDragStart(e) {
    if (!templateInfo) return;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ employeeId, date }));
  }

  function handleDragOver(e) {
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOver(true);
  }

  function handleDrop(e) {
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
    e.preventDefault();
    setDragOver(false);
    const raw = e.dataTransfer.getData(DRAG_MIME);
    if (!raw) return;
    const source = JSON.parse(raw);
    onMove(source.employeeId, source.date, employeeId, date);
  }

  return (
    <button
      type="button"
      className={`shift-chip-btn${dragOver ? " is-drop-target" : ""}`}
      draggable={Boolean(templateInfo)}
      onClick={() => setEditing(true)}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      aria-label={templateInfo ? `${templateInfo.name}, Schicht ändern oder ziehen` : "Schicht zuweisen"}
    >
      {templateInfo ? (
        <span className="shift-chip" style={{ "--chip-color": templateInfo.color }}>
          {templateInfo.name.slice(0, 3)}
        </span>
      ) : (
        <span className="shift-chip shift-chip--empty" aria-hidden="true">
          +
        </span>
      )}
    </button>
  );
}

import { useEffect, useRef, useState } from "react";

export default function ShiftCell({ templates, selectedTemplateId, templateInfo, onChange }) {
  const [editing, setEditing] = useState(false);
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

  return (
    <button
      type="button"
      className="shift-chip-btn"
      onClick={() => setEditing(true)}
      aria-label={templateInfo ? `${templateInfo.name}, Schicht ändern` : "Schicht zuweisen"}
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

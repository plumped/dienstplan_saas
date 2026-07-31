import { useState } from "react";

const TYPE_OPTIONS = [
  { value: "wunschfrei", label: "Wunschfrei" },
  { value: "wunschdienst", label: "Wunschdienst" },
];

// Block 2.13: Wunschfrei/Wunschdienst -- ein Hinweis für den Planer, kein
// Anspruch und keine Sperre (anders als eine Absenz). Bewusst schlank
// gehalten: nur Typ + bei Wunschdienst der gewünschte Schichttyp, kein
// Genehmigungs-Workflow.
export default function WishEditor({ templates, existing, onSave, onCancel, onDelete, saving = false }) {
  const [type, setType] = useState(existing?.type ?? "wunschfrei");
  const [templateId, setTemplateId] = useState(existing?.template ?? "");

  const canSave = type === "wunschfrei" || (type === "wunschdienst" && templateId !== "");

  function handleSave() {
    if (!canSave) return;
    onSave({
      type,
      template: type === "wunschdienst" ? Number(templateId) : null,
    });
  }

  return (
    <div className="wish-editor">
      <p className="wish-editor-hint">Ein Hinweis für den Planer -- kein Anspruch, keine Sperre.</p>
      <div className="wish-editor-type">
        {TYPE_OPTIONS.map((opt) => (
          <label key={opt.value} className={`wish-type-option${type === opt.value ? " is-active" : ""}`}>
            <input
              type="radio"
              name="wish-type"
              value={opt.value}
              checked={type === opt.value}
              onChange={() => setType(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>
      {type === "wunschdienst" && (
        <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
          <option value="">Schichttyp wählen …</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} ({t.start_time.slice(0, 5)}–{t.end_time.slice(0, 5)})
            </option>
          ))}
        </select>
      )}
      <div className="entry-actions">
        <button type="button" disabled={saving || !canSave} onClick={handleSave}>
          Speichern
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Abbrechen
        </button>
        {onDelete && (
          <button type="button" className="btn-ghost" onClick={onDelete}>
            Löschen
          </button>
        )}
      </div>
    </div>
  );
}

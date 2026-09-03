// README Punkt 17 ("Teams pro Station + Mehrfachanstellungen"): pro Zeile
// eine Anstellung (Team + Pensum + Rollentitel + Teamleitung) -- ersetzt das
// frühere flache <select multiple> für "Stationen", weil Team-Zugehörigkeit
// jetzt Metadaten pro Zuordnung trägt, nicht nur eine blosse Mitgliedschaft.
// Gleiches Add/Remove-Zeilen-Muster wie TimeTemplateSegmentEditor.jsx: frei
// hinzufügen/entfernen, kein Umsortieren/Diffen nötig, weil die Liste kurz
// ist und beim Speichern ohnehin vollständig ersetzt wird (siehe
// EmployeeSerializer._sync_employments im Backend).
export default function EmploymentEditor({ employments, nodes, existingTitles, onChange }) {
  function updateEmployment(i, field, value) {
    onChange(employments.map((e, idx) => (idx === i ? { ...e, [field]: value } : e)));
  }

  function addEmployment() {
    onChange([...employments, { node: nodes[0]?.id ?? "", pensum_pct: 100, title: "", is_team_lead: false }]);
  }

  function removeEmployment(i) {
    onChange(employments.filter((_, idx) => idx !== i));
  }

  if (!employments.length) {
    return (
      <div className="employment-editor">
        <p className="panel-hint">Noch keine Anstellung erfasst -- ohne Team ist die Person im Planblatt nicht einteilbar.</p>
        <button type="button" className="add-segment" onClick={addEmployment}>
          + Anstellung hinzufügen
        </button>
      </div>
    );
  }

  return (
    <div className="employment-editor">
      <datalist id="employment-titles">
        {existingTitles.map((title) => (
          <option key={title} value={title} />
        ))}
      </datalist>
      {employments.map((employment, i) => (
        <div key={i} className="employment-editor-row">
          <select
            value={employment.node}
            onChange={(e) => updateEmployment(i, "node", Number(e.target.value))}
            aria-label={`Team für Anstellung ${i + 1}`}
          >
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            min="1"
            max="100"
            className="employment-editor-pensum"
            value={employment.pensum_pct}
            onChange={(e) => updateEmployment(i, "pensum_pct", Number(e.target.value))}
            aria-label={`Pensum für Anstellung ${i + 1}`}
          />
          <span className="segment-editor-sep">%</span>
          <input
            type="text"
            list="employment-titles"
            placeholder="Rolle, z. B. Arzt"
            value={employment.title}
            onChange={(e) => updateEmployment(i, "title", e.target.value)}
            aria-label={`Rollenbezeichnung für Anstellung ${i + 1}`}
          />
          <label className="employment-editor-lead">
            <input
              type="checkbox"
              checked={employment.is_team_lead}
              onChange={(e) => updateEmployment(i, "is_team_lead", e.target.checked)}
            />
            Teamleitung
          </label>
          <button
            type="button"
            className="remove-segment"
            onClick={() => removeEmployment(i)}
            aria-label={`Anstellung ${i + 1} entfernen`}
          >
            Entfernen
          </button>
        </div>
      ))}
      <button type="button" className="add-segment" onClick={addEmployment}>
        + Anstellung hinzufügen
      </button>
    </div>
  );
}

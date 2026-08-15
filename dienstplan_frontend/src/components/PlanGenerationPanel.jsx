// README Block 2 Punkt 19 (Automatisierte Planung): Auslöser + Ergebnis-
// Leiste für generate_draft_plan()/commit_draft_assignments(). Rein
// präsentational -- der gesamte Zustand (draftPlan/generatingPlan/
// committingPlan/removedDraftKeys) lebt in PlanGrid.jsx, das auch die
// Overlay-Chips im Grid selbst rendert (siehe dort).
export default function PlanGenerationPanel({
  generating,
  committing,
  draftPlan,
  activeCount,
  onGenerate,
  onCommit,
  onDiscard,
}) {
  if (!draftPlan) {
    return (
      <button type="button" className="btn-pill" onClick={onGenerate} disabled={generating}>
        {generating ? "Entwurf wird erstellt …" : "Automatisch planen"}
      </button>
    );
  }

  const hasHints = draftPlan.warnings.length > 0;

  return (
    <div className="plan-generation-panel">
      <span className="plan-generation-summary">
        {activeCount} Vorschlag/Vorschläge
        {draftPlan.status !== "ok" && " -- kein vollständiger Entwurf möglich"}
      </span>
      {hasHints && (
        <details className="plan-generation-warnings">
          <summary>{draftPlan.warnings.length} Hinweis(e)</summary>
          <ul>
            {draftPlan.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </details>
      )}
      <button
        type="button"
        className="btn-primary"
        onClick={onCommit}
        disabled={committing || activeCount === 0}
      >
        {committing ? "Wird übernommen …" : `Entwurf übernehmen (${activeCount})`}
      </button>
      <button type="button" className="btn-ghost" onClick={onDiscard} disabled={committing}>
        Verwerfen
      </button>
    </div>
  );
}

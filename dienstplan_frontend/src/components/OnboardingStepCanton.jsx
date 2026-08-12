import { useState } from "react";
import { api } from "../api.js";
import { SWISS_CANTONS } from "../cantons.js";

// Schritt 1 des OnboardingWizard: nur der Kanton (Grundlage für den
// automatischen Feiertagskalender, siehe TenantSettings.jsx) -- bewusst
// NICHT aus TenantSettings.jsx extrahiert (zu eng an die volle
// Feldgruppen-Form gekoppelt), frische Minimal-Komponente.
export default function OnboardingStepCanton({ onNext, onError }) {
  const [canton, setCanton] = useState("");
  const [saving, setSaving] = useState(false);

  async function handleNext() {
    setSaving(true);
    try {
      await api.updateTenant({ canton });
      onNext();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel-form onboarding-step-body">
      <h2>In welchem Kanton sind Sie tätig?</h2>
      <p className="panel-hint">
        Grundlage für den automatischen Feiertagskalender im Saldo. Kann später in den
        Einstellungen jederzeit geändert werden.
      </p>
      <label>
        Kanton
        <select value={canton} onChange={(e) => setCanton(e.target.value)}>
          <option value="">-- kein Kanton --</option>
          {SWISS_CANTONS.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="btn-primary" onClick={handleNext} disabled={saving}>
        {saving ? "Wird gespeichert …" : "Weiter"}
      </button>
    </div>
  );
}

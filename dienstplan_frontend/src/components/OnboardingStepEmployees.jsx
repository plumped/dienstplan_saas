import { useState } from "react";
import { api } from "../api.js";

// Schritt 4 des OnboardingWizard: CSV-Mitarbeitenden-Import (erstes
// File-Input im Frontend, siehe scheduling.views.EmployeeViewSet.import_csv)
// plus die Möglichkeit, die vom Seed vorbefüllten Beispiel-Mitarbeitenden/
// -Stationen wieder zu entfernen (last_name === "(Beispiel)" bzw. Name endet
// auf " (Beispiel)", siehe core.onboarding.seed_demo_tenant). Letzter
// Schritt -- "Fertigstellen" statt "Weiter".
export default function OnboardingStepEmployees({
  nodes,
  employees,
  onNodesChange,
  onEmployeesChange,
  onFinish,
  onError,
  busy,
}) {
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const demoEmployees = employees.filter((e) => e.last_name === "(Beispiel)");
  const demoNodes = nodes.filter((n) => n.name.endsWith(" (Beispiel)"));

  async function handleFileChange(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true);
    setResult(null);
    try {
      const data = await api.importEmployeesCsv(file);
      setResult(data);
      if (data.created > 0) {
        const refreshed = await api.getEmployees();
        onEmployeesChange(refreshed.results ?? refreshed);
      }
    } catch (e) {
      onError(e.message);
    } finally {
      setImporting(false);
    }
  }

  async function handleDeleteDemoData() {
    setDeleting(true);
    try {
      for (const employee of demoEmployees) {
        await api.deleteEmployee(employee.id);
      }
      for (const node of demoNodes) {
        await api.deleteNode(node.id);
      }
      onEmployeesChange(employees.filter((e) => e.last_name !== "(Beispiel)"));
      onNodesChange(nodes.filter((n) => !n.name.endsWith(" (Beispiel)")));
    } catch (e) {
      onError(e.message);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="panel-form onboarding-step-body">
      <h2>Mitarbeitende</h2>
      <p className="panel-hint">
        Laden Sie eine CSV-Datei mit Ihren Mitarbeitenden hoch, oder starten Sie mit den
        Beispiel-Mitarbeitenden und legen Sie später weitere manuell an.
      </p>

      <label>
        CSV-Datei
        <input type="file" accept=".csv" onChange={handleFileChange} disabled={importing} />
      </label>
      <p className="panel-hint">
        <button
          type="button"
          className="link-button"
          onClick={() => api.downloadEmployeeCsvTemplate().catch((e) => onError(e.message))}
        >
          Beispiel-CSV herunterladen
        </button>
      </p>

      {importing && <p className="loading-state">Wird importiert …</p>}

      {result && (
        <div className="onboarding-import-result">
          <p>{result.created} Mitarbeitende importiert.</p>
          {result.errors.length > 0 && (
            <ul className="onboarding-import-errors">
              {result.errors.map((err) => (
                <li key={err.row}>
                  Zeile {err.row}: {err.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {demoEmployees.length > 0 && (
        <div className="panel-form-row">
          <button type="button" className="btn-ghost" onClick={handleDeleteDemoData} disabled={deleting}>
            {deleting ? "Wird gelöscht …" : "Beispiel-Daten jetzt löschen"}
          </button>
        </div>
      )}

      <button type="button" className="btn-primary" onClick={onFinish} disabled={busy}>
        {busy ? "Wird abgeschlossen …" : "Fertigstellen"}
      </button>
    </div>
  );
}

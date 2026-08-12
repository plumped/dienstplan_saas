import { useEffect, useState } from "react";
import { api } from "../api.js";
import OnboardingStepCanton from "./OnboardingStepCanton.jsx";
import OnboardingStepEmployees from "./OnboardingStepEmployees.jsx";
import OnboardingStepShiftTypes from "./OnboardingStepShiftTypes.jsx";
import OnboardingStepStations from "./OnboardingStepStations.jsx";

const STEPS = [
  { id: 1, label: "Kanton" },
  { id: 2, label: "Stationen" },
  { id: 3, label: "Schichttypen" },
  { id: 4, label: "Mitarbeitende" },
];

// README Block 3 (Onboarding & Mandantenfähigkeit): Setup-Assistent nach dem
// Self-Signup (core.views.SignupView) -- der frisch angelegte Tenant landet
// mit Beispieldaten vorbefüllt (core.onboarding.seed_demo_tenant), dieser
// Wizard führt einmalig durch Kanton, Stationen, Schichttypen und
// Mitarbeitenden-Import, bevor die normale App erscheint (siehe
// App.jsx-Gate: me.tenant_onboarding_completed === false). Plain React
// State für den Schritt (kein Routing, konsistent mit dem Rest der App).
export default function OnboardingWizard({ onFinished }) {
  const [step, setStep] = useState(1);
  const [nodes, setNodes] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [timeTemplates, setTimeTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [skipping, setSkipping] = useState(false);

  useEffect(() => {
    Promise.all([api.getNodes(), api.getEmployees(), api.getTimeTemplates()])
      .then(([nodesData, employeesData, templatesData]) => {
        setNodes(nodesData.results ?? nodesData);
        setEmployees(employeesData.results ?? employeesData);
        setTimeTemplates(templatesData.results ?? templatesData);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  // Persistenter "Später fertigstellen"-Button: der Nutzer soll nie
  // eingesperrt sein -- anders als beim erzwungenen Passwortwechsel gibt es
  // hier keinen Sicherheitsgrund, das Aufschieben zu verhindern, jedes
  // Tenant-Feld hat sinnvolle Defaults.
  async function handleSkip() {
    setSkipping(true);
    try {
      await api.updateTenant({ onboarding_completed: true });
      onFinished();
    } catch (e) {
      setError(e.message);
      setSkipping(false);
    }
  }

  async function handleFinish() {
    setSkipping(true);
    try {
      await api.updateTenant({ onboarding_completed: true });
      onFinished();
    } catch (e) {
      setError(e.message);
      setSkipping(false);
    }
  }

  return (
    <div className="onboarding-screen">
      <div className="onboarding-card">
        <div className="brand-mark-lg" aria-hidden="true">
          ◒
        </div>
        <h1>Willkommen bei Dienstplan</h1>
        <p className="login-sub">
          Ein paar Schritte, um Ihre Station, Schichttypen und Mitarbeitenden einzurichten --
          jederzeit überspringbar.
        </p>

        <div className="onboarding-steps">
          {STEPS.map((s) => (
            <div
              key={s.id}
              className={`onboarding-step${
                s.id === step ? " is-current" : s.id < step ? " is-done" : " is-upcoming"
              }`}
            >
              <span className="onboarding-step-index">{s.id}</span>
              {s.label}
            </div>
          ))}
        </div>

        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}

        {loading ? (
          <p className="loading-state">Lädt …</p>
        ) : (
          <>
            {step === 1 && <OnboardingStepCanton onNext={() => setStep(2)} onError={setError} />}
            {step === 2 && (
              <OnboardingStepStations
                nodes={nodes}
                onNodesChange={setNodes}
                onNext={() => setStep(3)}
                onError={setError}
              />
            )}
            {step === 3 && (
              <OnboardingStepShiftTypes
                nodes={nodes}
                timeTemplates={timeTemplates}
                onTimeTemplatesChange={setTimeTemplates}
                onNext={() => setStep(4)}
                onError={setError}
              />
            )}
            {step === 4 && (
              <OnboardingStepEmployees
                nodes={nodes}
                employees={employees}
                onNodesChange={setNodes}
                onEmployeesChange={setEmployees}
                onFinish={handleFinish}
                onError={setError}
                busy={skipping}
              />
            )}
          </>
        )}

        <div className="onboarding-footer">
          <button type="button" className="link-button" onClick={handleSkip} disabled={skipping}>
            Später fertigstellen
          </button>
        </div>
      </div>
    </div>
  );
}

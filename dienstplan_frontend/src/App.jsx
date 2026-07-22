import { useEffect, useState } from "react";
import { api } from "./api.js";
import LoginForm from "./components/LoginForm.jsx";
import MonthNav from "./components/MonthNav.jsx";
import NodeSelector from "./components/NodeSelector.jsx";
import PlanGrid from "./components/PlanGrid.jsx";

function currentPeriod() {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(api.isLoggedIn());
  const [nodes, setNodes] = useState([]);
  const [nodeId, setNodeId] = useState(null);
  const [period, setPeriod] = useState(currentPeriod());
  const [error, setError] = useState("");

  useEffect(() => {
    if (!loggedIn) return;
    api
      .getNodes()
      .then((data) => {
        const list = data.results ?? data;
        setNodes(list);
        setNodeId((current) => current ?? list[0]?.id ?? null);
      })
      .catch((e) => {
        if (e.message === "unauthorized") setLoggedIn(false);
        else setError(e.message);
      });
  }, [loggedIn]);

  if (!loggedIn) {
    return <LoginForm onSuccess={() => setLoggedIn(true)} />;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ◒
          </span>
          Dienstplan
        </div>

        {nodes.length > 0 && <NodeSelector nodes={nodes} value={nodeId} onChange={setNodeId} />}
        <MonthNav
          year={period.year}
          month={period.month}
          onChange={(year, month) => setPeriod({ year, month })}
        />

        <button
          type="button"
          className="btn-ghost"
          onClick={() => {
            api.logout();
            setLoggedIn(false);
          }}
        >
          Abmelden
        </button>
      </header>

      {error && (
        <div className="banner-error" role="alert">
          {error}
          <button type="button" onClick={() => setError("")} aria-label="Meldung schliessen">
            ×
          </button>
        </div>
      )}

      <main>
        {nodeId ? (
          <PlanGrid nodeId={nodeId} year={period.year} month={period.month} onError={setError} />
        ) : (
          <p className="empty-state">
            Keine Stationen vorhanden. Im Admin unter „Nodes“ zuerst einen Standort anlegen.
          </p>
        )}
      </main>
    </div>
  );
}

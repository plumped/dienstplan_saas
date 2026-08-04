import { useEffect, useState } from "react";
import { api } from "./api.js";
import AbsencePanel from "./components/AbsencePanel.jsx";
import BalanceBadge from "./components/BalanceBadge.jsx";
import LoginForm from "./components/LoginForm.jsx";
import MonthNav from "./components/MonthNav.jsx";
import NodeSelector from "./components/NodeSelector.jsx";
import PlanGrid from "./components/PlanGrid.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";
import TimeRecordPanel from "./components/TimeRecordPanel.jsx";
import TradeRequestPanel from "./components/TradeRequestPanel.jsx";
import YearPlan from "./components/YearPlan.jsx";
import { canManageSchedule, ROLE_LABELS } from "./roles.js";

const TABS = [
  { id: "grid", label: "Planblatt" },
  { id: "yearplan", label: "Jahresplan" },
  { id: "absences", label: "Abwesenheiten" },
  { id: "trades", label: "Diensttausch" },
  { id: "timerecords", label: "Zeiterfassung" },
  { id: "settings", label: "Einstellungen", managerOnly: true },
];

function currentPeriod() {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(api.isLoggedIn());
  const [me, setMe] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [nodeId, setNodeId] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [period, setPeriod] = useState(currentPeriod());
  const [tab, setTab] = useState("grid");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!loggedIn) {
      setMe(null);
      return;
    }
    api
      .getMe()
      .then(setMe)
      .catch((e) => {
        if (e.message === "unauthorized") setLoggedIn(false);
        else setError(e.message);
      });
  }, [loggedIn]);

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

  useEffect(() => {
    if (!loggedIn || !nodeId) {
      setEmployees([]);
      return;
    }
    api
      .getEmployees()
      .then((data) => {
        const list = data.results ?? data;
        setEmployees(list.filter((e) => e.nodes.includes(nodeId)));
      })
      .catch((e) => {
        if (e.message === "unauthorized") setLoggedIn(false);
        else setError(e.message);
      });
  }, [loggedIn, nodeId]);

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

        <nav className="tab-nav">
          {TABS.filter((t) => !t.managerOnly || canManageSchedule(me)).map((t) => (
            <button
              key={t.id}
              type="button"
              className={`tab-btn${tab === t.id ? " is-active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {nodes.length > 0 && <NodeSelector nodes={nodes} value={nodeId} onChange={setNodeId} />}
        {(tab === "grid" || tab === "timerecords") && (
          <MonthNav
            year={period.year}
            month={period.month}
            onChange={(year, month) => setPeriod({ year, month })}
          />
        )}

        {me?.employee && <BalanceBadge employeeId={me.employee.id} />}
        {me?.role && <span className="role-badge">{ROLE_LABELS[me.role] ?? me.role}</span>}

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
        {tab === "settings" ? (
          // Bewusst ausserhalb der !nodeId-Sperre unten: ein frischer Tenant
          // ohne Stationen muss die Einstellungen erreichen können, um
          // überhaupt eine erste Station anzulegen (siehe SettingsPanel ->
          // NodeSettings).
          <SettingsPanel me={me} onError={setError} />
        ) : !nodeId ? (
          <p className="empty-state">
            {canManageSchedule(me)
              ? "Keine Stationen vorhanden. Unter „Einstellungen“ zuerst eine Station anlegen."
              : // Ein Mitarbeiter sieht nur die eigene(n) Station(en) -- ist er
                // an keine gebunden, liefert GET /api/nodes/ eine leere Liste
                // (nicht, weil es tenant-weit keine Stationen gäbe).
                "Keiner Station zugeordnet. Bitte an Admin/Planer wenden."}
          </p>
        ) : (
          <>
            {tab === "grid" && (
              <PlanGrid
                nodeId={nodeId}
                year={period.year}
                month={period.month}
                employees={employees}
                me={me}
                onError={setError}
              />
            )}
            {tab === "yearplan" && (
              <YearPlan nodeId={nodeId} employees={employees} me={me} onError={setError} />
            )}
            {tab === "absences" && <AbsencePanel employees={employees} me={me} onError={setError} />}
            {tab === "trades" && <TradeRequestPanel me={me} onError={setError} />}
            {tab === "timerecords" && (
              <TimeRecordPanel
                nodeId={nodeId}
                year={period.year}
                month={period.month}
                employees={employees}
                me={me}
                onError={setError}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}

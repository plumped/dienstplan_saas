import { useEffect, useState } from "react";
import { api, onTasksChanged } from "./api.js";
import AbsencePanel from "./components/AbsencePanel.jsx";
import BalanceBadge from "./components/BalanceBadge.jsx";
import Dashboard from "./components/Dashboard.jsx";
import LoginForm from "./components/LoginForm.jsx";
import MonthNav from "./components/MonthNav.jsx";
import NodeSelector from "./components/NodeSelector.jsx";
import PlanGrid from "./components/PlanGrid.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";
import TimeRecordPanel from "./components/TimeRecordPanel.jsx";
import TradeRequestPanel from "./components/TradeRequestPanel.jsx";
import YearPlan from "./components/YearPlan.jsx";
import { canManageSchedule, ROLE_LABELS } from "./roles.js";

// Block 2.4: taskCountKey verweist auf GET /api/me/: task_counts (siehe
// core.views._task_counts) -- Grundlage für die Zähler-Badges neben den
// jeweiligen Tabs, sichtbar für Admin/Planer (tenant-weite offene
// Genehmigungen) bzw. Mitarbeitende (eigene, an sie adressierte
// Tauschanfragen).
const TABS = [
  // README Punkt 21: zusätzlicher Tab, bewusst NICHT die neue
  // Standard-Landing-Page nach dem Login (siehe README-Diskussion) -- wer
  // sich nur schnell einloggt, um eine Schicht einzutragen, soll keinen
  // zusätzlichen Klick brauchen. Nur für Admin/Planer sichtbar, für die
  // Selbstbedienungs-Rolle ist "was hat Handlungsbedarf" nicht relevant.
  { id: "dashboard", label: "Übersicht", managerOnly: true },
  { id: "grid", label: "Planblatt" },
  { id: "yearplan", label: "Jahresplan" },
  { id: "absences", label: "Abwesenheiten", taskCountKey: "absences" },
  { id: "trades", label: "Diensttausch", taskCountKey: "trades" },
  { id: "timerecords", label: "Zeiterfassung", taskCountKey: "time_records" },
  { id: "settings", label: "Einstellungen", managerOnly: true },
];

function currentPeriod() {
  const now = new Date();
  return { year: now.getFullYear(), month: now.getMonth() + 1 };
}

// README Punkt 17 ("Teams pro Station + Mehrfachanstellungen"): eine Station
// mit Teams (direkte Kind-Knoten) zeigt im Planblatt/Jahresplan ein
// gemeinsames Bild -- Mitarbeitende werden deshalb nicht mehr nur exakt auf
// die gewählte Station gefiltert, sondern auf die Station + ihre direkten
// Team-Kinder (bewusst nur eine Ebene, siehe README). Nodes tragen bereits
// depth/path aus der treebeard-Baumstruktur (NodeSerializer), das reicht für
// den Vergleich, ohne einen zweiten Request zu brauchen.
export function relevantNodeIds(nodes, nodeId) {
  const selected = nodes.find((n) => n.id === nodeId);
  if (!selected) return [nodeId];
  const children = nodes.filter((n) => n.depth === selected.depth + 1 && n.path.startsWith(selected.path));
  return [nodeId, ...children.map((n) => n.id)];
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
    function loadMe() {
      api
        .getMe()
        .then(setMe)
        .catch((e) => {
          if (e.message === "unauthorized") setLoggedIn(false);
          else setError(e.message);
        });
    }
    loadMe();
    // Block 2.4: task_counts (Header-Badges) sollen sich ohne Reload
    // aktualisieren, sobald irgendwo eine Absenz/Tauschanfrage/Zeiterfassung
    // erstellt oder entschieden wurde (siehe affectsTasks in api.js) --
    // gleiches Pub/Sub-Muster wie onBalanceChanged für den Saldo.
    const unsubscribe = onTasksChanged(loadMe);
    return unsubscribe;
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
        const scopedIds = relevantNodeIds(nodes, nodeId);
        setEmployees(list.filter((e) => e.nodes.some((id) => scopedIds.includes(id))));
      })
      .catch((e) => {
        if (e.message === "unauthorized") setLoggedIn(false);
        else setError(e.message);
      });
  }, [loggedIn, nodeId, nodes]);

  // README Punkt 21: Deep-Link-Ziel aus dem Dashboard -- ein Klick auf eine
  // "Unterbesetzt"-Karte soll direkt bei der betroffenen Station/dem Monat im
  // Planblatt landen statt nur generisch auf den Tab "Planblatt" zu wechseln.
  function handleNavigate({ tab: nextTab, nodeId: nextNodeId, year, month }) {
    if (nextTab) setTab(nextTab);
    if (nextNodeId) setNodeId(nextNodeId);
    if (year && month) setPeriod({ year, month });
  }

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
          {TABS.filter((t) => !t.managerOnly || canManageSchedule(me)).map((t) => {
            const count = t.taskCountKey ? me?.task_counts?.[t.taskCountKey] : 0;
            return (
              <button
                key={t.id}
                type="button"
                className={`tab-btn${tab === t.id ? " is-active" : ""}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
                {!!count && (
                  <span className="tab-badge" title={`${count} offen`}>
                    {count}
                  </span>
                )}
              </button>
            );
          })}
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
        ) : tab === "dashboard" ? (
          // Ebenfalls ausserhalb der !nodeId-Sperre: die Übersicht ist
          // stationsübergreifend (siehe Dashboard.jsx), hängt an keiner
          // einzeln gewählten Station.
          <Dashboard me={me} onNavigate={handleNavigate} onError={setError} />
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
                nodes={nodes}
                year={period.year}
                month={period.month}
                employees={employees}
                me={me}
                onError={setError}
              />
            )}
            {tab === "yearplan" && (
              <YearPlan nodeId={nodeId} nodes={nodes} employees={employees} me={me} onError={setError} />
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

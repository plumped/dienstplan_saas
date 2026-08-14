import { useEffect, useState } from "react";
import { api, onTasksChanged } from "./api.js";
import AbsenceOverview from "./components/AbsenceOverview.jsx";
import AbsencePanel from "./components/AbsencePanel.jsx";
import BalanceBadge from "./components/BalanceBadge.jsx";
import Dashboard from "./components/Dashboard.jsx";
import ForcePasswordChangeModal from "./components/ForcePasswordChangeModal.jsx";
import LandingPage from "./components/LandingPage.jsx";
import LoginForm from "./components/LoginForm.jsx";
import MonthNav from "./components/MonthNav.jsx";
import NodeSelector from "./components/NodeSelector.jsx";
import OnboardingWizard from "./components/OnboardingWizard.jsx";
import PlanGrid from "./components/PlanGrid.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";
import SignupForm from "./components/SignupForm.jsx";
import TimeRecordOverview from "./components/TimeRecordOverview.jsx";
import TimeRecordPanel from "./components/TimeRecordPanel.jsx";
import TradeRequestOverview from "./components/TradeRequestOverview.jsx";
import TradeRequestPanel from "./components/TradeRequestPanel.jsx";
import YearPlan from "./components/YearPlan.jsx";
import { canManageSchedule, canViewScheduleReports, isTenantAdmin, ROLE_LABELS } from "./roles.js";

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
  // README Block 3: Bildschirm für ausgeloggte Besucher, bevor überhaupt ein
  // Login-Formular erscheint -- "landing"|"login"|"signup", nur relevant
  // solange !loggedIn (siehe Verzweigung unten).
  const [screen, setScreen] = useState("landing");
  const [me, setMe] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [nodeId, setNodeId] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [period, setPeriod] = useState(currentPeriod());
  const [tab, setTab] = useState("grid");
  const [error, setError] = useState("");
  // Nutzer-Feedback (2026-08): "Badge-Klick soll direkt in 'Zu bestätigen'
  // springen, nicht nur den Tab wechseln" -- der Badge sitzt im selben
  // Tab-Button (kein eigenes Klickziel), daher: jeder Klick auf den
  // Zeiterfassung-Tab erzwingt per key-Remount die "Zu bestätigen"-Ansicht
  // in TimeRecordOverview (deren initialView-Default), auch wenn zuvor auf
  // "Noch nicht erfasst" umgeschaltet war.
  const [timeRecordTabNonce, setTimeRecordTabNonce] = useState(0);
  // README Block 6 (Abrechnung): Admin bekommt einen dezenten Hinweis, sobald
  // die Testphase abgelaufen ist bzw. kein aktives Abo mehr besteht (siehe
  // Tenant.has_active_access()) -- ohne das müsste man erst zufällig in
  // Einstellungen -> Abrechnung schauen, um den Grund für die dann bereits
  // greifende 402-Sperre (core.billing.enforce_billing_access) zu verstehen.
  // Nur für Admin geladen, da BillingStatusView ohnehin Admin-only ist.
  const [billingBlocked, setBillingBlocked] = useState(false);

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
    if (!loggedIn || !isTenantAdmin(me)) {
      setBillingBlocked(false);
      return;
    }
    api
      .getBillingStatus()
      .then((data) => setBillingBlocked(!data.has_active_access))
      .catch(() => {}); // still, kein zusätzlicher Fehlerbanner für einen reinen Info-Hinweis
  }, [loggedIn, me]);

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

  // Bugfix (Nutzer-Feedback 2026-08): App.jsx bleibt beim Login-Wechsel
  // gemountet (nur `loggedIn` togglet) -- tab/nodeId von einer VORHERIGEN
  // Sitzung blieben sonst stehen. Marco Bianchi landete so nach dem
  // erzwungenen Passwortwechsel auf "Einstellungen", weil dort zuvor ein
  // Admin unterwegs war, obwohl der Tab-Button für seine Rolle gar nicht
  // sichtbar ist. Jeder frische Login/Signup startet deshalb explizit auf
  // dem Planblatt; nodeId auf null setzt den untenstehenden
  // Default-Auswahl-Effekt zurück, der dann automatisch die (bereits
  // backend-seitig auf die eigene(n) Station(en) gescopte) erste Station
  // wählt. Von LoginForm UND SignupForm aufgerufen (README Block 3).
  function handleAuthSuccess() {
    setTab("grid");
    setNodeId(null);
    setLoggedIn(true);
  }

  if (!loggedIn) {
    if (screen === "signup") {
      return <SignupForm onSuccess={handleAuthSuccess} onBack={() => setScreen("landing")} />;
    }
    if (screen === "login") {
      return <LoginForm onSuccess={handleAuthSuccess} onBack={() => setScreen("landing")} />;
    }
    return <LandingPage onStart={() => setScreen("signup")} onLogin={() => setScreen("login")} />;
  }

  // Verteidigungslinie gegen genau dieses Szenario, falls `tab` aus
  // irgendeinem Grund dennoch auf einem Wert steht, den die aktuelle Rolle
  // nicht sehen darf (z. B. noch nicht geladenes `me` direkt nach Login) --
  // der Inhaltsbereich rendert dann "Planblatt" statt eines für die Rolle
  // unsichtbaren Tabs.
  const activeTab = TABS.some((t) => t.id === tab && (!t.managerOnly || canManageSchedule(me))) ? tab : "grid";

  // Nutzer-Feedback (2026-08): erzwungener Passwortwechsel nach admin-
  // seitiger Direktanlage (siehe MembershipAccessSettings.jsx) -- blockiert
  // den Rest der Oberfläche, solange must_change_password gesetzt ist. `me`
  // ist beim allerersten Render nach Login noch null (GET /api/me/ lädt
  // asynchron), das Gate greift erst, sobald die Antwort da ist.
  if (me?.must_change_password) {
    return (
      <ForcePasswordChangeModal
        onDone={() => setMe((prev) => ({ ...prev, must_change_password: false }))}
      />
    );
  }

  // README Block 3: frisch per Self-Signup angelegte Tenants (siehe
  // core.views.SignupView) starten mit onboarding_completed=False -- die
  // signup-erzeugte Admin-Membership sieht statt der normalen App zuerst den
  // Einrichtungsassistenten. isTenantAdmin-Guard bewusst: ein hypothetischer
  // Nicht-Admin mit onboarding_completed=false fällt sauber in die normale
  // App durch statt in einem Wizard festzustecken, für den er keine Rechte
  // hat (Node/TimeTemplate-Erzeugung braucht IsTenantManager, der
  // Kanton-Schritt IsTenantAdmin).
  if (me && me.tenant_onboarding_completed === false && isTenantAdmin(me)) {
    return (
      <OnboardingWizard
        onFinished={() => setMe((prev) => ({ ...prev, tenant_onboarding_completed: true }))}
      />
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ◒
          </span>
          neravo
          {/* Nutzer-Feedback (2026-08): "oben Links sollte auch noch der
              Name stehen, damit man weiss wer gerade eingeloggt ist" --
              Employee-Name bevorzugt (lesbarer als der Login-Benutzername),
              Fallback auf `username` für die seltenen Konten ohne
              Mitarbeiterprofil (siehe core.views.MeView). */}
          {me && (
            <span className="current-user-name">
              {me.employee ? `${me.employee.first_name} ${me.employee.last_name}` : me.username}
            </span>
          )}
        </div>

        <nav className="tab-nav">
          {TABS.filter((t) => !t.managerOnly || canManageSchedule(me)).map((t) => {
            const count = t.taskCountKey ? me?.task_counts?.[t.taskCountKey] : 0;
            return (
              <button
                key={t.id}
                type="button"
                className={`tab-btn${activeTab === t.id ? " is-active" : ""}`}
                onClick={() => {
                  setTab(t.id);
                  if (t.id === "timerecords") setTimeRecordTabNonce((n) => n + 1);
                }}
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
        {(activeTab === "grid" || activeTab === "timerecords") && (
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
            setScreen("landing");
            setTab("grid");
            setNodeId(null);
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

      {billingBlocked && (
        <div className="banner-error" role="alert">
          Die Testphase ist abgelaufen oder es besteht kein aktives Abo mehr -- neue Einträge sind
          gesperrt, bis ein Abo abgeschlossen ist.
          <button type="button" onClick={() => setTab("settings")}>
            Zur Abrechnung
          </button>
        </div>
      )}

      <main>
        {activeTab === "settings" ? (
          // Bewusst ausserhalb der !nodeId-Sperre unten: ein frischer Tenant
          // ohne Stationen muss die Einstellungen erreichen können, um
          // überhaupt eine erste Station anzulegen (siehe SettingsPanel ->
          // NodeSettings).
          <SettingsPanel me={me} onError={setError} />
        ) : activeTab === "dashboard" ? (
          // Ebenfalls ausserhalb der !nodeId-Sperre: die Übersicht ist
          // stationsübergreifend (siehe Dashboard.jsx), hängt an keiner
          // einzeln gewählten Station.
          <Dashboard me={me} onNavigate={handleNavigate} onError={setError} />
        ) : activeTab === "timerecords" && canViewScheduleReports(me) ? (
          // Nutzer-Feedback (2026-08): "ich muss die Stationen durchsuchen,
          // bis ich die zu bestätigende Erfassung finde" -- für Admin/
          // Planer/HR ersetzt die stationsübergreifende Übersicht die
          // bisherige, auf eine Station begrenzte Ansicht; ebenfalls
          // ausserhalb der !nodeId-Sperre, weil sie explizit NICHT an eine
          // einzeln gewählte Station gebunden ist. Mitarbeitende (Self-
          // Service) behalten unverändert die stationsgebundene
          // TimeRecordPanel weiter unten.
          <TimeRecordOverview key={timeRecordTabNonce} nodes={nodes} onError={setError} />
        ) : activeTab === "absences" && canViewScheduleReports(me) ? (
          // Nutzer-Feedback (2026-08): "Abwesenheiten/Diensttausch sollen
          // gleich aufgebaut sein wie Zeiterfassung" -- gleiches Muster wie
          // beim timerecords-Zweig oben: stationsübergreifende Übersicht für
          // Admin/Planer/HR statt der stationsgebundenen Ansicht.
          // Mitarbeitende (Self-Service) behalten unverändert die
          // stationsgebundene AbsencePanel weiter unten.
          <AbsenceOverview nodes={nodes} me={me} onError={setError} />
        ) : activeTab === "trades" && canViewScheduleReports(me) ? (
          <TradeRequestOverview nodes={nodes} me={me} onError={setError} />
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
            {activeTab === "grid" && (
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
            {activeTab === "yearplan" && (
              <YearPlan nodeId={nodeId} nodes={nodes} employees={employees} me={me} onError={setError} />
            )}
            {activeTab === "absences" && <AbsencePanel employees={employees} me={me} onError={setError} />}
            {activeTab === "trades" && <TradeRequestPanel me={me} onError={setError} />}
            {activeTab === "timerecords" && (
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

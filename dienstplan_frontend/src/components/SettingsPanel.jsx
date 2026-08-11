import { useEffect, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule, isTenantAdmin } from "../roles.js";
import AbsenceTypeSettings from "./AbsenceTypeSettings.jsx";
import EmployeeSettings from "./EmployeeSettings.jsx";
import MembershipAccessSettings from "./MembershipAccessSettings.jsx";
import MonthlySummaryPanel from "./MonthlySummaryPanel.jsx";
import NodeSettings from "./NodeSettings.jsx";
import PayrollSettings from "./PayrollSettings.jsx";
import SkillSettings from "./SkillSettings.jsx";
import TenantSettings from "./TenantSettings.jsx";
import TimeTemplateSettings from "./TimeTemplateSettings.jsx";

const MODULES = [
  {
    id: "templates",
    label: "Schichttypen",
    description: "Frühdienst, Spätdienst & Co. definieren, inkl. optionaler Blockstruktur für die Ist-Erfassung.",
  },
  {
    id: "absenceTypes",
    label: "Absenzarten",
    description: "Ferien, Krankheit & Co. definieren -- frei erweiterbar, z. B. um Militärdienst oder Weiterbildung.",
  },
  {
    id: "employees",
    label: "Mitarbeitende",
    description:
      "Stammdaten, Stationen/Skills-Zuordnung, individuelle Wochenstunden- und Ferien-Overrides -- inkl. " +
      "Login-Zugang und Rolle, falls die Person die App selbst nutzt.",
  },
  {
    id: "nodes",
    label: "Stationen",
    description: "Organisationsstruktur der Klinik/Praxis -- Standorte, Abteilungen, Teams.",
  },
  { id: "skills", label: "Skills", description: "Qualifikationen, die für einzelne Schichttypen vorausgesetzt werden können." },
  // Block 2.6: bewusst KEINE Mitarbeiter-Selbstauskunft (anders als die
  // Saldo-Badges), sondern Lohnlauf-Vorbereitung -- daher managerOnly statt
  // für alle sichtbar, deckungsgleich mit der Backend-Berechtigung in
  // EmployeeViewSet.monthly_summary.
  {
    id: "payroll",
    label: "Monatsauswertung",
    description: "Soll/Ist-Stunden, Überzeit sowie Nacht-/Sonntagszuschlag pro Monat -- Basis für den Lohnlauf.",
    managerOnly: true,
  },
  // Block 2 Punkt 30/31: Admin-only wie "Regel-Engine & Zuschläge" (unten)
  // -- diese Codes steuern direkt die Übergabe an das Lohnsystem des
  // Kunden, nicht das Tagesgeschäft der Planung.
  {
    id: "payrollMapping",
    label: "Lohnarten",
    description: "Zuordnung der Zuschlagskategorien zu Lohnart-Codes + CSV-Export der Lohn-Rohdaten (nur Admin).",
    adminOnly: true,
  },
  // Block 2.14: Admin-only, strenger als die übrigen Module (die auch
  // Planer sehen/bearbeiten dürfen) -- steuert Rechtssicherheit und
  // Lohnzuschläge, siehe README Architektur-Abschnitt.
  {
    id: "tenant",
    label: "Regel-Engine & Zuschläge",
    description: "Ruhezeit, Höchstarbeitszeit, Überzeit-/Nacht-/Sonntagszuschläge, Ferienanspruch (nur Admin).",
    adminOnly: true,
  },
  // Nutzer-Feedback (2026-08): "Es gibt nun Tab Mitarbeitende, Tab Mitglieder
  // und Zugriff [...] Das muss doch intuitiver gelöst werden?" -- Login-
  // Zugang für Personen MIT Mitarbeiterprofil ist in "Mitarbeitende"
  // gewandert (ein Ort, ein Formular pro Person). Diese Kachel bleibt nur
  // für den seltenen Sonderfall: ein Konto OHNE Mitarbeiterprofil (z. B.
  // externe IT-Administration). Bewusst ans Ende der Liste, weil kein
  // Alltagsweg mehr.
  {
    id: "access",
    label: "Konten ohne Mitarbeiterprofil",
    description: "Seltener Sonderfall: Login-Konten, die zu keiner Person unter Mitarbeitende gehören.",
    adminOnly: true,
  },
];

// Block 2.10: Stammdaten-Selfservice für Admin/Planer, bisher nur im
// Django-Admin möglich -- der Nodes/Skills als geteilte Referenzdaten
// zwischen mehreren Modulen (Schichttypen brauchen Stationen+Skills,
// Mitarbeitende brauchen Stationen+Skills) hier zentral lädt.
//
// Block 2.16: startet auf einer Kachel-Übersicht statt sofort im ersten
// Modul (module === null) -- vorher sprang der Tab immer direkt in
// "Schichttypen", ohne vorher zu zeigen, was sich hier überhaupt einstellen
// lässt. Ein "← Übersicht"-Link führt aus jedem Modul zurück dorthin.
export default function SettingsPanel({ me, onError }) {
  const [module, setModule] = useState(null);
  const visibleModules = MODULES.filter(
    (m) => (!m.adminOnly || isTenantAdmin(me)) && (!m.managerOnly || canManageSchedule(me))
  );
  const [nodes, setNodes] = useState([]);
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([api.getNodes(), api.getSkills()])
      .then(([nodesRes, skillsRes]) => {
        if (cancelled) return;
        setNodes(nodesRes.results ?? nodesRes);
        setSkills(skillsRes.results ?? skillsRes);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Nutzer-Feedback (2026-08): Drag & Drop im Stationen-Baum (NodeSettings.jsx)
  // verschiebt einen Knoten samt aller Nachfahren -- deren depth/path ändern
  // sich serverseitig alle auf einmal. Statt das clientseitig nachzurechnen,
  // einfach den kompletten (kleinen) Baum neu laden.
  function reloadNodes() {
    api
      .getNodes()
      .then((data) => setNodes(data.results ?? data))
      .catch((e) => onError(e.message));
  }

  if (loading) return <p className="loading-state">Einstellungen werden geladen …</p>;

  if (!module) {
    return (
      <div className="settings-panel">
        <div className="settings-module-grid">
          {visibleModules.map((m) => (
            <button
              key={m.id}
              type="button"
              className="settings-module-card"
              onClick={() => setModule(m.id)}
            >
              <strong>{m.label}</strong>
              <span>{m.description}</span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="settings-panel">
      <nav className="settings-nav">
        <button type="button" className="settings-nav-btn settings-nav-btn--back" onClick={() => setModule(null)}>
          ← Übersicht
        </button>
        {visibleModules.map((m) => (
          <button
            key={m.id}
            type="button"
            className={`settings-nav-btn${module === m.id ? " is-active" : ""}`}
            onClick={() => setModule(m.id)}
          >
            {m.label}
          </button>
        ))}
      </nav>

      {module === "templates" && <TimeTemplateSettings nodes={nodes} skills={skills} onError={onError} />}
      {module === "absenceTypes" && <AbsenceTypeSettings onError={onError} />}
      {module === "employees" && <EmployeeSettings nodes={nodes} skills={skills} me={me} onError={onError} />}
      {module === "nodes" && (
        <NodeSettings
          nodes={nodes}
          onCreated={(n) => setNodes((prev) => [...prev, n])}
          onUpdated={(n) => setNodes((prev) => prev.map((x) => (x.id === n.id ? n : x)))}
          onDeleted={(id) => setNodes((prev) => prev.filter((x) => x.id !== id))}
          onNodesChanged={reloadNodes}
          onError={onError}
        />
      )}
      {module === "skills" && (
        <SkillSettings
          skills={skills}
          onCreated={(s) => setSkills((prev) => [...prev, s])}
          onUpdated={(s) => setSkills((prev) => prev.map((x) => (x.id === s.id ? s : x)))}
          onDeleted={(id) => setSkills((prev) => prev.filter((x) => x.id !== id))}
          onError={onError}
        />
      )}
      {module === "payroll" && canManageSchedule(me) && <MonthlySummaryPanel onError={onError} />}
      {module === "payrollMapping" && isTenantAdmin(me) && <PayrollSettings onError={onError} />}
      {module === "tenant" && isTenantAdmin(me) && <TenantSettings onError={onError} />}
      {module === "access" && isTenantAdmin(me) && <MembershipAccessSettings nodes={nodes} onError={onError} />}
    </div>
  );
}

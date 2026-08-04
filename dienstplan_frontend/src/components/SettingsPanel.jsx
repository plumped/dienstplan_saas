import { useEffect, useState } from "react";
import { api } from "../api.js";
import { isTenantAdmin } from "../roles.js";
import EmployeeSettings from "./EmployeeSettings.jsx";
import NodeSettings from "./NodeSettings.jsx";
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
    id: "employees",
    label: "Mitarbeitende",
    description: "Stammdaten, Stationen/Skills-Zuordnung, individuelle Wochenstunden- und Ferien-Overrides.",
  },
  {
    id: "nodes",
    label: "Stationen",
    description: "Organisationsstruktur der Klinik/Praxis -- Standorte, Abteilungen, Teams.",
  },
  { id: "skills", label: "Skills", description: "Qualifikationen, die für einzelne Schichttypen vorausgesetzt werden können." },
  // Block 2.14: Admin-only, strenger als die übrigen Module (die auch
  // Planer sehen/bearbeiten dürfen) -- steuert Rechtssicherheit und
  // Lohnzuschläge, siehe README Architektur-Abschnitt.
  {
    id: "tenant",
    label: "Regel-Engine & Zuschläge",
    description: "Ruhezeit, Höchstarbeitszeit, Überzeit-/Nacht-/Sonntagszuschläge, Ferienanspruch (nur Admin).",
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
  const visibleModules = MODULES.filter((m) => !m.adminOnly || isTenantAdmin(me));
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
      {module === "employees" && <EmployeeSettings nodes={nodes} skills={skills} onError={onError} />}
      {module === "nodes" && (
        <NodeSettings
          nodes={nodes}
          onCreated={(n) => setNodes((prev) => [...prev, n])}
          onUpdated={(n) => setNodes((prev) => prev.map((x) => (x.id === n.id ? n : x)))}
          onDeleted={(id) => setNodes((prev) => prev.filter((x) => x.id !== id))}
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
      {module === "tenant" && isTenantAdmin(me) && <TenantSettings onError={onError} />}
    </div>
  );
}

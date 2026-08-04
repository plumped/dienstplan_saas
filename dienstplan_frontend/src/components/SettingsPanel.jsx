import { useEffect, useState } from "react";
import { api } from "../api.js";
import { isTenantAdmin } from "../roles.js";
import EmployeeSettings from "./EmployeeSettings.jsx";
import NodeSettings from "./NodeSettings.jsx";
import SkillSettings from "./SkillSettings.jsx";
import TenantSettings from "./TenantSettings.jsx";
import TimeTemplateSettings from "./TimeTemplateSettings.jsx";

const MODULES = [
  { id: "templates", label: "Schichttypen" },
  { id: "employees", label: "Mitarbeitende" },
  { id: "nodes", label: "Stationen" },
  { id: "skills", label: "Skills" },
  // Block 2.14: Admin-only, strenger als die übrigen Module (die auch
  // Planer sehen/bearbeiten dürfen) -- steuert Rechtssicherheit und
  // Lohnzuschläge, siehe README Architektur-Abschnitt.
  { id: "tenant", label: "Regel-Engine & Zuschläge", adminOnly: true },
];

// Block 2.10: Stammdaten-Selfservice für Admin/Planer, bisher nur im
// Django-Admin möglich -- der Nodes/Skills als geteilte Referenzdaten
// zwischen mehreren Modulen (Schichttypen brauchen Stationen+Skills,
// Mitarbeitende brauchen Stationen+Skills) hier zentral lädt.
export default function SettingsPanel({ me, onError }) {
  const [module, setModule] = useState("templates");
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

  return (
    <div className="settings-panel">
      <nav className="settings-nav">
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

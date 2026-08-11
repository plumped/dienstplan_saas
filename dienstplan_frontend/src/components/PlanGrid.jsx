import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import BalanceBadge from "./BalanceBadge.jsx";
import FloatingPopover from "./FloatingPopover.jsx";
import PlacementToolbar from "./PlacementToolbar.jsx";
import ShiftCell from "./ShiftCell.jsx";
import SpecialBadge from "./SpecialBadge.jsx";

// README Block 2.9: kleines, klickbares Warn-Badge in der Tages-Kopfzelle,
// wenn mindestens ein Schichttyp mit minimum_staffing an diesem Tag
// unterbesetzt ist -- bewusst ein FloatingPopover statt eines reinen
// title=-Tooltips (hover-only, würde ausserdem mit dem bereits vorhandenen
// title={holidayName} auf derselben Zelle kollidieren, funktioniert nicht
// auf Tablet/Touch), gleiches Muster wie die Wunsch-/Zeiterfassungs-Badges
// in ShiftCell.jsx.
function DayStaffingBadge({ shortfalls }) {
  const [open, setOpen] = useState(false);
  const badgeRef = useRef(null);
  if (!shortfalls.length) return null;
  return (
    <>
      <button
        ref={badgeRef}
        type="button"
        className="staffing-warning-badge"
        title="Mindestbesetzung unterschritten -- Details anzeigen"
        onClick={() => setOpen((v) => !v)}
      >
        ⚠<span className="visually-hidden"> Mindestbesetzung unterschritten</span>
      </button>
      {open && (
        <FloatingPopover anchorRef={badgeRef} onClose={() => setOpen(false)} className="staffing-popover">
          <ul className="staffing-popover-list">
            {shortfalls.map(({ template, count }) => (
              <li key={template.id}>
                {template.name}: {count}/{template.minimum_staffing} besetzt
              </li>
            ))}
          </ul>
        </FloatingPopover>
      )}
    </>
  );
}

// Bugfix ("massiver Bug", Nutzer-Feedback): eine sonst leere Zelle liess
// sich trotzdem nicht beplanen ("... hat am ... bereits 'Frühschicht' ...
// überschneidet"), weil die Person eine Mehrfachanstellung (README Punkt 17)
// hat und in einem ANDEREN Team/einer anderen Station bereits verplant ist --
// die Regel-Engine prüft zu Recht tenant-weit, aber das Grid lädt nur die
// aktuell gewählte Station, der blockierende Dienst war dadurch für den
// Planer unsichtbar (siehe api.getOtherTeamConflicts). Zeigt genau das
// proaktiv als kleines Warn-Badge an, statt dass es erst beim gescheiterten
// Beplanungsversuch als kryptische Fehlermeldung auftaucht.
function CrossTeamConflictBadge({ conflict }) {
  const [open, setOpen] = useState(false);
  const badgeRef = useRef(null);
  if (!conflict) return null;
  return (
    <>
      <button
        ref={badgeRef}
        type="button"
        className="cross-team-conflict-badge"
        title="Bereits in einem anderen Team verplant -- Details anzeigen"
        onClick={() => setOpen((v) => !v)}
      >
        ⚠<span className="visually-hidden"> Bereits in einem anderen Team verplant</span>
      </button>
      {open && (
        <FloatingPopover anchorRef={badgeRef} onClose={() => setOpen(false)} className="cross-team-conflict-popover">
          <p>
            Bereits verplant: <strong>{conflict.template_name}</strong> ({conflict.start_time}–
            {conflict.end_time}) in Team/Station "{conflict.node_name}".
          </p>
        </FloatingPopover>
      )}
    </>
  );
}

const WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

function pad(n) {
  return String(n).padStart(2, "0");
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function isoDate(year, month, day) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

function addDays(iso, delta) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  date.setDate(date.getDate() + delta);
  return isoDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

// Fasst eine Menge von ISO-Tagen zu möglichst wenigen zusammenhängenden
// [start, end]-Bereichen zusammen -- damit z. B. zwei markierte Ferienwochen
// als zwei Absence-Einträge entstehen statt sieben Einzeltagen (gleiches
// Muster wie YearPlan.jsx: groupConsecutiveDates).
function groupConsecutiveDates(dates) {
  const sorted = [...dates].sort();
  const ranges = [];
  let start = null;
  let prev = null;
  for (const d of sorted) {
    if (start === null) {
      start = d;
    } else if (addDays(prev, 1) !== d) {
      ranges.push([start, prev]);
      start = d;
    }
    prev = d;
  }
  if (start !== null) ranges.push([start, prev]);
  return ranges;
}

function weekdayLabel(year, month, day) {
  const jsDay = new Date(year, month - 1, day).getDay(); // 0 = Sonntag
  return WEEKDAYS_SHORT[jsDay === 0 ? 6 : jsDay - 1];
}

// Löst für einen im NodeSelector gewählten Knoten die zugehörige Station
// (Elternknoten, falls der gewählte Knoten selbst ein Team ist) plus alle
// ihre Team-Kinder auf -- unabhängig davon, ob im Selector die Station
// selbst oder direkt eines ihrer Teams gewählt wurde. Bugfix: bisher wurden
// TimeTemplates ausschliesslich nach `t.node === nodeId` geladen, was bei
// direkter Team-Auswahl immer leer blieb (TimeTemplate.node kann seit der
// Team-Filterung unten sowohl die Station als auch ein einzelnes Team sein).
function stationScope(nodes, nodeId) {
  const selected = nodes.find((n) => n.id === nodeId);
  if (!selected) return { stationId: nodeId, scopeIds: [nodeId] };
  const parent = nodes.find((n) => n.depth === selected.depth - 1 && selected.path.startsWith(n.path));
  const station = parent ?? selected;
  const children = nodes.filter((n) => n.depth === station.depth + 1 && n.path.startsWith(station.path));
  return { stationId: station.id, scopeIds: [station.id, ...children.map((n) => n.id)] };
}

export default function PlanGrid({ nodeId, nodes, year, month, employees, me, onError }) {
  const [templates, setTemplates] = useState([]);
  const [absenceTypes, setAbsenceTypes] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [absences, setAbsences] = useState([]);
  const [timeRecords, setTimeRecords] = useState([]);
  const [preferences, setPreferences] = useState([]);
  // Arbeitszeitmodell (Block 2.7 Punkt 7): Feiertage des Tenant-Kantons +
  // manuelle Overrides (core.views.TenantHolidaysView) -- Map<isoDate, name>
  // fürs Markieren der Spalten/Zellen unten.
  const [holidays, setHolidays] = useState(new Map());
  // Bugfix ("massiver Bug"): blockierende Zuweisungen aus ANDEREN Teams/
  // Stationen derselben Person (Mehrfachanstellung), die das Grid sonst nie
  // lädt -- siehe CrossTeamConflictBadge oben und api.getOtherTeamConflicts.
  const [otherTeamConflicts, setOtherTeamConflicts] = useState([]);
  const [loading, setLoading] = useState(true);
  // Workflow-Redesign (2026-08, Nutzer-Feedback: "erst Tage markieren, dann
  // beplanen -- nicht umgekehrt"): EIN einziges Modell für Einzel- UND
  // Mehrfachplanung statt zweier widersprüchlicher (Werkzeug-zuerst beim
  // Icon-Toolbar-Klick vs. Ziel-zuerst bei der alten separaten
  // Mehrfachauswahl). Klicken/Ziehen auf eine Zelle markiert IMMER (kein
  // Moduswechsel mehr nötig); die Icon-Toolbar (PlacementToolbar.jsx) ist
  // der Stempel für die aktuelle Markierung -- ein Klick auf ein Dienst-Icon
  // wendet es sofort auf alle markierten Tage an (bei nur einem markierten
  // Tag exakt wie ein Einzelklick vorher, keine Mehrarbeit). Ersetzt sowohl
  // die alte separate Mehrfachauswahl-Stempelleiste als auch das frühere
  // "Werkzeug bewaffnen, dann Zelle klicken"-Modell komplett.
  const [markedCells, setMarkedCells] = useState(() => new Set());
  // placementMode bestimmt, welche Hälfte einer Zelle ein Stempel trifft
  // (Ganz spannt beide, Links/Rechts je eine feste Hälfte, Pikett fügt
  // additiv eine Spezialität hinzu).
  const [placementMode, setPlacementMode] = useState("full");
  // Ziehen mit gedrückter Maustaste markiert mehrere Zellen am Stück, statt
  // jede einzeln anklicken zu müssen: "mark" oder "unmark", je nachdem, ob
  // die Zelle, auf der die Maustaste gedrückt wurde, schon markiert war;
  // null = kein Ziehvorgang aktiv. Ref statt State, weil das nur die laufende
  // Maus-Interaktion steuert und kein Re-Render braucht.
  const dragMarkModeRef = useRef(null);
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;
  const todayIso = new Date().toISOString().slice(0, 10);

  const days = useMemo(
    () => Array.from({ length: daysInMonth(year, month) }, (_, i) => i + 1),
    [year, month]
  );
  const dateFrom = isoDate(year, month, 1);
  const dateTo = isoDate(year, month, days.length);

  const { stationId, scopeIds: templateScopeIds } = useMemo(() => stationScope(nodes, nodeId), [nodes, nodeId]);

  // README Punkt 17: eine Station mit Teams (direkte Kind-Knoten, genau eine
  // Ebene) zeigt das Planblatt als gemeinsame Tabelle mit Trennzeilen pro
  // Team statt einer flachen Mitarbeiterliste. Eine Station ohne Teams
  // (heutiger Normalfall) hat hier immer ein leeres Array -- dann verhält
  // sich das Rendering weiter unten exakt wie vorher.
  const teamNodes = useMemo(() => {
    const selected = nodes.find((n) => n.id === nodeId);
    if (!selected) return [];
    return nodes
      .filter((n) => n.depth === selected.depth + 1 && n.path.startsWith(selected.path))
      .sort((a, b) => a.path.localeCompare(b.path));
  }, [nodes, nodeId]);

  // Baut die tatsächlich zu rendernden Zeilen: ohne Teams eine Zeile pro
  // Mitarbeiter (Backwards-kompatibel, rowNodeId = die Station selbst).
  // Mit Teams: pro Team eine Trennzeile + eine Zeile pro Employment in
  // diesem Team (eine Person mit mehreren Anstellungen erscheint entsprechend
  // mehrfach, je einmal pro Team) -- Mitarbeitende ohne passende Employment
  // in einem der sichtbaren Teams landen in einem eigenen, schreibgeschützten
  // Block ("Kein Team zugeordnet"), weil Direktbuchung auf die Station bei
  // vorhandenen Teams serverseitig abgelehnt wird (siehe
  // ShiftAssignment._check_node_has_no_children).
  const rows = useMemo(() => {
    if (teamNodes.length === 0) {
      // Auch eine flache Station (kein Team-Kind) kann eine Employment-Zeile
      // für genau diesen Knoten haben (Pensum/Titel/Teamleitung) -- die wird
      // hier mitgenommen, statt immer auf emp.employment_pct zurückzufallen,
      // damit z. B. eine Teamleitung auch ohne Unterteams als Badge sichtbar
      // ist.
      return employees.map((emp) => ({
        type: "employee",
        key: String(emp.id),
        emp,
        employment: (emp.employments ?? []).find((e) => e.node === nodeId) ?? null,
        rowNodeId: nodeId,
      }));
    }
    const teamNodeIds = teamNodes.map((n) => n.id);
    const result = [];
    const unassigned = employees.filter(
      (emp) => !(emp.employments ?? []).some((e) => teamNodeIds.includes(e.node))
    );
    if (unassigned.length) {
      result.push({ type: "divider", key: "divider:unassigned", label: "Kein Team zugeordnet" });
      for (const emp of unassigned) {
        result.push({ type: "unassigned", key: `unassigned:${emp.id}`, emp });
      }
    }
    for (const team of teamNodes) {
      const teamRows = employees.flatMap((emp) =>
        (emp.employments ?? [])
          .filter((e) => e.node === team.id)
          .map((employment) => ({
            type: "employee",
            key: `${emp.id}:${team.id}`,
            emp,
            employment,
            rowNodeId: team.id,
          }))
      );
      if (!teamRows.length) continue;
      result.push({ type: "divider", key: `divider:${team.id}`, label: team.name });
      result.push(...teamRows);
    }
    return result;
  }, [teamNodes, employees, nodeId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // Wunschfrei/Wunschdienst (Block 2.13): der Planer (canManage) sieht die
    // Wünsche aller Mitarbeitenden, um sie beim Ausfüllen zu berücksichtigen;
    // eine einfache Mitarbeiter-Rolle sieht nur die eigenen (Privatsphäre --
    // ein Wunsch ist höchstpersönlich, siehe ShiftPreferencePermission).
    // Ohne eigenes Employee-Profil (z. B. HR) gibt es nichts zu laden.
    const preferencesRequest = canManage
      ? api.getShiftPreferences()
      : ownEmployeeId
        ? api.getShiftPreferences(ownEmployeeId)
        : Promise.resolve([]);
    Promise.all([
      api.getTimeTemplates(),
      api.getAbsenceTypes(),
      api.getShiftAssignments(nodeId, dateFrom, dateTo),
      api.getAbsences(),
      api.getTimeRecords(dateFrom, dateTo),
      preferencesRequest,
      api.getTenantHolidays(year),
    ])
      .then(([templatesRes, absenceTypesRes, assignmentsRes, absencesRes, timeRecordsRes, preferencesRes, holidaysRes]) => {
        if (cancelled) return;
        setTemplates((templatesRes.results ?? templatesRes).filter((t) => templateScopeIds.includes(t.node)));
        setAbsenceTypes(absenceTypesRes.results ?? absenceTypesRes);
        setAssignments(assignmentsRes.results ?? assignmentsRes);
        // Nur genehmigte Absenzen blockieren/zeigen sich im Grid (siehe
        // ShiftAssignment._check_no_absence_conflict im Backend) -- offene
        // Anträge sieht man im Tab "Abwesenheiten", nicht hier.
        const absenceList = absencesRes.results ?? absencesRes;
        setAbsences(absenceList.filter((a) => a.status === "approved"));
        setTimeRecords(timeRecordsRes.results ?? timeRecordsRes);
        setPreferences(preferencesRes.results ?? preferencesRes);
        setHolidays(new Map(holidaysRes.dates.map((entry) => [entry.date, entry.name])));
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, dateFrom, dateTo, canManage, ownEmployeeId, year, templateScopeIds]);

  // Bugfix ("massiver Bug"): separat vom Haupt-Fetch oben, weil er von
  // `employees`/`stationId` abhängt (nicht von den dortigen Deps) und nur
  // für Admin/Planer überhaupt etwas liefert (das Backend lehnt den Aufruf
  // sonst mit 403 ab, siehe ShiftAssignmentViewSet.other_team_conflicts) --
  // exclude_node=stationId, weil ALLE Teams der aktuell gewählten Station
  // ohnehin schon über den Haupt-Fetch geladen sind (README Punkt 17: das
  // Grid zeigt Stations-Teams gemeinsam in einer Tabelle) und daher keine
  // eigene Warnung brauchen -- nur eine WIRKLICH andere Station ist hier
  // sonst unsichtbar.
  useEffect(() => {
    if (!canManage || employees.length === 0) {
      setOtherTeamConflicts([]);
      return;
    }
    let cancelled = false;
    api
      .getOtherTeamConflicts(
        employees.map((e) => e.id),
        dateFrom,
        dateTo,
        stationId
      )
      .then((res) => {
        if (!cancelled) setOtherTeamConflicts(res.results ?? res);
      })
      .catch(() => {
        // Nicht kritisch fürs Kernfeature -- stumm ignorieren, das Grid
        // bleibt ansonsten voll funktionsfähig, nur ohne die Zusatzwarnung.
      });
    return () => {
      cancelled = true;
    };
  }, [canManage, employees, dateFrom, dateTo, stationId]);

  // Beendet einen laufenden Ziehvorgang auch dann, wenn die Maustaste
  // ausserhalb einer Zelle losgelassen wird (z. B. nach dem Verlassen des
  // Grids) -- sonst bliebe dragMarkModeRef "aktiv" hängen.
  useEffect(() => {
    function handleWindowMouseUp() {
      dragMarkModeRef.current = null;
    }
    window.addEventListener("mouseup", handleWindowMouseUp);
    return () => window.removeEventListener("mouseup", handleWindowMouseUp);
  }, []);

  // README Punkt 18 (Split-Shifts): ein Schlüssel kann seit der Lockerung
  // von ShiftAssignment.unique_together jetzt mehrere Zuweisungen liefern
  // (z. B. Frühdienst + Spätdienst derselben Person am selben Tag) --
  // deshalb ein Array statt eines einzelnen Werts, anders als vor Punkt 18.
  const assignmentMap = useMemo(() => {
    const map = new Map();
    for (const a of assignments) {
      const key = `${a.employee}:${a.date}`;
      const list = map.get(key);
      if (list) list.push(a);
      else map.set(key, [a]);
    }
    return map;
  }, [assignments]);

  // Bugfix ("massiver Bug"): eine Konfliktzuweisung pro Tag reicht für die
  // Warnung -- gäbe es mehrere (theoretisch möglich bei mehr als zwei
  // Anstellungen), zeigt das Popover ohnehin nur die erste, die konkrete
  // Fehlermeldung beim tatsächlichen Beplanungsversuch bleibt die
  // vollständige, massgebliche Quelle.
  const otherTeamConflictMap = useMemo(() => {
    const map = new Map();
    for (const c of otherTeamConflicts) {
      const key = `${c.employee}:${c.date}`;
      if (!map.has(key)) map.set(key, c);
    }
    return map;
  }, [otherTeamConflicts]);

  const timeRecordByAssignment = useMemo(() => {
    const map = new Map();
    for (const r of timeRecords) map.set(r.assignment, r);
    return map;
  }, [timeRecords]);

  // README Block 2.9: Mindestbesetzung ist rein informativ (keine neue
  // Regel-Engine-Prüfung, kein eigener Endpoint) -- templates und
  // assignments sind für diese Station bereits vollständig geladen
  // (inkl. aller Team-Kinder, siehe ShiftAssignmentViewSet), ein
  // GROUP BY (date, template) lässt sich daher rein clientseitig bilden.
  const understaffedByDate = useMemo(() => {
    const relevantTemplates = templates.filter((t) => t.minimum_staffing > 0);
    if (relevantTemplates.length === 0) return new Map();
    const counts = new Map();
    for (const a of assignments) {
      const key = `${a.date}:${a.template}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const map = new Map();
    for (const d of days) {
      const date = isoDate(year, month, d);
      const shortfalls = [];
      for (const t of relevantTemplates) {
        const count = counts.get(`${date}:${t.id}`) ?? 0;
        if (count < t.minimum_staffing) shortfalls.push({ template: t, count });
      }
      if (shortfalls.length) map.set(date, shortfalls);
    }
    return map;
  }, [templates, assignments, days, year, month]);

  const preferenceMap = useMemo(() => {
    const map = new Map();
    for (const p of preferences) map.set(`${p.employee}:${p.date}`, p);
    return map;
  }, [preferences]);

  // Stempel-Leiste zeigt nur die Schichttypen der tatsächlich markierten
  // Zeilen (Team-Knoten aus dem dritten Teil jedes markedCells-Schlüssels,
  // siehe startMark) -- sind Zellen aus mehreren Teams markiert, wird die
  // Vereinigung ihrer jeweiligen Schichttypen angezeigt. Ein Schichttyp
  // direkt auf der Station (t.node === stationId) gilt als geteilter
  // Katalog für jede markierte Team-Zeile und zählt daher immer mit --
  // sonst würde die Stempelleiste bei stationsweiten Schichttypen (der
  // Normalfall, solange niemand manuell auf Team-Ebene umgestellt hat)
  // komplett leer bleiben.
  // README (2026-08, Bugfix): vorher lieferte dieser useMemo bei leerer
  // Auswahl `[]`, wodurch die komplette Stempelleiste (3 Zeilen) erst beim
  // ersten markierten Tag erschien -- das liess das ganze Planblatt genau in
  // dem Moment nach unten springen, in dem der Nutzer den ersten Tag anklickt,
  // wodurch nachfolgende Klicks/Ziehen auf die falschen, jetzt verschobenen
  // Zellen trafen. Fallback jetzt: die stationsweiten (geteilten) Vorlagen
  // schon vor jeder Markierung zeigen -- das ist der weit überwiegende Fall
  // (README Punkt 17: "ein Schichttyp auf einer Station steht allen Teams
  // gemeinsam zur Verfügung"), reserviert den Platz von Anfang an und wächst
  // nur noch in dem selteneren Fall team-spezifischer Vorlagen nach dem
  // Markieren.
  const stampTemplates = useMemo(() => {
    if (markedCells.size === 0) return templates.filter((t) => t.node === stationId);
    const markedRowNodeIds = new Set(Array.from(markedCells, (key) => Number(key.split(":")[2])));
    return templates.filter((t) => t.node === stationId || markedRowNodeIds.has(t.node));
  }, [templates, markedCells, stationId]);

  // Nutzer-Feedback (2026-08): Stempelleiste soll mehrzeilig sein -- eine
  // Zeile für reguläre Dienste, eine für Spezialitäten (z. B. Pikettdienst,
  // TimeTemplate.category === "special", siehe TimeTemplateSettings.jsx).
  // Beide Gruppen werden über dieselbe applyToolToMarked() gestempelt (eine
  // Spezialität ist technisch dasselbe wie ein regulärer Dienst, nur anders
  // eingeordnet für die Anzeige). Diese team-bewusste Auswahl (stampTemplates
  // oben) versorgt jetzt auch die Icon-Toolbar (PlacementToolbar) -- vorher
  // hatte die Toolbar ihre eigene, simplere "immer der volle Katalog"-Version
  // (placementRegularTemplates/placementSpecialTemplates), die mit der
  // Vereinheitlichung von Werkzeug-Toolbar und Mehrfachauswahl (s. o.)
  // überflüssig wurde.
  const regularStampTemplates = useMemo(
    () => stampTemplates.filter((t) => t.category !== "special"),
    [stampTemplates]
  );
  const specialStampTemplates = useMemo(
    () => stampTemplates.filter((t) => t.category === "special"),
    [stampTemplates]
  );

  function handlePlacementModeChange(mode) {
    setPlacementMode(mode);
  }

  async function handleSaveWish(date, existing, payload) {
    try {
      const saved = existing
        ? await api.updateShiftPreference(existing.id, payload)
        : await api.createShiftPreference({ date, ...payload });
      setPreferences((prev) => (existing ? prev.map((p) => (p.id === saved.id ? saved : p)) : [...prev, saved]));
      return true;
    } catch (e) {
      onError(e.message);
      return false;
    }
  }

  async function handleDeleteWish(preference) {
    try {
      await api.deleteShiftPreference(preference.id);
      setPreferences((prev) => prev.filter((p) => p.id !== preference.id));
      return true;
    } catch (e) {
      onError(e.message);
      return false;
    }
  }

  async function handleSaveTimeRecord(assignmentId, record, payload) {
    try {
      const saved = record
        ? await api.updateTimeRecord(record.id, { assignment: assignmentId, ...payload })
        : await api.createTimeRecord({ assignment: assignmentId, ...payload });
      setTimeRecords((prev) => (record ? prev.map((r) => (r.id === saved.id ? saved : r)) : [...prev, saved]));
      return true;
    } catch (e) {
      onError(e.message);
      return false;
    }
  }

  async function handleDeleteTimeRecord(record) {
    try {
      await api.deleteTimeRecord(record.id);
      setTimeRecords((prev) => prev.filter((r) => r.id !== record.id));
      return true;
    } catch (e) {
      onError(e.message);
      return false;
    }
  }

  function findAbsence(employeeId, date) {
    // ISO-Datumsstrings (YYYY-MM-DD) lassen sich direkt lexikographisch vergleichen.
    return absences.find(
      (a) => a.employee === employeeId && a.start_date <= date && date <= a.end_date
    );
  }

  // README Punkt 18 (Split-Shifts): assignmentId identifiziert bei einer
  // Zelle mit bis zu zwei Zuweisungen, WELCHE davon geändert/gelöscht
  // werden soll -- undefined (leerer Slot) legt stattdessen eine neue an.
  async function handleAssign(employeeId, date, templateId, rowNodeId, assignmentId) {
    try {
      if (templateId === null) {
        if (!assignmentId) return;
        await api.deleteShiftAssignment(assignmentId);
        setAssignments((prev) => prev.filter((a) => a.id !== assignmentId));
        return;
      }
      if (assignmentId) {
        const updated = await api.updateShiftAssignment(assignmentId, { template: templateId });
        setAssignments((prev) => prev.map((a) => (a.id === assignmentId ? updated : a)));
      } else {
        // README Punkt 17: rowNodeId ist bei einer Station mit Teams die
        // Team-Id der Zeile, sonst (keine Teams) die Station selbst -- eine
        // Zuweisung landet also nie mehr direkt auf einer Station mit Teams
        // (das würde das Backend ohnehin ablehnen, siehe
        // ShiftAssignment._check_node_has_no_children).
        const created = await api.createShiftAssignment({
          employee: employeeId,
          node: rowNodeId ?? nodeId,
          date,
          template: templateId,
        });
        setAssignments((prev) => [...prev, created]);
      }
    } catch (e) {
      // Greift z. B. bei einer Ruhezeit-Verletzung (siehe ShiftAssignment.clean() im Backend)
      onError(e.message);
    }
  }

  // Nutzer-Feedback (2026-08, Punkt 3): Absenzen fehlten im Einzelzell-Dropdown
  // komplett -- nur über die Mehrfachauswahl-Stempelleiste eintragbar. Löscht
  // zuerst eine ggf. vorhandene Zuweisung in diesem Slot (Absenz und Schicht
  // schliessen sich am selben Tag gegenseitig aus, siehe
  // ShiftAssignment._check_no_absence_conflict), legt dann eine Ein-Tages-
  // Absence an (start=end=date).
  async function handleAssignAbsence(employeeId, date, absenceTypeId, assignmentId) {
    try {
      if (assignmentId) {
        await api.deleteShiftAssignment(assignmentId);
        setAssignments((prev) => prev.filter((a) => a.id !== assignmentId));
      }
      const created = await api.createAbsence({
        employee: employeeId,
        start_date: date,
        end_date: date,
        type: absenceTypeId,
      });
      setAbsences((prev) => [...prev, created]);
    } catch (e) {
      onError(e.message);
    }
  }

  // Nutzer-Feedback (2026-08, Punkt 4): eine Spezialität (z. B. Pikettdienst)
  // ist ein additiver Zusatz zu einem bestehenden Dienst, kein Ersatz dafür --
  // technisch weiterhin ein ganz normaler ShiftAssignment (nur mit
  // category="special"), daher dünne Wrapper um die bestehenden
  // create/delete-Endpunkte statt eines neuen.
  async function handleAddSpecial(employeeId, date, rowNodeId, templateId) {
    try {
      const created = await api.createShiftAssignment({
        employee: employeeId,
        node: rowNodeId ?? nodeId,
        date,
        template: templateId,
      });
      setAssignments((prev) => [...prev, created]);
    } catch (e) {
      onError(e.message);
    }
  }

  async function handleRemoveSpecial(assignmentId) {
    try {
      await api.deleteShiftAssignment(assignmentId);
      setAssignments((prev) => prev.filter((a) => a.id !== assignmentId));
    } catch (e) {
      onError(e.message);
    }
  }

  // Dünner Wrapper analog handleRemoveAbsences (bulk) -- entfernt genau eine
  // Absenz, damit handleCellClick unten eine bestehende Absenz löschen kann,
  // bevor es ein anderes Werkzeug auf dieselbe Zelle anwendet ("ein
  // Zellklick ersetzt immer, was da ist").
  async function handleRemoveAbsence(absenceId) {
    try {
      await api.deleteAbsence(absenceId);
      setAbsences((prev) => prev.filter((a) => a.id !== absenceId));
    } catch (e) {
      onError(e.message);
    }
  }

  // Nutzer-Feedback (2026-08, Phase 2): "Oben überschreibt nur die obere
  // Hälfte" -- eine bestehende GANZTÄGIGE Absenz (an einem einzelnen Tag)
  // wird beim Bestempeln nur einer Hälfte nicht mehr komplett gelöscht,
  // sondern auf die jeweils NICHT angeklickte Hälfte reduziert (day_portion
  // full -> morning/afternoon, gleicher Typ, gleiches Datum). Absence hat
  // (anders als ShiftAssignment) ein explizites day_portion-Feld -- ein
  // sauberer Delete-und-Neuanlegen-Mechanismus dafür, im Gegensatz zum
  // Dienst, der bei diesem Vorgang unverändert bleibt (siehe
  // shiftShouldBeReplacedByAbsencePortion() weiter unten).
  async function handleShrinkAbsence(absence, keepPortion) {
    try {
      await api.deleteAbsence(absence.id);
      const created = await api.createAbsence({
        employee: absence.employee,
        start_date: absence.start_date,
        end_date: absence.end_date,
        type: absence.type,
        day_portion: keepPortion,
      });
      setAbsences((prev) => [...prev.filter((a) => a.id !== absence.id), created]);
    } catch (e) {
      onError(e.message);
    }
  }

  // Workflow-Redesign (2026-08): pro-Zelle-Anwendung eines Werkzeugs
  // (Dienst/Radiergummi -- Absenzen laufen separat über applyToolToMarked
  // unten, siehe dort). Vorher hiess das handleCellClick() und wurde direkt
  // von einem Zellklick ausgelöst (mit dem "bewaffneten" armedTool aus dem
  // State); jetzt ist `tool` ein expliziter Parameter, aufgerufen aus
  // applyToolToMarked() für jede markierte Zelle -- ein Klick auf eine
  // Zelle markiert nur noch, er wendet nichts mehr direkt an. slot0/slot1
  // sind regularAssignments[0]/[1] der Zelle, specials die
  // Spezialitäten-Zuweisungen (Pikett-Zeile) desselben Tages.
  // Absenz-Entfernung läuft NICHT mehr hier drin, sondern zentral vorab in
  // applyToolToMarked() (dedupliziert nach Absenz-ID) -- siehe Kommentar
  // dort, Bugfix für "No Absence matches the given query".
  //
  // Bugfix (Nutzer-Feedback): im Pikett-Modus liess sich eine Spezialität
  // bisher NUR hinzufügen -- ein zweiter Klick auf dasselbe Icon legte
  // versehentlich eine DUPLIZIERTE Zuweisung an (additiv, kein Toggle), und
  // der Radiergummi war im Pikett-Modus komplett ausgeblendet, es gab also
  // gar keinen Weg, eine Spezialität über die Toolbar wieder zu entfernen
  // (nur einzeln über das Popover in SpecialBadge.jsx). Jetzt: ein Klick auf
  // ein Spezialität-Icon TOGGELT (schon vorhanden -> entfernen, sonst ->
  // hinzufügen, pro markierter Zelle einzeln geprüft), der Radiergummi
  // entfernt im Pikett-Modus ALLE Spezialitäten der markierten Zellen auf
  // einmal (Analogie zu "Ganz" bei Diensten).
  async function applyToolToCell(tool, employeeId, date, rowNodeId, slot0, slot1, specials = [], absenceHandled = false) {
    if (tool.kind === "template") {
      if (placementMode === "pikett") {
        const existing = specials.find((s) => s.template === tool.id);
        if (existing) await handleRemoveSpecial(existing.id);
        else await handleAddSpecial(employeeId, date, rowNodeId, tool.id);
        return;
      }
      if (placementMode === "full") {
        if (slot1) await handleAssign(employeeId, date, null, rowNodeId, slot1.id);
        await handleAssign(employeeId, date, tool.id, rowNodeId, slot0?.id);
      } else if (placementMode === "top" || placementMode === "bottom") {
        // Nutzer-Feedback (2026-08): "oben/unten überschreibt nur die
        // jeweilige Hälfte" -- ein bestehender EINZELNER (durchgehender)
        // Dienst wird dabei nie umbenannt/ersetzt, sondern bleibt exakt der
        // Datensatz, der er ist (z. B. beim Playwright-Test dieser Session
        // beobachtet: "Unten"+Nachmittag, danach "Oben"+Vormittag hat den
        // Nachmittag-Dienst fälschlich in Vormittag umbenannt statt beide
        // nebeneinander anzulegen). Nur wenn BEREITS zwei eigenständige
        // Dienste an diesem Tag liegen (ein echter Split), wird gezielt der
        // in der Zielhälfte ersetzt -- sonst wird immer ein NEUER,
        // unabhängiger Dienst angelegt. Ein echter Zeit-Overlap zwischen dem
        // neuen und dem bestehenden Dienst wird dabei weiterhin korrekt vom
        // Backend (ShiftAssignment._check_no_overlap) verhindert.
        const isSplit = Boolean(slot0) && Boolean(slot1);
        const target = placementMode === "top" ? slot0 : slot1;
        await handleAssign(employeeId, date, tool.id, rowNodeId, isSplit ? target?.id : undefined);
      }
      return;
    }

    if (tool.kind === "empty") {
      if (placementMode === "pikett") {
        for (const special of specials) await handleRemoveSpecial(special.id);
        return;
      }
      if (placementMode === "full") {
        if (slot0) await handleAssign(employeeId, date, null, rowNodeId, slot0.id);
        if (slot1) await handleAssign(employeeId, date, null, rowNodeId, slot1.id);
      } else if (placementMode === "top" || placementMode === "bottom") {
        // Analog oben: bei einem ECHTEN Split (zwei eigenständige Dienste)
        // löscht der Radiergummi gezielt nur den Dienst in der Zielhälfte.
        // Nutzer-Feedback (2026-08): ein einzelner, durchgehender Dienst
        // liess sich bisher in Oben/Unten NICHT löschen (stiller No-Op,
        // `if (!isSplit) return`) -- verwirrend, wenn dort z. B. neben einer
        // Halbtags-Absenz noch ein durchgehender Dienst steht und man genau
        // diesen per Klick entfernen will. Ein einzelner Dienst lässt sich
        // aber nicht halbieren (kein day_portion-Feld wie bei Absence) -- er
        // wird deshalb komplett gelöscht, unabhängig davon ob "Oben" oder
        // "Unten" geklickt wurde (beide Klicks zielen ja auf denselben,
        // einzigen Datensatz).
        //
        // Bugfix (Nutzer-Feedback 2026-08): trifft der Klick stattdessen die
        // Hälfte, in der bereits eine Absenz sitzt (deckungsgleicher
        // placementMode, `absenceHandled` von applyToolToMarked() gesetzt),
        // ist die Absenz das eigentliche Ziel -- ein koexistierender,
        // durchgehender Dienst (reicht per Definition in die ANDERE Hälfte
        // hinein) darf dann NICHT zusätzlich gelöscht werden, sonst löscht
        // "Unten Ferien entfernen" fälschlich auch den Frühdienst mit.
        const isSplit = Boolean(slot0) && Boolean(slot1);
        if (isSplit) {
          const target = placementMode === "top" ? slot0 : slot1;
          await handleAssign(employeeId, date, null, rowNodeId, target.id);
        } else if (slot0 && !absenceHandled) {
          await handleAssign(employeeId, date, null, rowNodeId, slot0.id);
        }
      }
    }
  }

  // Löst für eine Zellenkoordinate (employeeId, date, rowNodeId) ihren
  // aktuellen Zustand auf -- dieselbe Logik wie im Render-Loop weiter unten
  // (regularAssignments/specialAssignments/absence), aber als eigenständige
  // Funktion, damit applyToolToMarked() sie für JEDE markierte Zelle einzeln
  // aufrufen kann, ausserhalb des JSX-Loops.
  function resolveCellState(employeeId, date, rowNodeId) {
    // Bugfix: chronologisch nach Beginnzeit sortieren (wie im Render-Loop
    // unten), sonst könnte "Oben"/"Unten" hier den falschen der beiden
    // Slots treffen, wenn rowAssignmentsFor sie in anderer Reihenfolge
    // liefert als sie angezeigt werden (oben=früher/unten=später).
    const cellAssignments = rowAssignmentsFor(employeeId, date, rowNodeId)
      .slice()
      .sort((a, b) => {
        const ta = templates.find((t) => t.id === a.template);
        const tb = templates.find((t) => t.id === b.template);
        return (ta?.start_time ?? "").localeCompare(tb?.start_time ?? "");
      });
    const regularAssignments = cellAssignments.filter(
      (a) => templates.find((t) => t.id === a.template)?.category !== "special"
    );
    const specialAssignments = cellAssignments.filter(
      (a) => templates.find((t) => t.id === a.template)?.category === "special"
    );
    return { regularAssignments, specialAssignments, absence: findAbsence(employeeId, date) };
  }

  // JS-Gegenstück zu Absence._shift_extends_into_other_half() im Backend
  // (scheduling/models.py). Nutzer-Feedback (2026-08): "ein normaler
  // (durchgehender) Dienst bleibt bei einer Halbtags-Absenz unverändert
  // stehen -- Krankheit/Ferien sind arbeitszeitrechtlich weiterhin
  // Arbeitszeit (Lohnfortzahlungspflicht, Schweizer ArG)". Ein Dienst wird
  // beim Stempeln einer Halbtags-Absenz nur dann geräumt, wenn er
  // AUSSCHLIESSLICH in der Zielhälfte liegt (ein echter, eigenständiger
  // Halbtags-Dienst, z. B. "Nachmittag" 13:30-17:00 bei einer
  // Nachmittags-Absenz) -- reicht er auch in die jeweils ANDERE Hälfte
  // hinein (ein durchgehender Dienst über Mittag hinweg), bleibt er
  // unangetastet. Ein über Mitternacht laufender Dienst (Ende <= Start,
  // z. B. Nachtwache 20:00–07:00) gilt konservativ immer als "reicht in die
  // andere Hälfte hinein" (wird nie automatisch geräumt).
  function shiftShouldBeReplacedByAbsencePortion(templateId, dayPortion) {
    if (dayPortion === "full") return true;
    const template = templates.find((t) => t.id === templateId);
    if (!template?.start_time || !template?.end_time) return true;
    const toMinutes = (t) => {
      const [h, m] = t.split(":").map(Number);
      return h * 60 + m;
    };
    const start = toMinutes(template.start_time);
    const end = toMinutes(template.end_time);
    if (end <= start) return false; // über Mitternacht -- nie automatisch ersetzen
    const noon = 12 * 60;
    const overlapsTargetPortion = dayPortion === "morning" ? start < noon : end > noon;
    if (!overlapsTargetPortion) return false; // berührt die Zielhälfte gar nicht
    const extendsIntoOtherHalf = dayPortion === "morning" ? end > noon : start < noon;
    return !extendsIntoOtherHalf;
  }

  // Zentrale Stempel-Funktion (Workflow-Redesign 2026-08): wendet `tool`
  // (Dienst/Absenz/Radiergummi aus der Icon-Toolbar) auf ALLE aktuell
  // markierten Zellen an -- bei nur einer markierten Zelle exakt das
  // Verhalten des früheren Einzelklicks. Absenzen sind ein Sonderfall:
  // anders als Dienste/Radiergummi (die pro Tag ohnehin einzelne
  // ShiftAssignment-Datensätze sind) ist eine Absenz ein zusammenhängender
  // Zeitraum (start_date/end_date) -- mehrere markierte, aufeinander
  // folgende Tage sollen EINEN Absenz-Eintrag ergeben, nicht einen pro Tag
  // (sonst zeigt AbsencePanel.jsx z. B. eine Ferienwoche als fünf einzelne
  // Zeilen statt einer). Erst wie gewohnt räumen (jede Zelle ersetzt, was
  // vorher da war), dann je Mitarbeiter zu möglichst wenigen
  // zusammenhängenden Zeiträumen gruppiert neu anlegen (gleiches Muster wie
  // YearPlan.jsx: groupConsecutiveDates).
  async function applyToolToMarked(tool) {
    if (markedCells.size === 0) return;
    const keys = Array.from(markedCells);

    // Nutzer-Feedback (2026-08): "Absenzen sollen genau gleich zuteilbar
    // sein" wie Dienste -- Oben/Unten/Alles bestimmt jetzt auch bei einer
    // Absenz, welche Tageshälfte betroffen ist (Absence.day_portion, siehe
    // scheduling/models.py), statt eine Absenz immer als ganzen Tag
    // anzulegen. "Alles" ersetzt weiterhin GANZ, was an bestehender Absenz
    // auf dem Tag lag. "Oben"/"Unten" lässt eine bestehende Absenz der
    // JEWEILS ANDEREN Hälfte in Ruhe, damit ein "Vormittags frei,
    // nachmittags Dienst"-Tag nicht durch das blosse Beplanen der
    // Dienst-Hälfte wieder verschwindet.
    //
    // Phase 2 (Nutzer-Feedback: "Oben überschreibt nur die obere Hälfte"):
    // eine bestehende GANZTÄGIGE Absenz an einem EINZELNEN Tag wird beim
    // Bestempeln nur einer Hälfte mit einem DIENST oder dem Radiergummi
    // nicht mehr komplett gelöscht, sondern auf die nicht angeklickte
    // Hälfte reduziert (handleShrinkAbsence) -- ein Dienst "oben" lässt eine
    // bisher ganztägige Absenz z. B. nur noch "unten" bestehen. Stempelt man
    // dagegen selbst eine ANDERE Absenz auf die Zielhälfte (tool.kind ===
    // "absence"), bleibt es beim einfachen Ein-Absenz-pro-Tag-Modell (volles
    // Löschen) -- zwei verschiedene Absenzarten am selben Tag nebeneinander
    // würde die Zell-Anzeige (die pro Tag nur EINE Absenz kennt,
    // findAbsence()) nicht abbilden können. Bei einer MEHRTÄGIGEN Absenz
    // (z. B. eine Ferienwoche) wäre eine Reduktion ausserdem ein
    // Range-Split (den einzelnen Tag aus dem Zeitraum heraustrennen) --
    // bewusst nicht Teil dieses Features, dort bleibt es beim vollständigen
    // Löschen (bestehendes Verhalten).
    const absenceIdsToClear = new Set();
    const absencesToShrink = new Map(); // absenceId -> { absence, keepPortion }
    // Bugfix (Nutzer-Feedback 2026-08): "Frühdienst eingeplant, Unten
    // halber Tag Ferien, Unten halber Tag Ferien wieder entfernen löscht
    // auch den Dienst". Der Radiergummi räumt in Oben/Unten sowohl Absenzen
    // (oben) als auch -- seit dem Bugfix zum "Dienst lässt sich in
    // Oben/Unten gar nicht löschen" -- einen einzelnen, durchgehenden
    // Dienst (siehe applyToolToCell). Trifft der Klick aber genau die
    // Hälfte, in der bereits eine Absenz sitzt (deckungsgleicher
    // placementMode), ist die Absenz das eigentliche Ziel -- der
    // koexistierende, durchgehende Dienst (der ja per Definition NICHT nur
    // in dieser Hälfte liegt, siehe shiftShouldBeReplacedByAbsencePortion)
    // darf dann nicht zusätzlich gelöscht werden. datesWithHandledAbsence
    // merkt sich, für welche Tage die Absenz-Vorräumung oben tatsächlich
    // etwas getan hat, damit applyToolToCell() weiter unten die
    // Dienst-Löschung für genau diese Tage überspringt.
    const datesWithHandledAbsence = new Set();
    for (const key of keys) {
      const [employeeIdStr, date] = key.split(":");
      const absence = findAbsence(Number(employeeIdStr), date);
      if (!absence) continue;
      if (tool.kind === "absence" || placementMode === "full") {
        absenceIdsToClear.add(absence.id);
        datesWithHandledAbsence.add(date);
        continue;
      }
      if (placementMode === "pikett") continue; // Pikett betrifft nie Absenzen
      const existingPortion = absence.day_portion ?? "full";
      if (existingPortion === "full") {
        if (absence.start_date === absence.end_date) {
          const keepPortion = placementMode === "top" ? "afternoon" : "morning";
          absencesToShrink.set(absence.id, { absence, keepPortion });
        } else {
          absenceIdsToClear.add(absence.id);
        }
        datesWithHandledAbsence.add(date);
        continue;
      }
      const overlapsThisMode =
        (placementMode === "top" && existingPortion === "morning") ||
        (placementMode === "bottom" && existingPortion === "afternoon");
      if (overlapsThisMode) {
        absenceIdsToClear.add(absence.id);
        datesWithHandledAbsence.add(date);
      }
    }
    // Bugfix ("No Absence matches the given query"): eine Absenz ist EIN
    // Datensatz über einen ganzen Zeitraum (start_date/end_date) -- markiert
    // man mehrere Tage, die zur selben Absenz gehören (z. B. alle drei Tage
    // einer bestehenden Ferienwoche), lösten frühere Versuche pro markierter
    // Zelle einzeln `handleRemoveAbsence(absence.id)` aus: derselbe
    // Datensatz wurde dadurch mehrfach zu löschen versucht, der zweite
    // Versuch schlug serverseitig fehl (404, da schon gelöscht). Ein Set
    // dedupliziert automatisch, jede betroffene Absenz wird genau einmal
    // gelöscht bzw. reduziert, bevor irgendein Werkzeug angewendet wird.
    for (const absenceId of absenceIdsToClear) {
      await handleRemoveAbsence(absenceId);
    }
    for (const { absence, keepPortion } of absencesToShrink.values()) {
      await handleShrinkAbsence(absence, keepPortion);
    }

    if (tool.kind === "absence") {
      const dayPortion = placementMode === "top" ? "morning" : placementMode === "bottom" ? "afternoon" : "full";
      for (const key of keys) {
        const [employeeIdStr, date, rowNodeIdStr] = key.split(":");
        const employeeId = Number(employeeIdStr);
        const rowNodeId = Number(rowNodeIdStr);
        const { regularAssignments, specialAssignments } = resolveCellState(employeeId, date, rowNodeId);
        // Nutzer-Feedback (2026-08): "ein normaler Dienst bleibt bei einer
        // Halbtags-Absenz unverändert stehen -- oben bleibt Dienst, unten
        // wird frei" (bzw. umgekehrt). NICHT blind nach Slot-Index räumen
        // ("Oben"=Index 0) -- ein durchgehender Dienst (reicht auch in die
        // jeweils andere Hälfte hinein) wird nie geräumt, nur ein echter,
        // ausschliesslich in der Zielhälfte liegender Dienst wird ersetzt
        // (shiftShouldBeReplacedByAbsencePortion(), spiegelt
        // Absence._shift_extends_into_other_half() im Backend).
        for (const a of regularAssignments) {
          if (shiftShouldBeReplacedByAbsencePortion(a.template, dayPortion)) {
            await handleAssign(employeeId, date, null, rowNodeId, a.id);
          }
        }
        if (dayPortion === "full") {
          for (const special of specialAssignments) await handleRemoveSpecial(special.id);
        }
      }
      const created = [];
      if (dayPortion === "full") {
        // Ganztägig weiterhin zu möglichst wenigen zusammenhängenden
        // Zeiträumen gruppiert (z. B. eine Ferienwoche = ein Datensatz statt
        // fünf). Ein Tagesanteil ist laut Backend (Absence.clean()) nur für
        // EINEN einzelnen Tag gültig, deshalb unten je markiertem Tag ein
        // eigener Datensatz statt einer Gruppierung.
        const byEmployee = absenceDatesByEmployee(keys);
        for (const [employeeId, dateSet] of byEmployee) {
          for (const [start, end] of groupConsecutiveDates(Array.from(dateSet))) {
            try {
              created.push(
                await api.createAbsence({ employee: employeeId, start_date: start, end_date: end, type: tool.id, day_portion: "full" })
              );
            } catch (e) {
              onError(e.message);
            }
          }
        }
      } else {
        for (const key of keys) {
          const [employeeIdStr, date] = key.split(":");
          const employeeId = Number(employeeIdStr);
          try {
            created.push(
              await api.createAbsence({ employee: employeeId, start_date: date, end_date: date, type: tool.id, day_portion: dayPortion })
            );
          } catch (e) {
            onError(e.message);
          }
        }
      }
      if (created.length) setAbsences((prev) => [...prev, ...created]);
      setMarkedCells(new Set());
      return;
    }

    for (const key of keys) {
      const [employeeIdStr, date, rowNodeIdStr] = key.split(":");
      const employeeId = Number(employeeIdStr);
      const rowNodeId = Number(rowNodeIdStr);
      const { regularAssignments, specialAssignments } = resolveCellState(employeeId, date, rowNodeId);
      await applyToolToCell(
        tool,
        employeeId,
        date,
        rowNodeId,
        regularAssignments[0],
        regularAssignments[1],
        specialAssignments,
        datesWithHandledAbsence.has(date)
      );
    }
    setMarkedCells(new Set());
  }

  // README Punkt 18 (Split-Shifts): Quelle UND Ziel werden direkt über ihre
  // assignmentId aufgelöst statt über employee+date (das ist bei mehreren
  // Zuweisungen desselben Tages nicht mehr eindeutig) -- fromAssignmentId
  // kommt aus dem Drag-Payload (ShiftCell.jsx), toAssignmentId/toRowNodeId
  // aus dem Render-Closure der Zielzelle (PlanGrid kennt ihre eigene
  // Zuweisung bereits, kein erneutes Suchen nötig, anders als früher).
  async function handleMove(fromAssignmentId, toEmployeeId, toDate, toRowNodeId, toAssignmentId) {
    if (!fromAssignmentId || fromAssignmentId === toAssignmentId) return;
    const source = assignments.find((a) => a.id === fromAssignmentId);
    if (!source) return;
    const target = toAssignmentId ? assignments.find((a) => a.id === toAssignmentId) : undefined;
    try {
      if (target) {
        // Echter Swap statt Ablehnung: tauscht employee/date/node zwischen
        // den beiden Zuweisungen (siehe ShiftAssignment.swap() im Backend).
        const { first, second } = await api.swapShiftAssignments(source.id, target.id);
        setAssignments((prev) =>
          prev.map((a) => (a.id === first.id ? first : a.id === second.id ? second : a))
        );
      } else {
        const updated = await api.updateShiftAssignment(source.id, {
          employee: toEmployeeId,
          date: toDate,
          node: toRowNodeId,
        });
        setAssignments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      }
    } catch (e) {
      // Greift z. B. bei einer Ruhezeit-Verletzung am Zieltag (siehe ShiftAssignment.clean())
      onError(e.message);
    }
  }

  // README Punkt 18 (Split-Shifts): eine Quellzeile kann jetzt mehrere
  // Zuweisungen pro Tag haben (z. B. Früh- + Spätdienst) -- alle werden
  // kopiert, nicht nur die erste. Zusätzlich (Nachbesserung, vorher latent
  // falsch bei Mehrfachanstellung): nur die Zuweisungen DIESER Zeile
  // (rowNodeId) zählen als Quelle/"schon belegt", eine Zuweisung derselben
  // Person in einem ANDEREN Team an diesem Tag darf weder als Vorlage
  // dienen noch das Kopieren blockieren.
  function rowAssignmentsFor(employeeId, targetDate, rowNodeId) {
    return (assignmentMap.get(`${employeeId}:${targetDate}`) ?? []).filter((a) => a.node === rowNodeId);
  }

  async function handleCopyWeekPattern(employeeId, rowNodeId) {
    const sourceDays = days.filter((d) => d <= 7);
    const hasSourceShift = sourceDays.some(
      (d) => rowAssignmentsFor(employeeId, isoDate(year, month, d), rowNodeId).length > 0
    );
    if (!hasSourceShift) {
      onError("Die erste Woche hat für diesen Mitarbeiter noch keine Schichten zum Kopieren.");
      return;
    }

    const created = [];
    let skippedByConflict = 0;
    for (let targetDay = 8; targetDay <= days.length; targetDay += 1) {
      const sourceDay = ((targetDay - 1) % 7) + 1;
      const sourceAssignments = rowAssignmentsFor(employeeId, isoDate(year, month, sourceDay), rowNodeId);
      if (!sourceAssignments.length) continue;
      const targetDate = isoDate(year, month, targetDay);
      if (rowAssignmentsFor(employeeId, targetDate, rowNodeId).length > 0) continue; // bestehende Einträge nicht überschreiben

      for (const source of sourceAssignments) {
        try {
          const createdAssignment = await api.createShiftAssignment({
            employee: employeeId,
            node: rowNodeId ?? nodeId,
            date: targetDate,
            template: source.template,
          });
          created.push(createdAssignment);
        } catch {
          // z. B. Ruhezeit- oder Höchstarbeitszeit-Konflikt an diesem Tag -- Zuweisung
          // überspringen, restliche Wochen/Slots trotzdem weiterkopieren.
          skippedByConflict += 1;
        }
      }
    }

    if (created.length) {
      setAssignments((prev) => [...prev, ...created]);
    }
    if (skippedByConflict > 0) {
      onError(
        `Wochenmuster kopiert, ${skippedByConflict} Zuweisung(en) wegen Regel-Konflikten (z. B. Ruhezeit) übersprungen.`
      );
    }
  }

  function applyMark(key, shouldMark) {
    setMarkedCells((prev) => {
      if (prev.has(key) === shouldMark) return prev;
      const next = new Set(prev);
      if (shouldMark) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  // Setzt beim ersten Antippen/Klicken einer Zelle den Ziehen-Modus (markieren
  // oder entmarkieren, je nachdem, ob die Zelle schon markiert war) und wendet
  // ihn gleich auf diese Zelle an. Bei einem einfachen Klick ohne Ziehen ist
  // das schlicht ein Toggle; beim Ziehen wenden nachfolgende continueMark()-
  // Aufrufe denselben Modus auf weitere Zellen an. Der Schlüssel trägt die
  // Zeilen-Node-Id mit (README Punkt 17) -- bei Teams braucht handleStampAssign
  // sonst keine Möglichkeit zu wissen, auf welches Team eine markierte Zelle
  // gehört.
  function startMark(employeeId, date, rowNodeId) {
    const key = `${employeeId}:${date}:${rowNodeId}`;
    const shouldMark = !markedCells.has(key);
    dragMarkModeRef.current = shouldMark ? "mark" : "unmark";
    applyMark(key, shouldMark);
  }

  function continueMark(employeeId, date, rowNodeId) {
    if (!dragMarkModeRef.current) return;
    applyMark(`${employeeId}:${date}:${rowNodeId}`, dragMarkModeRef.current === "mark");
  }

  // Bugfix (Nutzer-Feedback): reines Klick-Toggle für den `onClick`-Pfad in
  // ShiftCell.jsx (belegte/draggable Zellen ohne eigenen mousedown-Handler,
  // s. dort -- sowie der Tastatur-Fallback bei leeren/Absenz-Zellen). Anders
  // als startMark() setzt diese Funktion dragMarkModeRef NICHT: startMark()
  // ist für die mousedown-gestartete Ziehmarkierung gedacht, deren
  // Ziehmodus per globalem window-"mouseup" wieder beendet wird (s. o.).
  // Ein `click`-Event feuert aber IMMER NACH diesem mouseup -- rief die
  // click-Fallback bislang ebenfalls startMark() auf, blieb
  // dragMarkModeRef unbemerkt "aktiv" hängen (kein weiteres mouseup folgt
  // mehr), und jede spätere Mausbewegung über andere Zellen (auch OHNE
  // gedrückte Taste) markierte über continueMark() ungewollt weiter --
  // genau das vom Nutzer beschriebene Verhalten bei belegten Zellen.
  function toggleMark(employeeId, date, rowNodeId) {
    const key = `${employeeId}:${date}:${rowNodeId}`;
    applyMark(key, !markedCells.has(key));
  }

  // Markierte Zellen (Schlüssel employeeId:date:rowNodeId) auf ihre reinen
  // Datumsmengen je Mitarbeiter reduzieren -- rowNodeId ist für Absenzen
  // irrelevant (eine Absenz gehört zur Person, nicht zum Team), ein Set
  // dedupliziert automatisch, falls dieselbe Person an diesem Tag in
  // mehreren Team-Zeilen markiert wurde (Mehrfachanstellung, README Punkt 17).
  function absenceDatesByEmployee(keys) {
    const map = new Map();
    for (const key of keys) {
      const [employeeIdStr, date] = key.split(":");
      const employeeId = Number(employeeIdStr);
      if (!map.has(employeeId)) map.set(employeeId, new Set());
      map.get(employeeId).add(date);
    }
    return map;
  }

  // README Punkt 18: assignmentId statt employeeId+date -- bei mehreren
  // Zuweisungen desselben Tages (Split-Shifts) ist jede unabhängig
  // tauschbar, die frühere Ableitung über employee+date wäre mehrdeutig.
  // MVP-Fahrplan Block 2, Punkt 5: Planblatt-Export -- für alle Rollen
  // sichtbar (nicht nur canManage), da Mitarbeitende dasselbe Planblatt für
  // ihre eigene(n) Station(en) ohnehin schon sehen (siehe
  // scheduling.views.PlanExportView-Docstring zur Sichtbarkeit).
  const [exportingFormat, setExportingFormat] = useState(null);

  async function handleExportPlan(outputFormat) {
    setExportingFormat(outputFormat);
    try {
      const monthParam = `${year}-${String(month).padStart(2, "0")}`;
      await api.downloadPlanExport(nodeId, monthParam, outputFormat);
    } catch (e) {
      onError(e.message);
    } finally {
      setExportingFormat(null);
    }
  }

  async function handleOfferTrade(assignmentId, targetEmployeeId) {
    try {
      await api.createShiftTradeRequest({
        requester_assignment: assignmentId,
        target_employee: targetEmployeeId,
      });
      const targetName = employees.find((e) => e.id === targetEmployeeId);
      onError(
        `Tausch angeboten an ${targetName ? `${targetName.first_name} ${targetName.last_name}` : "Kolleg:in"} ` +
          "-- siehe Tab „Diensttausch“."
      );
    } catch (e) {
      onError(e.message);
    }
  }

  if (loading) return <p className="loading-state">Planblatt wird geladen …</p>;
  if (!employees.length) {
    return (
      <p className="empty-state">
        Keine Mitarbeiter dieser Station zugeordnet. Im Admin unter „Employees“ ergänzen.
      </p>
    );
  }

  return (
    <>
      <div className="plan-export-bar">
        <button type="button" onClick={() => handleExportPlan("pdf")} disabled={exportingFormat !== null}>
          {exportingFormat === "pdf" ? "…" : "Als PDF exportieren"}
        </button>
        <button type="button" onClick={() => handleExportPlan("csv")} disabled={exportingFormat !== null}>
          {exportingFormat === "csv" ? "…" : "Als CSV exportieren"}
        </button>
      </div>
      {canManage && (
        <PlacementToolbar
          placementMode={placementMode}
          onPlacementModeChange={handlePlacementModeChange}
          regularTemplates={regularStampTemplates}
          specialTemplates={specialStampTemplates}
          absenceTypes={absenceTypes}
          markedCount={markedCells.size}
          onApplyTool={applyToolToMarked}
          onClearMarked={() => setMarkedCells(new Set())}
        />
      )}
      <div className="grid-scroll">
        <table className="plan-grid">
          <thead>
            <tr>
              <th className="col-employee">Mitarbeiter</th>
              {days.map((d) => {
                const date = isoDate(year, month, d);
                const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                const holidayName = holidays.get(date);
                return (
                  <th
                    key={d}
                    className={[weekend && "is-weekend", holidayName && "is-holiday"].filter(Boolean).join(" ")}
                    title={holidayName || undefined}
                  >
                    <span className="day-num">{d}</span>
                    <span className="day-weekday">{weekdayLabel(year, month, d)}</span>
                    <DayStaffingBadge shortfalls={understaffedByDate.get(date) ?? []} />
                  </th>
                );
              })}
              {/* Block 2.7: nur für Admin/Planer -- eigene, am rechten Rand
                  fixierte Spalte statt in die ohnehin schon volle
                  Mitarbeiter-Zelle gequetscht, damit der Saldo unabhängig
                  von der Scroll-Position sichtbar bleibt. */}
              {canManage && <th className="col-balance">Saldo</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              if (row.type === "divider") {
                return (
                  <tr key={row.key} className="team-divider">
                    <td colSpan={days.length + 1 + (canManage ? 1 : 0)}>{row.label}</td>
                  </tr>
                );
              }
              if (row.type === "unassigned") {
                const emp = row.emp;
                return (
                  <tr key={row.key} className="employment-unassigned-row">
                    <th scope="row" className="col-employee">
                      <span className="employee-row-inner">
                        <span className="employee-name">
                          {emp.first_name} {emp.last_name}
                        </span>
                      </span>
                    </th>
                    <td
                      colSpan={days.length + (canManage ? 1 : 0)}
                      title="Diese Person hat kein Team an dieser Station -- Zuweisung erst möglich, nachdem ihr im Mitarbeiter-Formular eine Anstellung für ein Team dieser Station zugewiesen wurde."
                    >
                      Kein Team zugeordnet
                    </td>
                  </tr>
                );
              }

              const { emp, employment, rowNodeId } = row;
              const pctLabel = employment
                ? `${employment.pensum_pct}%${employment.title ? ` ${employment.title}` : ""}`
                : `${emp.employment_pct}%`;
              return (
                <tr key={row.key}>
                  <th scope="row" className="col-employee">
                    <span className="employee-row-inner">
                      <span className="employee-name">
                        {emp.first_name} {emp.last_name}
                      </span>
                      {employment?.is_team_lead && (
                        <span className="lead-badge" title="Teamleitung">
                          ★
                        </span>
                      )}
                      <span className="pct">{pctLabel}</span>
                      {canManage && (
                        <button
                          type="button"
                          className="btn-copy-week"
                          title="Muster der ersten Woche auf die restlichen Wochen dieses Monats kopieren (belegte Tage bleiben unverändert)"
                          onClick={() => handleCopyWeekPattern(emp.id, rowNodeId)}
                        >
                          ⧉<span className="visually-hidden"> Wochenmuster kopieren für {emp.first_name} {emp.last_name}</span>
                        </button>
                      )}
                    </span>
                  </th>
                  {days.map((d) => {
                    const date = isoDate(year, month, d);
                    // README Punkt 17: bei mehreren Anstellungen derselben
                    // Person in unterschiedlichen Teams zeigt eine Zeile nur
                    // die Zuweisungen, die tatsächlich zu ihrem eigenen Team
                    // gehören (a.node) -- die Schicht(en) der jeweils
                    // anderen Rolle bleiben in dieser Zeile korrekt leer.
                    // README Punkt 18 (Split-Shifts): jetzt potenziell MEHR
                    // als eine, chronologisch nach Beginnzeit sortiert, damit
                    // die gestapelten Chips oben=früher/unten=später zeigen.
                    const cellAssignments = rowAssignmentsFor(emp.id, date, rowNodeId)
                      .slice()
                      .sort((a, b) => {
                        const ta = templates.find((t) => t.id === a.template);
                        const tb = templates.find((t) => t.id === b.template);
                        return (ta?.start_time ?? "").localeCompare(tb?.start_time ?? "");
                      });
                    // Nutzer-Feedback (2026-08, Punkt 4): eine Spezialität (z. B.
                    // Pikettdienst) ist ein additiver Zusatz zu einem Dienst, kein
                    // Konkurrent um Slot 0/1 -- daher getrennt von den regulären
                    // Zuweisungen behandelt und als kleiner Eck-Badge dargestellt
                    // (SpecialBadge.jsx, siehe unten), statt Platz in der Zelle
                    // zu beanspruchen.
                    // Wichtig für Halbtags-Absenzen (siehe slotAbsence/slotAssignment
                    // unten): dank der zeitbewussten Konfliktprüfung
                    // (ShiftAssignment._check_no_absence_conflict) enthält
                    // regularAssignments in diesem Fall höchstens EINEN Eintrag --
                    // die Absenz belegt ja bereits die andere Tageshälfte.
                    const regularAssignments = cellAssignments.filter(
                      (a) => templates.find((t) => t.id === a.template)?.category !== "special"
                    );
                    const specialAssignments = cellAssignments.filter(
                      (a) => templates.find((t) => t.id === a.template)?.category === "special"
                    );
                    const absence = findAbsence(emp.id, date);
                    const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                    const holidayName = holidays.get(date);
                    const canOfferTrade = canManage || me?.employee?.id === emp.id;
                    // Workflow-Redesign (2026-08): Markierung ist jetzt pro Tag
                    // (nicht pro Slot) -- derselbe Wert geht an beide
                    // renderSlot()-Aufrufe eines Split-Tages, damit ein Klick auf
                    // IRGENDEINEN der beiden Slots denselben Tag markiert/entmarkiert.
                    const cellKey = `${emp.id}:${date}:${rowNodeId}`;
                    const marked = markedCells.has(cellKey);
                    // Nutzer-Feedback (2026-08): der zweite Slot -- und damit
                    // der horizontale Split (oben/unten) -- erscheint, wenn
                    // WIRKLICH zwei Dienste an diesem Tag liegen ODER eine
                    // Halbtags-Absenz (nur vormittags/nachmittags) die andere
                    // Hälfte für einen Dienst freilässt. Der frühere leere
                    // "+"-Zweitslot für Admin/Planer (auch bei nur einem echten
                    // Dienst) stammte noch aus der Zeit des Klick-auf-Zelle-
                    // Dropdowns, wo ein sichtbares Klickziel nötig war, um
                    // einen zweiten Dienst anzulegen. Die Icon-Toolbar braucht das
                    // nicht mehr -- Modus "Unten" wählen, Tag markieren, stempeln
                    // reicht. Zählt nur reguläre Zuweisungen (eine Spezialität
                    // allein soll keinen zweiten Slot erzwingen).
                    const halfDayAbsence = Boolean(absence) && absence.day_portion !== "full";
                    const showSecondSlot = halfDayAbsence || (!absence && regularAssignments.length >= 2);

                    // Nutzer-Feedback (2026-08): "wofür haben wir Alles/Oben/Unten
                    // gebaut? Absenzen sollen genau gleich zuteilbar sein" -- Oben
                    // trifft bei einer Absenz jetzt genauso nur die obere
                    // (vormittags) statt immer den ganzen Tag, Unten entsprechend
                    // nachmittags. Eine Halbtags-Absenz belegt GENAU einen der
                    // beiden Slots; der jeweils andere bleibt frei für einen
                    // regulären Dienst (oder einfach leer). Eine ganztägige
                    // Absenz belegt weiterhin nur slot0, der dank
                    // showSecondSlot=false die volle Zellbreite einnimmt
                    // (unverändertes Verhalten).
                    function slotAbsence(slotIndex) {
                      if (!absence) return null;
                      if (absence.day_portion === "morning") return slotIndex === 0 ? absence : null;
                      if (absence.day_portion === "afternoon") return slotIndex === 1 ? absence : null;
                      return slotIndex === 0 ? absence : null;
                    }
                    function slotAssignment(slotIndex) {
                      if (halfDayAbsence) {
                        const isAbsenceSlot =
                          (absence.day_portion === "morning" && slotIndex === 0) ||
                          (absence.day_portion === "afternoon" && slotIndex === 1);
                        return isAbsenceSlot ? null : regularAssignments[0] ?? null;
                      }
                      if (absence) return null;
                      return regularAssignments[slotIndex] ?? null;
                    }

                    function renderSlot(assignment, slotIndex, slotAbsenceValue) {
                      const template = templates.find((t) => t.id === assignment?.template);
                      // Block 1.13: Ist-Zeit-Badge nur auf der eigenen, bereits
                      // stattgefundenen Schicht -- unabhängig von canManage, damit
                      // auch ein Admin/Planer mit eigenem Employee-Profil seine
                      // eigenen Schichten erfassen kann.
                      const canRecordTime = Boolean(assignment) && date <= todayIso && ownEmployeeId === emp.id;
                      const timeRecord = assignment ? timeRecordByAssignment.get(assignment.id) : undefined;
                      // Block 2.13: höchstpersönlich -- nur die eigene Person darf
                      // für einen heutigen/zukünftigen Tag einen Wunsch äussern.
                      // Gilt personen-/tagesweise (nicht pro Slot), siehe
                      // showWishBadge unten.
                      const preference = preferenceMap.get(`${emp.id}:${date}`);
                      const canEditOwnWish = ownEmployeeId === emp.id && date >= todayIso;
                      return (
                        <ShiftCell
                          key={assignment?.id ?? `empty-${slotIndex}`}
                          templates={templates}
                          templateInfo={template}
                          assignmentId={assignment?.id}
                          employeeId={emp.id}
                          date={date}
                          absence={slotAbsenceValue}
                          absenceTypes={absenceTypes}
                          colleagues={employees.filter((e) => e.id !== emp.id)}
                          canEdit={canManage}
                          canOfferTrade={canOfferTrade}
                          onMove={(fromEmployeeId, fromDate, fromAssignmentId) =>
                            handleMove(fromAssignmentId, emp.id, date, rowNodeId, assignment?.id)
                          }
                          onOfferTrade={(targetEmployeeId) => handleOfferTrade(assignment.id, targetEmployeeId)}
                          timeRecord={timeRecord}
                          canRecordTime={canRecordTime}
                          onSaveTimeRecord={(payload) => handleSaveTimeRecord(assignment.id, timeRecord, payload)}
                          onDeleteTimeRecord={() => handleDeleteTimeRecord(timeRecord)}
                          preference={preference}
                          canEditOwnWish={canEditOwnWish}
                          onSaveWish={(payload) => handleSaveWish(date, preference, payload)}
                          onDeleteWish={() => handleDeleteWish(preference)}
                          showWishBadge={slotIndex === 0}
                          marked={marked}
                          onMarkStart={() => startMark(emp.id, date, rowNodeId)}
                          onMarkEnter={() => continueMark(emp.id, date, rowNodeId)}
                          onMarkToggle={() => toggleMark(emp.id, date, rowNodeId)}
                        />
                      );
                    }

                    return (
                      <td
                        key={d}
                        className={[weekend && "is-weekend", holidayName && "is-holiday"].filter(Boolean).join(" ")}
                        title={holidayName || undefined}
                      >
                        <div className={`day-cell${marked ? " is-marked" : ""}`}>
                          {/* Polypoint-Vorbild (2026-08): die Zelle bleibt IMMER
                              gleich breit (table-layout: fixed) statt zu wachsen.
                              Redesign (2026-08, "hand aufs Herz"-Nachbesserung):
                              zwei Dienste werden horizontal gestapelt (oben =
                              zeitlich früher, unten = später) statt diagonal --
                              eine echte, sofort verständliche Achse statt einer
                              willkürlichen Dreiecksgeometrie. --chip-color muss
                              hier nicht mehr am Wrapper gesetzt werden: die Farbe
                              lebt direkt am .shift-chip (ShiftCell.jsx setzt sie
                              dort schon selbst), nicht mehr am .shift-chip-btn
                              darüber. Nur ein Dienst ("Alles") spannt weiterhin die
                              ganze Zelle.
                              Workflow-Redesign (2026-08, Nachbesserung): is-marked
                              sitzt auf .day-cell (dem äussersten Container), nicht
                              mehr auf .day-cell-slots -- Letzteres füllt bei einem
                              belegten Tag die Zelle randlos mit der opaken
                              .shift-chip-Farbfläche aus, ein Ring DORT war bei
                              gesetztem Dienst unsichtbar. .day-cell selbst hat
                              KEIN overflow:hidden und ist genau 1px grösser als
                              .day-cell-slots (dessen inset) -- der Ring liegt
                              dadurch sichtbar in diesem 1px-Rand, unabhängig davon
                              ob die Zelle leer oder belegt ist. */}
                          <div className={`day-cell-slots${showSecondSlot ? " day-cell-slots--split" : ""}`}>
                            <div className={`cell-wrap${showSecondSlot ? " cell-wrap--top" : " cell-wrap--span"}`}>
                              {renderSlot(slotAssignment(0), 0, slotAbsence(0))}
                            </div>
                            {showSecondSlot && (
                              <div className="cell-wrap cell-wrap--bottom">
                                {renderSlot(slotAssignment(1), 1, slotAbsence(1))}
                              </div>
                            )}
                          </div>
                          {/* Redesign (2026-08): Spezialitäten (z. B. Pikettdienst)
                              als kleiner Eck-Badge statt eigener Zeile unter den
                              Slots -- siehe SpecialBadge.jsx. Nicht bei einer
                              GANZTÄGIGEN Absenz (schliesst Spezialitäten am selben
                              Tag ohnehin aus); eine Halbtags-Absenz lässt die
                              andere Hälfte offen für eine Spezialität. */}
                          {!(absence && absence.day_portion === "full") && (
                            <SpecialBadge
                              specialAssignments={specialAssignments}
                              templates={templates}
                              canEdit={canManage}
                              onRemove={(specialAssignmentId) => handleRemoveSpecial(specialAssignmentId)}
                            />
                          )}
                          {/* Bugfix ("massiver Bug"): nur auf einer sonst leeren
                              Zelle zeigen -- ist bereits ein Dienst sichtbar,
                              braucht es die proaktive Warnung nicht (der
                              Konflikt bezieht sich ohnehin auf den ganzen Tag,
                              nicht auf einen einzelnen Slot). */}
                          {!(absence && absence.day_portion === "full") && regularAssignments.length === 0 && (
                            <CrossTeamConflictBadge conflict={otherTeamConflictMap.get(`${emp.id}:${date}`)} />
                          )}
                        </div>
                      </td>
                    );
                  })}
                  {canManage && (
                    <td className="col-balance">
                      <BalanceBadge employeeId={emp.id} variant="cell" />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

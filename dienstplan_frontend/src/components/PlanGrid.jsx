import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import BalanceBadge from "./BalanceBadge.jsx";
import FloatingPopover from "./FloatingPopover.jsx";
import ShiftCell from "./ShiftCell.jsx";
import SpecialStrip from "./SpecialStrip.jsx";

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
  const [loading, setLoading] = useState(true);
  // Mehrfachauswahl + Schicht-Stempel (README-Task, inspiriert von Polypoint):
  // Zellen markieren, dann per Klick auf einen Schichttyp alle markierten
  // Zellen auf einmal beplanen -- Ergänzung zum bestehenden Einzel-Dropdown,
  // nicht dessen Ersatz.
  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const [markedCells, setMarkedCells] = useState(() => new Set());
  // README (2026-08, Bugfix): die Stempelleiste zielte fest auf den ersten
  // Slot einer Zelle -- ein zweiter (Split-Shift-)Dienst war darüber nicht
  // stempelbar, nur einzeln über das Zelle-für-Zelle-Dropdown. Dieser
  // Umschalter lässt den Stempel stattdessen auf den zweiten Slot zielen,
  // ohne den ersten anzutasten.
  const [stampSecondSlot, setStampSecondSlot] = useState(false);
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
  // Beide Gruppen stempeln weiterhin über dasselbe handleStampAssign (eine
  // Spezialität ist technisch dasselbe wie ein regulärer Dienst, nur anders
  // eingeordnet für die Anzeige).
  const regularStampTemplates = useMemo(
    () => stampTemplates.filter((t) => t.category !== "special"),
    [stampTemplates]
  );
  const specialStampTemplates = useMemo(
    () => stampTemplates.filter((t) => t.category === "special"),
    [stampTemplates]
  );

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

  function toggleMultiSelectMode() {
    setMultiSelectMode((v) => !v);
    setMarkedCells(new Set());
    setStampSecondSlot(false);
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

  // Stempel-Leiste: weist templateId (oder null zum Leeren) allen markierten
  // Zellen auf einmal zu. Läuft absichtlich sequenziell wie
  // handleCopyWeekPattern -- ein Konflikt (z. B. Ruhezeit) auf einer Zelle
  // soll die übrigen nicht blockieren, nur summarisch gemeldet werden.
  //
  // README (2026-08, Bugfix): zielt bei aktivem stampSecondSlot auf den
  // zweiten Slot (Split-Shift) statt den ersten, analog zum "+"-Slot im
  // Einzelzell-Dropdown (ShiftCell), der ebenfalls erst ab einer
  // vorhandenen ersten Zuweisung als Split-Shift-Angebot erscheint -- eine
  // Zelle ohne ersten Dienst wird deshalb übersprungen statt einen
  // "zweiten" Dienst ohne ersten anzulegen.
  async function handleStampAssign(templateId) {
    const keys = Array.from(markedCells);
    const upserted = [];
    const deletedIds = [];
    let skipped = 0;
    // Nutzer-Feedback (2026-08, Punkt 4): ein Chip aus der "Spezialitäten"-
    // Zeile legt IMMER eine additive neue Zuweisung an (unabhängig von
    // stampSecondSlot/Slot 0/1) statt fälschlich um einen der beiden Slots
    // zu konkurrieren -- eine Spezialität ist ein Zusatz, kein Ersatz.
    const stampedTemplate = templateId !== null ? templates.find((t) => t.id === templateId) : null;
    const isSpecial = stampedTemplate?.category === "special";

    for (const key of keys) {
      const [employeeIdStr, date, rowNodeIdStr] = key.split(":");
      const employeeId = Number(employeeIdStr);
      const rowNodeId = Number(rowNodeIdStr);
      if (isSpecial) {
        try {
          const created = await api.createShiftAssignment({
            employee: employeeId,
            node: rowNodeId,
            date,
            template: templateId,
          });
          upserted.push(created);
        } catch {
          skipped += 1;
        }
        continue;
      }
      const rowAssignments = rowAssignmentsFor(employeeId, date, rowNodeId).filter(
        (a) => templates.find((t) => t.id === a.template)?.category !== "special"
      );
      if (stampSecondSlot && !rowAssignments[0]) {
        skipped += 1;
        continue;
      }
      const existing = stampSecondSlot ? rowAssignments[1] : rowAssignments[0];
      try {
        if (templateId === null) {
          if (existing) {
            await api.deleteShiftAssignment(existing.id);
            deletedIds.push(existing.id);
          }
        } else if (existing) {
          const updated = await api.updateShiftAssignment(existing.id, { template: templateId });
          upserted.push(updated);
        } else {
          const created = await api.createShiftAssignment({
            employee: employeeId,
            node: rowNodeId,
            date,
            template: templateId,
          });
          upserted.push(created);
        }
      } catch {
        // z. B. Ruhezeit-, Höchstarbeitszeit- oder Absenz-Konflikt -- Zelle
        // überspringen, restliche markierte Zellen trotzdem weiterstempeln.
        skipped += 1;
      }
    }

    if (upserted.length) {
      setAssignments((prev) => {
        const byId = new Map(prev.map((a) => [a.id, a]));
        for (const a of upserted) byId.set(a.id, a);
        return Array.from(byId.values());
      });
    }
    if (deletedIds.length) {
      setAssignments((prev) => prev.filter((a) => !deletedIds.includes(a.id)));
    }
    setMarkedCells(new Set());
    if (skipped > 0) {
      onError(
        `${keys.length - skipped} von ${keys.length} markierten Zellen zugewiesen, ${skipped} wegen ` +
          "Regel-Konflikten (z. B. Ruhezeit) übersprungen."
      );
    }
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

  // README (2026-08, Bugfix): Absenz-Stempeln fehlte im Planblatt komplett --
  // nur der Jahresplan (YearPlan.jsx) konnte Ferien/Krankheit/Sonstiges
  // eintragen. Gleiches Muster wie dort (handleStampAbsence), aber über
  // mehrere Mitarbeiter hinweg gruppiert, da im Planblatt (anders als im
  // Jahresplan) Zellen verschiedener Personen gleichzeitig markiert sein
  // können.
  async function handleStampAbsence(type) {
    const byEmployee = absenceDatesByEmployee(Array.from(markedCells));
    const created = [];
    let skippedExisting = 0;
    let skippedConflict = 0;
    for (const [employeeId, dateSet] of byEmployee) {
      const dates = Array.from(dateSet).filter((d) => !findAbsence(employeeId, d));
      skippedExisting += dateSet.size - dates.length;
      const ranges = groupConsecutiveDates(dates);
      for (const [start, end] of ranges) {
        try {
          created.push(await api.createAbsence({ employee: employeeId, start_date: start, end_date: end, type }));
        } catch {
          skippedConflict += 1;
        }
      }
    }
    if (created.length) setAbsences((prev) => [...prev, ...created]);
    setMarkedCells(new Set());
    const notes = [];
    if (skippedExisting > 0) {
      notes.push(`${skippedExisting} Tag(e) übersprungen, dort besteht bereits eine Absenz.`);
    }
    if (skippedConflict > 0) {
      notes.push(`${skippedConflict} Zeitraum(e) konnten nicht angelegt werden.`);
    }
    if (notes.length) onError(notes.join(" "));
  }

  async function handleRemoveAbsences() {
    // Löscht die GANZE Absenz, nicht nur die markierten Tage daraus, falls
    // nur ein Teil eines mehrtägigen Zeitraums markiert war (gleiches
    // Verhalten wie YearPlan.jsx: eine Absenz lässt sich nicht teilweise
    // löschen, ohne sie in zwei neue Zeiträume aufzuspalten).
    const byEmployee = absenceDatesByEmployee(Array.from(markedCells));
    const toDelete = new Map();
    for (const [employeeId, dateSet] of byEmployee) {
      for (const date of dateSet) {
        const absence = findAbsence(employeeId, date);
        if (absence) toDelete.set(absence.id, absence);
      }
    }
    const deletedIds = [];
    let failed = 0;
    for (const absence of toDelete.values()) {
      try {
        await api.deleteAbsence(absence.id);
        deletedIds.push(absence.id);
      } catch {
        failed += 1;
      }
    }
    if (deletedIds.length) {
      setAbsences((prev) => prev.filter((a) => !deletedIds.includes(a.id)));
    }
    setMarkedCells(new Set());
    if (failed > 0) {
      onError(`${failed} Absenz(en) konnten nicht entfernt werden.`);
    }
  }

  // README Punkt 18: assignmentId statt employeeId+date -- bei mehreren
  // Zuweisungen desselben Tages (Split-Shifts) ist jede unabhängig
  // tauschbar, die frühere Ableitung über employee+date wäre mehrdeutig.
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
      {canManage && (
        <div className="multi-select-toolbar">
          <button
            type="button"
            className={`btn-toggle-multiselect${multiSelectMode ? " is-active" : ""}`}
            onClick={toggleMultiSelectMode}
          >
            {multiSelectMode ? "✕ Mehrfachauswahl beenden" : "☐ Mehrfachauswahl"}
          </button>
          {multiSelectMode && (
            <span className="stamp-palette">
              <span className="multi-select-hint">
                {markedCells.size === 0 ? "Tage anklicken, um sie zu markieren." : `${markedCells.size} markiert:`}
              </span>
              <span className="stamp-row">
                <span className="stamp-row-label">Dienste</span>
                <label className="stamp-second-slot-toggle" title="Bestehenden ersten Dienst nicht ersetzen, sondern einen zweiten (Split-Shift) danebenstellen. Gilt auch für Spezialitäten unten.">
                  <input
                    type="checkbox"
                    checked={stampSecondSlot}
                    disabled={markedCells.size === 0}
                    onChange={(e) => setStampSecondSlot(e.target.checked)}
                  />
                  Als zweiten Dienst hinzufügen
                </label>
                {regularStampTemplates.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="stamp-chip"
                    style={{ "--chip-color": t.color }}
                    disabled={markedCells.size === 0}
                    title={`${t.name} (${t.start_time.slice(0, 5)}–${t.end_time.slice(0, 5)}) auf alle markierten Tage anwenden`}
                    onClick={() => handleStampAssign(t.id)}
                  >
                    {t.name.slice(0, 3)}
                  </button>
                ))}
                <button
                  type="button"
                  className="stamp-chip stamp-chip--empty"
                  disabled={markedCells.size === 0}
                  title="Markierte Tage leeren"
                  onClick={() => handleStampAssign(null)}
                >
                  — leer —
                </button>
              </span>
              <span className="stamp-row">
                <span className="stamp-row-label">Abwesenheiten</span>
                {absenceTypes.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="stamp-chip stamp-chip--absence"
                    style={{ "--chip-color": t.color }}
                    disabled={markedCells.size === 0}
                    title={`${t.name} für alle markierten Tage eintragen`}
                    onClick={() => handleStampAbsence(t.id)}
                  >
                    {t.name}
                  </button>
                ))}
                <button
                  type="button"
                  className="stamp-chip stamp-chip--empty"
                  disabled={markedCells.size === 0}
                  title="Absenz(en) der markierten Tage entfernen -- löscht den ganzen Zeitraum, nicht nur die markierten Tage daraus"
                  onClick={handleRemoveAbsences}
                >
                  Absenz entfernen
                </button>
              </span>
              {specialStampTemplates.length > 0 && (
                <span className="stamp-row">
                  <span className="stamp-row-label">Spezialitäten</span>
                  {specialStampTemplates.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className="stamp-chip"
                      style={{ "--chip-color": t.color }}
                      disabled={markedCells.size === 0}
                      title={`${t.name} (${t.start_time.slice(0, 5)}–${t.end_time.slice(0, 5)}) auf alle markierten Tage anwenden`}
                      onClick={() => handleStampAssign(t.id)}
                    >
                      {t.name.slice(0, 3)}
                    </button>
                  ))}
                </span>
              )}
              <span className="stamp-row">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={markedCells.size === 0}
                  onClick={() => setMarkedCells(new Set())}
                >
                  Auswahl aufheben
                </button>
              </span>
            </span>
          )}
        </div>
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
              // Schichttypen direkt auf diesem Team stehen exklusiv dieser
              // Zeile zur Auswahl (z. B. "Nachtwache" nur beim Nacht-Team,
              // nicht auch beim Tag-Team derselben Station); Schichttypen
              // direkt auf der Station (t.node === stationId) gelten als
              // geteilter Katalog und stehen JEDER Team-Zeile zusätzlich zur
              // Verfügung -- das ist der Normalfall, solange niemand einen
              // Schichttyp manuell auf eine einzelne Team-Ebene verschoben
              // hat (siehe TimeTemplateSettings-Hinweistext).
              const rowAssignableTemplates = templates.filter(
                (t) => t.node === rowNodeId || t.node === stationId
              );
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
                    // Zuweisungen behandelt und in einer eigenen, dünnen Zeile
                    // unter den Slots gerendert (SpecialStrip.jsx, siehe unten).
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
                    // Zweiter Slot nur anzeigen, wenn er entweder schon eine
                    // echte zweite Zuweisung enthält, oder (leer) als
                    // "+"-Angebot für Admin/Planer, um einen Split-Shift
                    // anzulegen -- ein nicht bedienbares leeres "+" für
                    // reine Betrachter wäre nur verwirrend. Zählt nur reguläre
                    // Zuweisungen (eine Spezialität allein soll keinen zweiten
                    // Slot erzwingen).
                    const showSecondSlot =
                      !absence && (regularAssignments.length >= 2 || (canManage && regularAssignments.length === 1));
                    const rowAssignableSpecialTemplates = rowAssignableTemplates.filter(
                      (t) => t.category === "special"
                    );

                    function renderSlot(assignment, slotIndex) {
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
                          assignableTemplates={rowAssignableTemplates.filter((t) => t.category !== "special")}
                          selectedTemplateId={assignment?.template ?? null}
                          templateInfo={template}
                          assignmentId={assignment?.id}
                          employeeId={emp.id}
                          date={date}
                          absence={absence}
                          absenceTypes={absenceTypes}
                          colleagues={employees.filter((e) => e.id !== emp.id)}
                          canEdit={canManage}
                          canOfferTrade={canOfferTrade}
                          onChange={(templateId) =>
                            handleAssign(emp.id, date, templateId, rowNodeId, assignment?.id)
                          }
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
                          onAssignAbsence={(absenceTypeId) =>
                            handleAssignAbsence(emp.id, date, absenceTypeId, assignment?.id)
                          }
                        />
                      );
                    }

                    return (
                      <td
                        key={d}
                        className={[weekend && "is-weekend", holidayName && "is-holiday"].filter(Boolean).join(" ")}
                        title={holidayName || undefined}
                      >
                        {multiSelectMode ? (
                          <ShiftCell
                            templates={templates}
                            assignableTemplates={rowAssignableTemplates}
                            selectedTemplateId={regularAssignments[0]?.template ?? null}
                            templateInfo={templates.find((t) => t.id === regularAssignments[0]?.template)}
                            secondTemplateInfo={templates.find((t) => t.id === regularAssignments[1]?.template)}
                            employeeId={emp.id}
                            date={date}
                            absence={absence}
                            absenceTypes={absenceTypes}
                            selectionMode
                            marked={markedCells.has(`${emp.id}:${date}:${rowNodeId}`)}
                            onMarkStart={() => startMark(emp.id, date, rowNodeId)}
                            onMarkEnter={() => continueMark(emp.id, date, rowNodeId)}
                          />
                        ) : (
                          <div className="day-cell">
                            <div className="shift-slots-row">
                              {renderSlot(regularAssignments[0], 0)}
                              {showSecondSlot && renderSlot(regularAssignments[1], 1)}
                            </div>
                            {/* Nutzer-Feedback (2026-08, Nachbesserung): Spezialitäten
                                (z. B. Pikettdienst) waren im Split-Shift-Badge (in der
                                ShiftCell-Ecke) faktisch unsichtbar -- eigene, dünne
                                Chip-Zeile unter den Dienst-Slots statt versteckt hinter
                                einem Zähler. Nicht bei einer Absenz (schliesst
                                Spezialitäten am selben Tag ohnehin aus). */}
                            {!absence && (
                              <SpecialStrip
                                specialAssignments={specialAssignments}
                                templates={templates}
                                assignableTemplates={rowAssignableSpecialTemplates}
                                canEdit={canManage}
                                onAdd={(templateId) => handleAddSpecial(emp.id, date, rowNodeId, templateId)}
                                onRemove={(specialAssignmentId) => handleRemoveSpecial(specialAssignmentId)}
                              />
                            )}
                          </div>
                        )}
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

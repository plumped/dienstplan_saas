import { useEffect, useMemo, useRef, useState } from "react";
import { relevantNodeIds } from "../App.jsx";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import { chipGlyph } from "../chipGlyph.js";
import PlacementToolbar from "./PlacementToolbar.jsx";

const WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTH_NAMES = [
  "Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember",
];

const STATUS_LABELS = { pending: "offen", approved: "genehmigt", rejected: "abgelehnt" };

function pad(n) {
  return String(n).padStart(2, "0");
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function isoDate(year, month, day) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

// 0 = Montag ... 6 = Sonntag, damit Wochen wie im übrigen Frontend Mo-So laufen.
function weekdayIndex(year, month, day) {
  const jsDay = new Date(year, month - 1, day).getDay();
  return jsDay === 0 ? 6 : jsDay - 1;
}

// Löst zu einem Knoten die zugehörige Station auf (sich selbst, falls er
// bereits die Station ist) -- gleiches Muster wie stationScope() in
// PlanGrid.jsx, hier aber nur der stationId-Teil, da YearPlan Assignments
// ohnehin schon exakt scoped lädt (siehe getShiftAssignments(selectedNode)).
function stationIdFor(nodes, nodeId) {
  const selected = nodes.find((n) => n.id === nodeId);
  if (!selected) return nodeId;
  const parent = nodes.find((n) => n.depth === selected.depth - 1 && selected.path.startsWith(n.path));
  return parent ? parent.id : selected.id;
}

function addDays(iso, delta) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  date.setDate(date.getDate() + delta);
  return isoDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

// Fasst eine Menge von ISO-Tagen zu möglichst wenigen zusammenhängenden
// [start, end]-Bereichen zusammen -- damit z. B. zwei markierte Ferienwochen
// als zwei Absence-Einträge entstehen statt vierzehn Einzeltagen.
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

export default function YearPlan({ nodeId, nodes, employees, me, onError }) {
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;

  const [year, setYear] = useState(() => new Date().getFullYear());
  // README Punkt 17: bei einer Station mit Teams (oder einer Person mit
  // mehreren Anstellungen) reicht eine reine Employee-Id nicht mehr, um
  // eindeutig zu sagen, wessen/welchen Kalender man sieht -- employmentKey
  // ("employeeId:node") wählt zusätzlich die konkrete Anstellung/das Team,
  // dessen Zuweisungen tatsächlich geladen werden (siehe Datenabruf unten).
  const [employmentKey, setEmploymentKey] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [absenceTypes, setAbsenceTypes] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [absences, setAbsences] = useState([]);
  const [preferences, setPreferences] = useState([]);
  // Arbeitszeitmodell (Block 2.7 Punkt 7): Feiertage des Tenant-Kantons +
  // manuelle Overrides (core.views.TenantHolidaysView) -- Map<isoDate, name>
  // fürs Markieren der Tageszellen unten (analog zu PlanGrid.jsx).
  const [holidays, setHolidays] = useState(new Map());
  const [loading, setLoading] = useState(true);
  // Wunschfrei/Wunschdienst (Block 2.13): höchstpersönlich -- auch im
  // Jahresplan nur stempelbar, solange die ausgewählte Person die eigene
  // ist, unabhängig von der Rolle (Admin/Planer dürfen für andere Personen
  // zwar den Jahresplan ansehen/Schichten stempeln, aber keine Wünsche).
  const [markedDates, setMarkedDates] = useState(() => new Set());
  // Port (2026-08, Nutzer-Feedback: "übernimm die genau gleiche Logik wie im
  // Planblatt um zu beplanen"): ersetzt das frühere stampSecondSlot-Modell
  // durch denselben Alles/Oben/Unten/Pikett-Platzierungsmodus wie
  // PlanGrid.jsx (siehe PlacementToolbar.jsx) -- identisches
  // Interaktionsmodell für beide Ansichten.
  const [placementMode, setPlacementMode] = useState("full");
  // Ziehen mit gedrückter Maustaste markiert mehrere Tage am Stück (analog
  // zu PlanGrid.jsx): "mark"/"unmark" je nach Zustand des zuerst angeklickten
  // Tages, null = kein Ziehvorgang aktiv.
  const dragMarkModeRef = useRef(null);
  // Unterdrückt das synthetische click-Event nach einem bereits per
  // mousedown verarbeiteten Klick (siehe ShiftCell.jsx: gleiches Muster) --
  // ein einzelner globaler Ref genügt, weil zu jedem Zeitpunkt höchstens ein
  // Ziehvorgang aktiv ist.
  const suppressClickRef = useRef(false);

  // Beendet einen laufenden Ziehvorgang auch dann, wenn die Maustaste
  // ausserhalb einer Tages-Zelle losgelassen wird.
  useEffect(() => {
    function handleWindowMouseUp() {
      dragMarkModeRef.current = null;
    }
    window.addEventListener("mouseup", handleWindowMouseUp);
    return () => window.removeEventListener("mouseup", handleWindowMouseUp);
  }, []);

  // README Punkt 17: eine Anstellung pro Team/Node, die im aktuellen
  // Stations-Scope (Station + ihre Teams) liegt. Mitarbeitende sehen nur
  // ihre eigenen Anstellungen (höchstpersönlich, analog zur Sperrung im
  // Abwesenheiten-Formular); Admin/Planer sehen die aller Mitarbeitenden.
  // Der Rollen-/Team-Zusatz im Label erscheint nur, wenn dieselbe Person
  // wirklich mehr als eine Anstellung im Scope hat (progressive disclosure --
  // der weit überwiegende Einzel-Anstellungs-Fall sieht exakt wie vorher aus).
  const employmentOptions = useMemo(() => {
    const scopedNodeIds = relevantNodeIds(nodes, nodeId);
    const relevantEmployees = canManage ? employees : employees.filter((e) => e.id === ownEmployeeId);
    const options = [];
    for (const emp of relevantEmployees) {
      const employments = (emp.employments ?? []).filter((e) => scopedNodeIds.includes(e.node));
      if (employments.length === 0) {
        // Fallback für Datensätze ohne passende Anstellung im Scope (z. B.
        // frisch angelegt, noch kein Team zugewiesen) -- die Person soll
        // trotzdem im Jahresplan auswählbar bleiben, auch ohne Rollen-Zusatz.
        options.push({ key: `${emp.id}:${nodeId}`, employeeId: emp.id, node: nodeId, emp, employment: null });
        continue;
      }
      for (const employment of employments) {
        options.push({ key: `${emp.id}:${employment.node}`, employeeId: emp.id, node: employment.node, emp, employment });
      }
    }
    return options;
  }, [nodes, nodeId, employees, canManage, ownEmployeeId]);

  const selectedOption = employmentOptions.find((o) => o.key === employmentKey) ?? null;
  const employeeId = selectedOption?.employeeId ?? null;
  const selectedNode = selectedOption?.node ?? null;
  const selectedStationId = useMemo(
    () => (selectedNode ? stationIdFor(nodes, selectedNode) : null),
    [nodes, selectedNode]
  );
  const isOwnEmployeeSelected = employeeId !== null && employeeId === ownEmployeeId;

  useEffect(() => {
    setEmploymentKey((current) =>
      employmentOptions.some((o) => o.key === current) ? current : employmentOptions[0]?.key ?? null
    );
  }, [employmentOptions]);

  useEffect(() => {
    if (!employeeId || !selectedNode) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setMarkedDates(new Set());
    const dateFrom = `${year}-01-01`;
    const dateTo = `${year}-12-31`;
    Promise.all([
      api.getTimeTemplates(),
      api.getAbsenceTypes(),
      api.getShiftAssignments(selectedNode, dateFrom, dateTo),
      api.getAbsences(employeeId),
      api.getShiftPreferences(employeeId),
      api.getTenantHolidays(year),
    ])
      .then(([templatesRes, absenceTypesRes, assignmentsRes, absencesRes, preferencesRes, holidaysRes]) => {
        if (cancelled) return;
        setAbsenceTypes(absenceTypesRes.results ?? absenceTypesRes);
        // Nachbesserung: TimeTemplate.node kann sowohl die Station (geteilter
        // Katalog, für JEDES Team der Station sichtbar) als auch ein
        // einzelnes Team sein (exklusiv, siehe PlanGrid.jsx). Nur nach
        // selectedNode zu filtern liess bei einer stationsweiten Vorlage
        // (der Normalfall) den Jahresplan komplett leer -- daher zusätzlich
        // Vorlagen der übergeordneten Station zulassen.
        setTemplates(
          (templatesRes.results ?? templatesRes).filter(
            (t) => t.node === selectedNode || t.node === selectedStationId
          )
        );
        const allAssignments = assignmentsRes.results ?? assignmentsRes;
        // README Punkt 17: nur die Zuweisungen dieser konkreten Anstellung
        // (Employee UND Node) -- bei Mehrfachanstellung liefert
        // getShiftAssignments(selectedNode, ...) bereits nur diesen
        // Team-Scope, der employee-Filter grenzt zusätzlich auf die Person ein.
        setAssignments(allAssignments.filter((a) => a.employee === employeeId && a.node === selectedNode));
        setAbsences(absencesRes.results ?? absencesRes);
        setPreferences(preferencesRes.results ?? preferencesRes);
        setHolidays(new Map(holidaysRes.dates.map((entry) => [entry.date, entry.name])));
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employeeId, selectedNode, selectedStationId, year]);

  // Nutzer-Feedback (2026-08): Stempelleiste soll mehrzeilig sein -- eine
  // Zeile für reguläre Dienste, eine für Spezialitäten (z. B. Pikettdienst,
  // TimeTemplate.category === "special", siehe TimeTemplateSettings.jsx und
  // gleiches Muster in PlanGrid.jsx).
  const regularTemplates = useMemo(() => templates.filter((t) => t.category !== "special"), [templates]);
  const specialTemplates = useMemo(() => templates.filter((t) => t.category === "special"), [templates]);
  const absenceTypesById = useMemo(() => new Map(absenceTypes.map((t) => [t.id, t])), [absenceTypes]);

  // README (2026-08, Bugfix): Array statt Einzelwert pro Datum -- ein Tag
  // kann jetzt (Split-Shifts, README Punkt 18) mehr als eine Zuweisung
  // haben. Chronologisch nach Beginnzeit sortiert, analog zu PlanGrid.jsx.
  const assignmentsByDate = useMemo(() => {
    const map = new Map();
    for (const a of assignments) {
      if (!map.has(a.date)) map.set(a.date, []);
      map.get(a.date).push(a);
    }
    for (const list of map.values()) {
      list.sort((a, b) => {
        const ta = templates.find((t) => t.id === a.template);
        const tb = templates.find((t) => t.id === b.template);
        return (ta?.start_time ?? "").localeCompare(tb?.start_time ?? "");
      });
    }
    return map;
  }, [assignments, templates]);

  // Zerlegt die (bereits chronologisch sortierten) Zuweisungen eines Tages in
  // reguläre Dienste (konkurrieren um Slot 0/1) und additive Spezialitäten
  // (Pikett etc.) -- gleiche Trennung wie resolveCellState() in
  // PlanGrid.jsx, hier als kleine Helper statt für jede Zelle/jeden
  // Stempel-Aufruf neu inline gefiltert.
  function regularAssignmentsOf(date) {
    return (assignmentsByDate.get(date) ?? []).filter(
      (a) => templates.find((t) => t.id === a.template)?.category !== "special"
    );
  }
  function specialAssignmentsOf(date) {
    return (assignmentsByDate.get(date) ?? []).filter(
      (a) => templates.find((t) => t.id === a.template)?.category === "special"
    );
  }

  // Absenzen sind Zeiträume (start_date/end_date) -- für die Tages-Zellen auf
  // eine Datum->Absenz-Map auflösen, damit jede Zelle in O(1) weiss, ob sie
  // dazugehört.
  const absenceByDate = useMemo(() => {
    const map = new Map();
    for (const a of absences) {
      let d = a.start_date;
      while (d <= a.end_date) {
        map.set(d, a);
        d = addDays(d, 1);
      }
    }
    return map;
  }, [absences]);

  const preferenceByDate = useMemo(() => {
    const map = new Map();
    for (const p of preferences) map.set(p.date, p);
    return map;
  }, [preferences]);

  function applyMark(date, shouldMark) {
    setMarkedDates((prev) => {
      if (prev.has(date) === shouldMark) return prev;
      const next = new Set(prev);
      if (shouldMark) next.add(date);
      else next.delete(date);
      return next;
    });
  }

  // Startet den Ziehen-Modus beim ersten Antippen/Klicken eines Tages (siehe
  // PlanGrid.jsx: startMark/continueMark -- identisches Muster, hier auf
  // einzelne Tage statt Mitarbeiter+Tag angewendet).
  function startMark(date) {
    const shouldMark = !markedDates.has(date);
    dragMarkModeRef.current = shouldMark ? "mark" : "unmark";
    applyMark(date, shouldMark);
  }

  function continueMark(date) {
    if (!dragMarkModeRef.current) return;
    applyMark(date, dragMarkModeRef.current === "mark");
  }

  // Port (2026-08, Nutzer-Feedback: "übernimm die genau gleiche Logik wie im
  // Planblatt um zu beplanen"): identische Stempel-Logik wie PlanGrid.jsx
  // (applyToolToCell/applyToolToMarked, shiftShouldBeReplacedByAbsencePortion,
  // shrinkAbsence), hier auf die einfacheren Jahresplan-Datenstrukturen (ein
  // Mitarbeiter/eine Anstellung fix, markedDates ist ein flaches Set<date>
  // statt eines employeeId:date:rowNodeId-Schlüssels) umgeschrieben statt
  // eines eigenen, abweichenden Modells.

  async function assignTemplate(date, templateId, assignmentId) {
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
        const created = await api.createShiftAssignment({
          employee: employeeId,
          node: selectedNode,
          date,
          template: templateId,
        });
        setAssignments((prev) => [...prev, created]);
      }
    } catch (e) {
      // z. B. Ruhezeit-, Höchstarbeitszeit- oder Absenz-Konflikt (siehe
      // ShiftAssignment.clean()).
      onError(e.message);
    }
  }

  async function addSpecial(date, templateId) {
    try {
      const created = await api.createShiftAssignment({
        employee: employeeId,
        node: selectedNode,
        date,
        template: templateId,
      });
      setAssignments((prev) => [...prev, created]);
    } catch (e) {
      onError(e.message);
    }
  }

  async function removeSpecial(assignmentId) {
    try {
      await api.deleteShiftAssignment(assignmentId);
      setAssignments((prev) => prev.filter((a) => a.id !== assignmentId));
    } catch (e) {
      onError(e.message);
    }
  }

  async function removeAbsence(absenceId) {
    try {
      await api.deleteAbsence(absenceId);
      setAbsences((prev) => prev.filter((a) => a.id !== absenceId));
    } catch (e) {
      // z. B. bereits genehmigte Absenz einer Mitarbeiter-Rolle (nur
      // Admin/Planer dürfen die noch löschen, siehe OwnEmployeeRecordPermission).
      onError(e.message);
    }
  }

  // Löscht eine bestehende ganztägige Einzeltag-Absenz und legt sie mit
  // `keepPortion` neu an -- lässt so die jeweils andere (nicht angeklickte)
  // Hälfte des Tages bestehen, statt die ganze Absenz zu entfernen.
  async function shrinkAbsence(absence, keepPortion) {
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

  // Spiegelt PlanGrid.jsx: ein bestehender Dienst, dessen Zeitfenster auch
  // die jeweils ANDERE (nicht von dayPortion beanspruchte) Tageshälfte
  // berührt (z. B. eine durchgehende Frühschicht 07:00-17:00 bei einer
  // Nachmittags-Absenz), wird nie automatisch durch eine Halbtags-Absenz
  // ersetzt -- nur ein echter, ausschliesslich in der Zielhälfte liegender
  // Dienst wird ersetzt. Gilt nicht für `full`.
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
    if (!overlapsTargetPortion) return false;
    const extendsIntoOtherHalf = dayPortion === "morning" ? end > noon : start < noon;
    return !extendsIntoOtherHalf;
  }

  // Spiegelt PlanGrid.jsx: applyToolToCell() -- wendet ein Dienst- oder
  // Radiergummi-Werkzeug auf EINEN Tag an. slot0/slot1 sind die
  // (chronologisch sortierten) regulären Zuweisungen des Tages, specials die
  // Spezialitäten-Zuweisungen (Pikett-Modus).
  async function applyToolToCell(tool, date, slot0, slot1, specials = [], absenceHandled = false) {
    if (tool.kind === "template") {
      if (placementMode === "special") {
        const existing = specials.find((s) => s.template === tool.id);
        if (existing) await removeSpecial(existing.id);
        else await addSpecial(date, tool.id);
        return;
      }
      if (placementMode === "full") {
        if (slot1) await assignTemplate(date, null, slot1.id);
        await assignTemplate(date, tool.id, slot0?.id);
      } else if (placementMode === "top" || placementMode === "bottom") {
        // "Oben"/"Unten" überschreibt nur die jeweilige Hälfte: ein
        // bestehender einzelner (durchgehender) Dienst wird nie
        // umbenannt/ersetzt, nur wenn bereits zwei eigenständige Dienste an
        // diesem Tag liegen (echter Split), wird gezielt der in der
        // Zielhälfte ersetzt -- sonst wird immer ein neuer, unabhängiger
        // Dienst angelegt.
        const isSplit = Boolean(slot0) && Boolean(slot1);
        const target = placementMode === "top" ? slot0 : slot1;
        await assignTemplate(date, tool.id, isSplit ? target?.id : undefined);
      }
      return;
    }

    if (tool.kind === "empty") {
      if (placementMode === "special") {
        for (const special of specials) await removeSpecial(special.id);
        return;
      }
      if (placementMode === "full") {
        if (slot0) await assignTemplate(date, null, slot0.id);
        if (slot1) await assignTemplate(date, null, slot1.id);
      } else if (placementMode === "top" || placementMode === "bottom") {
        // Nutzer-Feedback (2026-08): bei einem echten Split löscht der
        // Radiergummi gezielt nur den Dienst der Zielhälfte. Ein einzelner,
        // durchgehender Dienst (kein echter Split) liess sich in Oben/Unten
        // bisher NICHT löschen (stiller No-Op) -- verwirrend, wenn dort
        // z. B. neben einer Halbtags-Absenz noch ein durchgehender Dienst
        // steht. Da sich ein einzelner Dienst nicht halbieren lässt (kein
        // day_portion wie bei Absence), wird er komplett gelöscht,
        // unabhängig ob "Oben" oder "Unten" geklickt wurde (beide zielen auf
        // denselben, einzigen Datensatz).
        //
        // Bugfix (Nutzer-Feedback 2026-08): trifft der Klick stattdessen die
        // Hälfte, in der bereits eine Absenz sitzt (deckungsgleicher
        // placementMode, `absenceHandled` von applyToolToMarked() gesetzt),
        // ist die Absenz das eigentliche Ziel -- ein koexistierender,
        // durchgehender Dienst darf dann NICHT zusätzlich gelöscht werden,
        // sonst löscht "Unten Ferien entfernen" fälschlich auch den
        // Frühdienst mit.
        const isSplit = Boolean(slot0) && Boolean(slot1);
        if (isSplit) {
          const target = placementMode === "top" ? slot0 : slot1;
          await assignTemplate(date, null, target.id);
        } else if (slot0 && !absenceHandled) {
          await assignTemplate(date, null, slot0.id);
        }
      }
    }
  }

  // Spiegelt PlanGrid.jsx: applyToolToMarked() -- wendet `tool` auf ALLE
  // aktuell markierten Tage an. Absenzen sind ein Sonderfall: Oben/Unten
  // bestimmt jetzt auch bei einer Absenz die betroffene Tageshälfte
  // (Absence.day_portion) statt sie immer als ganzen Tag anzulegen. Eine
  // bestehende ganztägige Absenz an einem EINZELNEN Tag wird beim Bestempeln
  // nur einer Hälfte mit einem Dienst oder dem Radiergummi nicht mehr
  // komplett gelöscht, sondern auf die nicht angeklickte Hälfte reduziert
  // (shrinkAbsence). Bei einer mehrtägigen Absenz bleibt es beim
  // vollständigen Löschen (Range-Split bewusst nicht Teil dieses Features).
  async function applyToolToMarked(tool) {
    if (markedDates.size === 0) return;
    const dates = Array.from(markedDates);

    const absenceIdsToClear = new Set();
    const absencesToShrink = new Map(); // absenceId -> { absence, keepPortion }
    // Bugfix (Nutzer-Feedback 2026-08): "Frühdienst eingeplant, Unten
    // halber Tag Ferien, Unten halber Tag Ferien wieder entfernen löscht
    // auch den Dienst". Trifft der Radiergummi-Klick genau die Hälfte, in
    // der bereits eine Absenz sitzt (deckungsgleicher placementMode), ist
    // die Absenz das eigentliche Ziel -- der koexistierende, durchgehende
    // Dienst darf dann nicht zusätzlich gelöscht werden (siehe
    // applyToolToCell). datesWithHandledAbsence merkt sich, für welche Tage
    // die Absenz-Vorräumung tatsächlich etwas getan hat.
    const datesWithHandledAbsence = new Set();
    for (const date of dates) {
      const absence = absenceByDate.get(date);
      if (!absence) continue;
      if (tool.kind === "absence" || placementMode === "full") {
        absenceIdsToClear.add(absence.id);
        datesWithHandledAbsence.add(date);
        continue;
      }
      if (placementMode === "special") continue; // Pikett betrifft nie Absenzen
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
    for (const absenceId of absenceIdsToClear) {
      await removeAbsence(absenceId);
    }
    for (const { absence, keepPortion } of absencesToShrink.values()) {
      await shrinkAbsence(absence, keepPortion);
    }

    if (tool.kind === "absence") {
      const dayPortion = placementMode === "top" ? "morning" : placementMode === "bottom" ? "afternoon" : "full";
      for (const date of dates) {
        // "Ein normaler Dienst bleibt bei einer Halbtags-Absenz unverändert
        // stehen" -- nicht blind nach Slot-Index räumen, sondern nur einen
        // Dienst ersetzen, der ausschliesslich in der Zielhälfte liegt.
        for (const a of regularAssignmentsOf(date)) {
          if (shiftShouldBeReplacedByAbsencePortion(a.template, dayPortion)) {
            await assignTemplate(date, null, a.id);
          }
        }
        if (dayPortion === "full") {
          for (const special of specialAssignmentsOf(date)) await removeSpecial(special.id);
        }
      }
      const created = [];
      if (dayPortion === "full") {
        // Ganztägig weiterhin zu möglichst wenigen zusammenhängenden
        // Zeiträumen gruppiert (z. B. eine Ferienwoche = ein Datensatz statt
        // sieben).
        for (const [start, end] of groupConsecutiveDates(dates)) {
          try {
            created.push(
              await api.createAbsence({ employee: employeeId, start_date: start, end_date: end, type: tool.id, day_portion: "full" })
            );
          } catch (e) {
            onError(e.message);
          }
        }
      } else {
        for (const date of dates) {
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
      setMarkedDates(new Set());
      return;
    }

    for (const date of dates) {
      const regularAssignments = regularAssignmentsOf(date);
      await applyToolToCell(
        tool,
        date,
        regularAssignments[0],
        regularAssignments[1],
        specialAssignmentsOf(date),
        datesWithHandledAbsence.has(date)
      );
    }
    setMarkedDates(new Set());
  }

  // Wunschfrei/Wunschdienst (Block 2.13): anders als Absenz-Stempeln wird ein
  // bereits bestehender Wunsch am selben Tag überschrieben (upsert) statt
  // übersprungen -- ein Wunsch ist keine rechtlich bedeutsame Absenz,
  // "Meinung ändern" soll ohne Umweg über "erst entfernen" möglich sein.
  async function handleStampWish(type, templateId) {
    const dates = Array.from(markedDates);
    const upserted = [];
    let failed = 0;
    for (const date of dates) {
      const existing = preferenceByDate.get(date);
      const payload = { type, template: type === "wunschdienst" ? templateId : null };
      try {
        upserted.push(
          existing ? await api.updateShiftPreference(existing.id, payload) : await api.createShiftPreference({ date, ...payload })
        );
      } catch {
        failed += 1;
      }
    }
    if (upserted.length) {
      setPreferences((prev) => {
        const byId = new Map(prev.map((p) => [p.id, p]));
        for (const p of upserted) byId.set(p.id, p);
        return Array.from(byId.values());
      });
    }
    setMarkedDates(new Set());
    if (failed > 0) onError(`${failed} von ${dates.length} Wünschen konnten nicht gespeichert werden.`);
  }

  async function handleRemoveWishes() {
    const toDelete = Array.from(markedDates)
      .map((d) => preferenceByDate.get(d))
      .filter(Boolean);
    const deletedIds = [];
    let failed = 0;
    for (const p of toDelete) {
      try {
        await api.deleteShiftPreference(p.id);
        deletedIds.push(p.id);
      } catch {
        failed += 1;
      }
    }
    if (deletedIds.length) setPreferences((prev) => prev.filter((p) => !deletedIds.includes(p.id)));
    setMarkedDates(new Set());
    if (failed > 0) onError(`${failed} Wunsch/Wünsche konnten nicht entfernt werden.`);
  }

  if (!employees.length) {
    return (
      <p className="empty-state">
        Keine Mitarbeiter dieser Station zugeordnet. Im Admin unter „Employees“ ergänzen.
      </p>
    );
  }

  return (
    <div className="year-plan">
      <div className="year-plan-toolbar">
        {canManage || employmentOptions.length > 1 ? (
          <label className="year-plan-employee-select">
            Mitarbeiter
            <select value={employmentKey ?? ""} onChange={(e) => setEmploymentKey(e.target.value)}>
              {employmentOptions.map((option) => {
                const hasMultiple =
                  employmentOptions.filter((o) => o.employeeId === option.employeeId).length > 1;
                const roleSuffix =
                  hasMultiple && option.employment
                    ? ` — ${option.employment.pensum_pct}%${
                        option.employment.title ? ` ${option.employment.title}` : ""
                      }`
                    : "";
                return (
                  <option key={option.key} value={option.key}>
                    {option.emp.first_name} {option.emp.last_name}
                    {roleSuffix}
                  </option>
                );
              })}
            </select>
          </label>
        ) : (
          <span className="year-plan-employee-fixed">
            {selectedOption?.emp.first_name} {selectedOption?.emp.last_name}
          </span>
        )}
        <span className="year-nav">
          <button type="button" className="btn-ghost" onClick={() => setYear((y) => y - 1)} aria-label="Vorheriges Jahr">
            ‹
          </button>
          <strong>{year}</strong>
          <button type="button" className="btn-ghost" onClick={() => setYear((y) => y + 1)} aria-label="Nächstes Jahr">
            ›
          </button>
        </span>
        {/* Port (2026-08, Nutzer-Feedback: "übernimm die genau gleiche
            Logik wie im Planblatt um zu beplanen"): dieselbe
            PlacementToolbar wie PlanGrid.jsx statt einer eigenen,
            abweichenden Stempelleiste -- Alles/Oben/Unten/Pikett gilt jetzt
            identisch für Dienste UND Absenzen. Mitarbeitende ohne
            canManage dürfen weiterhin keine Dienste/Spezialitäten stempeln
            (leere Arrays -- die Komponente blendet die jeweilige Gruppe von
            selbst aus/zeigt nur den Pikett-Leer-Hinweis), Absenzen bleiben
            für alle stempelbar. Bleibt (wie zuvor) immer sichtbar, auch
            ohne Markierung, damit das Grid beim ersten Markieren nicht nach
            unten springt (README, ursprünglicher Bugfix). */}
        <span className="stamp-palette">
          <PlacementToolbar
            placementMode={placementMode}
            onPlacementModeChange={setPlacementMode}
            regularTemplates={canManage ? regularTemplates : []}
            specialTemplates={canManage ? specialTemplates : []}
            absenceTypes={absenceTypes}
            markedCount={markedDates.size}
            onApplyTool={applyToolToMarked}
            onClearMarked={() => setMarkedDates(new Set())}
          />
          {isOwnEmployeeSelected && (
            <span className="stamp-row">
              <span className="stamp-row-label">Wünsche</span>
              <button
                type="button"
                className="stamp-chip stamp-chip--wish"
                disabled={markedDates.size === 0}
                title="Wunschfrei für alle markierten Tage eintragen (ein Hinweis für den Planer, keine Absenz)"
                onClick={() => handleStampWish("wunschfrei", null)}
              >
                Wunschfrei
              </button>
              {templates.map((t) => (
                <button
                  key={`wish-${t.id}`}
                  type="button"
                  className="stamp-chip stamp-chip--wish"
                  style={{ "--chip-color": t.color }}
                  disabled={markedDates.size === 0}
                  title={`Wunschdienst ${t.name} für alle markierten Tage eintragen`}
                  onClick={() => handleStampWish("wunschdienst", t.id)}
                >
                  Wunsch: {chipGlyph(t)}
                </button>
              ))}
              <button
                type="button"
                className="stamp-chip stamp-chip--empty"
                disabled={markedDates.size === 0}
                title="Wunschfrei/Wunschdienst der markierten Tage entfernen"
                onClick={handleRemoveWishes}
              >
                Wunsch entfernen
              </button>
            </span>
          )}
        </span>
      </div>

      {loading ? (
        <p className="loading-state">Jahresplan wird geladen …</p>
      ) : (
        <div className="year-grid">
          {MONTH_NAMES.map((monthName, monthIndex) => {
            const month = monthIndex + 1;
            const numDays = daysInMonth(year, month);
            const leadingBlanks = weekdayIndex(year, month, 1);
            const cells = [
              ...Array.from({ length: leadingBlanks }, () => null),
              ...Array.from({ length: numDays }, (_, i) => i + 1),
            ];
            return (
              <div key={month} className="year-month-card">
                <div className="year-month-title">{monthName}</div>
                <div className="year-month-weekdays">
                  {WEEKDAYS_SHORT.map((w) => (
                    <span key={w}>{w}</span>
                  ))}
                </div>
                <div className="year-month-days">
                  {cells.map((day, i) => {
                    if (day === null) return <span key={`blank-${i}`} className="year-day-cell is-empty" />;
                    const date = isoDate(year, month, day);
                    // README (2026-08, Bugfix): bis zu zwei Zuweisungen pro Tag
                    // (Split-Shift, README Punkt 18) statt nur der ersten.
                    const dayAssignments = assignmentsByDate.get(date) ?? [];
                    // Nutzer-Feedback (2026-08, Punkt 4): eine Spezialität (z. B.
                    // Pikettdienst) ist additiv, kein Konkurrent um Slot 0/1 --
                    // nur reguläre Zuweisungen zählen für die Haupt-Zellenfarbe,
                    // Spezialitäten zeigen sich rein informativ als kleiner Punkt
                    // (siehe year-day-special-dot unten).
                    const regularDayAssignments = regularAssignmentsOf(date);
                    const hasSpecialAssignment = specialAssignmentsOf(date).length > 0;
                    const [assignment, secondAssignment] = regularDayAssignments;
                    const template = assignment ? templates.find((t) => t.id === assignment.template) : null;
                    const secondTemplate = secondAssignment
                      ? templates.find((t) => t.id === secondAssignment.template)
                      : null;
                    const absence = absenceByDate.get(date);
                    const preference = preferenceByDate.get(date);
                    const wishedTemplate =
                      preference?.type === "wunschdienst" ? templates.find((t) => t.id === preference.template) : null;
                    const marked = markedDates.has(date);
                    // Port (2026-08): ein durchgehender Dienst bleibt bei einer
                    // Halbtags-Absenz unverändert bestehen (applyToolToMarked) --
                    // beide können also koexistieren (nur bei
                    // day_portion !== "full", eine ganztägige Absenz blockiert
                    // wie bisher jeden Dienst). Ohne diese Fallunterscheidung
                    // würde kind="absence" den weiterlaufenden Dienst
                    // unsichtbar machen.
                    const halfDayAbsenceWithShift = Boolean(absence) && absence.day_portion !== "full" && Boolean(assignment);
                    const kind = absence && !halfDayAbsenceWithShift ? "absence" : assignment ? "shift" : "empty";
                    const absenceColor = absence ? absenceTypesById.get(absence.type)?.color ?? "var(--ink-muted)" : null;
                    const color = kind === "absence" ? absenceColor : template?.color;
                    // Bei einem echten Split zeigt secondColor den zweiten
                    // Dienst; koexistiert stattdessen eine Halbtags-Absenz mit
                    // dem Dienst, tritt die Absenzfarbe an ihre Stelle --
                    // Reihenfolge nach day_portion (vormittags → Absenz zuerst,
                    // nachmittags → Absenz an zweiter Stelle), damit die
                    // Diagonale die betroffene Tageshälfte widerspiegelt.
                    const secondColor = halfDayAbsenceWithShift
                      ? absenceColor
                      : kind === "shift"
                        ? secondTemplate?.color
                        : null;
                    const isSplit = halfDayAbsenceWithShift ? true : Boolean(secondColor);
                    const [chipColor, chipColor2] =
                      halfDayAbsenceWithShift && absence.day_portion === "morning" ? [secondColor, color] : [color, secondColor];
                    const holidayName = holidays.get(date);
                    // Nutzer-Feedback (2026-08): Halbtags-Absenzen ("ich kann auch
                    // einen Nachmittag frei nehmen") sollen auch im Jahresplan
                    // erkennbar sein, analog zum ½-Suffix in ShiftCell.jsx.
                    const absencePortionLabel =
                      absence?.day_portion === "morning"
                        ? "Nur vormittags"
                        : absence?.day_portion === "afternoon"
                          ? "Nur nachmittags"
                          : null;
                    let title = halfDayAbsenceWithShift
                      ? `${date}: ${template.name} (${template.start_time.slice(0, 5)}–${template.end_time.slice(0, 5)}) + ` +
                        `${absenceTypesById.get(absence.type)?.name ?? absence.type}${absencePortionLabel ? `, ${absencePortionLabel}` : ""} ` +
                        `(${STATUS_LABELS[absence.status] ?? absence.status})`
                      : absence
                        ? `${date}: ${absenceTypesById.get(absence.type)?.name ?? absence.type}${absencePortionLabel ? `, ${absencePortionLabel}` : ""} (${STATUS_LABELS[absence.status] ?? absence.status})`
                        : assignment && template
                          ? `${date}: ${template.name} (${template.start_time.slice(0, 5)}–${template.end_time.slice(0, 5)})` +
                            (secondTemplate
                              ? ` + ${secondTemplate.name} (${secondTemplate.start_time.slice(0, 5)}–${secondTemplate.end_time.slice(0, 5)})`
                              : "")
                          : `${date}: frei`;
                    if (preference) {
                      title +=
                        preference.type === "wunschfrei"
                          ? " -- Wunschfrei geäussert"
                          : ` -- Wunschdienst geäussert: ${wishedTemplate?.name ?? "?"}`;
                    }
                    if (hasSpecialAssignment) {
                      const specialNames = dayAssignments
                        .filter((a) => templates.find((t) => t.id === a.template)?.category === "special")
                        .map((a) => templates.find((t) => t.id === a.template)?.name)
                        .join(", ");
                      title += ` -- Spezialität: ${specialNames}`;
                    }
                    if (holidayName) title += ` -- Feiertag: ${holidayName}`;
                    return (
                      <button
                        key={date}
                        type="button"
                        className={`year-day-cell${marked ? " is-marked" : ""}${holidayName ? " is-holiday" : ""}`}
                        title={title}
                        aria-pressed={marked}
                        onMouseDown={(e) => {
                          if (e.button !== 0) return;
                          e.preventDefault();
                          suppressClickRef.current = true;
                          startMark(date);
                        }}
                        onMouseEnter={() => continueMark(date)}
                        onClick={() => {
                          if (suppressClickRef.current) {
                            suppressClickRef.current = false;
                            return;
                          }
                          startMark(date);
                        }}
                      >
                        <span
                          className={`year-day-fill year-day-fill--${kind}${isSplit ? " is-split" : ""}${kind === "absence" && absencePortionLabel ? ` is-half-day is-half-day--${absence.day_portion}` : ""}`}
                          style={
                            chipColor2
                              ? { "--chip-color": chipColor, "--chip-color-2": chipColor2 }
                              : chipColor
                                ? { "--chip-color": chipColor }
                                : undefined
                          }
                        >
                          {day}
                        </span>
                        {preference && <span className={`year-day-wish-dot is-${preference.type}`} aria-hidden="true" />}
                        {hasSpecialAssignment && <span className="year-day-special-dot" aria-hidden="true" />}
                        {marked && (
                          <span className="select-check" aria-hidden="true">
                            ✓
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

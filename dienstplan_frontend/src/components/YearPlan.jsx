import { useEffect, useMemo, useRef, useState } from "react";
import { relevantNodeIds } from "../App.jsx";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";

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
  // README (2026-08, Bugfix): der Jahresplan kannte pro Tag nur eine
  // Zuweisung (assignmentByDate überschrieb eine zweite stillschweigend) und
  // hatte dadurch keinen Weg, einen zweiten (Split-Shift-)Dienst zu stempeln
  // -- anders als das Planblatt (PlanGrid.jsx, README Punkt 18) zeigte der
  // Jahresplan Split-Shifts nicht einmal an. stampSecondSlot spiegelt den
  // gleichnamigen Umschalter aus PlanGrid.jsx.
  const [stampSecondSlot, setStampSecondSlot] = useState(false);
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

  // README (2026-08, Bugfix): zielt bei aktivem stampSecondSlot auf den
  // zweiten Slot (Split-Shift) statt den ersten -- ein Tag ohne ersten
  // Dienst wird übersprungen (analog zu PlanGrid.jsx: handleStampAssign).
  async function handleStampShift(templateId) {
    const dates = Array.from(markedDates);
    const upserted = [];
    let skipped = 0;
    // Nutzer-Feedback (2026-08, Punkt 4): ein Chip aus der "Spezialitäten"-
    // Zeile legt IMMER eine additive neue Zuweisung an (unabhängig von
    // stampSecondSlot/Slot 0/1), analog zu PlanGrid.jsx: handleStampAssign.
    const stampedTemplate = templates.find((t) => t.id === templateId);
    const isSpecial = stampedTemplate?.category === "special";
    for (const date of dates) {
      if (isSpecial) {
        try {
          upserted.push(
            await api.createShiftAssignment({ employee: employeeId, node: selectedNode, date, template: templateId })
          );
        } catch {
          skipped += 1;
        }
        continue;
      }
      const dayAssignments = (assignmentsByDate.get(date) ?? []).filter(
        (a) => templates.find((t) => t.id === a.template)?.category !== "special"
      );
      if (stampSecondSlot && !dayAssignments[0]) {
        skipped += 1;
        continue;
      }
      const existing = stampSecondSlot ? dayAssignments[1] : dayAssignments[0];
      try {
        if (existing) {
          upserted.push(await api.updateShiftAssignment(existing.id, { template: templateId }));
        } else {
          upserted.push(
            await api.createShiftAssignment({ employee: employeeId, node: selectedNode, date, template: templateId })
          );
        }
      } catch {
        // z. B. Ruhezeit-, Höchstarbeitszeit- oder Absenz-Konflikt (siehe
        // ShiftAssignment.clean()) -- Tag überspringen, Rest weiterstempeln.
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
    setMarkedDates(new Set());
    setStampSecondSlot(false);
    if (skipped > 0) {
      onError(
        `${dates.length - skipped} von ${dates.length} Tagen zugewiesen, ${skipped} wegen Regel-Konflikten ` +
          "(z. B. Ruhezeit) übersprungen."
      );
    }
  }

  async function handleClearShifts() {
    // "Schicht leeren" bleibt auf Slot 0/1 beschränkt -- additive
    // Spezialitäten dieses Tages bleiben unangetastet (Punkt 4).
    const toDelete = Array.from(markedDates)
      .map(
        (d) =>
          (assignmentsByDate.get(d) ?? []).filter(
            (a) => templates.find((t) => t.id === a.template)?.category !== "special"
          )[stampSecondSlot ? 1 : 0]
      )
      .filter(Boolean);
    const deletedIds = [];
    let failed = 0;
    for (const a of toDelete) {
      try {
        await api.deleteShiftAssignment(a.id);
        deletedIds.push(a.id);
      } catch {
        failed += 1;
      }
    }
    if (deletedIds.length) {
      setAssignments((prev) => prev.filter((a) => !deletedIds.includes(a.id)));
    }
    setMarkedDates(new Set());
    setStampSecondSlot(false);
    if (failed > 0) onError(`${failed} Schicht(en) konnten nicht entfernt werden.`);
  }

  async function handleStampAbsence(type) {
    // Tage mit bestehender Absenz überspringen statt zu überlappen (analog
    // zum Wochenmuster-Kopieren: bereits belegte Zieltage nicht überschreiben).
    const dates = Array.from(markedDates).filter((d) => !absenceByDate.has(d));
    const skippedExisting = markedDates.size - dates.length;
    const ranges = groupConsecutiveDates(dates);
    const created = [];
    let skippedConflict = 0;
    for (const [start, end] of ranges) {
      try {
        created.push(await api.createAbsence({ employee: employeeId, start_date: start, end_date: end, type }));
      } catch {
        skippedConflict += 1;
      }
    }
    if (created.length) setAbsences((prev) => [...prev, ...created]);
    setMarkedDates(new Set());
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
    // nur ein Teil eines mehrtägigen Zeitraums markiert war (siehe README) --
    // ein Absenz-Datensatz lässt sich nicht teilweise löschen, ohne ihn in
    // zwei neue Zeiträume aufzuspalten, was hier bewusst nicht automatisiert
    // wird.
    const toDelete = new Map();
    for (const d of markedDates) {
      const absence = absenceByDate.get(d);
      if (absence) toDelete.set(absence.id, absence);
    }
    const deletedIds = [];
    let failed = 0;
    for (const absence of toDelete.values()) {
      try {
        await api.deleteAbsence(absence.id);
        deletedIds.push(absence.id);
      } catch {
        // z. B. bereits genehmigte Absenz einer Mitarbeiter-Rolle (nur
        // Admin/Planer dürfen die noch löschen, siehe OwnEmployeeRecordPermission).
        failed += 1;
      }
    }
    if (deletedIds.length) {
      setAbsences((prev) => prev.filter((a) => !deletedIds.includes(a.id)));
    }
    setMarkedDates(new Set());
    if (failed > 0) {
      onError(
        `${failed} Absenz(en) konnten nicht entfernt werden -- bereits genehmigte Absenzen dürfen ` +
          "Mitarbeitende nicht mehr selbst löschen."
      );
    }
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
        {/* README (2026-08, Bugfix): die Stempelleiste erschien bisher erst
            nach dem ersten markierten Tag -- das liess den ganzen
            Kalender-Grid genau in dem Moment nach unten springen, in dem der
            Nutzer den ersten Tag anklickt (analog zum selben Bug im
            Planblatt, siehe PlanGrid.jsx). Palette jetzt immer sichtbar,
            Buttons/Chips nur deaktiviert, solange nichts markiert ist --
            reserviert den Platz von Anfang an. */}
        <span className="stamp-palette">
          <span className="multi-select-hint">
            {markedDates.size === 0 ? "Tage anklicken, um sie zu markieren." : `${markedDates.size} markiert:`}
          </span>
          {canManage && (
            <span className="stamp-row">
              <span className="stamp-row-label">Dienste</span>
              <label className="stamp-second-slot-toggle" title="Bestehenden ersten Dienst nicht ersetzen, sondern einen zweiten (Split-Shift) danebenstellen. Gilt auch für Spezialitäten unten.">
                <input
                  type="checkbox"
                  checked={stampSecondSlot}
                  disabled={markedDates.size === 0}
                  onChange={(e) => setStampSecondSlot(e.target.checked)}
                />
                Als zweiten Dienst hinzufügen
              </label>
              {regularTemplates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="stamp-chip"
                  style={{ "--chip-color": t.color }}
                  disabled={markedDates.size === 0}
                  title={`${t.name} (${t.start_time.slice(0, 5)}–${t.end_time.slice(0, 5)}) auf alle markierten Tage anwenden`}
                  onClick={() => handleStampShift(t.id)}
                >
                  {t.name.slice(0, 3)}
                </button>
              ))}
              <button
                type="button"
                className="stamp-chip stamp-chip--empty"
                disabled={markedDates.size === 0}
                title="Schicht(en) entfernen"
                onClick={handleClearShifts}
              >
                Schicht leeren
              </button>
            </span>
          )}
          <span className="stamp-row">
            <span className="stamp-row-label">Abwesenheiten</span>
            {absenceTypes.map((t) => (
              <button
                key={t.id}
                type="button"
                className="stamp-chip stamp-chip--absence"
                style={{ "--chip-color": t.color }}
                disabled={markedDates.size === 0}
                title={`${t.name} für alle markierten Tage eintragen`}
                onClick={() => handleStampAbsence(t.id)}
              >
                {t.name}
              </button>
            ))}
            <button
              type="button"
              className="stamp-chip stamp-chip--empty"
              disabled={markedDates.size === 0}
              title="Absenz(en) der markierten Tage entfernen -- löscht den ganzen Zeitraum, nicht nur die markierten Tage daraus"
              onClick={handleRemoveAbsences}
            >
              Absenz entfernen
            </button>
          </span>
          {canManage && specialTemplates.length > 0 && (
            <span className="stamp-row">
              <span className="stamp-row-label">Spezialitäten</span>
              {specialTemplates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="stamp-chip"
                  style={{ "--chip-color": t.color }}
                  disabled={markedDates.size === 0}
                  title={`${t.name} (${t.start_time.slice(0, 5)}–${t.end_time.slice(0, 5)}) auf alle markierten Tage anwenden`}
                  onClick={() => handleStampShift(t.id)}
                >
                  {t.name.slice(0, 3)}
                </button>
              ))}
            </span>
          )}
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
                  Wunsch: {t.name.slice(0, 3)}
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
          <span className="stamp-row">
            <button
              type="button"
              className="btn-ghost"
              disabled={markedDates.size === 0}
              onClick={() => {
                setMarkedDates(new Set());
                setStampSecondSlot(false);
              }}
            >
              Auswahl aufheben
            </button>
          </span>
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
                    const regularDayAssignments = dayAssignments.filter(
                      (a) => templates.find((t) => t.id === a.template)?.category !== "special"
                    );
                    const hasSpecialAssignment = dayAssignments.some(
                      (a) => templates.find((t) => t.id === a.template)?.category === "special"
                    );
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
                    const kind = absence ? "absence" : assignment ? "shift" : "empty";
                    const color = absence ? absenceTypesById.get(absence.type)?.color ?? "var(--ink-muted)" : template?.color;
                    const secondColor = !absence ? secondTemplate?.color : null;
                    const holidayName = holidays.get(date);
                    let title = absence
                      ? `${date}: ${absenceTypesById.get(absence.type)?.name ?? absence.type} (${STATUS_LABELS[absence.status] ?? absence.status})`
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
                          className={`year-day-fill year-day-fill--${kind}${secondColor ? " is-split" : ""}`}
                          style={
                            secondColor
                              ? { "--chip-color": color, "--chip-color-2": secondColor }
                              : color
                                ? { "--chip-color": color }
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

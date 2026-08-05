import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import BalanceBadge from "./BalanceBadge.jsx";
import FloatingPopover from "./FloatingPopover.jsx";
import ShiftCell from "./ShiftCell.jsx";

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

function weekdayLabel(year, month, day) {
  const jsDay = new Date(year, month - 1, day).getDay(); // 0 = Sonntag
  return WEEKDAYS_SHORT[jsDay === 0 ? 6 : jsDay - 1];
}

export default function PlanGrid({ nodeId, nodes, year, month, employees, me, onError }) {
  const [templates, setTemplates] = useState([]);
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
      api.getShiftAssignments(nodeId, dateFrom, dateTo),
      api.getAbsences(),
      api.getTimeRecords(dateFrom, dateTo),
      preferencesRequest,
      api.getTenantHolidays(year),
    ])
      .then(([templatesRes, assignmentsRes, absencesRes, timeRecordsRes, preferencesRes, holidaysRes]) => {
        if (cancelled) return;
        setTemplates((templatesRes.results ?? templatesRes).filter((t) => t.node === nodeId));
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
  }, [nodeId, dateFrom, dateTo, canManage, ownEmployeeId, year]);

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

  const assignmentMap = useMemo(() => {
    const map = new Map();
    for (const a of assignments) map.set(`${a.employee}:${a.date}`, a);
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

  async function handleAssign(employeeId, date, templateId, rowNodeId) {
    const key = `${employeeId}:${date}`;
    const existing = assignmentMap.get(key);
    try {
      if (templateId === null) {
        if (!existing) return;
        await api.deleteShiftAssignment(existing.id);
        setAssignments((prev) => prev.filter((a) => a.id !== existing.id));
        return;
      }
      if (existing) {
        const updated = await api.updateShiftAssignment(existing.id, { template: templateId });
        setAssignments((prev) => prev.map((a) => (a.id === existing.id ? updated : a)));
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

  async function handleMove(fromEmployeeId, fromDate, toEmployeeId, toDate, toRowNodeId) {
    if (fromEmployeeId === toEmployeeId && fromDate === toDate) return;
    const source = assignmentMap.get(`${fromEmployeeId}:${fromDate}`);
    if (!source) return;
    // README Block 2.8: die Zielzelle muss zusätzlich nach ihrem eigenen
    // Team-Knoten aufgelöst werden, nicht nur nach employee+date -- bei
    // Mehrfachanstellung (Punkt 17) kann dieselbe Person an diesem Tag
    // bereits eine Zuweisung in einem ANDEREN Team haben, die optisch
    // leere Zielzelle wäre sonst fälschlich "belegt".
    const target = assignments.find(
      (a) => a.employee === toEmployeeId && a.date === toDate && a.node === toRowNodeId
    );
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

  async function handleCopyWeekPattern(employeeId, rowNodeId) {
    const sourceDays = days.filter((d) => d <= 7);
    const hasSourceShift = sourceDays.some((d) =>
      assignmentMap.has(`${employeeId}:${isoDate(year, month, d)}`)
    );
    if (!hasSourceShift) {
      onError("Die erste Woche hat für diesen Mitarbeiter noch keine Schichten zum Kopieren.");
      return;
    }

    const created = [];
    let skippedByConflict = 0;
    for (let targetDay = 8; targetDay <= days.length; targetDay += 1) {
      const sourceDay = ((targetDay - 1) % 7) + 1;
      const source = assignmentMap.get(`${employeeId}:${isoDate(year, month, sourceDay)}`);
      if (!source) continue;
      const targetDate = isoDate(year, month, targetDay);
      if (assignmentMap.has(`${employeeId}:${targetDate}`)) continue; // bestehende Einträge nicht überschreiben

      try {
        const createdAssignment = await api.createShiftAssignment({
          employee: employeeId,
          node: rowNodeId ?? nodeId,
          date: targetDate,
          template: source.template,
        });
        created.push(createdAssignment);
      } catch {
        // z. B. Ruhezeit- oder Höchstarbeitszeit-Konflikt an diesem Tag -- Zelle
        // überspringen, restliche Wochen trotzdem weiterkopieren.
        skippedByConflict += 1;
      }
    }

    if (created.length) {
      setAssignments((prev) => [...prev, ...created]);
    }
    if (skippedByConflict > 0) {
      onError(
        `Wochenmuster kopiert, ${skippedByConflict} Tag(e) wegen Regel-Konflikten (z. B. Ruhezeit) übersprungen.`
      );
    }
  }

  function toggleMultiSelectMode() {
    setMultiSelectMode((v) => !v);
    setMarkedCells(new Set());
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
  async function handleStampAssign(templateId) {
    const keys = Array.from(markedCells);
    const upserted = [];
    const deletedIds = [];
    let skipped = 0;

    for (const key of keys) {
      const [employeeIdStr, date, rowNodeIdStr] = key.split(":");
      const employeeId = Number(employeeIdStr);
      const rowNodeId = Number(rowNodeIdStr);
      const assignmentKey = `${employeeId}:${date}`;
      const existing = assignmentMap.get(assignmentKey);
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

  async function handleOfferTrade(employeeId, date, targetEmployeeId) {
    const assignment = assignmentMap.get(`${employeeId}:${date}`);
    if (!assignment) return;
    try {
      await api.createShiftTradeRequest({
        requester_assignment: assignment.id,
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
          {multiSelectMode && markedCells.size === 0 && (
            <span className="multi-select-hint">Tage anklicken, um sie zu markieren.</span>
          )}
          {multiSelectMode && markedCells.size > 0 && (
            <span className="stamp-palette">
              <span className="multi-select-hint">
                {markedCells.size} markiert -- Schichttyp zum Zuweisen anklicken:
              </span>
              {templates.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="stamp-chip"
                  style={{ "--chip-color": t.color }}
                  title={`${t.name} (${t.start_time.slice(0, 5)}–${t.end_time.slice(0, 5)}) auf alle markierten Tage anwenden`}
                  onClick={() => handleStampAssign(t.id)}
                >
                  {t.name.slice(0, 3)}
                </button>
              ))}
              <button
                type="button"
                className="stamp-chip stamp-chip--empty"
                title="Markierte Tage leeren"
                onClick={() => handleStampAssign(null)}
              >
                — leer —
              </button>
              <button type="button" className="btn-ghost" onClick={() => setMarkedCells(new Set())}>
                Auswahl aufheben
              </button>
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
                    const rawAssignment = assignmentMap.get(`${emp.id}:${date}`);
                    // README Punkt 17: bei mehreren Anstellungen derselben
                    // Person in unterschiedlichen Teams zeigt eine Zeile nur
                    // die Zuweisung, die tatsächlich zu ihrem eigenen Team
                    // gehört (assignment.node) -- die Schicht der jeweils
                    // anderen Rolle bleibt in dieser Zeile korrekt leer.
                    const assignment = rawAssignment && rawAssignment.node === rowNodeId ? rawAssignment : undefined;
                    const template = templates.find((t) => t.id === assignment?.template);
                    const absence = findAbsence(emp.id, date);
                    const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                    const holidayName = holidays.get(date);
                    const canOfferTrade = canManage || me?.employee?.id === emp.id;
                    // Block 1.13: Ist-Zeit-Badge nur auf der eigenen, bereits
                    // stattgefundenen Schicht -- unabhängig von canManage, damit
                    // auch ein Admin/Planer mit eigenem Employee-Profil seine
                    // eigenen Schichten erfassen kann.
                    const canRecordTime = Boolean(assignment) && date <= todayIso && ownEmployeeId === emp.id;
                    const timeRecord = assignment ? timeRecordByAssignment.get(assignment.id) : undefined;
                    // Block 2.13: höchstpersönlich -- nur die eigene Person darf
                    // für einen heutigen/zukünftigen Tag einen Wunsch äussern.
                    const preference = preferenceMap.get(`${emp.id}:${date}`);
                    const canEditOwnWish = ownEmployeeId === emp.id && date >= todayIso;
                    return (
                      <td
                        key={d}
                        className={[weekend && "is-weekend", holidayName && "is-holiday"].filter(Boolean).join(" ")}
                        title={holidayName || undefined}
                      >
                        <ShiftCell
                          templates={templates}
                          selectedTemplateId={assignment?.template ?? null}
                          templateInfo={template}
                          employeeId={emp.id}
                          date={date}
                          absence={absence}
                          colleagues={employees.filter((e) => e.id !== emp.id)}
                          canEdit={canManage}
                          canOfferTrade={canOfferTrade}
                          onChange={(templateId) => handleAssign(emp.id, date, templateId, rowNodeId)}
                          onMove={(fromEmployeeId, fromDate, toEmployeeId, toDate) =>
                            handleMove(fromEmployeeId, fromDate, toEmployeeId, toDate, rowNodeId)
                          }
                          onOfferTrade={(targetEmployeeId) =>
                            handleOfferTrade(emp.id, date, targetEmployeeId)
                          }
                          timeRecord={timeRecord}
                          canRecordTime={canRecordTime}
                          onSaveTimeRecord={(payload) => handleSaveTimeRecord(assignment.id, timeRecord, payload)}
                          onDeleteTimeRecord={() => handleDeleteTimeRecord(timeRecord)}
                          preference={preference}
                          canEditOwnWish={canEditOwnWish}
                          onSaveWish={(payload) => handleSaveWish(date, preference, payload)}
                          onDeleteWish={() => handleDeleteWish(preference)}
                          selectionMode={multiSelectMode}
                          marked={markedCells.has(`${emp.id}:${date}:${rowNodeId}`)}
                          onMarkStart={() => startMark(emp.id, date, rowNodeId)}
                          onMarkEnter={() => continueMark(emp.id, date, rowNodeId)}
                        />
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

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import BalanceBadge from "./BalanceBadge.jsx";
import ShiftCell from "./ShiftCell.jsx";

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

export default function PlanGrid({ nodeId, year, month, employees, me, onError }) {
  const [templates, setTemplates] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [absences, setAbsences] = useState([]);
  const [timeRecords, setTimeRecords] = useState([]);
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

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.getTimeTemplates(),
      api.getShiftAssignments(nodeId, dateFrom, dateTo),
      api.getAbsences(),
      api.getTimeRecords(dateFrom, dateTo),
    ])
      .then(([templatesRes, assignmentsRes, absencesRes, timeRecordsRes]) => {
        if (cancelled) return;
        setTemplates((templatesRes.results ?? templatesRes).filter((t) => t.node === nodeId));
        setAssignments(assignmentsRes.results ?? assignmentsRes);
        // Nur genehmigte Absenzen blockieren/zeigen sich im Grid (siehe
        // ShiftAssignment._check_no_absence_conflict im Backend) -- offene
        // Anträge sieht man im Tab "Abwesenheiten", nicht hier.
        const absenceList = absencesRes.results ?? absencesRes;
        setAbsences(absenceList.filter((a) => a.status === "approved"));
        setTimeRecords(timeRecordsRes.results ?? timeRecordsRes);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, dateFrom, dateTo]);

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

  async function handleAssign(employeeId, date, templateId) {
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
        const created = await api.createShiftAssignment({
          employee: employeeId,
          node: nodeId,
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

  async function handleMove(fromEmployeeId, fromDate, toEmployeeId, toDate) {
    if (fromEmployeeId === toEmployeeId && fromDate === toDate) return;
    const source = assignmentMap.get(`${fromEmployeeId}:${fromDate}`);
    if (!source) return;
    if (assignmentMap.has(`${toEmployeeId}:${toDate}`)) {
      onError("Zielfeld ist bereits belegt. Bitte zuerst leeren, bevor eine Schicht dorthin verschoben wird.");
      return;
    }
    try {
      const updated = await api.updateShiftAssignment(source.id, {
        employee: toEmployeeId,
        date: toDate,
      });
      setAssignments((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
    } catch (e) {
      // Greift z. B. bei einer Ruhezeit-Verletzung am Zieltag (siehe ShiftAssignment.clean())
      onError(e.message);
    }
  }

  async function handleCopyWeekPattern(employeeId) {
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
          node: nodeId,
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
  // Aufrufe denselben Modus auf weitere Zellen an.
  function startMark(employeeId, date) {
    const key = `${employeeId}:${date}`;
    const shouldMark = !markedCells.has(key);
    dragMarkModeRef.current = shouldMark ? "mark" : "unmark";
    applyMark(key, shouldMark);
  }

  function continueMark(employeeId, date) {
    if (!dragMarkModeRef.current) return;
    applyMark(`${employeeId}:${date}`, dragMarkModeRef.current === "mark");
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
      const [employeeIdStr, date] = key.split(":");
      const employeeId = Number(employeeIdStr);
      const existing = assignmentMap.get(key);
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
            node: nodeId,
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
                const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                return (
                  <th key={d} className={weekend ? "is-weekend" : ""}>
                    <span className="day-num">{d}</span>
                    <span className="day-weekday">{weekdayLabel(year, month, d)}</span>
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
            {employees.map((emp) => (
              <tr key={emp.id}>
                <th scope="row" className="col-employee">
                  <span className="employee-row-inner">
                    <span className="employee-name">
                      {emp.first_name} {emp.last_name}
                    </span>
                    <span className="pct">{emp.employment_pct}%</span>
                    {canManage && (
                      <button
                        type="button"
                        className="btn-copy-week"
                        title="Muster der ersten Woche auf die restlichen Wochen dieses Monats kopieren (belegte Tage bleiben unverändert)"
                        onClick={() => handleCopyWeekPattern(emp.id)}
                      >
                        ⧉<span className="visually-hidden"> Wochenmuster kopieren für {emp.first_name} {emp.last_name}</span>
                      </button>
                    )}
                  </span>
                </th>
                {days.map((d) => {
                  const date = isoDate(year, month, d);
                  const assignment = assignmentMap.get(`${emp.id}:${date}`);
                  const template = templates.find((t) => t.id === assignment?.template);
                  const absence = findAbsence(emp.id, date);
                  const weekend = ["Sa", "So"].includes(weekdayLabel(year, month, d));
                  const canOfferTrade = canManage || me?.employee?.id === emp.id;
                  // Block 1.13: Ist-Zeit-Badge nur auf der eigenen, bereits
                  // stattgefundenen Schicht -- unabhängig von canManage, damit
                  // auch ein Admin/Planer mit eigenem Employee-Profil seine
                  // eigenen Schichten erfassen kann.
                  const canRecordTime = Boolean(assignment) && date <= todayIso && ownEmployeeId === emp.id;
                  const timeRecord = assignment ? timeRecordByAssignment.get(assignment.id) : undefined;
                  return (
                    <td key={d} className={weekend ? "is-weekend" : ""}>
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
                        onChange={(templateId) => handleAssign(emp.id, date, templateId)}
                        onMove={handleMove}
                        onOfferTrade={(targetEmployeeId) =>
                          handleOfferTrade(emp.id, date, targetEmployeeId)
                        }
                        timeRecord={timeRecord}
                        canRecordTime={canRecordTime}
                        onSaveTimeRecord={(payload) => handleSaveTimeRecord(assignment.id, timeRecord, payload)}
                        onDeleteTimeRecord={() => handleDeleteTimeRecord(timeRecord)}
                        selectionMode={multiSelectMode}
                        marked={markedCells.has(`${emp.id}:${date}`)}
                        onMarkStart={() => startMark(emp.id, date)}
                        onMarkEnter={() => continueMark(emp.id, date)}
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
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

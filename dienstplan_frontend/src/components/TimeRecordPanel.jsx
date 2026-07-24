import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";
import { effectiveRecordSegments, effectiveTemplateSegments, formatDeviation } from "../timeRecordSegments.js";
import TimeRecordSegmentEditor from "./TimeRecordSegmentEditor.jsx";

const STATUS_LABELS = {
  submitted: "Erfasst",
  confirmed: "Geprüft",
};

function pad(n) {
  return String(n).padStart(2, "0");
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function isoDate(year, month, day) {
  return `${year}-${pad(month)}-${pad(day)}`;
}

export default function TimeRecordPanel({ nodeId, year, month, employees, me, onError }) {
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;

  const [assignments, setAssignments] = useState([]);
  const [records, setRecords] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);

  const dateFrom = isoDate(year, month, 1);
  const dateTo = isoDate(year, month, daysInMonth(year, month));
  const todayIso = new Date().toISOString().slice(0, 10);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([api.getShiftAssignments(nodeId, dateFrom, dateTo), api.getTimeRecords(), api.getTimeTemplates()])
      .then(([assignmentsRes, recordsRes, templatesRes]) => {
        if (cancelled) return;
        setAssignments(assignmentsRes.results ?? assignmentsRes);
        setRecords(recordsRes.results ?? recordsRes);
        setTemplates(templatesRes.results ?? templatesRes);
      })
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, dateFrom, dateTo]);

  const recordByAssignment = useMemo(() => {
    const map = new Map();
    for (const r of records) map.set(r.assignment, r);
    return map;
  }, [records]);

  const visibleAssignments = useMemo(() => {
    return assignments
      .filter((a) => a.date <= todayIso)
      .filter((a) => canManage || a.employee === ownEmployeeId)
      .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assignments, canManage, ownEmployeeId, todayIso]);

  function employeeName(id) {
    const employee = employees.find((e) => e.id === id);
    return employee ? `${employee.first_name} ${employee.last_name}` : `#${id}`;
  }

  function templateFor(id) {
    return templates.find((t) => t.id === id);
  }

  async function handleSave(assignment, record, payload) {
    setSaving(true);
    try {
      const saved = record
        ? await api.updateTimeRecord(record.id, { assignment: assignment.id, ...payload })
        : await api.createTimeRecord({ assignment: assignment.id, ...payload });
      setRecords((prev) => (record ? prev.map((r) => (r.id === saved.id ? saved : r)) : [...prev, saved]));
      setEditingId(null);
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(record) {
    try {
      await api.deleteTimeRecord(record.id);
      setRecords((prev) => prev.filter((r) => r.id !== record.id));
    } catch (e) {
      onError(e.message);
    }
  }

  async function handleConfirm(record) {
    try {
      const updated = await api.confirmTimeRecord(record.id);
      setRecords((prev) => prev.map((r) => (r.id === record.id ? updated : r)));
    } catch (e) {
      onError(e.message);
    }
  }

  if (loading) return <p className="loading-state">Zeiterfassung wird geladen …</p>;
  if (!visibleAssignments.length) {
    return <p className="empty-state">Keine vergangenen Schichten in diesem Zeitraum.</p>;
  }

  return (
    <div className="side-panel">
      <div className="panel-list panel-list--full">
        <h2>Zeiterfassung</h2>
        <p className="panel-hint">
          Ist-Arbeitszeit zu vergangenen Schichten erfassen (Art. 73 ArGV 1). Die geplanten Zeiten
          sind zur Korrektur vorausgefüllt.
        </p>
        <ul className="entry-list">
          {visibleAssignments.map((a) => {
            const record = recordByAssignment.get(a.id);
            const template = templateFor(a.template);
            const plannedSegments = effectiveTemplateSegments(template);
            const recordSegments = effectiveRecordSegments(record);
            const canEditThis = canManage || a.employee === ownEmployeeId;
            const canEditRecord = canEditThis && (!record || record.status === "submitted");
            const isEditing = editingId === a.id;
            return (
              <li key={a.id} className="entry-list-item entry-list-item--trade">
                {record && (
                  <span className={`status-badge status-badge--${record.status}`}>
                    {STATUS_LABELS[record.status] ?? record.status}
                  </span>
                )}
                <span className="entry-main">
                  <strong>{employeeName(a.employee)}</strong> · {a.date} · Geplant{" "}
                  {plannedSegments.map((s) => `${s.start_time.slice(0, 5)}–${s.end_time.slice(0, 5)}`).join(", ")}
                  {record ? (
                    <>
                      {" "}
                      · Ist{" "}
                      {recordSegments.map((s) => `${s.actual_start.slice(0, 5)}–${s.actual_end.slice(0, 5)}`).join(", ")}{" "}
                      ({formatDeviation(record.deviation_minutes)}
                      {plannedSegments.length > 1 && ` / ${formatDeviation(record.end_deviation_minutes)}`})
                      {record.break_below_minimum && (
                        <span className="entry-note"> · Pause unter Art.-15-Minimum</span>
                      )}
                      {record.note && <span className="entry-note"> · {record.note}</span>}
                    </>
                  ) : (
                    <span className="entry-note"> · noch nicht erfasst</span>
                  )}
                </span>
                {isEditing ? (
                  <TimeRecordSegmentEditor
                    plannedSegments={plannedSegments}
                    initialSegments={recordSegments}
                    initialNote={record?.note ?? ""}
                    saving={saving}
                    canDelete={Boolean(record)}
                    onSave={(payload) => handleSave(a, record, payload)}
                    onCancel={() => setEditingId(null)}
                    onDelete={() => {
                      handleDelete(record);
                      setEditingId(null);
                    }}
                  />
                ) : (
                  <span className="entry-actions">
                    {canEditRecord && (
                      <button type="button" className="btn-ghost" onClick={() => setEditingId(a.id)}>
                        {record ? "Korrigieren" : "Ist-Zeit erfassen"}
                      </button>
                    )}
                    {canManage && record && record.status === "submitted" && (
                      <button type="button" onClick={() => handleConfirm(record)}>
                        Bestätigen
                      </button>
                    )}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

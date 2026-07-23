import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { canManageSchedule } from "../roles.js";

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

function emptyForm(start, end, breakMinutes) {
  return {
    actual_start: start ?? "",
    actual_end: end ?? "",
    actual_break_minutes: breakMinutes ?? 0,
    note: "",
  };
}

export default function TimeRecordPanel({ nodeId, year, month, employees, me, onError }) {
  const canManage = canManageSchedule(me);
  const ownEmployeeId = me?.employee?.id ?? null;

  const [assignments, setAssignments] = useState([]);
  const [records, setRecords] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(() => emptyForm());
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

  function startEditing(assignment, record) {
    const template = templateFor(assignment.template);
    setEditingId(assignment.id);
    setForm(
      record
        ? {
            actual_start: record.actual_start.slice(0, 5),
            actual_end: record.actual_end.slice(0, 5),
            actual_break_minutes: record.actual_break_minutes,
            note: record.note,
          }
        : emptyForm(template?.start_time?.slice(0, 5), template?.end_time?.slice(0, 5), template?.break_minutes)
    );
  }

  async function handleSave(assignment) {
    const existing = recordByAssignment.get(assignment.id);
    setSaving(true);
    try {
      const payload = {
        assignment: assignment.id,
        actual_start: form.actual_start,
        actual_end: form.actual_end,
        actual_break_minutes: Number(form.actual_break_minutes),
        note: form.note,
      };
      const saved = existing
        ? await api.updateTimeRecord(existing.id, payload)
        : await api.createTimeRecord(payload);
      setRecords((prev) => (existing ? prev.map((r) => (r.id === saved.id ? saved : r)) : [...prev, saved]));
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
                  {template ? `${template.start_time.slice(0, 5)}–${template.end_time.slice(0, 5)}` : "…"}
                  {record ? (
                    <>
                      {" "}
                      · Ist {record.actual_start.slice(0, 5)}–{record.actual_end.slice(0, 5)} (
                      {record.deviation_minutes > 0 ? "+" : ""}
                      {record.deviation_minutes} Min.)
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
                  <span className="entry-actions time-record-form">
                    <input
                      type="time"
                      value={form.actual_start}
                      onChange={(e) => setForm((prev) => ({ ...prev, actual_start: e.target.value }))}
                    />
                    <input
                      type="time"
                      value={form.actual_end}
                      onChange={(e) => setForm((prev) => ({ ...prev, actual_end: e.target.value }))}
                    />
                    <input
                      type="number"
                      min="0"
                      title="Pause in Minuten"
                      value={form.actual_break_minutes}
                      onChange={(e) => setForm((prev) => ({ ...prev, actual_break_minutes: e.target.value }))}
                    />
                    <input
                      type="text"
                      placeholder="Notiz / Begründung"
                      value={form.note}
                      onChange={(e) => setForm((prev) => ({ ...prev, note: e.target.value }))}
                    />
                    <button type="button" disabled={saving} onClick={() => handleSave(a)}>
                      Speichern
                    </button>
                    <button type="button" className="btn-ghost" onClick={() => setEditingId(null)}>
                      Abbrechen
                    </button>
                  </span>
                ) : (
                  <span className="entry-actions">
                    {canEditRecord && (
                      <button type="button" className="btn-ghost" onClick={() => startEditing(a, record)}>
                        {record ? "Korrigieren" : "Ist-Zeit erfassen"}
                      </button>
                    )}
                    {canManage && record && record.status === "submitted" && (
                      <button type="button" onClick={() => handleConfirm(record)}>
                        Bestätigen
                      </button>
                    )}
                    {canEditRecord && record && (
                      <button type="button" className="btn-ghost" onClick={() => handleDelete(record)}>
                        Löschen
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

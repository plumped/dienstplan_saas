import { useEffect, useState } from "react";
import { api, onBalanceChanged } from "../api.js";
import { isTenantAdmin, ROLE_LABELS, ROLE_OPTIONS } from "../roles.js";
import BalanceBadge from "./BalanceBadge.jsx";
import EmploymentEditor from "./EmploymentEditor.jsx";
import FairnessBadge from "./FairnessBadge.jsx";
import NodeScopeEditor, { toggleNodeSelection } from "./NodeScopeEditor.jsx";
import PregnancyEditor from "./PregnancyEditor.jsx";
import { IconPencil, IconPlus, IconSearch, IconUsers } from "../icons.jsx";

// Nutzer-Feedback (2026-08): "warum soll ich Freitext-Benutzernamen
// vergeben?" -- statt der Admin muss sich einen ausdenken, wird er aus den
// bereits eingegebenen Namen vorgeschlagen (editierbar, siehe
// newUsernameTouched-Flag unten). Rein clientseitig, keine Eindeutigkeits-
// Prüfung hier -- die passiert serverseitig (EmployeeAccessSetupSerializer.
// validate_username) und zeigt sich als Feldfehler, falls der Vorschlag
// bereits vergeben ist.
function suggestUsername(firstName, lastName) {
  const normalize = (s) =>
    s
      .trim()
      .toLowerCase()
      .replace(/ä/g, "ae")
      .replace(/ö/g, "oe")
      .replace(/ü/g, "ue")
      .replace(/ß/g, "ss")
      .replace(/[^a-z0-9]+/g, "");
  const first = normalize(firstName);
  const last = normalize(lastName);
  return first && last ? `${first}.${last}` : "";
}

// Nutzer-Feedback (2026-08): "die Stammdatenpflege ist bei vielen
// Mitarbeitenden katastrophal -- unsortierte Liste, alles scrollend suchen".
// Ab dieser Grössenordnung ist "alles laden und im Frontend filtern" (die
// bisherige Lösung) keine Option mehr -- Suche/Sortierung/Filterung laufen
// jetzt serverseitig (api.searchEmployees(), EmployeeViewSet:
// ?search=/?ordering=/?node=/?is_active=/?page=), das Frontend hält jeweils
// nur eine Tabellenseite (PAGE_SIZE=50) im Speicher.
// Muss mit REST_FRAMEWORK.PAGE_SIZE in config/settings.py übereinstimmen --
// nur für die "Seite X von Y"-Anzeige, keine Server-Logik hängt daran (der
// eigentliche next/previous-Zustand kommt direkt aus der DRF-Pagination).
const EMPLOYEE_PAGE_SIZE = 50;

const SORT_COLUMNS = [
  { field: "last_name", label: "Nachname" },
  { field: "first_name", label: "Vorname" },
  { field: "employment_pct", label: "Pensum" },
  { field: "is_active", label: "Status" },
];

// 0=Montag ... 6=Sonntag, wie date.weekday() im Backend (scheduling.models:
// Employee.fixed_weekdays_off) -- dieselbe Konvention wie PlanGrid.jsx.
const WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

function emptyForm() {
  return {
    first_name: "",
    last_name: "",
    birth_date: "",
    employment_pct: 100,
    fixed_weekdays_off: [],
    employment_start_date: new Date().toISOString().slice(0, 10),
    termination_date: "",
    employments: [],
    skills: [],
    is_active: true,
    maximum_weekly_hours: "",
    standard_weekly_hours: "",
    overtime_balance_carryover_hours: 0,
    vacation_days_per_year: "",
    last_night_work_medical_exam_date: "",
    // Login-Zugang (Nutzer-Feedback 2026-08, siehe Komponenten-Kommentar
    // unten): existingUsername/membership_id beschreiben einen bereits
    // bestehenden Account, hasLogin/newUsername/role die NEUANLAGE.
    hasLogin: false,
    existingUsername: null,
    membership_id: null,
    newUsername: "",
    newUsernameTouched: false,
    role: "employee",
  };
}

function toFormValues(employee) {
  return {
    first_name: employee.first_name,
    last_name: employee.last_name,
    birth_date: employee.birth_date ?? "",
    employment_pct: employee.employment_pct,
    fixed_weekdays_off: employee.fixed_weekdays_off ?? [],
    employment_start_date: employee.employment_start_date,
    termination_date: employee.termination_date ?? "",
    // README Punkt 17: employments statt nodes -- siehe EmploymentEditor.jsx.
    // node/pensum_pct/title/is_team_lead werden 1:1 aus der API übernommen,
    // die id lassen wir bewusst weg (wird beim Speichern ohnehin komplett
    // ersetzt, siehe EmployeeSerializer._sync_nested).
    employments: employee.employments.map((e) => ({
      node: e.node,
      pensum_pct: e.pensum_pct,
      title: e.title,
      is_team_lead: e.is_team_lead,
    })),
    skills: employee.skills,
    is_active: employee.is_active,
    maximum_weekly_hours: employee.maximum_weekly_hours ?? "",
    standard_weekly_hours: employee.standard_weekly_hours ?? "",
    overtime_balance_carryover_hours: employee.overtime_balance_carryover_hours ?? 0,
    vacation_days_per_year: employee.vacation_days_per_year ?? "",
    last_night_work_medical_exam_date: employee.last_night_work_medical_exam_date ?? "",
    hasLogin: Boolean(employee.username),
    existingUsername: employee.username || null,
    membership_id: employee.membership_id || null,
    newUsername: "",
    newUsernameTouched: false,
    role: employee.role || "employee",
  };
}

function selectedOptions(select) {
  return Array.from(select.selectedOptions, (o) => Number(o.value));
}

// Block 2.10: Mitarbeitenden-Stammdaten inkl. der Wochenstunden-Override-
// Felder aus Block 1.14 (z. B. Ärztin 50h statt der 42h-Tenant-Vorgabe) --
// bisher nur im Django-Admin editierbar, den ein Planer (Membership.Role.
// PLANNER) normalerweise gar nicht erreicht (separates Berechtigungssystem
// über User.is_staff).
//
// Block 2.16: Formular in Abschnitte gegliedert (fieldset), Felder mit
// Erklärtext analog zum Django-Admin-help_text, Fehler direkt am Feld statt
// nur im globalen Banner (error.fields aus api.js).
export default function EmployeeSettings({ nodes, skills, me, onError }) {
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  // Nutzer-Feedback (2026-08): das Formular sass zuvor als Dauer-Sidebar
  // neben der Tabelle -- "Rechts ist Mist, ich würde ein sauberes Popup
  // erwarten". Jetzt ein Modal, das nur beim expliziten "+ Neuer
  // Mitarbeiter"/"Bearbeiten" erscheint und die Tabelle wieder freigibt.
  const [formOpen, setFormOpen] = useState(false);

  // Login-Zugang (Nutzer-Feedback 2026-08: "Es gibt nun Tab Mitarbeitende,
  // Tab Mitglieder und Zugriff [...] Das muss doch intuitiver gelöst
  // werden?" -- ein Ort, ein Formular pro Person statt zwei getrennter
  // Tabs). scopedNodeIds nur relevant, solange form.role planner/hr ist;
  // newCredentials zeigt das Temp-Passwort nach Erstanlage EINMALIG.
  const [scopedNodeIds, setScopedNodeIds] = useState(new Set());
  const [roleChanging, setRoleChanging] = useState(false);
  const [scopedNodesSaving, setScopedNodesSaving] = useState(false);
  const [newCredentials, setNewCredentials] = useState(null);
  const [resettingPassword, setResettingPassword] = useState(false);
  // Nutzer-Feedback (2026-08): "ein Deaktivieren Button [...] deaktiviert
  // diesen inklusive seines Logins! Wenn einer Austritt aus dem Unternehmen
  // muss das Handlebar sein" -- eigener Ladezustand pro Zeile, damit ein
  // Doppelklick während des Requests keinen zweiten auslöst.
  const [deactivatingId, setDeactivatingId] = useState(null);

  // Modal per Escape schliessen -- Standard-Erwartung an einen Dialog.
  useEffect(() => {
    if (!formOpen) return;
    function handleKeyDown(e) {
      if (e.key === "Escape") closeForm();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formOpen]);

  // Tabellen-Zustand: Freitext-Suche (mit Eingabe-Debounce), Sortierung,
  // Stations-/Status-Filter, Seite -- alles zusammen bestimmt die eine
  // Server-Anfrage, die jeweils geladen wird.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [ordering, setOrdering] = useState("last_name");
  const [nodeFilter, setNodeFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const [page, setPage] = useState(1);
  const [tableData, setTableData] = useState({ count: 0, next: null, previous: null, results: [] });
  const [listLoading, setListLoading] = useState(true);
  // Nutzer-Feedback (2026-08, Performance): Saldo+Fairness für die ganze
  // sichtbare Seite in einem einzigen Bulk-Request statt 2xN
  // Einzelrequests (siehe BalanceBadge.jsx/FairnessBadge.jsx Bulk-Modus,
  // api.getEmployeesBalanceFairnessBulk). Keyed by employee.id.
  const [balanceFairnessById, setBalanceFairnessById] = useState({});

  // Freitext-Suche debouncen (300ms), Filter/Sortierung/Seite lösen sofort
  // eine neue Anfrage aus -- ein Tippvorgang soll nicht bei jedem Zeichen
  // einen eigenen Request auslösen.
  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  // Bei jeder Änderung an Suche/Sortierung/Filter zurück auf Seite 1 --
  // sonst könnte man auf einer Seite landen, die es im neuen Ergebnis gar
  // nicht mehr gibt.
  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, ordering, nodeFilter, activeFilter]);

  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    api
      .searchEmployees({ search, ordering, node: nodeFilter, isActive: activeFilter, page })
      .then((data) => !cancelled && setTableData(data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setListLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, ordering, nodeFilter, activeFilter, page]);

  // Bulk-Laden von Saldo+Fairness für die aktuell sichtbare Seite (ein
  // Request statt 2xN, siehe balanceFairnessById oben) -- läuft immer dann
  // neu, wenn sich die geladene Tabellenseite ändert (neue Seite/Suche/
  // Filter/Sortierung ODER reloadCurrentPage() nach einer Mutation) UND
  // zusätzlich bei jeder onBalanceChanged-Meldung von ANDERSWO in der App
  // (z. B. eine Schicht-Zuweisung im Planblatt), analog zum bisherigen
  // Pub/Sub-Abo, das vorher jede einzelne Badge selbst hatte -- jetzt nur
  // noch ein Abo für die ganze Tabelle statt N*2.
  useEffect(() => {
    const ids = tableData.results.map((emp) => emp.id);
    if (!ids.length) {
      setBalanceFairnessById({});
      return;
    }
    let cancelled = false;
    function load() {
      api
        .getEmployeesBalanceFairnessBulk(ids)
        .then((entries) => {
          if (cancelled) return;
          const byId = {};
          entries.forEach((entry) => {
            byId[entry.id] = entry;
          });
          setBalanceFairnessById(byId);
        })
        .catch(() => {
          // Saldo/Fairness sind Zusatzinfos -- ein Fehler hier soll die
          // restliche Seite nicht mit einer globalen Fehlermeldung stören
          // (gleiches Verhalten wie die einzelnen Badges vorher).
        });
    }
    load();
    const unsubscribe = onBalanceChanged(load);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [tableData.results]);

  // Nach jeder Anlage/Änderung die aktuelle Seite neu laden statt den
  // Eintrag manuell im lokalen Array zu patchen -- so bleiben Sortierung/
  // Filter/Seitenzahl immer korrekt, auch wenn sich durch die Änderung z. B.
  // die Sortierposition oder die Sichtbarkeit unter einem aktiven Filter
  // verschiebt (z. B. Status-Filter "Nur aktive" + gerade deaktiviert).
  function reloadCurrentPage() {
    setListLoading(true);
    api
      .searchEmployees({ search, ordering, node: nodeFilter, isActive: activeFilter, page })
      .then(setTableData)
      .catch((e) => onError(e.message))
      .finally(() => setListLoading(false));
  }

  function toggleSort(field) {
    setOrdering((prev) => (prev === field ? `-${field}` : field));
  }

  function startEditing(employee) {
    setEditingId(employee.id);
    setForm(toFormValues(employee));
    setScopedNodeIds(new Set(employee.scoped_nodes ?? []));
    setFieldErrors({});
    setFormOpen(true);
  }

  function startCreating() {
    setEditingId(null);
    setForm(emptyForm());
    setScopedNodeIds(new Set());
    setFieldErrors({});
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
  }

  function updateField(key) {
    return (e) => {
      const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      setForm((prev) => ({ ...prev, [key]: value }));
      setFieldErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
    };
  }

  // Vor- und Nachname aktualisieren UND (solange der Benutzername nicht
  // manuell angefasst wurde) den Vorschlag nachziehen -- siehe
  // suggestUsername()-Kommentar oben.
  function updateNameField(key) {
    return (e) => {
      const value = e.target.value;
      setForm((prev) => {
        const next = { ...prev, [key]: value };
        if (!prev.newUsernameTouched) {
          next.newUsername = suggestUsername(
            key === "first_name" ? value : prev.first_name,
            key === "last_name" ? value : prev.last_name
          );
        }
        return next;
      });
      setFieldErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
    };
  }

  // Rollenwechsel für eine Person, die bereits einen Account hat -- sofort
  // wirksam (kein zusätzlicher "Speichern"-Klick nötig), analog zum
  // bisherigen Verhalten in MembershipAccessSettings.jsx.
  async function handleRoleChange(role) {
    if (!form.membership_id || role === form.role) return;
    setRoleChanging(true);
    try {
      const updated = await api.updateMembershipRole(form.membership_id, role);
      setForm((prev) => ({ ...prev, role: updated.role }));
      setTableData((prev) => ({
        ...prev,
        results: prev.results.map((e) => (e.id === editingId ? { ...e, role: updated.role } : e)),
      }));
    } catch (e) {
      onError(e.message);
    } finally {
      setRoleChanging(false);
    }
  }

  async function handleScopedNodesSave() {
    if (!form.membership_id) return;
    setScopedNodesSaving(true);
    try {
      const updated = await api.updateMembershipScopedNodes(form.membership_id, [...scopedNodeIds]);
      setTableData((prev) => ({
        ...prev,
        results: prev.results.map((e) => (e.id === editingId ? { ...e, scoped_nodes: updated.scoped_nodes } : e)),
      }));
    } catch (e) {
      onError(e.message);
    } finally {
      setScopedNodesSaving(false);
    }
  }

  // Nutzer-Feedback (2026-08): "Ja mach passwort reset" -- Admin generiert
  // ein neues Temp-Passwort, gezeigt im selben "einmalige Anzeige"-Modal wie
  // bei der Erstanlage (newCredentials wird hier bewusst wiederverwendet).
  async function handleResetPassword() {
    if (!form.membership_id) return;
    if (
      !window.confirm(
        `Neues Passwort für "${form.existingUsername}" generieren? Das bisherige Passwort wird dabei ungültig.`
      )
    )
      return;
    setResettingPassword(true);
    try {
      const result = await api.resetMembershipPassword(form.membership_id);
      setNewCredentials({ username: result.username, password: result.temporary_password });
    } catch (e) {
      onError(e.message);
    } finally {
      setResettingPassword(false);
    }
  }

  // Nutzer-Feedback (2026-08): "ein Deaktivieren Button [...] deaktiviert
  // diesen inklusive seines Logins! Wenn einer Austritt aus dem Unternehmen
  // muss das Handlebar sein" -- anders als die "Aktiv"-Checkbox im Formular
  // (die nur die Planblatt-Sichtbarkeit steuert) sperrt diese Aktion
  // zusätzlich den Login, siehe EmployeeViewSet.deactivate.
  async function handleDeactivate(employee) {
    if (
      !window.confirm(
        `${employee.first_name} ${employee.last_name} wirklich deaktivieren? Der Login-Zugang wird dabei ` +
          "ebenfalls gesperrt (über \"Reaktivieren\" später wieder rückgängig machbar)."
      )
    )
      return;
    setDeactivatingId(employee.id);
    try {
      await api.deactivateEmployee(employee.id);
      reloadCurrentPage();
    } catch (e) {
      onError(e.message);
    } finally {
      setDeactivatingId(null);
    }
  }

  // Nutzer-Feedback (2026-08, Nachtrag): Gegenstück zu handleDeactivate --
  // reaktiviert zusätzlich den Login-Zugang (falls vorhanden) und setzt ein
  // ggf. gesetztes Austrittsdatum zurück, siehe EmployeeViewSet.reactivate.
  async function handleReactivate(employee) {
    if (
      !window.confirm(
        `${employee.first_name} ${employee.last_name} wieder aktivieren? Der Login-Zugang wird dabei ` +
          "reaktiviert (falls vorhanden) und ein gesetztes Austrittsdatum entfernt."
      )
    )
      return;
    setDeactivatingId(employee.id);
    try {
      await api.reactivateEmployee(employee.id);
      reloadCurrentPage();
    } catch (e) {
      onError(e.message);
    } finally {
      setDeactivatingId(null);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.first_name.trim() || !form.last_name.trim()) {
      onError("Vor- und Nachname sind Pflichtfelder.");
      return;
    }
    // Login-Zugang nur für eine Person OHNE bestehenden Account relevant --
    // eine bereits bestehende Verknüpfung wird über handleRoleChange/
    // handleScopedNodesSave sofort gespeichert, nicht über diesen Button.
    const settingUpNewAccess = form.hasLogin && !form.existingUsername;
    if (settingUpNewAccess && !form.newUsername.trim()) {
      onError("Benutzername ist ein Pflichtfeld, wenn ein Zugang eingerichtet wird.");
      return;
    }
    const payload = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      birth_date: form.birth_date || null,
      employment_pct: Number(form.employment_pct),
      fixed_weekdays_off: form.fixed_weekdays_off,
      employment_start_date: form.employment_start_date,
      termination_date: form.termination_date || null,
      employments: form.employments,
      skills: form.skills,
      is_active: form.is_active,
      maximum_weekly_hours: form.maximum_weekly_hours === "" ? null : Number(form.maximum_weekly_hours),
      standard_weekly_hours: form.standard_weekly_hours === "" ? null : Number(form.standard_weekly_hours),
      overtime_balance_carryover_hours: Number(form.overtime_balance_carryover_hours) || 0,
      vacation_days_per_year: form.vacation_days_per_year === "" ? null : Number(form.vacation_days_per_year),
      last_night_work_medical_exam_date: form.last_night_work_medical_exam_date || null,
    };
    setSaving(true);
    setFieldErrors({});
    try {
      const savedEmployee = editingId
        ? await api.updateEmployee(editingId, payload)
        : await api.createEmployee(payload);
      // Nutzer-Feedback (2026-08): "ein Klick auf Speichern -> Employee +
      // User + Membership + Verknüpfung entstehen zusammen" -- aus Sicht des
      // Admins EIN Formular/EIN Klick, technisch zwei Requests: die Employee
      // muss zuerst existieren (ihre ID wird für setup-access gebraucht),
      // bei einer Neuanlage ist die also erst nach dem ersten Request
      // bekannt.
      if (settingUpNewAccess) {
        const access = await api.setupEmployeeAccess(savedEmployee.id, {
          username: form.newUsername.trim(),
          role: form.role,
        });
        setNewCredentials({ username: access.username, password: access.temporary_password });
      }
      reloadCurrentPage();
      closeForm();
    } catch (e) {
      if (e.fields && typeof e.fields === "object") setFieldErrors(e.fields);
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  function nodeNames(ids) {
    return ids.map((id) => nodes.find((n) => n.id === id)?.name ?? `#${id}`).join(", ") || "—";
  }

  // README Punkt 17: Freitext-Autovervollständigung statt eigener
  // Stammdaten-Liste -- Vorschläge kommen aus bereits im Tenant verwendeten
  // Rollentiteln. Seit der Umstellung auf serverseitige Suche/Pagination
  // (Nutzer-Feedback 2026-08) nur noch aus der aktuell geladenen Tabellen-
  // seite abgeleitet statt aus dem kompletten Bestand -- bewusster
  // Kompromiss: ein eigener "alle je verwendeten Titel"-Endpoint wäre für
  // eine reine Autovervollständigung unverhältnismässig.
  const existingTitles = [
    ...new Set(tableData.results.flatMap((emp) => emp.employments.map((e) => e.title)).filter(Boolean)),
  ];

  function fieldError(key) {
    const message = fieldErrors[key]?.[0];
    return message ? <span className="field-error">{message}</span> : null;
  }

  const totalPages = Math.max(1, Math.ceil(tableData.count / EMPLOYEE_PAGE_SIZE));

  return (
    <div className="settings-table-layout">
      <div className="panel-list panel-list--full settings-table-panel">
        <div className="settings-table-toolbar">
          <span className="panel-list-search">
            <IconSearch width={15} height={15} />
            <input
              type="search"
              className="panel-list-filter"
              placeholder="Name suchen …"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
          </span>
          <select value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)}>
            <option value="">Alle Stationen</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
          <select value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
            <option value="">Alle</option>
            <option value="true">Nur aktive</option>
            <option value="false">Nur inaktive</option>
          </select>
          <button type="button" className="btn-primary" onClick={startCreating}>
            <IconPlus width={15} height={15} />
            Neuer Mitarbeiter
          </button>
        </div>

        {listLoading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !tableData.results.length ? (
          <p className="empty-state">
            {search || nodeFilter || activeFilter
              ? "Keine Mitarbeitenden gefunden."
              : "Noch keine Mitarbeitenden angelegt."}
          </p>
        ) : (
          <>
            <div className="settings-table-scroll">
              <table className="settings-table">
                <thead>
                  <tr>
                    {SORT_COLUMNS.map((col) => (
                      <th key={col.field}>
                        <button type="button" className="settings-table-sort" onClick={() => toggleSort(col.field)}>
                          {col.label}
                          {ordering === col.field && " ▲"}
                          {ordering === `-${col.field}` && " ▼"}
                        </button>
                      </th>
                    ))}
                    <th>Station(en)</th>
                    <th>Zugang</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {tableData.results.map((emp) => (
                    <tr
                      key={emp.id}
                      className={`settings-table-row${formOpen && editingId === emp.id ? " is-editing" : ""}`}
                      onClick={() => startEditing(emp)}
                    >
                      <td>{emp.last_name}</td>
                      <td>{emp.first_name}</td>
                      <td>{emp.employment_pct}%</td>
                      <td>
                        {emp.is_active ? (
                          "Aktiv"
                        ) : (
                          <span className="status-badge status-badge--cancelled">Inaktiv</span>
                        )}
                      </td>
                      <td>{nodeNames(emp.nodes)}</td>
                      <td>
                        {emp.username ? (
                          <span className="entry-note entry-note--chip">{ROLE_LABELS[emp.role] ?? emp.role}</span>
                        ) : (
                          <span className="entry-note">— kein Zugang</span>
                        )}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className="settings-table-actions">
                          <BalanceBadge employeeId={emp.id} data={balanceFairnessById[emp.id]?.balance ?? null} />
                          <FairnessBadge employeeId={emp.id} data={balanceFairnessById[emp.id]?.fairness ?? null} />
                          <button type="button" className="icon-btn" onClick={() => startEditing(emp)}>
                            <IconPencil width={14} height={14} />
                            Bearbeiten
                          </button>
                          {emp.is_active ? (
                            <button
                              type="button"
                              className="icon-btn icon-btn-danger"
                              disabled={deactivatingId === emp.id}
                              onClick={() => handleDeactivate(emp)}
                            >
                              {deactivatingId === emp.id ? "Wird deaktiviert …" : "Deaktivieren"}
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="icon-btn"
                              disabled={deactivatingId === emp.id}
                              onClick={() => handleReactivate(emp)}
                            >
                              {deactivatingId === emp.id ? "Wird reaktiviert …" : "Reaktivieren"}
                            </button>
                          )}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="settings-table-pager">
              <span>
                {tableData.count} Mitarbeitende{totalPages > 1 ? ` · Seite ${page} von ${totalPages}` : ""}
              </span>
              <div className="entry-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={!tableData.previous}
                  onClick={() => setPage((p) => p - 1)}
                >
                  ← Zurück
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={!tableData.next}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Weiter →
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {formOpen && (
        <div className="modal-overlay" onClick={closeForm}>
          <form
            className="panel-form modal-dialog employee-modal"
            onSubmit={handleSubmit}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div className="settings-form-header">
                <span className="settings-form-icon">
                  <IconUsers />
                </span>
                <div>
                  <h2>{editingId ? "Mitarbeiter bearbeiten" : "Mitarbeiter anlegen"}</h2>
                  <p className="settings-form-subtitle">
                    {editingId
                      ? "Passe Stammdaten, Zugang und Anstellungen an."
                      : "Erfasse Stammdaten, Zugang und Anstellungen für eine neue Person."}
                  </p>
                </div>
              </div>
              <button type="button" className="modal-close" onClick={closeForm} aria-label="Schliessen">
                ×
              </button>
            </div>
            <div className="modal-body">
              <fieldset className="panel-form-group">
                <h3>Stammdaten</h3>
          <div className="panel-form-row">
            <label>
              Vorname
              <input type="text" value={form.first_name} onChange={updateNameField("first_name")} required />
              {fieldError("first_name")}
            </label>
            <label>
              Nachname
              <input type="text" value={form.last_name} onChange={updateNameField("last_name")} required />
              {fieldError("last_name")}
            </label>
          </div>
          <div className="panel-form-row">
            <label>
              Geburtsdatum (optional)
              <input type="date" value={form.birth_date} onChange={updateField("birth_date")} />
              <span className="panel-hint">
                Nötig für den Jugendschutz (ArGV 5) bei Lernenden unter 18 Jahren -- ohne Angabe gilt die
                Person als volljährig.
              </span>
              {fieldError("birth_date")}
            </label>
            <label>
              Pensum (%)
              <input
                type="number"
                min="1"
                max="100"
                value={form.employment_pct}
                onChange={updateField("employment_pct")}
                required
              />
              <span className="panel-hint">
                Gesamtpensum für die Saldo-Berechnung -- sollte ungefähr der Summe der Anstellungen
                unten entsprechen (wird nicht automatisch geprüft).
              </span>
              {fieldError("employment_pct")}
            </label>
          </div>
          <label>
            Feste freie Wochentage
            <div className="weekday-checkbox-row">
              {WEEKDAY_LABELS.map((label, weekday) => (
                <label key={weekday} className="checkbox-row weekday-checkbox">
                  <span className="pretty-checkbox">
                    <input
                      type="checkbox"
                      checked={form.fixed_weekdays_off.includes(weekday)}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          fixed_weekdays_off: e.target.checked
                            ? [...prev.fixed_weekdays_off, weekday]
                            : prev.fixed_weekdays_off.filter((d) => d !== weekday),
                        }))
                      }
                    />
                    <span className="pretty-checkbox-box" aria-hidden="true" />
                  </span>
                  {label}
                </label>
              ))}
            </div>
            <span className="panel-hint">
              Wochentage, die grundsätzlich arbeitsfrei sind (Teilzeit-Muster, oder Sa/So für ein Team
              ohne Wochenend-Betrieb). Die automatisierte Planung ("Automatisch planen") plant diese Tage
              nie ein -- manuelles Stempeln im Planblatt bleibt trotzdem möglich.
            </span>
            {fieldError("fixed_weekdays_off")}
          </label>
          <label>
            Skills
            <select
              multiple
              value={form.skills}
              onChange={(e) => setForm((prev) => ({ ...prev, skills: selectedOptions(e.target) }))}
            >
              {skills.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {fieldError("skills")}
          </label>
        </fieldset>

        {isTenantAdmin(me) && (
          <fieldset className="panel-form-group">
            <h3>Login-Zugang</h3>
            {form.existingUsername ? (
              <>
                <p className="panel-hint">
                  Benutzername: <strong>{form.existingUsername}</strong> -- Rolle jederzeit über das Dropdown
                  änderbar, wirkt sofort.
                </p>
                <div>
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={resettingPassword}
                    onClick={handleResetPassword}
                  >
                    {resettingPassword ? "Wird zurückgesetzt …" : "Passwort zurücksetzen"}
                  </button>
                </div>
                <label>
                  Rolle
                  <select
                    value={form.role}
                    disabled={roleChanging}
                    onChange={(e) => handleRoleChange(e.target.value)}
                  >
                    {ROLE_OPTIONS.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABELS[role]}
                      </option>
                    ))}
                  </select>
                </label>
                {(form.role === "planner" || form.role === "hr") && (
                  <div>
                    <p className="panel-hint">
                      Sichtbare Stationen für diese Person -- nichts ausgewählt = keine Einschränkung (alle
                      Stationen sichtbar).
                    </p>
                    <NodeScopeEditor
                      nodes={nodes}
                      selectedNodeIds={scopedNodeIds}
                      onToggle={(node) => setScopedNodeIds((prev) => toggleNodeSelection(prev, node, nodes))}
                    />
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={handleScopedNodesSave}
                      disabled={scopedNodesSaving}
                    >
                      {scopedNodesSaving ? "Speichert …" : "Stationssicht speichern"}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <>
                <label className="checkbox-row">
                  <span className="pretty-checkbox">
                    <input
                      type="checkbox"
                      checked={form.hasLogin}
                      onChange={(e) => setForm((prev) => ({ ...prev, hasLogin: e.target.checked }))}
                    />
                    <span className="pretty-checkbox-box" aria-hidden="true" />
                  </span>
                  Zugang aktiv (Person kann sich einloggen)
                </label>
                {form.hasLogin && (
                  <>
                    <div className="panel-form-row">
                      <label>
                        Benutzername
                        <input
                          type="text"
                          value={form.newUsername}
                          onChange={(e) =>
                            setForm((prev) => ({ ...prev, newUsername: e.target.value, newUsernameTouched: true }))
                          }
                          required={form.hasLogin}
                        />
                        {fieldError("username")}
                      </label>
                      <label>
                        Rolle
                        <select
                          value={form.role}
                          onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value }))}
                        >
                          {ROLE_OPTIONS.map((role) => (
                            <option key={role} value={role}>
                              {ROLE_LABELS[role]}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <p className="panel-hint">
                      Ein Temp-Passwort wird nach dem Speichern einmalig angezeigt -- beim ersten Login muss es
                      geändert werden.
                    </p>
                  </>
                )}
              </>
            )}
          </fieldset>
        )}

        <fieldset className="panel-form-group">
          <h3>Anstellungen (Teams)</h3>
          <p className="panel-hint">
            Ein Eintrag pro Team/Station, in dem diese Person tätig ist -- mehrere Einträge bilden eine
            Mehrfachanstellung ab (z. B. 40% Dozent + 60% Arzt in unterschiedlichen Teams). Ohne
            Anstellung ist die Person im Planblatt nicht einteilbar.
          </p>
          <EmploymentEditor
            employments={form.employments}
            nodes={nodes}
            existingTitles={existingTitles}
            onChange={(employments) => setForm((prev) => ({ ...prev, employments }))}
          />
          {fieldError("employments")}
        </fieldset>

        <fieldset className="panel-form-group">
          <h3>Wochenstunden-Override (Block 1.14)</h3>
          <p className="panel-hint">
            Beide Felder leer lassen, um die Tenant-weiten Standardwerte zu übernehmen -- nur setzen, wenn
            diese Person vertraglich abweicht (z. B. Ärzteschaft mit 50h statt 42h).
          </p>
          <div className="panel-form-row">
            <label>
              Höchstarbeitszeit/Woche, Std.
              <input
                type="number"
                min="1"
                max="80"
                placeholder="z. B. 50 für Ärzteschaft"
                value={form.maximum_weekly_hours}
                onChange={updateField("maximum_weekly_hours")}
              />
              <span className="panel-hint">Gesetzliche/GAV-Höchstgrenze (Art. 9 ArG).</span>
              {fieldError("maximum_weekly_hours")}
            </label>
            <label>
              Normalarbeitszeit/Woche, Std.
              <input
                type="number"
                min="1"
                max="80"
                placeholder="z. B. 42"
                value={form.standard_weekly_hours}
                onChange={updateField("standard_weekly_hours")}
              />
              <span className="panel-hint">Soll-Basis für die Überzeitberechnung (Art. 13 ArG).</span>
              {fieldError("standard_weekly_hours")}
            </label>
          </div>
        </fieldset>

        <fieldset className="panel-form-group">
          <h3>Saldo & Zeiterfassung (Block 2.7 / 1.5)</h3>
          <label>
            Eintrittsdatum
            <input
              type="date"
              value={form.employment_start_date}
              onChange={updateField("employment_start_date")}
              required
            />
            <span className="panel-hint">
              Startpunkt für das Jahressoll im Arbeitszeitmodell -- Wochen vor diesem Datum zählen weder
              als Soll noch als Ist, auch wenn sie im laufenden Kalenderjahr liegen.
            </span>
            {fieldError("employment_start_date")}
          </label>
          <label>
            Austrittsdatum
            <input type="date" value={form.termination_date} onChange={updateField("termination_date")} />
            <span className="panel-hint">
              Optional. Sobald dieses Datum erreicht ist, wird die Person automatisch deaktiviert -- inkl.
              Login-Sperre, falls ein Zugang besteht (gleiche Wirkung wie der "Deaktivieren"-Button).
            </span>
            {fieldError("termination_date")}
          </label>
          <div className="panel-form-row">
            <label>
              Ferienanspruch/Jahr, Tage
              <input
                type="number"
                min="0"
                max="60"
                placeholder="z. B. 25"
                value={form.vacation_days_per_year}
                onChange={updateField("vacation_days_per_year")}
              />
              <span className="panel-hint">Leer = Tenant-Standard (Art. 329a Abs. 1 OR: 20 Tage Minimum).</span>
              {fieldError("vacation_days_per_year")}
            </label>
            <label>
              Überstunden-Startsaldo, Std.
              <input
                type="number"
                step="0.5"
                value={form.overtime_balance_carryover_hours}
                onChange={updateField("overtime_balance_carryover_hours")}
              />
              <span className="panel-hint">Beim Systemstart übernommen, z. B. aus der Vorsystem-Zeiterfassung.</span>
              {fieldError("overtime_balance_carryover_hours")}
            </label>
          </div>
          <label>
            Letzte arbeitsmedizinische Untersuchung
            <input
              type="date"
              value={form.last_night_work_medical_exam_date}
              onChange={updateField("last_night_work_medical_exam_date")}
            />
            <span className="panel-hint">
              Nur relevant bei regelmässiger Nachtarbeit (Art. 17c ArG) -- wird nur ausgewertet, wenn die
              Person laut Saldo/Zeiterfassung regelmässig nachts arbeitet.
            </span>
            {fieldError("last_night_work_medical_exam_date")}
          </label>
        </fieldset>

        {isTenantAdmin(me) && editingId && (
          <fieldset className="panel-form-group">
            <h3>Mutterschutz (Art. 35a ArG, nur Admin sichtbar)</h3>
            <p className="panel-hint">
              Mehrere Einträge pro Person möglich (mehrere Schwangerschaften über die Anstellung
              hinweg). Sensibelste Kategorie personenbezogener Daten in der App -- deshalb
              unabhängig vom übrigen Formular sofort gespeichert und nur für Admin sowie die
              betroffene Person selbst sichtbar, nicht für Planer:innen allgemein.
            </p>
            <PregnancyEditor employeeId={editingId} onError={onError} />
          </fieldset>
        )}

              <label className="checkbox-row">
                <span className="pretty-checkbox">
                  <input type="checkbox" checked={form.is_active} onChange={updateField("is_active")} />
                  <span className="pretty-checkbox-box" aria-hidden="true" />
                </span>
                Aktiv (deaktivierte Mitarbeitende erscheinen nicht mehr im Planblatt)
              </label>
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-ghost" onClick={closeForm}>
                Abbrechen
              </button>
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? "Speichert …" : editingId ? "Speichern" : "Anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}

      {newCredentials && (
        <div className="modal-overlay" onClick={() => setNewCredentials(null)}>
          <div className="panel-form modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Zugangsdaten</h2>
            </div>
            <div className="modal-body">
              <p className="panel-hint">
                Dieses Passwort wird nur jetzt angezeigt -- bitte an {newCredentials.username} weitergeben
                (z. B. mündlich oder auf Papier). Beim ersten Login muss es geändert werden.
              </p>
              <p>
                <strong>Benutzername:</strong> {newCredentials.username}
              </p>
              <p>
                <strong>Temp-Passwort:</strong> <code>{newCredentials.password}</code>
              </p>
            </div>
            <div className="modal-footer">
              <button type="button" onClick={() => setNewCredentials(null)}>
                Verstanden, schliessen
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

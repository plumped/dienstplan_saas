import { useEffect, useState } from "react";
import { api } from "../api.js";
import { chipGlyph } from "../chipGlyph.js";
import TimeTemplateSegmentEditor from "./TimeTemplateSegmentEditor.jsx";

// Nutzer-Feedback (2026-08): gleiches Muster wie EmployeeSettings.jsx --
// bei vielen Stationen (z. B. 15 Stationen x 10 Schichttypen) wurde die
// bisherige Dauer-Sidebar-Liste (alles auf einmal, kein Filter) schnell
// unübersichtlich. Suche/Sortierung/Stations-/Kategorie-Filter laufen jetzt
// serverseitig (api.searchTimeTemplates(), TimeTemplateViewSet:
// ?search=/?ordering=/?node=/?category=/?page=), Bearbeiten öffnet ein Modal.
// Muss mit REST_FRAMEWORK.PAGE_SIZE in config/settings.py übereinstimmen --
// nur für die "Seite X von Y"-Anzeige.
const TEMPLATE_PAGE_SIZE = 50;

const SORT_COLUMNS = [
  { field: "name", label: "Name" },
  { field: "node__name", label: "Station" },
  { field: "start_time", label: "Zeit" },
  { field: "category", label: "Kategorie" },
];

function emptyForm(defaultNodeId) {
  return {
    name: "",
    node: defaultNodeId ?? "",
    start_time: "07:00",
    end_time: "15:00",
    break_minutes: 30,
    icon: "",
    color: "#2563eb",
    required_skill: "",
    minimum_staffing: 0,
    fills_remaining_capacity: false,
    category: "shift",
    surcharge_pct: 0,
    segments: [],
  };
}

function toFormValues(template) {
  return {
    name: template.name,
    node: template.node,
    start_time: template.start_time.slice(0, 5),
    end_time: template.end_time.slice(0, 5),
    break_minutes: template.break_minutes,
    icon: template.icon,
    color: template.color,
    required_skill: template.required_skill ?? "",
    minimum_staffing: template.minimum_staffing,
    fills_remaining_capacity: template.fills_remaining_capacity ?? false,
    category: template.category ?? "shift",
    surcharge_pct: template.surcharge_pct ?? 0,
    segments: (template.segments ?? []).map((s) => ({
      start_time: s.start_time.slice(0, 5),
      end_time: s.end_time.slice(0, 5),
    })),
  };
}

// Block 1.9/2.10: Schichttyp-Verwaltung inkl. optionaler Blockstruktur
// (TimeTemplateSegmentEditor) -- bisher nur im Django-Admin
// (TimeTemplateSegmentInline) editierbar.
export default function TimeTemplateSettings({ nodes, skills, onError }) {
  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(() => emptyForm(nodes[0]?.id));
  const [saving, setSaving] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  useEffect(() => {
    if (!formOpen) return;
    function handleKeyDown(e) {
      if (e.key === "Escape") closeForm();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formOpen]);

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [ordering, setOrdering] = useState("node__name");
  const [nodeFilter, setNodeFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [page, setPage] = useState(1);
  const [tableData, setTableData] = useState({ count: 0, next: null, previous: null, results: [] });
  const [listLoading, setListLoading] = useState(true);

  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, ordering, nodeFilter, categoryFilter]);

  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    api
      .searchTimeTemplates({ search, ordering, node: nodeFilter, category: categoryFilter, page })
      .then((data) => !cancelled && setTableData(data))
      .catch((e) => onError(e.message))
      .finally(() => !cancelled && setListLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, ordering, nodeFilter, categoryFilter, page]);

  function reloadCurrentPage() {
    setListLoading(true);
    api
      .searchTimeTemplates({ search, ordering, node: nodeFilter, category: categoryFilter, page })
      .then(setTableData)
      .catch((e) => onError(e.message))
      .finally(() => setListLoading(false));
  }

  function toggleSort(field) {
    setOrdering((prev) => (prev === field ? `-${field}` : field));
  }

  function startEditing(template) {
    setEditingId(template.id);
    setForm(toFormValues(template));
    setFormOpen(true);
  }

  function startCreating() {
    setEditingId(null);
    // Nutzer-Feedback (2026-08): "wenn ich nach Küche filtere und dann auf
    // Neuer Schichttyp klicke, sollte Station/Team im Modal bereits Küche
    // beinhalten" -- der aktive Stations-Filter der Tabelle ist die
    // naheliegendste Vorauswahl, sonst landet man immer wieder beim ersten
    // Knoten der Gesamtliste und muss manuell zurück zur gefilterten Station.
    setForm(emptyForm(nodeFilter || nodes[0]?.id));
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.name.trim() || !form.node) {
      onError("Name und Station sind Pflichtfelder.");
      return;
    }
    const payload = {
      name: form.name.trim(),
      node: Number(form.node),
      start_time: form.start_time,
      end_time: form.end_time,
      break_minutes: Number(form.break_minutes),
      icon: form.icon,
      color: form.color,
      required_skill: form.required_skill || null,
      minimum_staffing: Number(form.minimum_staffing) || 0,
      fills_remaining_capacity: form.fills_remaining_capacity,
      category: form.category,
      surcharge_pct: form.category === "special" ? Number(form.surcharge_pct) || 0 : 0,
      segments: form.segments.map((s, i) => ({ order: i, start_time: s.start_time, end_time: s.end_time })),
    };
    setSaving(true);
    try {
      if (editingId) {
        await api.updateTimeTemplate(editingId, payload);
      } else {
        await api.createTimeTemplate(payload);
      }
      reloadCurrentPage();
      closeForm();
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(template) {
    try {
      await api.deleteTimeTemplate(template.id);
      reloadCurrentPage();
    } catch (e) {
      onError(e.message);
    }
  }

  function nodeName(id) {
    return nodes.find((n) => n.id === id)?.name ?? `#${id}`;
  }

  if (!nodes.length) {
    return <p className="empty-state">Zuerst unter „Stationen“ eine Station anlegen.</p>;
  }

  const totalPages = Math.max(1, Math.ceil(tableData.count / TEMPLATE_PAGE_SIZE));

  return (
    <div className="settings-table-layout">
      <div className="panel-list panel-list--full settings-table-panel">
        <div className="settings-table-toolbar">
          <input
            type="search"
            className="panel-list-filter"
            placeholder="Name suchen …"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <select value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)}>
            <option value="">Alle Stationen</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
            <option value="">Alle Kategorien</option>
            <option value="shift">Dienst</option>
            <option value="special">Spezialität</option>
          </select>
          <button type="button" className="btn-ghost" onClick={startCreating}>
            + Neuer Schichttyp
          </button>
        </div>

        {listLoading ? (
          <p className="loading-state">Wird geladen …</p>
        ) : !tableData.results.length ? (
          <p className="empty-state">
            {search || nodeFilter || categoryFilter ? "Keine Schichttypen gefunden." : "Noch keine Schichttypen angelegt."}
          </p>
        ) : (
          <>
            <div className="settings-table-scroll">
              <table className="settings-table">
                <thead>
                  <tr>
                    <th />
                    {SORT_COLUMNS.map((col) => (
                      <th key={col.field}>
                        <button type="button" className="settings-table-sort" onClick={() => toggleSort(col.field)}>
                          {col.label}
                          {ordering === col.field && " ▲"}
                          {ordering === `-${col.field}` && " ▼"}
                        </button>
                      </th>
                    ))}
                    <th>Hinweise</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {tableData.results.map((t) => (
                    <tr key={t.id} className={`settings-table-row${formOpen && editingId === t.id ? " is-editing" : ""}`} onClick={() => startEditing(t)}>
                      <td>
                        <span className="shift-chip" style={{ "--chip-color": t.color }}>
                          {chipGlyph(t)}
                        </span>
                      </td>
                      <td>{t.name}</td>
                      <td>{nodeName(t.node)}</td>
                      <td>
                        {t.start_time.slice(0, 5)}–{t.end_time.slice(0, 5)}
                      </td>
                      <td>{t.category === "special" ? "Spezialität" : "Dienst"}</td>
                      <td>
                        {t.segments?.length > 1 && <span className="entry-note">{t.segments.length} Segmente</span>}
                        {t.minimum_staffing > 0 && <span className="entry-note"> min. {t.minimum_staffing} Pers.</span>}
                        {t.fills_remaining_capacity && <span className="entry-note"> Auffülldienst</span>}
                        {t.surcharge_pct > 0 && <span className="entry-note"> Zuschlag {t.surcharge_pct}%</span>}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className="settings-table-actions">
                          <button type="button" className="btn-ghost" onClick={() => startEditing(t)}>
                            Bearbeiten
                          </button>
                          <button type="button" className="btn-ghost" onClick={() => handleDelete(t)}>
                            Löschen
                          </button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="settings-table-pager">
              <span>
                {tableData.count} Schichttypen{totalPages > 1 ? ` · Seite ${page} von ${totalPages}` : ""}
              </span>
              <div className="entry-actions">
                <button type="button" className="btn-ghost" disabled={!tableData.previous} onClick={() => setPage((p) => p - 1)}>
                  ← Zurück
                </button>
                <button type="button" className="btn-ghost" disabled={!tableData.next} onClick={() => setPage((p) => p + 1)}>
                  Weiter →
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {formOpen && (
        <div className="modal-overlay" onClick={closeForm}>
          <form className="panel-form modal-dialog" onSubmit={handleSubmit} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingId ? "Schichttyp bearbeiten" : "Schichttyp anlegen"}</h2>
              <button type="button" className="modal-close" onClick={closeForm} aria-label="Schliessen">
                ×
              </button>
            </div>
            <div className="modal-body">
              <div className="panel-form-row">
                <label>
                  Name
                  <input
                    type="text"
                    value={form.name}
                    onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                    required
                  />
                </label>
                <label>
                  Station / Team
                  <select
                    value={form.node}
                    onChange={(e) => setForm((prev) => ({ ...prev, node: e.target.value }))}
                    required
                  >
                    {nodes.map((n) => (
                      <option key={n.id} value={n.id}>
                        {"  ".repeat(Math.max(n.depth - 2, 0))}
                        {n.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="panel-hint">
                Ein Schichttyp auf einer Station steht allen ihren Teams gemeinsam zur Verfügung; ein
                Schichttyp direkt auf einem Team ist nur dort auswählbar (z. B. "Nachtwache" nur beim
                Nacht-Team, nicht beim Tag-Team derselben Station).
              </p>
              <div className="panel-form-row">
                <label>
                  Beginn
                  <input
                    type="time"
                    value={form.start_time}
                    onChange={(e) => setForm((prev) => ({ ...prev, start_time: e.target.value }))}
                    required
                  />
                </label>
                <label>
                  Ende
                  <input
                    type="time"
                    value={form.end_time}
                    onChange={(e) => setForm((prev) => ({ ...prev, end_time: e.target.value }))}
                    required
                  />
                </label>
                <label>
                  Pause (Min., ohne Segmente unten)
                  <input
                    type="number"
                    min="0"
                    value={form.break_minutes}
                    onChange={(e) => setForm((prev) => ({ ...prev, break_minutes: e.target.value }))}
                  />
                </label>
              </div>
              <div className="panel-form-row">
                <label>
                  Farbe
                  <input
                    type="color"
                    value={form.color}
                    onChange={(e) => setForm((prev) => ({ ...prev, color: e.target.value }))}
                  />
                </label>
                <label>
                  Icon (optional)
                  <input
                    type="text"
                    maxLength={50}
                    placeholder="z. B. 🌙 oder ein Kürzel"
                    value={form.icon}
                    onChange={(e) => setForm((prev) => ({ ...prev, icon: e.target.value }))}
                  />
                </label>
                <label>
                  Erforderlicher Skill (optional)
                  <select
                    value={form.required_skill}
                    disabled={form.fills_remaining_capacity}
                    onChange={(e) => setForm((prev) => ({ ...prev, required_skill: e.target.value }))}
                  >
                    <option value="">— keiner —</option>
                    {skills.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="panel-form-row">
                <label>
                  Mindestbesetzung (0 = keine)
                  <input
                    type="number"
                    min="0"
                    value={form.minimum_staffing}
                    disabled={form.fills_remaining_capacity}
                    onChange={(e) => setForm((prev) => ({ ...prev, minimum_staffing: e.target.value }))}
                  />
                  <span className="panel-hint">
                    Mindestanzahl gleichzeitig eingeteilter Mitarbeitender an diesem Schichttyp -- wird im
                    Planblatt als Warn-Badge angezeigt, sobald ein Tag unterbesetzt ist (README Punkt 9).
                  </span>
                </label>
                <label>
                  Kategorie
                  <select
                    value={form.category}
                    onChange={(e) => setForm((prev) => ({ ...prev, category: e.target.value }))}
                  >
                    <option value="shift">Dienst</option>
                    <option value="special">Spezialität</option>
                  </select>
                  <span className="panel-hint">
                    Für die Stempelleisten im Planblatt/Jahresplan: Spezialitäten (z. B.
                    Pikettdienst) erscheinen dort in einer eigenen Zeile, getrennt von den regulären
                    Diensten, und können zusätzlich einen eigenen Lohnzuschlag tragen (siehe unten).
                  </span>
                </label>
                {form.category === "special" && (
                  <label>
                    Lohnzuschlag in % (0 = kein Zuschlag)
                    <input
                      type="number"
                      min="0"
                      max="100"
                      value={form.surcharge_pct}
                      onChange={(e) => setForm((prev) => ({ ...prev, surcharge_pct: e.target.value }))}
                    />
                    <span className="panel-hint">
                      Nutzer-Feedback (2026-08): "wenn jemand Pikett macht, ist dieser
                      zuschlagsberechtigt" -- wird auf die geplanten Stunden dieser Spezialität
                      angerechnet (z. B. 50% bei Pikett) und erscheint pro Spezialität einzeln
                      aufgeschlüsselt in der Monatsauswertung.
                    </span>
                  </label>
                )}
              </div>
              <div className="panel-form-row">
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={form.fills_remaining_capacity}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setForm((prev) => ({
                        ...prev,
                        fills_remaining_capacity: checked,
                        // Backend lehnt beides gleichzeitig ab (TimeTemplate.clean()) --
                        // beim Aktivieren gleich mit zurücksetzen statt den Nutzer über
                        // einen Validierungsfehler stolpern zu lassen.
                        minimum_staffing: checked ? 0 : prev.minimum_staffing,
                        required_skill: checked ? "" : prev.required_skill,
                      }));
                    }}
                  />
                  Auffülldienst (z. B. "Gleitzeit")
                </label>
                <span className="panel-hint">
                  Statt einer festen Mindestbesetzung erhalten alle an diesem Tag arbeitspflichtigen
                  Mitarbeitenden dieses Teams, die keinen anderen Dienst haben, automatisch diesen
                  Schichttyp -- die Anzahl ergibt sich täglich neu, statt fest vorgegeben zu sein.
                  Höchstens ein Auffülldienst pro Team; setzt Mindestbesetzung auf 0 voraus.
                </span>
              </div>

              <h3>Blockstruktur (optional, Block 1.9)</h3>
              <p className="panel-hint">
                Ohne Segmente gilt oben ein einzelnes Zeitfenster mit pauschaler Pause. Mit Segmenten
                (z. B. Vormittag/Nachmittag) ergibt sich die Pause aus der Lücke dazwischen -- die
                Ist-Erfassung übernimmt dann genau diese Blockanzahl.
              </p>
              <TimeTemplateSegmentEditor
                segments={form.segments}
                onChange={(segments) => setForm((prev) => ({ ...prev, segments }))}
              />
            </div>
            <div className="modal-footer">
              <button type="button" className="btn-ghost" onClick={closeForm}>
                Abbrechen
              </button>
              <button type="submit" disabled={saving}>
                {saving ? "Speichert …" : editingId ? "Speichern" : "Anlegen"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

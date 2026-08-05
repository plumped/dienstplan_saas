import { useEffect, useRef, useState } from "react";
import { effectiveRecordSegments, effectiveTemplateSegments } from "../timeRecordSegments.js";
import FloatingPopover from "./FloatingPopover.jsx";
import TimeRecordSegmentEditor from "./TimeRecordSegmentEditor.jsx";
import WishEditor from "./WishEditor.jsx";

const WISH_GLYPHS = { wunschfrei: "F", wunschdienst: "D" };
const WISH_LABELS = { wunschfrei: "Wunschfrei", wunschdienst: "Wunschdienst" };

const DRAG_MIME = "application/x-dienstplan-shift";

const ABSENCE_LABELS = {
  vacation: "FER",
  sick: "KRA",
  other: "SON",
};

const ABSENCE_NAMES = {
  vacation: "Ferien",
  sick: "Krankheit",
  other: "Sonstiges",
};

export default function ShiftCell({
  templates,
  // README Punkt 17 Nachbesserung: templates bleibt der volle, stationsweite
  // Katalog (für Namens-/Farb-Lookups z. B. beim Wunschdienst, der
  // personenweit gilt und nicht auf das eigene Team beschränkt ist).
  // assignableTemplates ist die für DIESE Zeile (Team) tatsächlich
  // zuweisbare Teilmenge -- fällt auf templates zurück, falls nicht gesetzt.
  assignableTemplates = templates,
  selectedTemplateId,
  templateInfo,
  onChange,
  employeeId,
  date,
  onMove,
  absence,
  colleagues = [],
  onOfferTrade,
  canEdit = true,
  canOfferTrade = true,
  timeRecord,
  canRecordTime = false,
  onSaveTimeRecord,
  onDeleteTimeRecord,
  // Wunschfrei/Wunschdienst (Block 2.13): preference ist der ShiftPreference-
  // Eintrag dieser Zelle, falls vorhanden. canEditOwnWish gilt nur für die
  // eigene Person an einem heutigen/zukünftigen Tag -- höchstpersönlich,
  // auch Admin/Planer dürfen keine Wünsche für andere anlegen/bearbeiten.
  preference,
  canEditOwnWish = false,
  onSaveWish,
  onDeleteWish,
  // Mehrfachauswahl + Schicht-Stempel (siehe README, inspiriert von Polypoint):
  // solange aktiv, markiert ein Klick/Ziehen die Zelle statt die übliche
  // Dropdown-Zuweisung zu öffnen -- die eigentliche Zuweisung passiert
  // gesammelt über die Stempel-Leiste in PlanGrid.jsx. onMarkStart entscheidet
  // anhand von `marked`, ob markiert oder entmarkiert wird, und startet damit
  // den Ziehen-Modus in PlanGrid; onMarkEnter wendet denselben Modus beim
  // Drüberziehen auf weitere Zellen an.
  selectionMode = false,
  marked = false,
  onMarkStart,
  onMarkEnter,
}) {
  const [editing, setEditing] = useState(false);
  const [offering, setOffering] = useState(false);
  const [recordingTime, setRecordingTime] = useState(false);
  const [savingTime, setSavingTime] = useState(false);
  const [wishing, setWishing] = useState(false);
  const [savingWish, setSavingWish] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const selectRef = useRef(null);
  const recordBadgeRef = useRef(null);
  const wishBadgeRef = useRef(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    if (editing || offering) selectRef.current?.focus();
  }, [editing, offering]);

  async function handleSaveTimeRecord(payload) {
    setSavingTime(true);
    const ok = await onSaveTimeRecord(payload);
    setSavingTime(false);
    if (ok) setRecordingTime(false);
  }

  async function handleDeleteTimeRecord() {
    const ok = await onDeleteTimeRecord();
    if (ok) setRecordingTime(false);
  }

  async function handleSaveWish(payload) {
    setSavingWish(true);
    const ok = await onSaveWish(payload);
    setSavingWish(false);
    if (ok) setWishing(false);
  }

  async function handleDeleteWish() {
    const ok = await onDeleteWish();
    if (ok) setWishing(false);
  }

  // Block 2.13: Badge oben links auf der eigenen, heutigen/zukünftigen
  // Zelle -- Wunschfrei/Wunschdienst direkt im Planblatt eintragen, analog
  // zum Ist-Zeit-Badge (Block 1.10) unten rechts. Für alle anderen Zellen,
  // in denen bereits ein Wunsch besteht, zeigt der Planer (canManage) einen
  // rein informativen, nicht klickbaren Hinweis, damit er die Wünsche beim
  // Ausfüllen des Plans vor Augen hat -- Bearbeiten bleibt höchstpersönlich.
  function renderWishBadge() {
    if (canEditOwnWish) {
      const hasWish = Boolean(preference);
      const glyph = hasWish ? WISH_GLYPHS[preference.type] : "?";
      const wishedTemplate = hasWish && preference.template ? templates.find((t) => t.id === preference.template) : null;
      const title = !hasWish
        ? "Wunschfrei/Wunschdienst äussern"
        : preference.type === "wunschfrei"
          ? "Wunschfrei -- bearbeiten"
          : `Wunschdienst: ${wishedTemplate?.name ?? "?"} -- bearbeiten`;
      return (
        <>
          <button
            ref={wishBadgeRef}
            type="button"
            className={`btn-wish${hasWish ? ` is-set is-${preference.type}` : ""}`}
            title={title}
            onClick={() => setWishing((v) => !v)}
          >
            {glyph}
            <span className="visually-hidden"> {title}</span>
          </button>
          {wishing && (
            <FloatingPopover anchorRef={wishBadgeRef} onClose={() => setWishing(false)} className="wish-popover">
              <WishEditor
                templates={templates}
                existing={preference}
                saving={savingWish}
                onSave={handleSaveWish}
                onCancel={() => setWishing(false)}
                onDelete={preference ? handleDeleteWish : undefined}
              />
            </FloatingPopover>
          )}
        </>
      );
    }
    if (canEdit && preference) {
      const wishedTemplate = preference.template ? templates.find((t) => t.id === preference.template) : null;
      const title =
        preference.type === "wunschfrei"
          ? "Wunschfrei geäussert"
          : `Wunschdienst geäussert: ${wishedTemplate?.name ?? "?"}`;
      return (
        <span className={`btn-wish is-set is-${preference.type} is-readonly`} title={title} aria-hidden="true">
          {WISH_GLYPHS[preference.type]}
        </span>
      );
    }
    return null;
  }

  // Block 1.13: Badge auf der eigenen vergangenen Schicht-Zelle, damit die
  // Ist-Zeit direkt im Planblatt erfasst werden kann statt nur über den
  // separaten Tab "Zeiterfassung" (der als Planer-Prüfliste bestehen
  // bleibt). Admin/Planer dürfen auch nach der Bestätigung noch korrigieren
  // (TimeRecordPermission), Mitarbeitende nur solange der Eintrag noch nicht
  // geprüft ist.
  function renderTimeRecordBadge() {
    if (!canRecordTime || !templateInfo) return null;
    const isConfirmed = timeRecord?.status === "confirmed";
    const statusClass = !timeRecord ? "is-missing" : isConfirmed ? "is-confirmed" : "is-submitted";
    const title = !timeRecord
      ? "Ist-Zeit noch nicht erfasst"
      : isConfirmed
        ? "Ist-Zeit geprüft"
        : "Ist-Zeit erfasst – bearbeiten";
    const canEditThisRecord = canEdit || !timeRecord || timeRecord.status === "submitted";

    if (!canEditThisRecord) {
      return (
        <span className={`btn-record-time ${statusClass}`} title={title} aria-hidden="true">
          ✓
        </span>
      );
    }

    return (
      <>
        <button
          ref={recordBadgeRef}
          type="button"
          className={`btn-record-time ${statusClass}`}
          title={title}
          onClick={() => setRecordingTime((v) => !v)}
        >
          {timeRecord ? "✓" : "!"}
          <span className="visually-hidden"> {title}</span>
        </button>
        {recordingTime && (
          <FloatingPopover
            anchorRef={recordBadgeRef}
            onClose={() => setRecordingTime(false)}
            className="time-record-popover"
          >
            <TimeRecordSegmentEditor
              plannedSegments={effectiveTemplateSegments(templateInfo)}
              initialSegments={effectiveRecordSegments(timeRecord)}
              initialNote={timeRecord?.note ?? ""}
              saving={savingTime}
              canDelete={Boolean(timeRecord)}
              onSave={handleSaveTimeRecord}
              onCancel={() => setRecordingTime(false)}
              onDelete={handleDeleteTimeRecord}
            />
          </FloatingPopover>
        )}
      </>
    );
  }

  if (absence) {
    return (
      <span
        className="shift-chip-btn is-absence"
        title={`${ABSENCE_NAMES[absence.type] ?? absence.type} (${absence.start_date} – ${absence.end_date})`}
      >
        <span className="shift-chip shift-chip--absence">
          {ABSENCE_LABELS[absence.type] ?? absence.type.slice(0, 3).toUpperCase()}
        </span>
      </span>
    );
  }

  if (selectionMode && canEdit) {
    // Absenz-Tage lassen sich nicht markieren -- eine Zuweisung würde die
    // Regel-Engine ohnehin ablehnen (_check_no_absence_conflict), das
    // Icon-Stempeln würde also nur stillschweigend übersprungen. Hier gleich
    // gar nicht erst als Ziel anbieten, statt das erst beim Zuweisen zu
    // melden.
    //
    // Markieren per Ziehen: onMouseDown startet den Ziehen-Modus (mark/
    // unmark, je nach aktuellem Zustand dieser Zelle) und markiert sie
    // gleich mit; onMouseEnter wendet denselben Modus beim Drüberziehen mit
    // gedrückter Maustaste auf weitere Zellen an. onClick bleibt als
    // Tastatur-Fallback (Enter/Leertaste lösen click ohne vorheriges
    // mousedown aus) -- ein durch Maus-Klick bereits verarbeitetes
    // mousedown unterdrückt das nachfolgende click, damit nicht doppelt
    // markiert/entmarkiert wird.
    const handleMouseDown = (e) => {
      if (e.button !== 0) return;
      e.preventDefault(); // verhindert Text-/Bild-Selektion beim Ziehen
      suppressClickRef.current = true;
      onMarkStart();
    };
    const handleClick = () => {
      if (suppressClickRef.current) {
        suppressClickRef.current = false;
        return;
      }
      onMarkStart();
    };
    return (
      <button
        type="button"
        className={`shift-chip-btn is-selectable${marked ? " is-marked" : ""}`}
        aria-pressed={marked}
        title={marked ? "Markierung aufheben" : "Für Mehrfachzuweisung markieren (auch durch Ziehen)"}
        onMouseDown={handleMouseDown}
        onMouseEnter={onMarkEnter}
        onClick={handleClick}
      >
        {templateInfo ? (
          <span className="shift-chip" style={{ "--chip-color": templateInfo.color }}>
            {templateInfo.name.slice(0, 3)}
          </span>
        ) : (
          <span className="shift-chip shift-chip--empty" aria-hidden="true">
            +
          </span>
        )}
        {marked && (
          <span className="select-check" aria-hidden="true">
            ✓
          </span>
        )}
      </button>
    );
  }

  if (editing) {
    return (
      <select
        ref={selectRef}
        className="cell-select"
        defaultValue={selectedTemplateId ?? ""}
        onBlur={() => setEditing(false)}
        onChange={(e) => {
          const val = e.target.value;
          onChange(val === "" ? null : Number(val));
          setEditing(false);
        }}
      >
        <option value="">— leer —</option>
        {assignableTemplates.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name} ({t.start_time.slice(0, 5)}–{t.end_time.slice(0, 5)})
          </option>
        ))}
      </select>
    );
  }

  if (offering) {
    return (
      <select
        ref={selectRef}
        className="cell-select"
        defaultValue=""
        onBlur={() => setOffering(false)}
        onChange={(e) => {
          const val = e.target.value;
          setOffering(false);
          if (val !== "") onOfferTrade(Number(val));
        }}
      >
        <option value="">Tausch anbieten an …</option>
        {colleagues.map((c) => (
          <option key={c.id} value={c.id}>
            {c.first_name} {c.last_name}
          </option>
        ))}
      </select>
    );
  }

  if (!canEdit) {
    // Nicht-Planer sehen den Plan nur (siehe core.permissions.IsTenantManager
    // im Backend, das schreibende Zugriffe ohnehin mit 403 ablehnen würde) --
    // deshalb bewusst kein <button>/keine Drag-Handler, nur das Angebot,
    // die eigene Schicht zum Tausch anzubieten (siehe canOfferTrade unten)
    // sowie die eigene Ist-Zeit zu erfassen (siehe canRecordTime oben).
    return (
      <span className="cell-wrap">
        <span className="shift-chip-btn is-readonly">
          {templateInfo ? (
            <span className="shift-chip" style={{ "--chip-color": templateInfo.color }}>
              {templateInfo.name.slice(0, 3)}
            </span>
          ) : (
            <span className="shift-chip shift-chip--empty" aria-hidden="true">
              +
            </span>
          )}
        </span>
        {templateInfo && canOfferTrade && colleagues.length > 0 && (
          <button
            type="button"
            className="btn-offer-trade"
            title="Diese Schicht zum Tausch anbieten"
            onClick={() => setOffering(true)}
          >
            ⇄<span className="visually-hidden"> Schicht zum Tausch anbieten</span>
          </button>
        )}
        {renderTimeRecordBadge()}
        {renderWishBadge()}
      </span>
    );
  }

  function handleDragStart(e) {
    if (!templateInfo) return;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ employeeId, date }));
  }

  function handleDragOver(e) {
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOver(true);
  }

  function handleDrop(e) {
    if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
    e.preventDefault();
    setDragOver(false);
    const raw = e.dataTransfer.getData(DRAG_MIME);
    if (!raw) return;
    const source = JSON.parse(raw);
    onMove(source.employeeId, source.date, employeeId, date);
  }

  return (
    <span className="cell-wrap">
      <button
        type="button"
        className={`shift-chip-btn${dragOver ? " is-drop-target" : ""}`}
        draggable={Boolean(templateInfo)}
        onClick={() => setEditing(true)}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        aria-label={templateInfo ? `${templateInfo.name}, Schicht ändern oder ziehen` : "Schicht zuweisen"}
      >
        {templateInfo ? (
          <span className="shift-chip" style={{ "--chip-color": templateInfo.color }}>
            {templateInfo.name.slice(0, 3)}
          </span>
        ) : (
          <span className="shift-chip shift-chip--empty" aria-hidden="true">
            +
          </span>
        )}
      </button>
      {templateInfo && canOfferTrade && colleagues.length > 0 && (
        <button
          type="button"
          className="btn-offer-trade"
          title="Diese Schicht zum Tausch anbieten"
          onClick={() => setOffering(true)}
        >
          ⇄<span className="visually-hidden"> Schicht zum Tausch anbieten</span>
        </button>
      )}
      {renderTimeRecordBadge()}
      {renderWishBadge()}
    </span>
  );
}

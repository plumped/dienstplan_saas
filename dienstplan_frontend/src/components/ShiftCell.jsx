import { useEffect, useRef, useState } from "react";
import { chipGlyph } from "../chipGlyph.js";
import { effectiveRecordSegments, effectiveTemplateSegments } from "../timeRecordSegments.js";
import FloatingPopover from "./FloatingPopover.jsx";
import TimeRecordSegmentEditor from "./TimeRecordSegmentEditor.jsx";
import WishEditor from "./WishEditor.jsx";

const WISH_GLYPHS = { wunschfrei: "F", wunschdienst: "D" };
const WISH_LABELS = { wunschfrei: "Wunschfrei", wunschdienst: "Wunschdienst" };

const DRAG_MIME = "application/x-dienstplan-shift";

export default function ShiftCell({
  templates,
  // Nutzer-Feedback (2026-08): Absenzarten sind ein tenant-eigener Katalog
  // (AbsenceType) statt hartcodiert -- absence.type ist die numerische
  // AbsenceType-ID, absenceTypes liefert dazu Name/Farbe fürs Badge.
  absenceTypes = [],
  templateInfo,
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
  // Genehmigungsprozess (2026-08, Automatisierte Planung mit Auffülldienst):
  // nur für canEdit (Admin/Planer) und nur, solange preference.status ===
  // "pending" -- ein entschiedener Wunsch ist nur noch lesbar (siehe
  // ShiftPreferencePermission).
  onApproveWish,
  onRejectWish,
  // Workflow-Redesign (2026-08, Nutzer-Feedback: "erst Tage markieren, dann
  // beplanen -- nicht umgekehrt"): ein Klick auf eine Zelle markiert IMMER
  // (kein separater "selectionMode" mehr) -- das tatsächliche Beplanen
  // passiert gesammelt über die Icon-Toolbar in PlanGrid.jsx
  // (applyToolToMarked). onMarkStart entscheidet anhand von `marked`, ob
  // markiert oder entmarkiert wird, und startet damit den Ziehen-Modus in
  // PlanGrid; onMarkEnter wendet denselben Modus beim Drüberziehen auf
  // weitere Zellen an. `marked` gilt pro TAG (nicht pro Slot) -- beide
  // ShiftCell-Instanzen eines Split-Tages bekommen denselben Wert, nur für
  // aria-pressed/Titel; die sichtbare Markierung sitzt eine Ebene höher an
  // .day-cell-slots (PlanGrid.jsx), weil sie sonst bei einem belegten Tag
  // hinter der opaken Farbfläche verschwinden würde.
  marked = false,
  onMarkStart,
  onMarkEnter,
  // Bugfix (Nutzer-Feedback): reines Toggle für den `onClick`-Fallback
  // (belegte/draggable Zellen ohne eigenen mousedown-Handler weiter unten,
  // sowie der Tastatur-Fallback bei Enter/Leertaste) -- bewusst NICHT
  // onMarkStart, das den Ziehmodus in PlanGrid.jsx (dragMarkModeRef) startet.
  // Ein `click` feuert immer NACH dem zugehörigen `mouseup`, der den
  // Ziehmodus dort beendet -- rief der Klick-Fallback onMarkStart() auf,
  // blieb der Ziehmodus unbemerkt aktiv (kein weiteres mouseup folgt), und
  // jede spätere Mausbewegung markierte ungewollt weitere Zellen.
  onMarkToggle,
  // README Punkt 18 (Split-Shifts): assignmentId identifiziert, WELCHE der
  // (bis zu zwei) Zuweisungen dieses Tages diese ShiftCell-Instanz gerade
  // darstellt -- undefined für einen leeren Slot. Wird 1:1 an onMove/
  // onOfferTrade durchgereicht, damit PlanGrid.jsx bei mehreren Zuweisungen
  // am selben Tag weiss, welche konkret gemeint ist, statt sie über
  // employee+date (mehrdeutig) neu aufzulösen.
  // showWishBadge blendet das Wunschfrei/Wunschdienst-Badge aus, wenn diese
  // Instanz der ZWEITE Slot eines Tages ist -- ShiftPreference gilt
  // personen-/tagesweise, nicht pro Zuweisung, ein zweites Badge wäre ein
  // verwirrendes Duplikat.
  assignmentId,
  showWishBadge = true,
}) {
  const absenceType = absence ? absenceTypes.find((t) => t.id === absence.type) : null;
  const [offering, setOffering] = useState(false);
  const [recordingTime, setRecordingTime] = useState(false);
  const [savingTime, setSavingTime] = useState(false);
  const [wishing, setWishing] = useState(false);
  const [savingWish, setSavingWish] = useState(false);
  const [decidingWish, setDecidingWish] = useState(false);
  const [decidingWishBusy, setDecidingWishBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const selectRef = useRef(null);
  const recordBadgeRef = useRef(null);
  const wishBadgeRef = useRef(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    if (offering) selectRef.current?.focus();
  }, [offering]);

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

  async function handleDecideWish(decision) {
    setDecidingWishBusy(true);
    const ok = await (decision === "approve" ? onApproveWish() : onRejectWish());
    setDecidingWishBusy(false);
    if (ok) setDecidingWish(false);
  }

  // Block 2.13: Badge oben links auf der eigenen, heutigen/zukünftigen
  // Zelle -- Wunschfrei/Wunschdienst direkt im Planblatt eintragen, analog
  // zum Ist-Zeit-Badge (Block 1.10) unten rechts. Für alle anderen Zellen,
  // in denen bereits ein Wunsch besteht, zeigt der Planer (canManage) einen
  // rein informativen, nicht klickbaren Hinweis, damit er die Wünsche beim
  // Ausfüllen des Plans vor Augen hat -- Bearbeiten bleibt höchstpersönlich.
  function renderWishBadge() {
    if (!showWishBadge) return null;
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
      const wishLabel =
        preference.type === "wunschfrei" ? "Wunschfrei" : `Wunschdienst: ${wishedTemplate?.name ?? "?"}`;
      const status = preference.status ?? "pending";
      // Genehmigungsprozess (2026-08): nur ein offener (PENDING) Wunsch ist
      // für den Planer anklickbar -- ein bereits entschiedener bleibt wie
      // bisher rein informativ (siehe ShiftPreferencePermission).
      if (status !== "pending") {
        const decisionLabel = status === "approved" ? "freigegeben" : "abgelehnt";
        return (
          <span
            className={`btn-wish is-set is-${preference.type} is-readonly is-${status}`}
            title={`${wishLabel} (${decisionLabel})`}
            aria-hidden="true"
          >
            {WISH_GLYPHS[preference.type]}
          </span>
        );
      }
      return (
        <>
          <button
            ref={wishBadgeRef}
            type="button"
            className={`btn-wish is-set is-${preference.type} is-pending`}
            title={`${wishLabel} -- offen, zum Entscheiden klicken`}
            onClick={() => setDecidingWish((v) => !v)}
          >
            {WISH_GLYPHS[preference.type]}
            <span className="visually-hidden"> {wishLabel} -- offen</span>
          </button>
          {decidingWish && (
            <FloatingPopover anchorRef={wishBadgeRef} onClose={() => setDecidingWish(false)} className="wish-popover">
              <div className="wish-decision-popover">
                <p>{wishLabel}</p>
                <div className="entry-actions">
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={decidingWishBusy}
                    onClick={() => handleDecideWish("approve")}
                  >
                    Freigeben
                  </button>
                  <button
                    type="button"
                    className="btn-ghost btn-danger-ghost"
                    disabled={decidingWishBusy}
                    onClick={() => handleDecideWish("reject")}
                  >
                    Ablehnen
                  </button>
                </div>
              </div>
            </FloatingPopover>
          )}
        </>
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
    // Workflow-Redesign (2026-08): eine Absenz-Zelle ist -- wie jede andere
    // -- Ziel des Markierens, nicht mehr eines direkt platzierenden
    // Werkzeugklicks. Nicht draggable (kein Verschieben einer Absenz per
    // Drag&Drop), daher hier -- anders als beim belegten Dienst unten --
    // volle mousedown+mouseenter-Ziehmarkierung ohne Rücksicht auf
    // natives HTML5-Drag-and-Drop. Für rein lesende Ansichten (!canEdit)
    // bleibt es bei der reinen Anzeige.
    // Nutzer-Feedback (2026-08): Halbtags-Absenzen ("nur vormittags"/"nur
    // nachmittags") sollen auch im Grid erkennbar sein, nicht nur im
    // Abwesenheiten-Tab -- ein kleines "½" neben dem Glyph, sichtbar in
    // Titel/Tooltip UND direkt in der Zelle. Ein ganztägiger Absenz-Chip
    // spannt weiterhin die volle Zellbreite (nur slot0 wird gerendert,
    // siehe PlanGrid.jsx showSecondSlot). Eine Halbtags-Absenz (Oben/Unten
    // in der Toolbar -- "Absenzen genau gleich zuteilbar wie Dienste",
    // Nutzer-Feedback 2026-08) belegt dagegen nur EINEN der beiden Slots,
    // genau wie ein regulärer Dienst -- das kleine "½" bleibt trotzdem
    // lesbar (0.6em, siehe .shift-chip-half-day), da chipGlyph ohnehin nur
    // ein Zeichen liefert (siehe chipGlyph.js-Kommentar zum Ein-Zeichen-Limit).
    const isHalfDay = absence.day_portion && absence.day_portion !== "full";
    const portionLabel = absence.day_portion === "morning" ? "Nur vormittags" : "Nur nachmittags";
    const title = `${absenceType?.name ?? absence.type} (${absence.start_date} – ${absence.end_date}${
      isHalfDay ? `, ${portionLabel}` : ""
    })`;
    const chip = (
      <span className="shift-chip shift-chip--absence" style={{ "--chip-color": absenceType?.color }}>
        {absenceType ? chipGlyph(absenceType) : absence.type}
        {isHalfDay && <span className="shift-chip-half-day">½</span>}
      </span>
    );
    if (!canEdit) {
      return (
        <span className="shift-chip-btn is-absence" title={title}>
          {chip}
        </span>
      );
    }
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
      onMarkToggle();
    };
    return (
      <button
        type="button"
        className="shift-chip-btn is-absence"
        aria-pressed={marked}
        title={`${title} -- ${marked ? "Markierung aufheben" : "Zum Beplanen markieren (auch durch Ziehen)"}`}
        onMouseDown={handleMouseDown}
        onMouseEnter={onMarkEnter}
        onClick={handleClick}
      >
        {chip}
      </button>
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
              {chipGlyph(templateInfo)}
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
    // README Punkt 18: assignmentId statt nur employeeId+date, damit die
    // Zielzelle bei mehreren Zuweisungen desselben Tages (Split-Shifts)
    // eindeutig weiss, welche der beiden konkret gezogen wurde.
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ employeeId, date, assignmentId }));
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
    onMove(source.employeeId, source.date, source.assignmentId, employeeId, date, assignmentId);
  }

  // Workflow-Redesign (2026-08): ein Klick markiert (statt direkt zu
  // beplanen), OHNE das bestehende Drag&Drop (Schicht auf einen anderen Tag
  // ziehen, s. o.) zu brechen -- beide Gesten beginnen mit mousedown auf
  // demselben Button. Bei einer LEEREN Zelle (nicht draggable) ist volle
  // Ziehmarkierung (mousedown+mouseenter, wie bei der Absenz oben) völlig
  // unproblematisch. Bei einer BELEGTEN (draggable) Zelle würde
  // preventDefault auf mousedown das native Drag-and-Drop verhindern (der
  // Browser startet keinen Drag, wenn sein mousedown gecancelt wurde) --
  // dort daher KEIN eigener mousedown-Handler, nur ein normales onClick
  // (markiert bei einfachem Klick ohne Bewegung, wie gehabt; ein
  // tatsächlicher Drag löst ohnehin kein click aus). onMouseEnter bleibt in
  // beiden Fällen aktiv: es feuert nur bei normaler Mausbewegung, ein
  // aktiver nativer Drag unterdrückt es im Browser ohnehin zugunsten von
  // dragenter/dragover.
  const handleMouseDown = (e) => {
    if (templateInfo) return; // belegt+draggable -- natives Drag nicht stören
    if (e.button !== 0) return;
    e.preventDefault();
    suppressClickRef.current = true;
    onMarkStart();
  };
  const handleClick = () => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    onMarkToggle();
  };

  return (
    <span className="cell-wrap">
      <button
        type="button"
        className={`shift-chip-btn${dragOver ? " is-drop-target" : ""}`}
        draggable={Boolean(templateInfo)}
        aria-pressed={marked}
        onMouseDown={handleMouseDown}
        onMouseEnter={onMarkEnter}
        onClick={handleClick}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        aria-label={
          templateInfo
            ? `${templateInfo.name}${marked ? ", markiert" : ""} -- klicken zum Markieren, ziehen zum Verschieben`
            : `Leer${marked ? ", markiert" : ""} -- klicken zum Markieren (auch durch Ziehen)`
        }
      >
        {templateInfo ? (
          <span className="shift-chip" style={{ "--chip-color": templateInfo.color }}>
            {chipGlyph(templateInfo)}
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

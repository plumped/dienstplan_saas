import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Rendert children in ein Portal auf document.body, positioniert relativ zu
// anchorRef und an den Viewport geklemmt (fällt nach oben um, wenn unten
// kein Platz mehr ist). Wird für den Ist-Zeit-Popover im Planblatt-Grid
// gebraucht (Block 1.13): das Grid hat horizontales Scrolling
// (overflow-x: auto), ein normal positioniertes Popover würde an Zellen nahe
// dem rechten Rand abgeschnitten -- ein Portal umgeht das.
export default function FloatingPopover({ anchorRef, onClose, className = "", children }) {
  const popoverRef = useRef(null);
  const [style, setStyle] = useState({ position: "fixed", top: 0, left: 0 });

  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const popover = popoverRef.current;
    if (!anchor || !popover) return;
    const rect = anchor.getBoundingClientRect();
    const margin = 8;

    let left = rect.left;
    left = Math.min(left, window.innerWidth - popover.offsetWidth - margin);
    left = Math.max(margin, left);

    let top = rect.bottom + 4;
    if (top + popover.offsetHeight > window.innerHeight - margin) {
      top = Math.max(margin, rect.top - popover.offsetHeight - 4);
    }

    setStyle({ position: "fixed", top, left });
  }, [anchorRef]);

  useEffect(() => {
    function handlePointerDown(e) {
      if (popoverRef.current?.contains(e.target) || anchorRef.current?.contains(e.target)) return;
      onClose();
    }
    function handleKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [anchorRef, onClose]);

  return createPortal(
    <div ref={popoverRef} className={className} style={style}>
      {children}
    </div>,
    document.body
  );
}

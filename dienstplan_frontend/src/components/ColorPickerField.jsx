import { IconChevronDown } from "../icons.jsx";

// Settings-Panel-Polish (2026-08): sieht aus wie ein eigenständiges
// Steuerelement (Swatch + Hex-Code + Chevron), ist funktional aber
// weiterhin der native <input type="color"> -- der liegt unsichtbar über
// der gesamten Trigger-Fläche (siehe .color-picker-input in styles.css),
// ein Klick irgendwo darauf öffnet also den normalen Browser-Farbwähler.
// onChange bekommt das reale Input-Change-Event durchgereicht, damit
// bestehende Handler (z. B. (e) => setForm(prev => ({ ...prev, color:
// e.target.value }))) unverändert weiterfunktionieren. Eigene Komponente,
// weil derselbe Trigger in mehreren Einstellungs-Tabs vorkommt
// (Absenzarten, Schichttypen, ...).
export default function ColorPickerField({ value, onChange }) {
  return (
    <label className="color-picker-trigger">
      <input type="color" className="color-picker-input" value={value} onChange={onChange} />
      <span className="color-picker-swatch" style={{ background: value }} aria-hidden="true" />
      <span className="color-picker-hex">{value}</span>
      <IconChevronDown width={14} height={14} />
    </label>
  );
}

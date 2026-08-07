// Nutzer-Feedback (2026-08, Polypoint-Vorbild): Grid-Zellen zeigen ein
// einzelnes, fettes Kürzel statt einer 3-Buchstaben-Textabkürzung -- bei
// Miniaturgrösse bleibt ein Zeichen lesbar, drei nicht. Nutzt
// TimeTemplate.icon/AbsenceType.icon (bisher ungenutztes Feld), falls
// gesetzt, sonst Fallback auf den ersten Buchstaben des Namens.
// Bugfix (2026-08, Playwright-Verifikation): der Fallback lieferte
// ursprünglich zwei Buchstaben ("TH") -- bei zwei Diensten nebeneinander
// (Links/Rechts-Split, ~20px pro Hälfte) überlagerten sich die Glyphen der
// beiden Chips sichtbar ("THTH"), weil zwei Zeichen bei 12px/fett nicht in
// die halbe Zellbreite passen. Ein einzelnes Zeichen passt zuverlässig;
// echte Unterscheidbarkeit bei ähnlichen Namen erfordert weiterhin ein
// explizites `icon` (siehe TimeTemplateSettings.jsx/AbsenceTypeSettings.jsx).
export function chipGlyph(item) {
  const icon = item?.icon?.trim();
  if (icon) return icon;
  return (item?.name ?? "?").slice(0, 1).toUpperCase();
}

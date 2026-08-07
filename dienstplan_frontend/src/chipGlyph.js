// Nutzer-Feedback (2026-08, Polypoint-Vorbild): Grid-Zellen zeigen ein
// einzelnes, fettes Kürzel statt einer 3-Buchstaben-Textabkürzung -- bei
// Miniaturgrösse bleibt ein Zeichen lesbar, drei nicht. Nutzt
// TimeTemplate.icon/AbsenceType.icon (bisher ungenutztes Feld), falls
// gesetzt, sonst Fallback auf die ersten zwei Buchstaben des Namens.
export function chipGlyph(item) {
  const icon = item?.icon?.trim();
  if (icon) return icon;
  return (item?.name ?? "?").slice(0, 2).toUpperCase();
}

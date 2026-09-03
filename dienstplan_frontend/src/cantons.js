// Dieselbe Kantons-Liste wie core.models.SWISS_CANTON_CHOICES (Backend) --
// hier dupliziert, weil die Auswahl fürs <select> im Frontend gebraucht wird
// und kein eigener API-Roundtrip dafür lohnt (26 statische Einträge).
// Gemeinsam genutzt von TenantSettings.jsx und SignupForm.jsx.
export const SWISS_CANTONS = [
  ["AG", "Aargau"],
  ["AI", "Appenzell Innerrhoden"],
  ["AR", "Appenzell Ausserrhoden"],
  ["BE", "Bern"],
  ["BL", "Basel-Landschaft"],
  ["BS", "Basel-Stadt"],
  ["FR", "Freiburg"],
  ["GE", "Genf"],
  ["GL", "Glarus"],
  ["GR", "Graubünden"],
  ["JU", "Jura"],
  ["LU", "Luzern"],
  ["NE", "Neuenburg"],
  ["NW", "Nidwalden"],
  ["OW", "Obwalden"],
  ["SG", "St. Gallen"],
  ["SH", "Schaffhausen"],
  ["SO", "Solothurn"],
  ["SZ", "Schwyz"],
  ["TG", "Thurgau"],
  ["TI", "Tessin"],
  ["UR", "Uri"],
  ["VD", "Waadt"],
  ["VS", "Wallis"],
  ["ZG", "Zug"],
  ["ZH", "Zürich"],
];

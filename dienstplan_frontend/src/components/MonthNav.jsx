const MONTHS = [
  "Januar",
  "Februar",
  "März",
  "April",
  "Mai",
  "Juni",
  "Juli",
  "August",
  "September",
  "Oktober",
  "November",
  "Dezember",
];

export default function MonthNav({ year, month, onChange }) {
  function shift(delta) {
    let m = month + delta;
    let y = year;
    if (m < 1) {
      m = 12;
      y -= 1;
    }
    if (m > 12) {
      m = 1;
      y += 1;
    }
    onChange(y, m);
  }

  return (
    <div className="month-nav">
      <button type="button" aria-label="Vorheriger Monat" onClick={() => shift(-1)}>
        ‹
      </button>
      <span>
        {MONTHS[month - 1]} {year}
      </span>
      <button type="button" aria-label="Nächster Monat" onClick={() => shift(1)}>
        ›
      </button>
    </div>
  );
}

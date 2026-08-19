// Kleine, abhängigkeitsfreie Outline-Icons (2026-08, Settings-Polish) --
// gleicher Stil wie FeatureIcon in LandingPage.jsx (24x24 Viewport,
// currentColor-Stroke), hier als eigenständige, wiederverwendbare
// Komponenten statt eines name-Switches, weil sie über mehrere
// Einstellungs-Tabs hinweg gebraucht werden (nicht nur auf der Landing
// Page). Bewusst kein Icon-Paket als neue Abhängigkeit -- das Projekt hat
// ausser react/react-dom keine Runtime-Dependencies, ein einzelnes Icon-Set
// für rund ein Dutzend Glyphen rechtfertigt das nicht.
const common = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

export function IconPlus(props) {
  return (
    <svg {...common} {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function IconCalendar(props) {
  return (
    <svg {...common} {...props}>
      <rect x="3.5" y="5" width="17" height="16" rx="2.5" />
      <path d="M3.5 10h17M8 3v4M16 3v4" />
    </svg>
  );
}

export function IconHeartPulse(props) {
  return (
    <svg {...common} {...props}>
      <path d="M12 20.5s-7.5-4.6-9.5-9.3C1.2 7.9 3 5 6.2 5c1.9 0 3.3 1 4.3 2.3C11.5 6 12.9 5 14.8 5 18 5 19.8 7.9 18.5 11.2c-.6 1.5-1.6 2.9-2.7 4.1H14l-1.4-2.2-1.8 3.6L9.6 14H6.7" />
    </svg>
  );
}

export function IconSearch(props) {
  return (
    <svg {...common} {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M20 20l-4.3-4.3" />
    </svg>
  );
}

export function IconPencil(props) {
  return (
    <svg {...common} {...props}>
      <path d="M4 20l.9-4L16.4 4.5a1.8 1.8 0 0 1 2.6 0l.5.5a1.8 1.8 0 0 1 0 2.6L8 19.1z" />
      <path d="M14.5 6.5l3 3" />
    </svg>
  );
}

export function IconTrash(props) {
  return (
    <svg {...common} {...props}>
      <path d="M4.5 7h15M9.5 7V4.8c0-.7.6-1.3 1.3-1.3h2.4c.7 0 1.3.6 1.3 1.3V7M18.5 7l-.7 12.2a2 2 0 0 1-2 1.8H8.2a2 2 0 0 1-2-1.8L5.5 7" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

export function IconChevronDown(props) {
  return (
    <svg {...common} {...props}>
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

export function IconList(props) {
  return (
    <svg {...common} {...props}>
      <path d="M8 6h12M8 12h12M8 18h12" />
      <path d="M4 6h.01M4 12h.01M4 18h.01" />
    </svg>
  );
}

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

export function IconBadge(props) {
  return (
    <svg {...common} {...props}>
      <circle cx="12" cy="9" r="6" />
      <path d="M8.5 14.2L7 21l5-2.5 5 2.5-1.5-6.8" />
    </svg>
  );
}

export function IconBuilding(props) {
  return (
    <svg {...common} {...props}>
      <rect x="4" y="3" width="12" height="18" rx="1.5" />
      <path d="M16 9h4v12h-4M7.5 7h1M11.5 7h1M7.5 11h1M11.5 11h1M7.5 15h1M11.5 15h1" />
    </svg>
  );
}

export function IconUsers(props) {
  return (
    <svg {...common} {...props}>
      <circle cx="9" cy="8" r="3.3" />
      <path d="M2.8 20c.6-3.3 3.2-5.5 6.2-5.5s5.6 2.2 6.2 5.5" />
      <path d="M15.5 5.2c1.6.4 2.8 1.9 2.8 3.6 0 1.7-1.2 3.2-2.8 3.6M18.7 14.8c2.2.6 3.8 2.5 4.3 4.9" />
    </svg>
  );
}

export function IconClock(props) {
  return (
    <svg {...common} {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3.2 2" />
    </svg>
  );
}

export function IconCoins(props) {
  return (
    <svg {...common} {...props}>
      <ellipse cx="9" cy="7.5" rx="6" ry="3.2" />
      <path d="M3 7.5V13c0 1.8 2.7 3.2 6 3.2s6-1.4 6-3.2V7.5" />
      <path d="M3 10.3c0 1.8 2.7 3.2 6 3.2s6-1.4 6-3.2" />
      <path d="M13.2 12.8c.9 1.6 3 2.7 5.3 2.7 3.3 0 6-2.1 6-3.9s-2.7-3.9-6-3.9c-.8 0-1.6.1-2.3.4" />
    </svg>
  );
}

export function IconDownload(props) {
  return (
    <svg {...common} {...props}>
      <path d="M12 3.5v12M7.5 11.5l4.5 4.5 4.5-4.5" />
      <path d="M4.5 17.5v2a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-2" />
    </svg>
  );
}

export function IconScale(props) {
  return (
    <svg {...common} {...props}>
      <path d="M12 3v18M8 21h8" />
      <path d="M5 6h14M5 6L2.5 11.5a2.5 2.5 0 0 0 5 0L5 6ZM19 6l-2.5 5.5a2.5 2.5 0 0 0 5 0L19 6Z" />
    </svg>
  );
}

export function IconKey(props) {
  return (
    <svg {...common} {...props}>
      <circle cx="8" cy="15" r="4.2" />
      <path d="M11 12l8.5-8.5M16.5 6.5l2.5 2.5M19.5 3.5L22 6" />
    </svg>
  );
}

export function IconBarChart(props) {
  return (
    <svg {...common} {...props}>
      <path d="M4 20V10M11 20V4M18 20v-7" />
      <path d="M2.5 20h19" />
    </svg>
  );
}

export function IconRefreshCw(props) {
  return (
    <svg {...common} {...props}>
      <path d="M4 12a8 8 0 0 1 13.66-5.66L20 8.5" />
      <path d="M20 3.5V8.5H15" />
      <path d="M20 12a8 8 0 0 1-13.66 5.66L4 15.5" />
      <path d="M4 20.5V15.5H9" />
    </svg>
  );
}

export function IconCreditCard(props) {
  return (
    <svg {...common} {...props}>
      <rect x="2.5" y="5.5" width="19" height="13" rx="2.2" />
      <path d="M2.5 10h19M6 14.5h4" />
    </svg>
  );
}

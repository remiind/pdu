// Inline-SVG-Icons im Stil des Design-Kits (24×24, Linie, runde Enden)
const P = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  auto: '<circle cx="12" cy="12" r="8"/><path d="M12 4v16M12 8a4 4 0 0 1 0 8"/>',
  pulse: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
  power: '<path d="M12 3v8"/><path d="M6.4 6.4a8 8 0 1 0 11.2 0"/>',
  bolt: '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
  cycle: '<path d="M20 11a8 8 0 0 0-14.5-4.5M4 4v3h3M4 13a8 8 0 0 0 14.5 4.5M20 20v-3h-3"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4"/>',
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  code: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.5 3-6 6.5-6s6.5 2.5 6.5 6M16 4.5a3.5 3.5 0 0 1 0 7M18 14.2c2 .7 3.5 2.6 3.5 5.8"/>',
  logout: '<path d="M10 4H5v16h5M15 8l4 4-4 4M19 12H9"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v3h3M12 8v4l3 2"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  swap: '<path d="M8 20V4M8 4L5 7M8 4l3 3M16 4v16M16 20l-3-3M16 20l3-3"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  thermo: '<path d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 10v6"/>',
};
export const ico = (n, cls = 'ico') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[n] || ''}</svg>`;

// Steckdosen-Ansicht (Blick auf die Buchse an der PDU), 22×18; Kontakte gefüllt, Umriss als Linie
const OUT = {
  // C13 (auch C14/C15): unten abgeschrägt, Schutzleiter oben mittig, L/N senkrecht unten
  c13: '<path class="po" d="M2.5 2.5h17v8.5l-4.5 4.5h-8L2.5 11z"/><rect x="10" y="4.6" width="2" height="4.4" rx=".5"/><rect x="5.6" y="8" width="2" height="4.4" rx=".5"/><rect x="14.4" y="8" width="2" height="4.4" rx=".5"/>',
  // C19 (auch C20/C21): rechteckig, Schutzleiter oben senkrecht, L/N waagerecht unten
  c19: '<rect class="po" x="1.5" y="2.5" width="19" height="13" rx="1.6"/><rect x="10" y="4.6" width="2" height="4.6" rx=".5"/><rect x="4.4" y="10.8" width="5" height="2" rx=".5"/><rect x="12.6" y="10.8" width="5" height="2" rx=".5"/>',
  // Schutzkontakt: rund, zwei Löcher, Erdungsbügel oben/unten
  schuko: '<circle class="po" cx="11" cy="9" r="7"/><circle cx="7.6" cy="9" r="1.3"/><circle cx="14.4" cy="9" r="1.3"/><rect x="9.5" y="2" width="3" height="1.6" rx=".4"/><rect x="9.5" y="14.4" width="3" height="1.6" rx=".4"/>',
};
const OUT_OF = { C13: 'c13', C14: 'c13', C15: 'c13', C19: 'c19', C20: 'c19', C21: 'c19', Schuko: 'schuko' };
// Icon zum Socket-Typ (leer, wenn unbekannt)
export const plugIco = (type) => {
  const k = OUT_OF[type];
  return k ? `<svg class="pico" viewBox="0 0 22 18" aria-hidden="true">${OUT[k]}</svg>` : '';
};

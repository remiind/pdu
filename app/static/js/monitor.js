// Monitoring: Live-Werte einer PDU (Phasen, Leistung, Sicherungen, Verbrauch je Socket)
import { ago, fmt, groupColor, h, setGroupColors, svgEl, watts } from './util.js';
import { api } from './api.js';
import { I, choosePdu, every, page, picker } from './shell.js';
import { plugIco } from './icons.js';

const HIST_MAX = 720; // 1 h bei 5 s
const HIST = new Map(); // PDU → [{t, w}] (nur im Browser, solange die Seite offen ist)

function pushHist(key, snap) {
  const w = snap.totals.power;
  if (w === null || w === undefined) return;
  const arr = HIST.get(key) || [];
  if (!arr.length || snap.ts - arr[arr.length - 1].t >= 1) arr.push({ t: snap.ts, w });
  while (arr.length > HIST_MAX) arr.shift();
  HIST.set(key, arr);
}

const healthBadge = (hl) => {
  if (!hl) return null;
  const cls = hl === 'OK' ? 'valid' : hl === 'Warning' ? 'expiring' : 'revoked';
  const txt = hl === 'OK' ? 'OK' : hl === 'Warning' ? 'Warnung' : hl === 'Critical' ? 'Kritisch' : hl;
  return h('span', { class: `badge ${cls}` }, txt);
};

const stateBadge = (st) => st === 'On' ? h('span', { class: 'sstate on' }, h('i'), 'An')
  : st === 'Off' ? h('span', { class: 'sstate off' }, h('i'), 'Aus')
    : h('span', { class: 'sstate' }, h('i'), st || '–');

/** Auslastungsbalken (Strom gegen Nennstrom); Farbe + Text ab 80 % bzw. 95 %. */
function meter(cur, rated) {
  const max = rated || 16;
  const pct = cur === null || cur === undefined ? 0 : Math.min(100, (cur / max) * 100);
  const lvl = pct >= 95 ? 'bad' : pct >= 80 ? 'warn' : '';
  const fill = h('span', { class: 'mfill ' + lvl });
  fill.style.width = `${Math.max(pct, cur ? 1.5 : 0)}%`;
  return h('div', { class: 'meter', title: `${fmt(cur, 2)} A von ${fmt(max)} A (${fmt(pct)} %)` },
    h('span', { class: 'mtrack' }, fill),
    h('span', { class: 'mlab num' + (lvl ? ` ${lvl}-t` : '') }, `${fmt(pct)} %${lvl === 'bad' ? ' · kritisch' : lvl === 'warn' ? ' · hoch' : ''}`));
}

/** Verlauf der Gesamtleistung (eine Linie, Hover mit Fadenkreuz + Wert). */
function powerChart(points) {
  const W = 640, H = 210, L = 60, R = 12, T = 12, B = 26;
  const wrap = h('div', { class: 'chart' });
  if (points.length < 2) {
    wrap.append(h('p', { class: 'muted small chartempty' }, 'Der Verlauf füllt sich, solange die Seite geöffnet ist (alle 5 s ein Messpunkt).'));
    return wrap;
  }
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const maxW = Math.max(...points.map((p) => p.w));
  const step = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000].find((s) => maxW / s <= 4) || 20000;
  const top = Math.max(step, Math.ceil((maxW * 1.08) / step) * step);
  const x = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R);
  const y = (w) => T + (1 - w / top) * (H - T - B);
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'pchart', role: 'img', 'aria-label': 'Verlauf der Gesamtleistung' });
  for (let v = 0; v <= top; v += step) {
    svg.append(svgEl('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), class: v === 0 ? 'base' : 'grid' }));
    const tx = svgEl('text', { x: L - 8, y: y(v) + 3.5, class: 'ax', 'text-anchor': 'end' });
    tx.textContent = v >= 10000 ? `${fmt(v / 1000)} kW` : `${fmt(v)} W`;
    svg.append(tx);
  }
  const span = t1 - t0;
  for (const frac of [0, 0.5, 1]) {
    const t = t0 + span * frac;
    const tx = svgEl('text', { x: x(t), y: H - 6, class: 'ax', 'text-anchor': frac === 0 ? 'start' : frac === 1 ? 'end' : 'middle' });
    tx.textContent = new Date(t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: span < 600 ? '2-digit' : undefined });
    svg.append(tx);
  }
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.w).toFixed(1)}`).join('');
  svg.append(svgEl('path', { d: `${d}L${x(t1)},${y(0)}L${x(t0)},${y(0)}Z`, class: 'parea' }));
  svg.append(svgEl('path', { d, class: 'pline' }));
  const last = points[points.length - 1];
  svg.append(svgEl('circle', { cx: x(last.t), cy: y(last.w), r: 4, class: 'pdotc' }));
  // Hover
  const cross = svgEl('line', { y1: T, y2: H - B, class: 'cross', visibility: 'hidden' });
  const mark = svgEl('circle', { r: 4.5, class: 'pdotc', visibility: 'hidden' });
  svg.append(cross, mark);
  const tip = h('div', { class: 'ctip', hidden: true });
  const hit = svgEl('rect', { x: L, y: T, width: W - L - R, height: H - T - B, class: 'hit' });
  svg.append(hit);
  const show = (ev) => {
    const r = svg.getBoundingClientRect();
    const sx = ((ev.clientX - r.left) / r.width) * W;
    const t = t0 + ((sx - L) / (W - L - R)) * (t1 - t0);
    let best = points[0];
    for (const p of points) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    cross.setAttribute('x1', x(best.t)); cross.setAttribute('x2', x(best.t)); cross.setAttribute('visibility', 'visible');
    mark.setAttribute('cx', x(best.t)); mark.setAttribute('cy', y(best.w)); mark.setAttribute('visibility', 'visible');
    tip.replaceChildren(h('b', { class: 'num' }, watts(best.w)), h('span', null, new Date(best.t * 1000).toLocaleTimeString('de-DE')));
    tip.hidden = false;
    const px = (x(best.t) / W) * r.width;
    tip.style.left = `${Math.min(Math.max(px, 60), r.width - 60)}px`;
    tip.style.top = `${(y(best.w) / H) * r.height}px`;
  };
  hit.addEventListener('pointermove', show);
  hit.addEventListener('pointerleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); mark.setAttribute('visibility', 'hidden'); });
  wrap.append(svg, tip);
  return wrap;
}

export async function renderMonitor(root, routeKey) {
  const main = page(root, 'monitor');
  let list;
  try { list = await api.pdus(); } catch (e) { main.replaceChildren(h('div', { class: 'flash err' }, e.message)); return null; }
  if (!list.pdus.length) {
    main.replaceChildren(h('header', { class: 'page' }, h('h1', null, 'Monitoring')), h('div', { class: 'card' }, h('p', null, 'Es sind keine PDUs eingerichtet. Trage sie in der .env unter PDU_LIST ein (siehe README).')));
    return null;
  }
  const key = choosePdu(list.pdus, routeKey);
  if (routeKey !== key) window.history.replaceState(null, '', `#/monitor/${key}`);
  const pick = picker(list.pdus, key, (k) => { location.hash = `#/monitor/${k}`; });
  const body = h('div', { class: 'pbody' }, h('p', { class: 'muted' }, 'Lade Messwerte …'));
  const stamp = h('span', { class: 'muted small num' });
  const refreshBtn = h('button', { type: 'button', class: 'btn', title: 'Jetzt aktualisieren', onclick: () => loop.now(true) }, I('refresh'), 'Aktualisieren');
  main.replaceChildren(
    h('header', { class: 'page phead' }, h('div', null, h('h1', null, 'Monitoring'), h('p', { class: 'sub' }, 'Live-Werte über die Redfish-API · alle 5 Sekunden')), h('div', { class: 'phead-r' }, stamp, refreshBtn)),
    pick.el, body);

  const state = { sort: 'num', dir: 1, q: '' };
  let snap = null;

  function sockets() {
    const q = state.q.toLowerCase();
    const rows = snap.outlets.filter((o) => !q || `${o.num} ${o.label} ${o.name} ${o.group || ''} ${o.type} ${o.phase}`.toLowerCase().includes(q));
    const k = state.sort;
    rows.sort((a, b) => {
      const va = a[k] ?? (typeof b[k] === 'string' ? '' : -1), vb = b[k] ?? (typeof a[k] === 'string' ? '' : -1);
      return (typeof va === 'string' ? va.localeCompare(vb, 'de', { numeric: true }) : va - vb) * state.dir || (a.num - b.num);
    });
    const maxP = Math.max(1, ...snap.outlets.map((o) => o.power || 0));
    const m = snap.metering !== false; // PDU misst je Socket (sonst Spalten ausblenden)
    const hasGroups = snap.outlets.some((o) => o.group);
    const th = (k2, label, cls = '') => h('th', { class: cls + ' sortable' + (state.sort === k2 ? ' on' : ''), onclick: () => { state.dir = state.sort === k2 ? -state.dir : (['power', 'current', 'energy'].includes(k2) ? -1 : 1); state.sort = k2; draw(); } },
      label, state.sort === k2 ? (state.dir > 0 ? ' ↑' : ' ↓') : '');
    return h('table', { class: 'data stable' },
      h('thead', null, h('tr', null, th('num', 'Nr'), th('label', 'Name'), hasGroups && th('group', 'Gruppe'), th('type', 'Typ'), th('phase', 'Phase'), th('state', 'Status'),
        m && th('current', 'Strom', 'r'), m && th('power', 'Leistung', 'r'), m && th('energy', 'Energie', 'r'))),
      h('tbody', null, rows.length ? rows.map((o) => {
        const bar = h('span', { class: 'ibar' });
        bar.style.width = `${((o.power || 0) / maxP) * 100}%`;
        return h('tr', { class: o.state === 'Off' ? 'dim' : '' },
          h('td', { class: 'num mono' }, String(o.num ?? o.id).padStart(2, '0')),
          h('td', null, o.label || h('span', { class: 'muted' }, o.name || '–')),
          hasGroups && h('td', null, o.group ? (() => { const c = h('span', { class: 'gchip' }, o.group); c.style.setProperty('--gc', groupColor(o.group)); return c; })() : ''),
          h('td', null, o.type ? h('span', { class: 'tchip', html: plugIco(o.type) }, o.type) : '–'),
          h('td', { class: 'muted' }, o.phase || '–'),
          h('td', null, stateBadge(o.state)),
          m && h('td', { class: 'r num' }, o.current === null ? '–' : `${fmt(o.current, 2)} A`),
          m && h('td', { class: 'r num pcell' }, h('span', { class: 'itrack' }, bar), o.power === null ? '–' : watts(o.power)),
          m && h('td', { class: 'r num muted' }, o.energy === null ? '–' : `${fmt(o.energy, 1)} kWh`));
      }) : h('tr', null, h('td', { colspan: (m ? 8 : 5) + (hasGroups ? 1 : 0), class: 'empty' }, 'Kein Socket passt zur Suche.'))));
  }

  const sockWrap = h('div', { class: 'scroll' });
  const search = h('input', { type: 'search', placeholder: 'Socket oder Gruppe suchen …', 'aria-label': 'Sockets durchsuchen', oninput: () => { state.q = search.value; sockWrap.replaceChildren(sockets()); } });

  function draw() {
    const s = snap, t = s.totals, i = s.info;
    const meta = [i.model, i.serial && `SN ${i.serial}`, i.firmware && `FW ${i.firmware}`, i.location].filter(Boolean).join(' · ');
    const tile = (n, l, cls = '', icon = null) => h('div', { class: 'tile ' + cls }, h('span', { class: 'l' }, icon && I(icon), l), h('span', { class: 'n' }, n));
    const phaseRows = s.phases.map((p) => h('tr', null,
      h('td', null, h('b', null, p.name)),
      h('td', { class: 'r num' }, p.voltage === null ? '–' : `${fmt(p.voltage, 1)} V`),
      h('td', { class: 'r num' }, p.current === null ? '–' : `${fmt(p.current, 2)} A`),
      h('td', { class: 'mcell' }, meter(p.current, p.rated)),
      h('td', { class: 'r num' }, watts(p.power)),
      h('td', { class: 'r num muted' }, p.energy === null ? '–' : `${fmt(p.energy, 1)} kWh`)));
    const phaseCard = h('div', { class: 'card flush' },
      h('div', { class: 'cardhead' }, h('h2', null, 'Phasen'), t.frequency !== null && h('span', { class: 'muted small num' }, `${fmt(t.frequency, 1)} Hz`)),
      s.phases.length ? h('div', { class: 'scroll' }, h('table', { class: 'data ptable' },
        h('thead', null, h('tr', null, h('th', null, 'Phase'), h('th', { class: 'r' }, 'Spannung'), h('th', { class: 'r' }, 'Strom'), h('th', null, 'Auslastung'), h('th', { class: 'r' }, 'Leistung'), h('th', { class: 'r' }, 'Energie'))),
        h('tbody', null, phaseRows,
          t.neutral !== null && h('tr', { class: 'nrow' }, h('td', null, h('b', null, 'N')), h('td'), h('td', { class: 'r num' }, `${fmt(t.neutral, 2)} A`), h('td'), h('td'), h('td'))))) : h('p', { class: 'pad muted' }, 'Die PDU liefert keine Phasenwerte.'));
    const hist = HIST.get(s.key) || [];
    const chartCard = h('div', { class: 'card' }, h('div', { class: 'cardhead0' }, h('h2', null, 'Gesamtleistung – Verlauf'), h('span', { class: 'muted small' }, hist.length > 1 ? `seit ${new Date(hist[0].t * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })} Uhr` : '')), powerChart(hist));
    const has = (k) => s.branches.some((b) => b[k] !== null && b[k] !== undefined);
    const redundant = s.branches.every((b) => b.phase && !b.breaker && s.phases.some((p) => p.name === b.phase));
    const branchCard = s.branches.length && !redundant ? h('div', { class: 'card flush' }, h('div', { class: 'cardhead' }, h('h2', null, 'Sicherungen / Abgänge')),
      h('div', { class: 'scroll' }, h('table', { class: 'data' },
        h('thead', null, h('tr', null, h('th', null, 'Name'), has('phase') && h('th', null, 'Phase'), h('th', null, 'Zustand'), has('current') && h('th', { class: 'r' }, 'Strom'),
          has('current') && h('th', null, 'Auslastung'), has('power') && h('th', { class: 'r' }, 'Leistung'))),
        h('tbody', null, s.branches.map((b) => h('tr', null,
          h('td', null, h('b', null, b.name)), has('phase') && h('td', { class: 'muted' }, b.phase || '–'),
          h('td', null, b.breaker ? h('span', { class: 'badge ' + (b.breaker === 'Normal' ? 'valid' : 'revoked') }, b.breaker === 'Normal' ? 'OK' : b.breaker === 'Tripped' ? 'Ausgelöst' : b.breaker === 'Off' ? 'Aus' : b.breaker) : healthBadge(b.health) || '–'),
          has('current') && h('td', { class: 'r num' }, b.current === null ? '–' : `${fmt(b.current, 2)} A`),
          has('current') && h('td', { class: 'mcell' }, b.current === null ? '–' : meter(b.current, b.rated)),
          has('power') && h('td', { class: 'r num' }, watts(b.power)))))))) : null;
    const sensorCard = s.sensors.length || s.env.temperature !== null || s.env.humidity !== null ? h('div', { class: 'card' }, h('h2', null, 'Sensoren'),
      h('table', { class: 'list' }, h('tbody', null,
        s.env.temperature !== null && h('tr', null, h('td', null, 'Temperatur'), h('td', { class: 'num' }, `${fmt(s.env.temperature, 1)} °C`)),
        s.env.humidity !== null && h('tr', null, h('td', null, 'Luftfeuchte'), h('td', { class: 'num' }, `${fmt(s.env.humidity)} %`)),
        s.sensors.map((x) => h('tr', null, h('td', null, x.name), h('td', { class: 'num' }, `${fmt(x.reading, 1)} ${x.units || ''}`)))))) : null;
    sockWrap.replaceChildren(sockets());
    body.replaceChildren(...[
      h('div', { class: 'card unitcard' }, h('div', { class: 'unit-head' },
        h('div', null, h('h2', null, s.name, ' ', healthBadge(i.health)), h('p', { class: 'muted small' }, meta || s.host),
          h('p', { class: 'muted small mono' }, s.host, i.rfName ? ` · ${i.rfName}` : '')),
        h('a', { class: 'btn', href: `#/control/${s.key}` }, I('power'), 'Zur Steuerung'))),
      h('div', { class: 'tiles' },
        tile(watts(t.power), 'Leistung gesamt', '', 'bolt'),
        tile(t.energy === null ? '–' : `${fmt(t.energy, 1)}`, 'Energie gesamt (kWh)', 'mute', 'pulse'),
        tile(`${s.counts.on} / ${s.counts.outlets}`, 'Sockets eingeschaltet', s.counts.off ? 'warn' : 'ok', 'plug'),
        (() => {
          const hi = s.phases.filter((p) => p.current !== null).sort((a, b) => b.current - a.current)[0];
          return hi ? tile(`${fmt(hi.current, 2)} A`, `Höchste Phasenlast (${hi.name})`, 'mute', 'pulse')
            : tile(t.neutral !== null ? `${fmt(t.neutral, 2)} A` : '–', 'Strom Neutralleiter', 'mute', 'pulse');
        })()),
      h('div', { class: 'grid2 mgrid' }, phaseCard, chartCard),
      (branchCard || sensorCard) && h('div', { class: 'grid2' }, branchCard, sensorCard),
      h('div', { class: 'card flush' }, h('div', { class: 'cardhead' }, h('h2', null, `Sockets (${s.outlets.length})`), h('label', { class: 'mini' }, search)), sockWrap)].filter(Boolean));
  }

  async function load(force) {
    try {
      snap = await api.pdu(key, force === true);
      setGroupColors(snap.groupColors);
      pushHist(key, snap);
      draw();
      stamp.textContent = `Stand ${new Date(snap.ts * 1000).toLocaleTimeString('de-DE')}`;
      stamp.classList.remove('bad-t');
    } catch (e) {
      if (!snap) body.replaceChildren(h('div', { class: 'card err-card' }, h('h2', null, 'PDU nicht erreichbar'), h('p', null, e.message), h('p', { class: 'muted small' }, 'Neuer Versuch in wenigen Sekunden …')));
      else { stamp.textContent = `Keine Verbindung (${e.message}) · letzter Stand ${ago(snap.ts)}`; stamp.classList.add('bad-t'); }
    }
  }
  const loop = every(5000, (force) => load(force));
  return { destroy() { loop.stop(); pick.destroy(); } };
}

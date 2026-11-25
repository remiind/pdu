// Seitenrahmen: Seitenleiste (Navigation, Farbschema, Konto) und PDU-Auswahl
import { h, watts } from './util.js';
import { ico } from './icons.js';
import { api } from './api.js';
import { session } from './session.js';
import { modal, toast } from './ui.js';
import { MODE_NAMES, setThemeMode, theme } from './theme.js';

export const I = (n) => h('span', { class: 'ic', html: ico(n) });

async function passwordDialog() {
  const old = h('input', { type: 'password', autocomplete: 'current-password', 'aria-label': 'Aktuelles Passwort' });
  const n1 = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'Neues Passwort' });
  const n2 = h('input', { type: 'password', autocomplete: 'new-password', 'aria-label': 'Neues Passwort wiederholen' });
  const r = await modal({
    title: 'Passwort ändern',
    body: h('div', null, h('label', null, 'Aktuelles Passwort', old), h('label', null, 'Neues Passwort ', h('small', null, '(mind. 8 Zeichen)'), n1), h('label', null, 'Neues Passwort wiederholen', n2)),
    actions: [{ label: 'Abbrechen', value: false }, { label: 'Ändern', value: true, cls: 'primary' }],
  });
  if (r !== true) return;
  if (n1.value !== n2.value) { toast('Die neuen Passwörter stimmen nicht überein.', 'err'); return; }
  try { await api.changePassword(old.value, n1.value); toast('Passwort geändert.'); } catch (e) { toast(e.message, 'err'); }
}

function themeSwitch() {
  const order = ['light', 'auto', 'dark'];
  const wrap = h('div', { class: 'themesw', role: 'group', 'aria-label': 'Farbschema' });
  const draw = () => wrap.replaceChildren(...order.map((m) => h('button', {
    type: 'button', class: m === theme.mode ? 'on' : '', title: m === 'auto' ? 'Farbschema folgt dem System' : `${MODE_NAMES[m]}es Farbschema`,
    onclick: () => { setThemeMode(m); draw(); },
  }, I(m === 'light' ? 'sun' : m === 'dark' ? 'moon' : 'auto'), MODE_NAMES[m])));
  draw();
  return wrap;
}

/** Seitenleiste; `active` = monitor | control | explorer | users. */
export function sidebar(active) {
  const u = session.user;
  const key = session.pdu ? `/${session.pdu}` : '';
  const a = (href, k, icon, label) => h('a', { href, class: active === k ? 'on' : '' }, I(icon), label);
  return h('aside', { class: 'side' },
    h('div', { class: 'brand' }, h('span', { class: 'mark', 'aria-hidden': 'true' }), h('div', null, h('b', null, 'PDU'), h('small', null, 'Rittal · Redfish'))),
    h('nav', null,
      a(`#/monitor${key}`, 'monitor', 'pulse', 'Monitoring'),
      a(`#/control${key}`, 'control', 'power', 'Steuerung'),
      u?.admin && h('div', { class: 'navsep' }, 'Verwaltung'),
      u?.admin && a(`#/explorer${key}`, 'explorer', 'code', 'Redfish-Explorer'),
      u?.admin && a('#/users', 'users', 'users', 'Benutzer')),
    h('div', { class: 'logout' },
      h('span', { class: 'who' }, `Angemeldet als ${u?.name || '?'}${u?.admin ? ' (Admin)' : u?.control === false ? ' (nur lesen)' : ''}`),
      themeSwitch(),
      h('button', { type: 'button', class: 'btn ghost', onclick: passwordDialog }, I('key'), 'Passwort ändern'),
      h('button', { type: 'button', class: 'btn ghost', onclick: () => window.dispatchEvent(new Event('pdu:logout')) }, I('logout'), 'Abmelden')));
}

/** Seitengerüst mit Seitenleiste; liefert das <main>. */
export function page(root, active) {
  const main = h('main', null, h('p', { class: 'muted' }, 'Lade …'));
  root.replaceChildren(h('div', { class: 'shell' }, sidebar(active), main));
  return main;
}

/**
 * PDU-Auswahl als Kartenleiste (gruppiert nach Standort) mit Live-Leistung je PDU.
 * onPick(key) wird beim Wechsel aufgerufen. Liefert { el, destroy }.
 */
export function picker(pdus, current, onPick) {
  const cards = new Map();
  const groups = [];
  for (const p of pdus) {
    let g = groups.find((x) => x.name === p.group);
    if (!g) groups.push(g = { name: p.group, items: [] });
    g.items.push(p);
  }
  const el = h('div', { class: 'picker', role: 'tablist', 'aria-label': 'PDU auswählen' }, groups.map((g) => h('div', { class: 'pgroup' },
    g.name && h('div', { class: 'pgname' }, g.name),
    h('div', { class: 'pgitems' }, g.items.map((p) => {
      const val = h('span', { class: 'pval num' }, '…');
      const dot = h('span', { class: 'pdot' });
      const sub = h('span', { class: 'psub' }, p.host.replace(/^https?:\/\//, ''));
      const btn = h('button', {
        type: 'button', role: 'tab', class: 'pcard' + (p.key === current ? ' on' : ''), 'aria-selected': p.key === current ? 'true' : 'false',
        onclick: () => { if (p.key !== current) onPick(p.key); },
      }, h('span', { class: 'ptop' }, dot, h('b', null, p.name)), sub, val);
      cards.set(p.key, { btn, val, dot, sub, p });
      return btn;
    })))));
  let timer = null;
  let dead = false;
  async function poll() {
    if (dead) return;
    if (!document.hidden) {
      try {
        const s = await api.summary();
        for (const [k, c] of cards) {
          const r = s[k];
          if (!r) continue;
          c.dot.className = 'pdot ' + (r.ok ? (r.health && r.health !== 'OK' ? 'warn' : 'ok') : 'bad');
          c.val.textContent = r.ok ? watts(r.power) : 'offline';
          c.val.classList.toggle('bad', !r.ok);
          c.btn.title = r.ok ? `${c.p.name} · ${r.model || ''}` : `${c.p.name}: ${r.error}`;
        }
      } catch { /* nächste Runde */ }
    }
    if (!dead) timer = setTimeout(poll, 10000);
  }
  poll();
  return { el, destroy() { dead = true; clearTimeout(timer); } };
}

/** Wählt die PDU aus der Route, sonst die zuletzt benutzte, sonst die erste. */
export function choosePdu(pdus, fromRoute) {
  let saved = null;
  try { saved = localStorage.getItem('pdu.last'); } catch { /* ignore */ }
  const key = [fromRoute, saved, pdus[0]?.key].find((k) => k && pdus.some((p) => p.key === k));
  if (key) { session.pdu = key; try { localStorage.setItem('pdu.last', key); } catch { /* ignore */ } }
  return key;
}

/** Wiederkehrender Abruf, pausiert im Hintergrund-Tab; now(...args) ruft sofort (mit Argumenten) auf. */
export function every(ms, fn) {
  let t = null;
  let dead = false;
  let busy = false;
  const run = async (...args) => {
    if (dead) return;
    clearTimeout(t);
    if (busy) { t = setTimeout(run, 300); return; }
    if (!document.hidden || args.length) {
      busy = true;
      try { await fn(...args); } catch (e) { console.error(e); } finally { busy = false; }
    }
    if (!dead) { clearTimeout(t); t = setTimeout(run, ms); }
  };
  const vis = () => { if (!document.hidden && !dead) run(); };
  document.addEventListener('visibilitychange', vis);
  run();
  return { stop() { dead = true; clearTimeout(t); document.removeEventListener('visibilitychange', vis); }, now: (...a) => run(...a) };
}

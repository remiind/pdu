// Redfish-Explorer (nur Admin): Rohdaten einer PDU ansehen, @odata.id-Links sind anklickbar
import { h } from './util.js';
import { api } from './api.js';
import { I, choosePdu, page, picker } from './shell.js';

const START = '/redfish/v1';
const SHORTCUTS = [
  ['Service Root', '/redfish/v1'],
  ['RackPDUs', '/redfish/v1/PowerEquipment/RackPDUs'],
  ['PDU', '/redfish/v1/PowerEquipment/RackPDUs/1'],
  ['Mains', '/redfish/v1/PowerEquipment/RackPDUs/1/Mains'],
  ['Branches', '/redfish/v1/PowerEquipment/RackPDUs/1/Branches'],
  ['Outlets', '/redfish/v1/PowerEquipment/RackPDUs/1/Outlets'],
  ['Outlet 1', '/redfish/v1/PowerEquipment/RackPDUs/1/Outlets/1'],
  ['Metrics', '/redfish/v1/PowerEquipment/RackPDUs/1/Metrics'],
  ['Sensors', '/redfish/v1/PowerEquipment/RackPDUs/1/Sensors'],
];

/** JSON als aufklappbarer Baum; Werte von @odata.id / target werden zu Links. */
function tree(v, go, depth = 0) {
  if (v === null || typeof v !== 'object') {
    const cls = v === null ? 'jn' : typeof v === 'string' ? 'js' : typeof v === 'number' ? 'jnum' : 'jb';
    return h('span', { class: cls }, typeof v === 'string' ? `"${v}"` : String(v));
  }
  const arr = Array.isArray(v);
  const entries = arr ? v.map((x, i) => [i, x]) : Object.entries(v);
  if (!entries.length) return h('span', { class: 'jp' }, arr ? '[]' : '{}');
  const rows = entries.map(([k, x]) => {
    const isLink = typeof x === 'string' && (k === '@odata.id' || k === 'target' || k === 'Location') && x.startsWith('/redfish/');
    return h('div', { class: 'jrow' },
      !arr && h('span', { class: 'jk' }, `"${k}"`), !arr && h('span', { class: 'jp' }, ': '),
      isLink ? h('a', { href: '#', class: 'js jl', onclick: (e) => { e.preventDefault(); go(x); } }, `"${x}"`) : tree(x, go, depth + 1));
  });
  const box = h('div', { class: 'jbox' }, rows);
  const det = h('details', { class: 'jdet', open: depth < 2 }, h('summary', null, h('span', { class: 'jp' }, arr ? `[ ${entries.length} ]` : `{ ${entries.length} }`)), box);
  return det;
}

export async function renderExplorer(root, routeKey, initialPath) {
  const main = page(root, 'explorer');
  let list;
  try { list = await api.pdus(); } catch (e) { main.replaceChildren(h('div', { class: 'flash err' }, e.message)); return null; }
  if (!list.pdus.length) { main.replaceChildren(h('div', { class: 'card' }, 'Keine PDUs eingerichtet.')); return null; }
  const key = choosePdu(list.pdus, routeKey);
  const pick = picker(list.pdus, key, (k) => { location.hash = `#/explorer/${k}`; });
  const path = h('input', { value: initialPath || START, class: 'mono', 'aria-label': 'Redfish-Pfad', spellcheck: 'false' });
  const out = h('div', { class: 'card jcard' }, h('p', { class: 'muted' }, 'Lade …'));
  const back = [];
  async function go(p, push = true) {
    if (push && path.value && path.value !== p) back.push(path.value);
    path.value = p;
    out.replaceChildren(h('p', { class: 'muted' }, 'Lade …'));
    try {
      const j = await api.raw(key, p);
      const raw = JSON.stringify(j, null, 2);
      out.replaceChildren(
        h('div', { class: 'jtools' },
          h('button', { type: 'button', class: 'btn', disabled: !back.length, onclick: () => go(back.pop(), false) }, 'Zurück'),
          h('button', { type: 'button', class: 'btn', onclick: () => { navigator.clipboard?.writeText(raw); } }, 'JSON kopieren')),
        h('div', { class: 'jtree mono' }, tree(j, go)));
    } catch (e) { out.replaceChildren(h('div', { class: 'flash err' }, e.message)); }
  }
  main.replaceChildren(
    h('header', { class: 'page' }, h('h1', null, 'Redfish-Explorer'), h('p', { class: 'sub' }, 'Rohdaten der PDU lesen (nur GET). Hilfreich, wenn ein Wert in Monitoring oder Steuerung fehlt.')),
    pick.el,
    h('form', { class: 'card xform', onsubmit: (e) => { e.preventDefault(); go(path.value.trim()); } },
      h('div', { class: 'xrow' }, path, h('button', { class: 'primary', type: 'submit' }, I('search'), 'Abrufen')),
      h('div', { class: 'views' }, SHORTCUTS.map(([l, p]) => h('a', { href: '#', dataset: { p }, onclick: (e) => { e.preventDefault(); go(e.currentTarget.dataset.p); } }, l)))),
    out);
  // Kurzwahl auf den tatsächlichen PDU-Pfad dieser PDU anpassen (Id ist nicht immer „1“)
  try {
    const col = await api.raw(key, '/redfish/v1/PowerEquipment/RackPDUs');
    const real = col.Members?.[0]?.['@odata.id'];
    if (real) main.querySelectorAll('.views a').forEach((a) => { a.dataset.p = a.dataset.p.replace('/redfish/v1/PowerEquipment/RackPDUs/1', real); });
  } catch { /* Kurzwahl bleibt beim Standard */ }
  go(path.value, false);
  return { destroy() { pick.destroy(); } };
}

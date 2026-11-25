// Kleine UI-Bausteine: Dialoge und Hinweise (ohne Inline-Styles, CSP-tauglich)
import { h } from './util.js';

/** Dialog mit Titel, Inhalt und Knöpfen; liefert Promise<Wert|null>. */
export function modal({ title, body, actions }) {
  return new Promise((resolve) => {
    const close = (v) => { overlay.remove(); window.removeEventListener('keydown', onKey, true); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } };
    const btns = actions.map((a) => h('button', { type: 'button', class: 'btn ' + (a.cls || ''), onclick: () => close(a.value ?? null) }, a.label));
    const card = h('div', { class: 'card modal-card', role: 'dialog', 'aria-modal': 'true' }, h('h2', null, title), body, h('div', { class: 'modal-actions' }, btns));
    const overlay = h('div', { class: 'modal', onpointerdown: (e) => { if (e.target === overlay) close(null); } }, card);
    window.addEventListener('keydown', onKey, true);
    document.body.append(overlay);
    setTimeout(() => (card.querySelector('input,textarea') || btns[btns.length - 1]).focus(), 0);
    overlay.close = close;
  });
}

export async function confirmBox(title, text, okLabel = 'Löschen', danger = true) {
  const r = await modal({
    title, body: h('p', { class: 'modal-text' }, text),
    actions: [{ label: 'Abbrechen', value: false }, { label: okLabel, value: true, cls: danger ? 'primary danger' : 'primary' }],
  });
  return r === true;
}

export async function promptBox(title, value = '', label = 'Name') {
  const input = h('input', { value, 'aria-label': label });
  const form = h('label', null, label, input);
  let res = null;
  const p = modal({ title, body: form, actions: [{ label: 'Abbrechen', value: null }, { label: 'OK', value: '__ok', cls: 'primary' }] });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); document.querySelector('.modal .btn.primary').click(); } });
  res = await p;
  return res === '__ok' ? input.value.trim() : null;
}

let toastEl;
export function toast(text, kind = 'ok') {
  toastEl?.remove();
  toastEl = h('div', { class: `flash ${kind} toast`, role: 'status' }, text);
  document.body.append(toastEl);
  setTimeout(() => toastEl?.remove(), 3500);
}


// Steuerung: Sockets einer PDU schalten (einzeln oder als Auswahl), beschriften, Verlauf
import { ago, fmt, h, time, watts } from './util.js';
import { api } from './api.js';
import { session } from './session.js';
import { confirmBox, promptBox, toast } from './ui.js';
import { I, choosePdu, every, page, picker } from './shell.js';
import { ico } from './icons.js';

const ACT_NAMES = { On: 'Einschalten', Off: 'Ausschalten', PowerCycle: 'Neu starten', Label: 'Beschriftet' };
const PREF = 'pdu.control';

function loadPrefs() {
  try { return { group: true, ...JSON.parse(localStorage.getItem(PREF) || '{}') }; } catch { return { group: true }; }
}
const savePrefs = (p) => { try { localStorage.setItem(PREF, JSON.stringify({ group: p.group })); } catch { /* ignore */ } };

const pad = (o) => String(o.num ?? o.id).padStart(2, '0');
const title = (o) => o.label || o.name || `Socket ${pad(o)}`;

export async function renderControl(root, routeKey) {
  const main = page(root, 'control');
  let list;
  try { list = await api.pdus(); } catch (e) { main.replaceChildren(h('div', { class: 'flash err' }, e.message)); return null; }
  if (!list.pdus.length) {
    main.replaceChildren(h('header', { class: 'page' }, h('h1', null, 'Steuerung')), h('div', { class: 'card' }, h('p', null, 'Es sind keine PDUs eingerichtet. Trage sie in der .env unter PDU_LIST ein (siehe README).')));
    return null;
  }
  const canControl = list.control;
  const key = choosePdu(list.pdus, routeKey);
  if (routeKey !== key) window.history.replaceState(null, '', `#/control/${key}`);
  const pick = picker(list.pdus, key, (k) => { location.hash = `#/control/${k}`; });

  const prefs = loadPrefs();
  const st = { q: '', show: 'all', phase: '', sel: new Set(), last: null, pending: new Set() };
  let snap = null;
  const tiles = new Map(); // Socket-ID → { el, update }
  let heads = []; // Gruppenköpfe { g, el } – Zahlen werden bei jeder Aktualisierung neu gesetzt
  let laidOut = ''; // sichtbare IDs beim letzten Aufbau

  // ---------- Kopf / Werkzeugleiste ----------
  const stamp = h('span', { class: 'muted small num' });
  const search = h('input', { type: 'search', placeholder: 'Nr., Beschreibung, Typ …', 'aria-label': 'Sockets durchsuchen', oninput: () => { st.q = search.value.trim().toLowerCase(); layout(); } });
  const showChips = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter Zustand' });
  const phaseChips = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter Phase' });
  const groupBox = h('input', { type: 'checkbox', checked: prefs.group, onchange: () => { prefs.group = groupBox.checked; savePrefs(prefs); layout(); } });
  const selAllBtn = h('button', { type: 'button', class: 'btn', onclick: () => { const vis = visible(); const all = vis.every((o) => st.sel.has(o.id)); vis.forEach((o) => (all ? st.sel.delete(o.id) : st.sel.add(o.id))); refresh(); } }, I('check'), 'Alle sichtbaren');
  const toolbar = h('div', { class: 'card ctool' },
    h('div', { class: 'ctool-row' }, h('label', { class: 'csearch' }, I('search'), search), showChips, phaseChips,
      h('label', { class: 'check' }, groupBox, h('span', null, 'Nach Phase gruppieren')), h('span', { class: 'grow' }), canControl && selAllBtn));

  const selCount = h('b', { class: 'num' });
  const selBar = h('div', { class: 'selbar', hidden: true, role: 'region', 'aria-label': 'Auswahl' },
    h('span', null, selCount, ' ausgewählt'),
    h('button', { type: 'button', class: 'btn primary', onclick: () => act([...st.sel], 'On', true) }, I('power'), 'Einschalten'),
    h('button', { type: 'button', class: 'btn danger', onclick: () => act([...st.sel], 'Off', true) }, I('power'), 'Ausschalten'),
    h('button', { type: 'button', class: 'btn', onclick: () => act([...st.sel], 'PowerCycle', true) }, I('cycle'), 'Neu starten'),
    h('button', { type: 'button', class: 'icb', title: 'Auswahl aufheben', 'aria-label': 'Auswahl aufheben', html: ico('x'), onclick: () => { st.sel.clear(); refresh(); } }));

  const grid = h('div', { class: 'cgrid' }, h('p', { class: 'muted' }, 'Lade Sockets …'));
  const head = h('div', { class: 'cinfo' });
  const logBody = h('div', { class: 'scroll' });
  main.classList.add('cmain');
  main.replaceChildren(...[
    h('header', { class: 'page phead' }, h('div', null, h('h1', null, 'Steuerung'), h('p', { class: 'sub' }, 'Sockets per Klick schalten · Mehrfachauswahl mit Klick auf die Kachel (Umschalt = Bereich)')),
      h('div', { class: 'phead-r' }, stamp, h('button', { type: 'button', class: 'btn', onclick: () => loop.now(true) }, I('refresh'), 'Aktualisieren'))),
    pick.el,
    !canControl && h('div', { class: 'flash warn' }, 'Dein Konto darf die Sockets nur ansehen, nicht schalten.'),
    head, toolbar, grid,
    h('div', { class: 'card flush logcard' }, h('div', { class: 'cardhead' }, h('h2', null, 'Letzte Schaltvorgänge'), I('history')), logBody),
    selBar].filter(Boolean));

  // ---------- Filter ----------
  function visible() {
    if (!snap) return [];
    return snap.outlets.filter((o) => (st.show === 'all' || (st.show === 'on' ? o.state === 'On' : o.state !== 'On'))
      && (!st.phase || o.phase === st.phase)
      && (!st.q || `${pad(o)} ${o.num} ${o.label} ${o.name} ${o.type || ''} ${o.phase || ''}`.toLowerCase().includes(st.q)));
  }

  function chips() {
    const n = { all: snap.outlets.length, on: snap.outlets.filter((o) => o.state === 'On').length };
    n.off = n.all - n.on;
    showChips.replaceChildren(...[['all', 'Alle'], ['on', 'An'], ['off', 'Aus']].map(([k, l]) => h('button', {
      type: 'button', class: st.show === k ? 'on' : '', 'aria-pressed': st.show === k ? 'true' : 'false', onclick: () => { st.show = k; layout(); },
    }, l, h('span', { class: 'n' }, String(n[k])))));
    const phases = [...new Set(snap.outlets.map((o) => o.phase).filter(Boolean))].sort();
    phaseChips.hidden = phases.length < 2;
    groupBox.parentElement.hidden = phases.length < 2;
    phaseChips.replaceChildren(...[['', 'Alle Phasen'], ...phases.map((p) => [p, p])].map(([k, l]) => h('button', {
      type: 'button', class: st.phase === k ? 'on' : '', 'aria-pressed': st.phase === k ? 'true' : 'false', onclick: () => { st.phase = k; layout(); },
    }, l)));
  }

  // ---------- Kacheln ----------
  function makeTile(o0) {
    let o = o0;
    const num = h('span', { class: 'snum num' });
    const type = h('span', { class: 'tchip' });
    const ph = h('span', { class: 'sph' });
    const label = h('div', { class: 'slabel' });
    const stText = h('span', { class: 'sstate' }, h('i'), h('span'));
    const pw = h('span', { class: 'spow num' });
    const sw = h('button', { type: 'button', class: 'switch', role: 'switch', disabled: !canControl, onclick: (e) => { e.stopPropagation(); act([o.id], o.state === 'On' ? 'Off' : 'On'); } }, h('span', { class: 'knob' }));
    const lock = h('span', { class: 'slock', title: 'Schalten an der PDU gesperrt', html: ico('lock'), hidden: true });
    const mk = (icon, tip, fn) => h('button', { type: 'button', class: 'icb', title: tip, 'aria-label': tip, html: ico(icon), disabled: !canControl, onclick: (e) => { e.stopPropagation(); fn(); } });
    const btnEdit = mk('pencil', 'Beschreibung ändern', () => rename(o));
    const btnCycle = mk('cycle', 'Neu starten (aus und wieder an)', () => act([o.id], 'PowerCycle'));
    const selDot = h('span', { class: 'ssel', 'aria-hidden': 'true', html: ico('check') });
    const el = h('div', {
      class: 'sock', tabindex: canControl ? '0' : '-1',
      onclick: (e) => { if (canControl) toggleSel(o, e.shiftKey); },
      onkeydown: (e) => { if (canControl && (e.key === ' ' || e.key === 'Enter') && e.target === el) { e.preventDefault(); toggleSel(o, e.shiftKey); } },
    },
    h('div', { class: 'srow' }, selDot, num, type, ph, h('span', { class: 'grow' }), lock, btnEdit, btnCycle),
    label,
    h('div', { class: 'srow sbot' }, stText, pw, h('span', { class: 'grow' }), sw));
    const update = (n) => {
      o = n;
      const on = o.state === 'On';
      const pending = st.pending.has(o.id);
      el.className = 'sock ' + (on ? 'on' : o.state === 'Off' ? 'off' : 'unk') + (st.sel.has(o.id) ? ' sel' : '') + (pending ? ' pending' : '') + (o.locked ? ' locked' : '');
      el.setAttribute('aria-label', `Socket ${pad(o)} ${title(o)}, ${on ? 'an' : 'aus'}${st.sel.has(o.id) ? ', ausgewählt' : ''}`);
      num.textContent = pad(o);
      type.textContent = o.type || '';
      type.hidden = !o.type;
      ph.textContent = o.phase || '';
      ph.hidden = !o.phase;
      label.replaceChildren(o.label ? o.label : h('span', { class: 'muted' }, o.name || `Socket ${pad(o)}`));
      label.title = o.label ? `${o.label} (${o.name || o.id})` : '';
      stText.className = 'sstate ' + (on ? 'on' : o.state === 'Off' ? 'off' : '');
      stText.lastChild.textContent = pending ? 'schaltet …' : on ? 'An' : o.state === 'Off' ? 'Aus' : (o.state || '–');
      pw.textContent = on && o.power !== null ? `${watts(o.power)}${o.current !== null ? ` · ${fmt(o.current, 2)} A` : ''}` : '';
      sw.setAttribute('aria-checked', on ? 'true' : 'false');
      sw.setAttribute('aria-label', `Socket ${pad(o)} ${on ? 'ausschalten' : 'einschalten'}`);
      sw.title = on ? 'Ausschalten' : 'Einschalten';
      sw.disabled = !canControl || pending || o.locked;
      btnCycle.disabled = !canControl || pending || o.locked;
      lock.hidden = !o.locked;
    };
    update(o);
    return { el, update };
  }

  function toggleSel(o, range) {
    const vis = visible();
    if (range && st.last) {
      const a = vis.findIndex((x) => x.id === st.last), b = vis.findIndex((x) => x.id === o.id);
      if (a >= 0 && b >= 0) {
        const on = !st.sel.has(o.id);
        vis.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((x) => (on ? st.sel.add(x.id) : st.sel.delete(x.id)));
      }
    } else if (st.sel.has(o.id)) st.sel.delete(o.id);
    else st.sel.add(o.id);
    st.last = o.id;
    refresh();
  }

  function layout() {
    if (!snap) return;
    chips();
    const vis = visible();
    laidOut = vis.map((o) => o.id).join(',');
    heads = [];
    for (const o of snap.outlets) if (!tiles.has(o.id)) tiles.set(o.id, makeTile(o));
    if (!vis.length) { grid.replaceChildren(h('div', { class: 'card empty-card' }, h('p', { class: 'muted' }, 'Kein Socket passt zum Filter.'))); refresh(); return; }
    const groups = prefs.group && !st.phase && snap.outlets.some((o) => o.phase) ? [...new Set(vis.map((o) => o.phase || '–'))].sort() : [null];
    grid.replaceChildren(...groups.map((g) => {
      const items = g === null ? vis : vis.filter((o) => (o.phase || '–') === g);
      const info = h('span', { class: 'muted small num' });
      if (g !== null) heads.push({ g, el: info });
      return h('section', { class: 'cgroup' },
        g !== null && h('div', { class: 'cghead' }, h('b', null, g === '–' ? 'Ohne Phase' : `Phase ${g}`), info),
        h('div', { class: 'socks' }, items.map((o) => tiles.get(o.id).el)));
    }));
    refresh();
  }

  function refresh() {
    if (!snap) return;
    for (const o of snap.outlets) tiles.get(o.id)?.update(o);
    for (const id of [...st.sel]) if (!snap.outlets.some((o) => o.id === id)) st.sel.delete(id);
    selBar.hidden = !st.sel.size;
    selCount.textContent = String(st.sel.size);
    const vis = visible();
    for (const { g, el } of heads) {
      const items = vis.filter((o) => (o.phase || '–') === g);
      const sum = items.reduce((a, o) => a + (o.state === 'On' && o.power ? o.power : 0), 0);
      const ph = snap.phases.find((p) => p.name === g);
      el.textContent = `${items.length} Sockets · ${items.filter((o) => o.state === 'On').length} an · ${watts(ph?.power ?? sum)}${ph && ph.current !== null ? ` · ${fmt(ph.current, 2)} A` : ''}`;
    }
    selAllBtn.replaceChildren(I('check'), vis.length && vis.every((o) => st.sel.has(o.id)) ? 'Auswahl aufheben' : 'Alle sichtbaren');
  }

  function drawHead() {
    const i = snap.info;
    head.replaceChildren(h('div', { class: 'card unitcard' }, h('div', { class: 'unit-head' },
      h('div', null, h('h2', null, snap.name), h('p', { class: 'muted small' }, [i.model, i.firmware && `FW ${i.firmware}`, snap.host].filter(Boolean).join(' · '))),
      h('div', { class: 'cstats' },
        h('div', null, h('b', { class: 'num' }, `${snap.counts.on}`), h('span', null, 'an')),
        h('div', null, h('b', { class: 'num' }, `${snap.counts.off}`), h('span', null, 'aus')),
        h('div', null, h('b', { class: 'num' }, watts(snap.totals.power)), h('span', null, 'gesamt')),
        h('a', { class: 'btn', href: `#/monitor/${snap.key}` }, I('pulse'), 'Monitoring')))));
  }

  // ---------- Aktionen ----------
  async function act(ids, action, fromSel = false) {
    if (!canControl || !ids.length || !snap) return;
    const outs = ids.map((id) => snap.outlets.find((o) => o.id === id)).filter(Boolean).filter((o) => !o.locked);
    if (!outs.length) { toast('Die gewählten Sockets sind gesperrt.', 'err'); return; }
    if (action !== 'On') {
      const names = outs.slice(0, 8).map((o) => `${pad(o)} „${title(o)}“`).join(', ') + (outs.length > 8 ? ` und ${outs.length - 8} weitere` : '');
      const what = outs.length === 1 ? `Socket ${names}` : `${outs.length} Sockets (${names})`;
      const txt = action === 'Off'
        ? `${what} auf ${snap.name} ausschalten? Angeschlossene Geräte verlieren sofort den Strom.`
        : `${what} auf ${snap.name} kurz aus- und wieder einschalten?`;
      if (!await confirmBox(action === 'Off' ? 'Ausschalten?' : 'Neu starten?', txt, ACT_NAMES[action], action === 'Off')) return;
    }
    outs.forEach((o) => st.pending.add(o.id));
    refresh();
    try {
      if (outs.length === 1) {
        await api.power(key, outs[0].id, action);
        toast(`${pad(outs[0])} ${title(outs[0])}: ${action === 'On' ? 'eingeschaltet' : action === 'Off' ? 'ausgeschaltet' : 'wird neu gestartet'}.`);
      } else {
        const r = await api.bulk(key, outs.map((o) => o.id), action);
        const bad = Object.entries(r.results).filter(([, v]) => !v.ok);
        if (bad.length) toast(`${outs.length - bad.length} von ${outs.length} geschaltet – Fehler: ${bad[0][1].error}`, 'err');
        else toast(`${outs.length} Sockets: ${ACT_NAMES[action].toLowerCase()} erledigt.`);
      }
      if (action !== 'PowerCycle') outs.forEach((o) => { o.state = action; });
      if (fromSel) st.sel.clear();
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      outs.forEach((o) => st.pending.delete(o.id));
      refresh();
      setTimeout(() => loop.now(true), 700);
      if (action === 'PowerCycle') setTimeout(() => loop.now(true), 6000);
      loadLog();
    }
  }

  async function rename(o) {
    const v = await promptBox(`Beschreibung für Socket ${pad(o)}`, o.label || '', 'Beschreibung (leer = keine)');
    if (v === null || v === (o.label || '')) return;
    try { await api.label(key, o.id, v); o.label = v; refresh(); toast('Beschreibung gespeichert.'); loop.now(true); loadLog(); } catch (e) { toast(e.message, 'err'); }
  }

  async function loadLog() {
    try {
      const rows = (await api.log(key)).slice(0, 20);
      logBody.replaceChildren(rows.length ? h('table', { class: 'data' },
        h('thead', null, h('tr', null, h('th', null, 'Zeit'), h('th', null, 'Benutzer'), h('th', null, 'Socket'), h('th', null, 'Aktion'), h('th', null, 'Ergebnis'))),
        h('tbody', null, rows.map((e) => h('tr', null,
          h('td', { class: 'num', title: time(e.ts) }, ago(e.ts)),
          h('td', null, e.user),
          h('td', null, h('span', { class: 'mono' }, String(e.outlet).padStart(2, '0')), e.label && e.action !== 'Label' ? h('span', { class: 'muted' }, ` ${e.label}`) : null),
          h('td', null, ACT_NAMES[e.action] || e.action, e.action === 'Label' && e.label ? h('span', { class: 'muted' }, ` ${e.label}`) : null),
          h('td', null, e.ok ? h('span', { class: 'badge valid' }, 'OK') : h('span', { class: 'badge revoked', title: e.error || '' }, 'Fehler'))))))
        : h('p', { class: 'pad muted' }, 'Noch keine Schaltvorgänge auf dieser PDU.'));
    } catch { /* ignore */ }
  }

  let firstLayout = true;
  async function load(force) {
    try {
      snap = await api.pdu(key, force === true);
      session.snap = snap;
      drawHead();
      if (firstLayout || visible().map((o) => o.id).join(',') !== laidOut) { firstLayout = false; layout(); } else { chips(); refresh(); }
      stamp.textContent = `Stand ${new Date(snap.ts * 1000).toLocaleTimeString('de-DE')}`;
      stamp.classList.remove('bad-t');
    } catch (e) {
      if (!snap) grid.replaceChildren(h('div', { class: 'card err-card' }, h('h2', null, 'PDU nicht erreichbar'), h('p', null, e.message), h('p', { class: 'muted small' }, 'Neuer Versuch in wenigen Sekunden …')));
      else { stamp.textContent = `Keine Verbindung (${e.message}) · letzter Stand ${ago(snap.ts)}`; stamp.classList.add('bad-t'); }
    }
  }
  const loop = every(5000, (force) => load(force));
  const logLoop = every(20000, loadLog);
  const onKey = (e) => { if (e.key === 'Escape' && st.sel.size && !document.querySelector('.modal')) { st.sel.clear(); refresh(); } };
  window.addEventListener('keydown', onKey);
  return { destroy() { loop.stop(); logLoop.stop(); pick.destroy(); window.removeEventListener('keydown', onKey); } };
}

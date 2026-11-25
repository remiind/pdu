// Steuerung: Sockets einer PDU schalten (einzeln oder als Auswahl), benennen, gruppieren, Verlauf
import { ago, fmt, groupColor, h, setGroupColors, time, watts } from './util.js';
import { api } from './api.js';
import { session } from './session.js';
import { confirmBox, modal, toast } from './ui.js';
import { I, choosePdu, every, page, picker } from './shell.js';
import { ico } from './icons.js';

const ACT_NAMES = { On: 'Einschalten', Off: 'Ausschalten', PowerCycle: 'Neu starten', Label: 'Umbenannt', Group: 'Gruppe' };
const PREF = 'pdu.control2'; // neuer Schlüssel: Standardansicht ist jetzt „Einbau“
const NAME_RE = /^[-_ a-zA-Z0-9]*$/; // Regeln der Rittal-PDU (DescName)
const VIEWS = [['rack', 'Einbau'], ['phase', 'Phase'], ['group', 'Gruppe'], ['none', 'Liste']];

function loadPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem(PREF) || '{}'); } catch { /* ignore */ }
  if (!VIEWS.some(([k]) => k === p.view)) p.view = 'rack';
  return p;
}
const savePrefs = (p) => { try { localStorage.setItem(PREF, JSON.stringify({ view: p.view })); } catch { /* ignore */ } };

const pad = (o) => String(o.num ?? o.id).padStart(2, '0');
const title = (o) => o.label || o.name || `Socket ${pad(o)}`;

/** Eingabefeld mit Vorschlagsliste vorhandener Gruppen. */
function groupInput(value, groups) {
  const id = 'grp-' + Math.random().toString(36).slice(2, 8);
  const input = h('input', { value: value || '', list: id, maxlength: '32', autocomplete: 'off', 'aria-label': 'Gruppe' });
  return { input, list: h('datalist', { id }, groups.map((g) => h('option', { value: g }))) };
}

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
  let heads = []; // Abschnittsköpfe { kind, g, items, el }
  let laidOut = ''; // Aufbau-Schlüssel (Ansicht + Abschnitte + sichtbare IDs)

  // ---------- Kopf / Werkzeugleiste ----------
  const stamp = h('span', { class: 'muted small num' });
  const search = h('input', { type: 'search', placeholder: 'Nr., Name, Gruppe, Typ …', 'aria-label': 'Sockets durchsuchen', oninput: () => { st.q = search.value.trim().toLowerCase(); layout(); } });
  const showChips = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter Zustand' });
  const phaseChips = h('div', { class: 'seg', role: 'group', 'aria-label': 'Filter Phase' });
  const viewChips = h('div', { class: 'seg', role: 'group', 'aria-label': 'Ansicht' });
  const stats = h('span', { class: 'cstat num' });
  const orientBtn = h('button', { type: 'button', class: 'btn sm', title: 'Einbaulage der PDU: welche Socket-Nummer oben ist (gilt für alle Benutzer)', onclick: () => toggleOrient() });
  const selAllBtn = h('button', { type: 'button', class: 'btn sm', onclick: () => { const vis = visible(); const all = vis.every((o) => st.sel.has(o.id)); vis.forEach((o) => (all ? st.sel.delete(o.id) : st.sel.add(o.id))); refresh(); } }, I('check'), 'Alle sichtbaren');
  const toolbar = h('div', { class: 'card ctool' },
    h('div', { class: 'ctool-row' }, h('label', { class: 'csearch' }, I('search'), search), showChips, phaseChips,
      h('span', { class: 'clabel' }, 'Ansicht'), viewChips, orientBtn, h('span', { class: 'grow' }), stats, canControl && selAllBtn));

  const selCount = h('b', { class: 'num' });
  const selBar = h('div', { class: 'selbar', hidden: true, role: 'region', 'aria-label': 'Auswahl' },
    h('span', null, selCount, ' ausgewählt'),
    h('button', { type: 'button', class: 'btn primary', onclick: () => act([...st.sel], 'On', true) }, I('power'), 'Einschalten'),
    h('button', { type: 'button', class: 'btn danger', onclick: () => act([...st.sel], 'Off', true) }, I('power'), 'Ausschalten'),
    h('button', { type: 'button', class: 'btn', onclick: () => act([...st.sel], 'PowerCycle', true) }, I('cycle'), 'Neu starten'),
    h('button', { type: 'button', class: 'btn', onclick: () => groupDialog([...st.sel]) }, I('grid'), 'Gruppe …'),
    h('button', { type: 'button', class: 'icb', title: 'Auswahl aufheben (Esc)', 'aria-label': 'Auswahl aufheben', html: ico('x'), onclick: () => { st.sel.clear(); refresh(); } }));

  const grid = h('div', { class: 'cgrid' }, h('p', { class: 'muted' }, 'Lade Sockets …'));
  const logBody = h('div', { class: 'scroll' });
  main.classList.add('cmain');
  main.replaceChildren(...[
    h('header', { class: 'page phead' }, h('div', null, h('h1', null, 'Steuerung'), h('p', { class: 'sub' }, 'Schalter = sofort schalten · Klick auf die Kachel = auswählen (Umschalt = Bereich) · Doppelklick = bearbeiten')),
      h('div', { class: 'phead-r' }, stamp, h('button', { type: 'button', class: 'btn', onclick: () => loop.now(true) }, I('refresh'), 'Aktualisieren'))),
    pick.el,
    !canControl && h('div', { class: 'flash warn' }, 'Dein Konto darf die Sockets nur ansehen, nicht schalten.'),
    toolbar, grid,
    h('div', { class: 'card flush logcard' }, h('div', { class: 'cardhead' }, h('h2', null, 'Letzte Vorgänge'), I('history')), logBody),
    selBar].filter(Boolean));

  // ---------- Filter ----------
  function visible() {
    if (!snap) return [];
    return snap.outlets.filter((o) => (st.show === 'all' || (st.show === 'on' ? o.state === 'On' : o.state !== 'On'))
      && (!st.phase || o.phase === st.phase)
      && (!st.q || `${pad(o)} ${o.num} ${o.label} ${o.name} ${o.group || ''} ${o.type || ''} ${o.phase || ''}`.toLowerCase().includes(st.q)));
  }

  const segBtn = (on, label, fn, extra) => h('button', { type: 'button', class: on ? 'on' : '', 'aria-pressed': on ? 'true' : 'false', onclick: fn }, label, extra);
  function chips() {
    const n = { all: snap.outlets.length, on: snap.outlets.filter((o) => o.state === 'On').length };
    n.off = n.all - n.on;
    showChips.replaceChildren(...[['all', 'Alle'], ['on', 'An'], ['off', 'Aus']].map(([k, l]) => segBtn(st.show === k, l, () => { st.show = k; layout(); }, h('span', { class: 'n' }, String(n[k])))));
    const phases = [...new Set(snap.outlets.map((o) => o.phase).filter(Boolean))].sort();
    phaseChips.hidden = phases.length < 2;
    phaseChips.replaceChildren(...[['', 'Alle Phasen'], ...phases.map((p) => [p, p])].map(([k, l]) => segBtn(st.phase === k, l, () => { st.phase = k; layout(); })));
    const orient = snap.settings?.orient || 'desc';
    const nums = snap.outlets.map((o) => o.num).filter((x) => x !== null && x !== undefined);
    const hi = nums.length ? Math.max(...nums) : 0, lo = nums.length ? Math.min(...nums) : 0;
    orientBtn.replaceChildren(I('swap'), `${String(orient === 'desc' ? hi : lo).padStart(2, '0')} oben`);
    orientBtn.hidden = prefs.view !== 'rack';
    orientBtn.disabled = !canControl;
    viewChips.replaceChildren(...VIEWS.filter(([k]) => !['phase', 'rack'].includes(k) || phases.length > 1 || k === 'rack').map(([k, l]) => segBtn(prefs.view === k, l, () => { prefs.view = k; savePrefs(prefs); layout(); })));
    stats.textContent = `${n.on} an · ${n.off} aus · ${watts(snap.totals.power)}`;
  }

  // ---------- Kacheln (kompakt: 2 Zeilen) ----------
  function makeTile(o0) {
    let o = o0;
    const num = h('span', { class: 'snum num' });
    const label = h('span', { class: 'slabel' });
    const type = h('span', { class: 'tchip' });
    const grp = h('span', { class: 'gchip' });
    const ph = h('span', { class: 'sph' });
    const info = h('span', { class: 'sinfo num' });
    const sw = h('button', { type: 'button', class: 'switch sm', role: 'switch', disabled: !canControl, onclick: (e) => { e.stopPropagation(); act([o.id], o.state === 'On' ? 'Off' : 'On'); } }, h('span', { class: 'knob' }));
    const lock = h('span', { class: 'slock', title: 'Schalten an der PDU gesperrt', html: ico('lock'), hidden: true });
    const mk = (icon, tip, fn) => h('button', { type: 'button', class: 'icb', title: tip, 'aria-label': tip, html: ico(icon), disabled: !canControl, onclick: (e) => { e.stopPropagation(); fn(); } });
    const btnEdit = mk('pencil', 'Name und Gruppe bearbeiten', () => editDialog(o));
    const btnCycle = mk('cycle', 'Neu starten (aus und wieder an)', () => act([o.id], 'PowerCycle'));
    const selDot = h('span', { class: 'ssel', 'aria-hidden': 'true', html: ico('check') });
    const el = h('div', {
      class: 'sock', tabindex: canControl ? '0' : '-1',
      onclick: (e) => { if (canControl) toggleSel(o, e.shiftKey); },
      ondblclick: (e) => { if (canControl) { e.preventDefault(); editDialog(o); } },
      onkeydown: (e) => { if (canControl && (e.key === ' ' || e.key === 'Enter') && e.target === el) { e.preventDefault(); toggleSel(o, e.shiftKey); } },
    },
    h('div', { class: 'srow' }, selDot, num, label, sw),
    h('div', { class: 'srow s2' }, type, ph, grp, info, h('span', { class: 'grow' }), lock, btnEdit, btnCycle));
    const update = (n) => {
      o = n;
      const on = o.state === 'On';
      const pending = st.pending.has(o.id);
      el.className = 'sock ' + (on ? 'on' : o.state === 'Off' ? 'off' : 'unk') + (st.sel.has(o.id) ? ' sel' : '') + (pending ? ' pending' : '') + (o.locked ? ' locked' : '');
      el.setAttribute('aria-label', `Socket ${pad(o)} ${title(o)}${o.group ? `, Gruppe ${o.group}` : ''}, ${on ? 'an' : 'aus'}${st.sel.has(o.id) ? ', ausgewählt' : ''}`);
      num.textContent = pad(o);
      label.textContent = title(o);
      label.title = [title(o), o.group && `Gruppe: ${o.group}`, o.name].filter(Boolean).join(' · ');
      type.textContent = o.type || '';
      type.hidden = !o.type;
      ph.textContent = o.phase || '';
      ph.hidden = !o.phase || prefs.view === 'phase' || prefs.view === 'rack';
      grp.textContent = o.group || '';
      grp.hidden = !o.group || prefs.view === 'group';
      const gc = groupColor(o.group);
      if (gc) { el.style.setProperty('--gc', gc); grp.style.setProperty('--gc', gc); } else { el.style.removeProperty('--gc'); }
      info.textContent = pending ? 'schaltet …' : on && o.power !== null ? `${watts(o.power)}` : o.state && o.state !== 'On' && o.state !== 'Off' ? o.state : '';
      sw.setAttribute('aria-checked', on ? 'true' : 'false');
      sw.setAttribute('aria-label', `Socket ${pad(o)} ${on ? 'ausschalten' : 'einschalten'}`);
      sw.title = on ? 'An – klicken zum Ausschalten' : 'Aus – klicken zum Einschalten';
      sw.disabled = !canControl || pending || o.locked;
      btnCycle.disabled = !canControl || pending || o.locked || !on;
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

  /** Abschnitte je Ansicht: [{kind, g, title, items}] */
  function sections(vis) {
    const view = prefs.view === 'phase' && st.phase ? 'none' : prefs.view;
    if (view === 'rack') {
      // wie montiert: je Phase eine Spalte, Spalten und Sockets in Einbaureihenfolge (desc = höchste Nummer oben)
      const desc = (snap.settings?.orient || 'desc') === 'desc';
      const by = (a, b) => (desc ? b.num - a.num : a.num - b.num);
      const groups = [...new Set(snap.outlets.map((o) => o.phase || '–'))].map((g) => {
        const all = snap.outlets.filter((o) => (o.phase || '–') === g).sort(by);
        return { kind: 'rack', g, title: g === '–' ? 'Ohne Phase' : `Phase ${g}`, range: all.length ? `${pad(all[0])}–${pad(all[all.length - 1])}` : '', key: all.length ? all[0].num : 0, items: vis.filter((o) => (o.phase || '–') === g).sort(by) };
      });
      return groups.sort((a, b) => (desc ? b.key - a.key : a.key - b.key));
    }
    if (view === 'phase' && vis.some((o) => o.phase)) {
      return [...new Set(vis.map((o) => o.phase || '–'))].sort().map((g) => ({ kind: 'phase', g, title: g === '–' ? 'Ohne Phase' : `Phase ${g}`, items: vis.filter((o) => (o.phase || '–') === g) }));
    }
    if (view === 'group') {
      const names = [...new Set(vis.map((o) => o.group).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'de', { numeric: true }));
      const out = names.map((g) => ({ kind: 'group', g, title: g, items: vis.filter((o) => o.group === g) }));
      const rest = vis.filter((o) => !o.group);
      if (rest.length) out.push({ kind: 'group', g: '', title: names.length ? 'Ohne Gruppe' : 'Noch keine Gruppen – Sockets auswählen und „Gruppe …“ wählen', items: rest });
      return out;
    }
    return [{ kind: 'none', g: null, title: null, items: vis }];
  }
  const layoutKey = (secs) => `${prefs.view}|${snap?.settings?.orient}|${secs.map((s) => `${s.g}:${s.items.map((o) => o.id).join(',')}`).join('|')}`;

  function layout() {
    if (!snap) return;
    chips();
    const vis = visible();
    const secs = sections(vis);
    laidOut = layoutKey(secs);
    heads = [];
    for (const o of snap.outlets) if (!tiles.has(o.id)) tiles.set(o.id, makeTile(o));
    if (!vis.length) { grid.replaceChildren(h('div', { class: 'card empty-card' }, h('p', { class: 'muted' }, 'Kein Socket passt zum Filter.'))); refresh(); return; }
    if (secs[0]?.kind === 'rack') {
      grid.replaceChildren(h('div', { class: 'rackgrid' }, secs.map((s) => {
        const info = h('span', { class: 'muted small num' });
        heads.push({ ...s, kind: 'phase', el: info });
        const stack = h('div', { class: 'rackstack' });
        let prevType = null;
        for (const o of s.items) { // kleine Lücke, wo der Socket-Typ wechselt (Blöcke wie auf der Leiste)
          if (prevType !== null && o.type !== prevType) stack.append(h('div', { class: 'rgap', 'aria-hidden': 'true' }));
          prevType = o.type;
          stack.append(tiles.get(o.id).el);
        }
        if (!s.items.length) stack.append(h('p', { class: 'muted small' }, 'Kein Socket passt zum Filter.'));
        return h('section', { class: 'rackcol' }, h('div', { class: 'cghead' }, h('b', null, s.title), h('span', { class: 'muted small num' }, s.range), info), stack);
      })));
      refresh();
      return;
    }
    grid.replaceChildren(...secs.map((s) => {
      const info = h('span', { class: 'muted small num' });
      if (s.title) heads.push({ ...s, el: info });
      const pickAll = s.kind === 'group' && s.g && canControl
        ? h('button', { type: 'button', class: 'linkbtn', onclick: () => { s.items.forEach((o) => st.sel.add(o.id)); refresh(); } }, 'Gruppe auswählen') : null;
      const dot = s.kind === 'group' ? h('span', { class: 'gdot' }) : null;
      if (dot) { if (s.g) dot.style.setProperty('--gc', groupColor(s.g)); else dot.classList.add('none'); }
      return h('section', { class: 'cgroup' },
        s.title && h('div', { class: 'cghead' }, dot, h('b', null, s.title), info, pickAll),
        h('div', { class: 'socks' }, s.items.map((o) => tiles.get(o.id).el)));
    }));
    refresh();
  }

  function refresh() {
    if (!snap) return;
    for (const o of snap.outlets) tiles.get(o.id)?.update(o);
    for (const id of [...st.sel]) if (!snap.outlets.some((o) => o.id === id)) st.sel.delete(id);
    selBar.hidden = !st.sel.size;
    selCount.textContent = String(st.sel.size);
    for (const s of heads) {
      const items = s.items.map((o) => snap.outlets.find((x) => x.id === o.id) || o);
      const onN = items.filter((o) => o.state === 'On').length;
      const ph = s.kind === 'phase' ? snap.phases.find((p) => p.name === s.g) : null;
      s.el.textContent = `${items.length} Sockets · ${onN} an` + (ph ? ` · ${watts(ph.power)}${ph.current !== null ? ` · ${fmt(ph.current, 2)} A` : ''}` : '');
    }
    const vis = visible();
    selAllBtn.replaceChildren(I('check'), vis.length && vis.every((o) => st.sel.has(o.id)) ? 'Auswahl aufheben' : 'Alle sichtbaren');
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
        toast(`${pad(outs[0])} ${title(outs[0])}: ${action === 'On' ? 'eingeschaltet' : action === 'Off' ? 'ausgeschaltet' : 'neu gestartet'}.`);
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
      loadLog();
    }
  }

  /** Name (auf der PDU) und Gruppe (in der App) eines Sockets bearbeiten. */
  async function editDialog(o) {
    const name = h('input', { value: o.label || '', maxlength: '20', autocomplete: 'off', 'aria-label': 'Name' });
    const gi = groupInput(o.group, snap.groups || []);
    const r = await modal({
      title: `Socket ${pad(o)} bearbeiten`,
      body: h('div', null,
        h('label', null, 'Name ', h('small', null, `(auf der PDU · max. 20 Zeichen: A–Z, 0–9, Leerzeichen, - und _ · leer = „Socket ${pad(o)}“)`), name),
        h('label', null, 'Gruppe ', h('small', null, '(in dieser App · leer = keine Gruppe)'), gi.input, gi.list)),
      actions: [{ label: 'Abbrechen', value: false }, { label: 'Speichern', value: true, cls: 'primary' }],
    });
    if (r !== true) return;
    const newName = name.value.trim();
    const newGroup = gi.input.value.trim().replace(/\s+/g, ' ');
    if (newName !== (o.label || '') && (newName.length > 20 || !NAME_RE.test(newName))) {
      toast('Name: höchstens 20 Zeichen, nur Buchstaben ohne Umlaute, Ziffern, Leerzeichen, - und _.', 'err');
      return;
    }
    try {
      if (newGroup !== (o.group || '')) {
        await api.group(key, [o.id], newGroup);
        o.group = newGroup;
        if (newGroup && !snap.groups.includes(newGroup)) snap.groups.push(newGroup);
      }
      if (newName !== (o.label || '')) { const res = await api.label(key, o.id, newName); o.label = res.label || newName; }
      st.sel.delete(o.id);
      toast(`Socket ${pad(o)} gespeichert.`);
    } catch (e) { toast(e.message, 'err'); }
    layout(); loop.now(true); loadLog();
  }

  async function toggleOrient() {
    if (!canControl || !snap) return;
    const next = (snap.settings?.orient || 'desc') === 'desc' ? 'asc' : 'desc';
    try {
      const r = await api.settings(key, { orient: next });
      snap.settings = r.settings;
      layout();
      toast(next === 'desc' ? 'Einbaulage: höchste Socket-Nummer oben.' : 'Einbaulage: Socket 01 oben.');
    } catch (e) { toast(e.message, 'err'); }
  }

  /** Gruppe für mehrere Sockets setzen (Auswahlleiste). */
  async function groupDialog(ids) {
    if (!ids.length) return;
    const outs = ids.map((id) => snap.outlets.find((o) => o.id === id)).filter(Boolean);
    const common = outs.every((o) => o.group === outs[0].group) ? outs[0].group : '';
    const gi = groupInput(common, snap.groups || []);
    const r = await modal({
      title: `Gruppe für ${outs.length} Socket${outs.length > 1 ? 's' : ''}`,
      body: h('div', null, h('p', { class: 'modal-text' }, outs.slice(0, 10).map((o) => `${pad(o)} ${title(o)}`).join(', ') + (outs.length > 10 ? ' …' : '')),
        h('label', null, 'Gruppe ', h('small', null, '(leer = aus der Gruppe nehmen)'), gi.input, gi.list)),
      actions: [{ label: 'Abbrechen', value: false }, { label: 'Zuweisen', value: true, cls: 'primary' }],
    });
    if (r !== true) return;
    const g = gi.input.value.trim().replace(/\s+/g, ' ');
    try {
      await api.group(key, outs.map((o) => o.id), g);
      outs.forEach((o) => { o.group = g; });
      if (g && !snap.groups.includes(g)) snap.groups.push(g);
      st.sel.clear();
      toast(g ? `${outs.length} Sockets in Gruppe „${g}“.` : `${outs.length} Sockets aus ihrer Gruppe genommen.`);
    } catch (e) { toast(e.message, 'err'); }
    layout(); loadLog(); loop.now(); // holt u. a. die vom Server vergebene Gruppenfarbe
  }

  async function loadLog() {
    try {
      const rows = (await api.log(key)).slice(0, 20);
      logBody.replaceChildren(rows.length ? h('table', { class: 'data' },
        h('thead', null, h('tr', null, h('th', null, 'Zeit'), h('th', null, 'Benutzer'), h('th', null, 'Socket'), h('th', null, 'Aktion'), h('th', null, 'Ergebnis'))),
        h('tbody', null, rows.map((e) => h('tr', null,
          h('td', { class: 'num', title: time(e.ts) }, ago(e.ts)),
          h('td', null, e.user),
          h('td', null, h('span', { class: 'mono' }, String(e.num ?? e.outlet).padStart(2, '0')), e.label && !['Label', 'Group'].includes(e.action) ? h('span', { class: 'muted' }, ` ${e.label}`) : null),
          h('td', null, ACT_NAMES[e.action] || e.action, ['Label', 'Group'].includes(e.action) && e.label ? h('span', { class: 'muted' }, ` ${e.label}`) : null),
          h('td', null, e.ok ? h('span', { class: 'badge valid' }, 'OK') : h('span', { class: 'badge revoked', title: e.error || '' }, 'Fehler'))))))
        : h('p', { class: 'pad muted' }, 'Noch keine Vorgänge auf dieser PDU.'));
    } catch { /* ignore */ }
  }

  let firstLayout = true;
  async function load(force) {
    try {
      snap = await api.pdu(key, force === true);
      session.snap = snap;
      setGroupColors(snap.groupColors);
      if (firstLayout || layoutKey(sections(visible())) !== laidOut) { firstLayout = false; layout(); } else { chips(); refresh(); }
      const stale = snap.age > 20;
      stamp.textContent = `Stand ${new Date(snap.ts * 1000).toLocaleTimeString('de-DE')}${stale ? ' · wird aktualisiert …' : ''}`;
      stamp.classList.remove('bad-t');
      if (stale) setTimeout(() => loop.now(), 1500); // gespeicherter Stand → gleich den frischen holen
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

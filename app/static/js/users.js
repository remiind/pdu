// Benutzerverwaltung (nur Administratoren)
import { h } from './util.js';
import { ico } from './icons.js';
import { api } from './api.js';
import { session } from './session.js';
import { confirmBox, modal, toast } from './ui.js';
import { I, page } from './shell.js';

async function userForm(title, { withName = true, withRights = false } = {}) {
  const name = h('input', { autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Benutzername' });
  const pw = h('input', { type: 'text', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Passwort' });
  const adm = h('input', { type: 'checkbox' });
  const ctl = h('input', { type: 'checkbox', checked: true });
  const gen = () => { const a = new Uint8Array(12); crypto.getRandomValues(a); return Array.from(a, (b) => 'abcdefghjkmnpqrstuvwxyz23456789'[b % 31]).join(''); };
  pw.value = gen();
  const body = h('div', null,
    withName && h('label', null, 'Benutzername', name),
    h('label', null, 'Passwort ', h('small', null, '(mind. 8 Zeichen – bitte dem Benutzer sicher weitergeben)'), pw),
    withRights && h('label', { class: 'check' }, ctl, h('span', null, 'Darf Sockets schalten')),
    withRights && h('label', { class: 'check' }, adm, h('span', null, 'Ist Administrator')));
  const r = await modal({ title, body, actions: [{ label: 'Abbrechen', value: false }, { label: 'OK', value: true, cls: 'primary' }] });
  return r === true ? { name: name.value.trim(), password: pw.value, admin: adm.checked, control: ctl.checked } : null;
}

export async function renderUsers(root) {
  const main = page(root, 'users');
  async function load() {
    let list;
    try { list = await api.adminUsers(); } catch (e) { main.replaceChildren(h('div', { class: 'flash err' }, e.message)); return; }
    const act = (icon, tip, fn, cls = '') => h('button', { type: 'button', class: 'icb ' + cls, title: tip, 'aria-label': tip, html: ico(icon), onclick: async () => { try { await fn(); } catch (e) { toast(e.message, 'err'); } } });
    const rows = list.map((u) => {
      const me = u.id === session.user.id;
      const ctl = h('input', { type: 'checkbox', checked: u.control, disabled: u.admin, 'aria-label': `${u.name} darf schalten`,
        onchange: async () => { try { await api.setUser(u.id, { control: ctl.checked }); toast(`„${u.name}“ ${ctl.checked ? 'darf jetzt schalten' : 'darf nur noch ansehen'}.`); } catch (e) { toast(e.message, 'err'); ctl.checked = !ctl.checked; } } });
      return h('tr', null,
        h('td', null, h('b', null, u.name), me && h('span', { class: 'badge valid ml' }, 'Du')),
        h('td', null, u.admin ? h('span', { class: 'badge expiring' }, 'Administrator') : h('span', { class: 'badge expired' }, 'Benutzer')),
        h('td', null, h('label', { class: 'check' }, ctl, h('span', null, u.admin ? 'immer' : 'Schalten'))),
        h('td', { class: 'muted' }, u.created ? new Date(u.created * 1000).toLocaleDateString('de-DE') : '–'),
        h('td', { class: 'ract' },
          act('key', 'Passwort zurücksetzen', async () => { const f = await userForm(`Neues Passwort für ${u.name}`, { withName: false }); if (f) { await api.setUser(u.id, { password: f.password }); toast('Passwort geändert – bestehende Sitzungen wurden abgemeldet.'); } }),
          act('users', u.admin ? 'Administratorrechte entziehen' : 'Zum Administrator machen', async () => { await api.setUser(u.id, { admin: !u.admin }); load(); }),
          !me && act('trash', 'Benutzer löschen', async () => {
            if (await confirmBox('Benutzer löschen?', `„${u.name}“ wird endgültig gelöscht.`)) { await api.delUser(u.id); load(); }
          }, 'danger')));
    });
    main.replaceChildren(
      h('header', { class: 'page' }, h('h1', null, 'Benutzer'), h('p', { class: 'sub' }, 'Benutzer ohne „Schalten“ sehen Monitoring und Steuerung, können aber nichts schalten oder umbenennen.')),
      h('div', { class: 'actions' }, h('button', { class: 'primary', type: 'button', onclick: async () => {
        const f = await userForm('Neuer Benutzer', { withRights: true });
        if (!f) return;
        try { await api.addUser(f); toast(`„${f.name}“ angelegt.`); load(); } catch (e) { toast(e.message, 'err'); }
      } }, I('plus'), 'Benutzer anlegen')),
      h('div', { class: 'card tablecard' }, h('table', { class: 'utable' },
        h('thead', null, h('tr', null, h('th', null, 'Name'), h('th', null, 'Rolle'), h('th', null, 'Rechte'), h('th', null, 'Angelegt'), h('th', null, ''))),
        h('tbody', null, rows))));
  }
  await load();
  return null;
}

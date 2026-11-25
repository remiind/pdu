// Anmelde-Maske; beim allerersten Start legt sie den Administrator an
import { h } from './util.js';
import { api } from './api.js';

/** Zeigt die Maske und liefert den angemeldeten Benutzer, sobald Login/Einrichtung gelungen ist. */
export function renderLogin(root, { setup }) {
  return new Promise((resolve) => {
    const name = h('input', { type: 'text', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', required: '', 'aria-label': 'Benutzername' });
    const pw = h('input', { type: 'password', autocomplete: setup ? 'new-password' : 'current-password', required: '', 'aria-label': 'Passwort' });
    const pw2 = setup ? h('input', { type: 'password', autocomplete: 'new-password', required: '', 'aria-label': 'Passwort wiederholen' }) : null;
    const err = h('div', { class: 'flash err', hidden: true });
    const btn = h('button', { class: 'primary', type: 'submit' }, setup ? 'Administrator anlegen' : 'Anmelden');
    const form = h('form', { class: 'loginform', novalidate: '' },
      setup && h('p', { class: 'hint' }, 'Willkommen! Das erste Konto wird zum Administrator und verwaltet die Benutzer.'),
      err,
      h('label', null, 'Benutzername', name),
      h('label', null, 'Passwort', setup && h('small', null, ' (mind. 8 Zeichen)'), pw),
      setup && h('label', null, 'Passwort wiederholen', pw2),
      btn);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      err.hidden = true;
      if (setup && pw.value !== pw2.value) { err.textContent = 'Die Passwörter stimmen nicht überein.'; err.hidden = false; return; }
      btn.disabled = true;
      try {
        const r = setup ? await api.setup(name.value.trim(), pw.value) : await api.login(name.value.trim(), pw.value);
        resolve(r.user);
      } catch (ex) {
        err.textContent = ex.message; err.hidden = false; pw.value = ''; if (pw2) pw2.value = ''; pw.focus();
      } finally { btn.disabled = false; }
    });
    root.replaceChildren(h('div', { class: 'login' }, h('div', { class: 'card' },
      h('div', { class: 'brand big' }, h('span', { class: 'mark', 'aria-hidden': 'true' }), h('div', null, h('b', null, 'PDU'), h('small', null, setup ? 'Ersteinrichtung' : 'Rittal-PDUs · Monitoring & Steuerung'))),
      form)));
    document.title = 'Anmelden · PDU';
    name.focus();
  });
}

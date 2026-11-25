// Einstieg: Anmeldung + Hash-Router
//   #/monitor[/<pdu>]   Monitoring    #/control[/<pdu>]   Steuerung
//   #/explorer[/<pdu>]  Redfish-Explorer (Admin)          #/users   Benutzer (Admin)
import { renderMonitor } from './monitor.js';
import { renderControl } from './control.js';
import { renderExplorer } from './explorer.js';
import { renderUsers } from './users.js';
import { renderLogin } from './login.js';
import { api, setUnauthHandler } from './api.js';
import { session } from './session.js';

const root = document.getElementById('root');
let current = null;
let seq = 0;

async function route() {
  const my = ++seq;
  try { await current?.destroy?.(); } catch (e) { console.error(e); }
  current = null;
  const m = location.hash.match(/^#\/(monitor|control|explorer|users)(?:\/([\w-]+))?/);
  const [, view = 'monitor', key = ''] = m || [];
  const titles = { monitor: 'Monitoring', control: 'Steuerung', explorer: 'Redfish-Explorer', users: 'Benutzer' };
  let v;
  if (view === 'users' && session.user?.admin) v = await renderUsers(root);
  else if (view === 'explorer' && session.user?.admin) v = await renderExplorer(root, key);
  else if (view === 'control') v = await renderControl(root, key);
  else v = await renderMonitor(root, key);
  document.title = `${titles[view] || 'Monitoring'} · PDU`;
  if (my !== seq) { await v?.destroy?.(); return; }
  current = v;
}

async function start() {
  ++seq;
  try { await current?.destroy?.(); } catch { /* ignore */ }
  current = null;
  let st;
  try { st = await api.state(); } catch (e) { root.textContent = 'Server nicht erreichbar: ' + e.message; return; }
  session.user = st.user;
  if (!session.user) session.user = await renderLogin(root, { setup: st.setup });
  route();
}

setUnauthHandler(() => { if (session.user) { session.user = null; start(); } });
window.addEventListener('hashchange', () => { if (session.user) route(); });
window.addEventListener('pdu:logout', async () => { try { await api.logout(); } catch { /* ignore */ } session.user = null; location.hash = '#/monitor'; start(); });
start();

// Dünner Client für die PDU-API
let onUnauth = null;
export const setUnauthHandler = (fn) => { onUnauth = fn; };

async function call(method, url, body) {
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = `Fehler ${res.status}`;
    try { msg = (await res.json()).error || msg; } catch { /* ignore */ }
    if (res.status === 401 && !url.startsWith('/api/auth/')) onUnauth?.();
    const err = new Error(msg); err.status = res.status;
    throw err;
  }
  return res.json();
}
const enc = encodeURIComponent;
export const api = {
  pdus: () => call('GET', '/api/pdus'),
  summary: () => call('GET', '/api/pdus/summary'),
  pdu: (key, force = false) => call('GET', `/api/pdus/${enc(key)}${force ? '?force=1' : ''}`),
  power: (key, id, action) => call('POST', `/api/pdus/${enc(key)}/outlets/${enc(id)}/power`, { action }),
  bulk: (key, ids, action) => call('POST', `/api/pdus/${enc(key)}/power`, { ids, action }),
  label: (key, id, label) => call('PUT', `/api/pdus/${enc(key)}/outlets/${enc(id)}`, { label }),
  log: (key) => call('GET', `/api/log?pdu=${enc(key)}`),
  raw: (key, path) => call('GET', `/api/admin/redfish/${enc(key)}?path=${enc(path)}`),
  // Anmeldung
  state: () => call('GET', '/api/auth/state'),
  setup: (name, password) => call('POST', '/api/auth/setup', { name, password }),
  login: (name, password) => call('POST', '/api/auth/login', { name, password }),
  logout: () => call('POST', '/api/auth/logout', {}),
  changePassword: (old, next) => call('POST', '/api/auth/password', { old, new: next }),
  // Benutzer
  adminUsers: () => call('GET', '/api/admin/users'),
  addUser: (u) => call('POST', '/api/admin/users', u),
  setUser: (id, patch) => call('PUT', `/api/admin/users/${id}`, patch),
  delUser: (id) => call('DELETE', `/api/admin/users/${id}`),
};

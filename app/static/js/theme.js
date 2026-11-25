// Hell / Dunkel / Auto (wie in Plan): Auswahl im Browser gespeichert, „Auto“ folgt dem System
const mq = window.matchMedia('(prefers-color-scheme: dark)');
const KEY = 'pdu.theme';
export const MODES = ['auto', 'light', 'dark'];
export const MODE_NAMES = { auto: 'Auto', light: 'Hell', dark: 'Dunkel' };
let mode = 'auto';
try { const v = localStorage.getItem(KEY); if (MODES.includes(v)) mode = v; } catch { /* ignore */ }
export const theme = { mode };

function apply() {
  const root = document.documentElement;
  if (mode === 'auto') { delete root.dataset.theme; root.style.colorScheme = ''; } else { root.dataset.theme = mode; root.style.colorScheme = mode; }
  theme.mode = mode;
}
export function setThemeMode(m) {
  if (!MODES.includes(m) || m === mode) return;
  mode = m;
  try { localStorage.setItem(KEY, m); } catch { /* ignore */ }
  apply();
}
export const isDark = () => mode === 'dark' || (mode === 'auto' && mq.matches);
apply();

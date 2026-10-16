// shared by the dashboard, profile and admin pages
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', ...opts });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search); return new Promise(() => {}); }
  if (!r.ok) throw new Error(j.detail ? (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : r.statusText);
  return j;
}
const post = (p, b) => api(p, { method: 'POST', body: JSON.stringify(b || {}) });
const fmtT = s => { s = Math.max(0, Math.round(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(x).padStart(2, '0'); };
const fmtDate = t => t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
const ROLE_NAMES = { admin: 'Administrator', instructor: 'Instructor', atc: 'Controller', pilot: 'Pseudo-pilot' };
const SEAT_NAMES = { instructor: 'Instructor', atc: 'Controller', pilot: 'Pseudo-pilot' };
let ME = null;
async function header(cur) {
  ME = await api('/api/me');
  const links = [['/', 'Home']];
  if (ME.role === 'instructor' || ME.role === 'admin') links.push(['/designer', 'Scenario designer'], ['/radar', 'Solo radar']);
  if (ME.role === 'admin') links.push(['/admin', 'Users']);
  links.push(['/stats', 'Stats'], ['/profile', 'Profile']);
  const h = document.createElement('header'); h.className = 'top';
  h.innerHTML = `<div class="brand">ATC <span>SIM</span></div><nav>${links.map(([u, t]) => `<a href="${u}" class="${u === cur ? 'cur' : ''}">${t}</a>`).join('')}</nav>
    <div class="who"><span>${esc(ME.name)} · ${ROLE_NAMES[ME.role] || ME.role}${ME.licence ? ' · ' + esc(ME.licence) : ''}</span><button class="small" id="logout">Sign out</button></div>`;
  document.body.prepend(h);
  $('#logout').onclick = async () => { await post('/auth/logout'); location.href = '/login'; };
  if (!ME.profile_complete && cur !== '/profile') { location.href = '/profile?first=1'; return new Promise(() => {}); }
  return ME;
}
function modal(html, onReady) {
  const bg = document.createElement('div'); bg.className = 'modal-bg on';
  bg.innerHTML = `<div class="modal">${html}</div>`;
  bg.addEventListener('mousedown', e => { if (e.target === bg) bg.remove(); });
  document.body.appendChild(bg);
  if (onReady) onReady(bg.querySelector('.modal'), () => bg.remove());
  return bg;
}

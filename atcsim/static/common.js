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
// scenario complexity / traffic density (1-5) as five pips; "~" = estimated (not set by the designer)
const LEVEL_NAMES = ['', 'very low', 'low', 'moderate', 'high', 'very high'];
function pips(v, est, what) {
  if (!v) return '';
  const col = ['', '#4caf7a', '#8bc34a', '#e0b84a', '#e8873a', '#e05252'][v];
  return `<span class="pips" title="${what}: ${LEVEL_NAMES[v]} (${v}/5)${est ? ' — estimated from the content, not set by the designer' : ''}" style="white-space:nowrap">` +
    `<span style="opacity:.75">${what}</span> ` + [1, 2, 3, 4, 5].map(i => `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:2px;background:${i <= v ? col : '#33404d'}"></span>`).join('') +
    `${est ? '<span style="opacity:.6">~</span>' : ''}</span>`;
}
function scnBadge(i, withAuthor = true) {
  if (!i) return '';
  const parts = [pips(i.complexity, i.complexity_estimated, 'complexity'), pips(i.density, i.density_estimated, 'density')];
  if (withAuthor && i.author) parts.push(`<span style="opacity:.75">by</span> ${esc(i.author)}`);
  return `<span class="scnb dim" style="font-size:11px;display:inline-flex;gap:10px;flex-wrap:wrap;align-items:center">${parts.filter(Boolean).join('')}</span>`;
}
const ROLE_NAMES = { admin: 'Administrator', instructor: 'Instructor', atc: 'Controller', pilot: 'Pseudo-pilot' };
const SEAT_NAMES = { instructor: 'Instructor', atc: 'Controller', pilot: 'Pseudo-pilot' };
let ME = null;
async function header(cur) {
  ME = await api('/api/me');
  const links = [['/', 'Home']];
  if (ME.role === 'instructor' || ME.role === 'admin') links.push(['/designer', 'Scenario designer'], ['/ai', 'AI lab']);
  links.push(['/solo', 'Solo radar']);
  if (ME.role === 'admin') links.push(['/admin', 'Users']);
  links.push(['/stats', 'Stats'], ['/help', 'Help'], ['/profile', 'Profile']);
  const h = document.createElement('header'); h.className = 'top';
  h.innerHTML = `<div class="brand">ATC <span>SIM</span></div><nav>${links.map(([u, t]) => `<a href="${u}" class="${u === cur ? 'cur' : ''}">${t}</a>`).join('')}</nav>
    <div class="who"><span>${esc(ME.name)} · ${ROLE_NAMES[ME.role] || ME.role}${ME.licence ? ' · ' + esc(ME.licence) : ''}</span><button class="small" id="logout">Sign out</button></div>`;
  document.body.prepend(h);
  if (cur !== '/help') ambientScene();
  $('#logout').onclick = async () => { await post('/auth/logout'); location.href = '/login'; };
  if (!ME.profile_complete && cur !== '/profile') { location.href = '/profile?first=1'; return new Promise(() => {}); }
  return ME;
}
// the slowly turning 3D CTA behind the dashboards (same scene as the sign-in page, without labels)
function ambientScene() {
  if (document.getElementById('scene')) return;
  const c = document.createElement('canvas'); c.id = 'scene'; c.dataset.mode = 'ambient'; c.setAttribute('aria-hidden', 'true');
  const s = document.createElement('div'); s.className = 'scene-scrim';
  document.body.prepend(s); document.body.prepend(c);
  const m = document.createElement('script'); m.type = 'module'; m.src = '/static/landing3d.js'; document.body.appendChild(m);
}
function modal(html, onReady) {
  const bg = document.createElement('div'); bg.className = 'modal-bg on';
  bg.innerHTML = `<div class="modal">${html}</div>`;
  bg.addEventListener('mousedown', e => { if (e.target === bg) bg.remove(); });
  const esc = e => { if (e.key === 'Escape' && document.body.contains(bg)) { bg.remove(); removeEventListener('keydown', esc); } };
  addEventListener('keydown', esc);
  document.body.appendChild(bg);
  if (onReady) onReady(bg.querySelector('.modal'), () => bg.remove());
  return bg;
}

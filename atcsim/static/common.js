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
    <div class="bell-wrap"><button class="bell" id="bell" aria-haspopup="true" aria-expanded="false" title="Notifications">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1l-2-2Z"/></svg>
      <span class="bell-n" id="bellN" hidden></span></button>
      <div class="bell-menu" id="bellMenu" role="menu" hidden></div></div>
    <div class="who"><span>${esc(ME.name)} · ${ROLE_NAMES[ME.role] || ME.role}${ME.licence ? ' · ' + esc(ME.licence) : ''}</span><button class="small" id="logout">Sign out</button></div>`;
  document.body.prepend(h);
  notifications();
  if (cur !== '/help') ambientScene();
  $('#logout').onclick = async () => { await post('/auth/logout'); location.href = '/login'; };
  if (!ME.profile_complete && cur !== '/profile') { location.href = '/profile?first=1'; return new Promise(() => {}); }
  return ME;
}
// the notifications bell: unread count, the latest in a drop-down (newest first); opening one marks it read and
// goes to it; "Mark all read"; refreshed every 30 s and when the page comes back into view
const NOTE_ICONS = { exercise_invite: '✈', debrief_invite: '▶', scenario_share: '⇄', evaluation: '★', account: '◉', role: '◉' };
function ago(t) {
  const d = Math.max(0, Date.now() / 1000 - t);
  if (d < 60) return 'just now'; if (d < 3600) return Math.floor(d / 60) + ' min ago';
  if (d < 86400) return Math.floor(d / 3600) + ' h ago'; if (d < 7 * 86400) return Math.floor(d / 86400) + ' d ago';
  return fmtDate(t);
}
function notifications() {
  const btn = $('#bell'), menu = $('#bellMenu'), badge = $('#bellN');
  let data = { unread: 0, items: [] };
  const draw = () => {
    badge.hidden = !data.unread; badge.textContent = data.unread > 99 ? '99+' : data.unread;
    btn.title = data.unread ? `${data.unread} unread notification${data.unread > 1 ? 's' : ''}` : 'Notifications';
    menu.innerHTML = `<div class="bell-hd"><b>Notifications</b>${data.unread ? '<button class="small" data-all>Mark all read</button>' : ''}</div>` +
      (data.items.length ? data.items.map(n => `<a class="bell-it ${n.read ? '' : 'new'}" href="${esc(n.link || '#')}" data-id="${n.id}" role="menuitem">
        <span class="bell-ic" aria-hidden="true">${NOTE_ICONS[n.kind] || '•'}</span>
        <span class="bell-tx"><span class="bell-t">${esc(n.title)}</span>${n.body ? `<span class="bell-b">${esc(n.body)}</span>` : ''}<span class="bell-w">${ago(n.created_at)}</span></span></a>`).join('')
        : '<div class="bell-empty">Nothing yet. Invitations to exercises and debriefs, shared scenarios and evaluations show up here.</div>');
  };
  const load = async () => { try { data = await api('/api/notifications?limit=30'); draw(); } catch (e) {} };
  const open = v => {
    menu.hidden = !v; btn.setAttribute('aria-expanded', v ? 'true' : 'false');
    // a phone: under the header, the width of the screen (the bell may be on the left there)
    menu.style.top = innerWidth <= 700 ? (btn.closest('header').getBoundingClientRect().bottom + 6) + 'px' : '';
    if (v) load();
  };
  btn.onclick = e => { e.stopPropagation(); open(menu.hidden); };
  document.addEventListener('mousedown', e => { if (!menu.hidden && !e.target.closest('.bell-wrap')) open(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { open(false); btn.focus(); } });
  menu.addEventListener('click', async e => {
    if (e.target.closest('[data-all]')) { e.preventDefault(); data = await post('/api/notifications/read', {}); draw(); return; }
    const it = e.target.closest('.bell-it'); if (!it) return;
    e.preventDefault();
    try { await post('/api/notifications/read', { ids: [+it.dataset.id] }); } catch (er) {}
    const href = it.getAttribute('href');
    if (href && href !== '#') location.href = href; else load();
  });
  load(); setInterval(load, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
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

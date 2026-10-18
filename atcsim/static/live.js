// Live actions during an exercise: add an aircraft, declare / clear an emergency, make a pilot ask, switch an area.
// The instructor has them in the Exercise tab, the pseudo-pilots in their panel, the solo radar in a window (LIVE).
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let opts = null, tab = 'aircraft', box = null;
  const canAct = () => (EXID && (SEAT === 'instructor' || SEAT === 'pilot')) || (EXINFO && EXINFO.solo);

  async function loadOpts() { if (!SID) return; try { opts = await api(`/sessions/${SID}/live`); } catch (e) {} }
  async function act(body, out) {
    try {
      const r = await api(`/sessions/${SID}/live`, { method: 'POST', body: JSON.stringify(body) });
      out.className = 'lv-out c-ok';
      out.textContent = '✓ ' + (r.text || (r.callsign ? `${r.callsign} ${body.action === 'aircraft' ? 'added: enters at ' + r.entry + (r.appears_at_s > (state ? state.sim_time_s + 30 : 0) ? ' at T+' + fmtT(r.appears_at_s) : ' now') : 'done'}` : r.area ? `${r.area} ${r.active ? 'active' : 'inactive'}` : 'done'));
      await loadOpts(); render(true); if (typeof pollState === 'function') pollState();
      return r;
    } catch (e) { out.className = 'lv-out c-los'; out.textContent = '✗ ' + e.message; }
  }
  const acSel = (name, list) => `<select data-f="${name}">${(list || []).map(c => `<option>${esc(c)}</option>`).join('')}</select>`;
  function form() {
    const o = opts || { aircraft: [], areas: [], emergencies: {}, aerodromes: [], types: [] };
    if (tab === 'aircraft') return `<div class="lv-g">
        <label>Callsign<input data-f="callsign" placeholder="RAM123" style="width:90px"></label>
        <label>Type<input data-f="type" list="lvTypes" value="A320" style="width:70px"></label>
        <label>Level<input data-f="fl" value="350" inputmode="numeric" style="width:56px"></label>
        <label class="lv-w">Route (entry first)<input data-f="route" list="plPts" placeholder="NIPRI ROLRO" style="width:100%"></label>
        <label>Appears<select data-f="appear"><option value="now">now, at its first point</option><option value="300">entering in 5 min</option><option value="600">entering in 10 min</option><option value="900">entering in 15 min</option></select></label>
        <label>From<input data-f="dep" placeholder="dep" style="width:56px"></label><label>to<input data-f="dest" placeholder="dest" style="width:56px"></label>
        <button class="primary" data-go="aircraft">Add aircraft</button></div><datalist id="lvTypes">${o.types.map(t => `<option value="${t}">`).join('')}</datalist>`;
    if (tab === 'emergency') {
      const em = Object.entries(o.emergencies || {});
      return `<div class="lv-g"><label>Aircraft${acSel('callsign', o.aircraft)}</label>
        <label>Kind<select data-f="kind"><option value="descent">Emergency descent (7700, MAYDAY)</option><option value="medical">Medical, diversion (7700, PAN PAN)</option><option value="radio">Radio failure (7600)</option><option value="general">General emergency (7700)</option></select></label>
        <label>Divert to<input data-f="aerodrome" list="lvAds" placeholder="nearest" style="width:70px"></label>
        <label class="lv-w">Reason<input data-f="why" placeholder="e.g. rapid decompression, engine failure" style="width:100%"></label>
        <button class="primary" data-go="emergency">Declare</button></div><datalist id="lvAds">${o.aerodromes.map(t => `<option value="${t}">`).join('')}</datalist>
        ${em.length ? `<div class="lv-sub">In progress</div>` + em.map(([cs, e]) => `<div class="lv-row"><b>${esc(cs)}</b> <span class="lv-sq">${e.code}</span> ${esc(e.kind)} <button class="small" data-clear="${esc(cs)}">Clear</button></div>`).join('') : ''}`;
    }
    if (tab === 'request') return `<div class="lv-g"><label>Aircraft${acSel('callsign', o.aircraft)}</label>
        <label>Asks for<select data-f="kind"><option value="level">a level</option><option value="direct">a direct</option><option value="speed">a speed</option><option value="deviation">a weather deviation</option><option value="report">(says something)</option></select></label>
        <label class="lv-w">Value<input data-f="value" list="plPts" placeholder="370 · ROLRO · M78 · free words for a report" style="width:100%"></label>
        <label class="lv-w">Reason<input data-f="reason" placeholder="e.g. turbulence, weight" style="width:100%"></label>
        <button class="primary" data-go="request">Make it ask</button></div>`;
    return (o.areas || []).length ? (o.areas.map(a => `<div class="lv-row"><b>${esc(a.name)}</b> <span class="c-dim">${esc(a.kind)} FL${a.lower_fl}–${a.upper_fl}${a.next_change ? ' · next change T+' + fmtT(a.next_change.time_s) : ''}</span>
        <button class="small ${a.active ? 'danger' : 'primary'}" data-area="${esc(a.id)}" data-v="${a.active ? 0 : 1}">${a.active ? 'Deactivate' : 'Activate'}</button></div>`).join(''))
      : '<div class="c-dim">This scenario has no area.</div>';
  }
  let lastSig = '';
  function render(force) {
    if (!box) return;
    const sig = tab + JSON.stringify(opts && [opts.aircraft, opts.emergencies, opts.areas]);
    if (!force && sig === lastSig && box.innerHTML) return;
    // keep what was typed across a refresh of the lists
    const keep = {}; box.querySelectorAll('[data-f]').forEach(i => { keep[i.dataset.f] = i.value; });
    const out = box.querySelector('.lv-out'), msg = out ? [out.className, out.textContent] : null;
    lastSig = sig;
    box.innerHTML = `<div class="lv-tabs">${[['aircraft', 'Aircraft'], ['emergency', 'Emergency'], ['request', 'Pilot request'], ['area', 'Areas']].map(([k, t]) => `<button data-tab-lv="${k}" class="${tab === k ? 'on' : ''}">${t}</button>`).join('')}</div>
      <div class="lv-body">${form()}</div><div class="lv-out"></div>`;
    for (const [k, v] of Object.entries(keep)) { const i = box.querySelector(`[data-f="${k}"]`); if (i && v !== undefined && i.tagName !== 'SELECT') i.value = v; else if (i && [...i.options].some(o => o.value === v)) i.value = v; }
    if (msg) { const o = box.querySelector('.lv-out'); o.className = msg[0]; o.textContent = msg[1]; }
  }
  function bind() {
    box.addEventListener('keydown', e => e.stopPropagation());
    box.addEventListener('click', async e => {
      const t = e.target.closest('[data-tab-lv]'); if (t) { tab = t.dataset.tabLv; render(true); return; }
      const out = box.querySelector('.lv-out'), f = k => { const el = box.querySelector(`[data-f="${k}"]`); return el ? el.value.trim() : ''; };
      const c = e.target.closest('[data-clear]'); if (c) { act({ action: 'clear_emergency', callsign: c.dataset.clear }, out); return; }
      const ar = e.target.closest('[data-area]'); if (ar) { act({ action: 'area', area: ar.dataset.area, active: ar.dataset.v === '1' }, out); return; }
      const g = e.target.closest('[data-go]'); if (!g) return;
      if (g.dataset.go === 'aircraft') {
        const r = await act({ action: 'aircraft', callsign: f('callsign').toUpperCase(), type: f('type').toUpperCase(), fl: +f('fl') || 350,
          route: f('route').toUpperCase().split(/[\s,]+/).filter(Boolean), appear: f('appear') === 'now' ? 'now' : +f('appear'), dep: f('dep').toUpperCase() || null, dest: f('dest').toUpperCase() || null }, out);
        if (r) { const i = box.querySelector('[data-f="callsign"]'); if (i) i.value = ''; }
      } else if (g.dataset.go === 'emergency') act({ action: 'emergency', callsign: f('callsign'), kind: f('kind'), aerodrome: f('aerodrome').toUpperCase() || null, why: f('why') || null }, out);
      else if (g.dataset.go === 'request') act({ action: 'request', callsign: f('callsign'), kind: f('kind'), value: f('value'), reason: f('reason') }, out);
    });
  }
  function mount() {
    if (EXINFO && EXINFO.solo) {                      // solo radar: a toolbar button and a floating window
      const btn = document.createElement('button'); btn.id = 'liveBtn'; btn.textContent = 'LIVE';
      btn.title = 'Live actions: add an aircraft, an emergency, a pilot request, switch an area';
      const help = document.getElementById('helpBtn'); if (help) help.parentNode.insertBefore(btn, help);
      btn.onclick = async () => {
        if (atcIsOpen('liveWin')) { atcCloseWin('liveWin'); box = null; return; }
        const el = atcWin('liveWin', 'Live actions', 140, 90, 430);
        box = el.querySelector('.wb'); box.classList.add('lv'); bind(); await loadOpts(); render(true);
      };
      return;
    }
    const sec = document.createElement('section'); sec.id = 'liveSec'; sec.className = 'ex-only';
    sec.dataset.tab = SEAT === 'instructor' ? 'exercise' : 'pilot';
    sec.innerHTML = `<h2>Live actions <span class="c-dim" style="text-transform:none;letter-spacing:0">${SEAT === 'instructor' ? 'change the exercise as it runs' : 'add traffic, emergencies, requests'}</span></h2><div class="lv"></div>`;
    const after = SEAT === 'instructor' ? document.getElementById('exSec') : document.getElementById('dlSec');
    if (after) { if (after.classList.contains('tab-on')) sec.classList.add('tab-on'); after.parentNode.insertBefore(sec, after.nextSibling); }
    box = sec.querySelector('.lv'); bind(); loadOpts().then(() => render(true));
  }
  const wait = setInterval(() => {
    if (!EXID || !EXINFO) { if (!EXID) clearInterval(wait); return; }
    if (!SID) return;
    clearInterval(wait);
    if (!canAct()) return;
    mount();
    setInterval(async () => { if (box) { await loadOpts(); render(); } }, 4000);
  }, 300);
})();

// Start-up clearances on the controller's radar (solo radar and the sandbox): an aerodrome's approach phones to ask
// start-up for its departures. The phone rings; picked up, the call shows its flights (one call for the close ones
// from the same aerodrome) with the point where each leaves the TMA and its level, to approve (as suggested or
// changed) or refuse. The call ends when every flight in it has an answer. Tools ▸ Start-ups lists the clearances
// given (change them until take-off). With a pseudo-pilot the request is the pseudo-pilot's task (seat.js) instead.
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const mine = () => typeof SID !== 'undefined' && SID && (!EXID || (EXINFO && EXINFO.solo && !EXINFO.debrief && !EXINFO.ai));
  let picked = null;                   // the group (call) picked up
  const answered = new Set();          // groups whose call ended
  // ---------------------------------------------------------------- ring (425 Hz, two bursts every 2.5 s)
  let actx = null, ringT = null;
  function ring(on) {
    if (on && !ringT) {
      const burst = () => {
        try {
          actx = actx || new (window.AudioContext || window.webkitAudioContext)();
          const t = actx.currentTime, o = actx.createOscillator(), g = actx.createGain();
          o.frequency.value = 425; o.connect(g); g.connect(actx.destination); g.gain.setValueAtTime(0, t);
          for (const s of [0, 0.4]) { g.gain.setValueAtTime(0.12, t + s); g.gain.setValueAtTime(0, t + s + 0.3); }
          o.start(t); o.stop(t + 0.8);
        } catch (e) {}
      };
      burst(); ringT = setInterval(burst, 2500);
    } else if (!on && ringT) { clearInterval(ringT); ringT = null; }
  }
  // ---------------------------------------------------------------- the ringing badge
  const badge = document.createElement('div');
  badge.id = 'suRing';
  badge.style.cssText = 'position:absolute;top:52px;left:50%;transform:translateX(-50%);z-index:9;display:none;background:#3a2a10f2;border:1px solid #f2b84b;border-radius:8px;padding:8px 12px;font-size:13px;box-shadow:0 8px 24px #000a;color:#ffe2a8';
  (document.getElementById('map') || document.body).appendChild(badge);
  badge.addEventListener('mousedown', e => { const b = e.target.closest('[data-pick]'); if (b) { e.preventDefault(); pickUp(b.dataset.pick); } });
  const groups = () => {
    const g = {};
    for (const r of (state && state.startups) || []) if (r.state === 'requested' && !answered.has(r.group)) (g[r.group] = g[r.group] || []).push(r);
    return g;
  };
  // ---------------------------------------------------------------- the call window
  function pickUp(gid) { picked = gid; ring(false); badge.style.display = 'none'; render(true); }
  function row(r, live) {
    const opts = (r.points || [r.point]).map(p => `<option${p === (r.cleared_point || r.point) ? ' selected' : ''}>${esc(p)}</option>`).join('');
    const eta = Math.max(0, Math.round((r.t_takeoff - state.sim_time_s) / 60));
    const st = r.state === 'cleared' ? `<span class="c-ok">cleared ${esc(r.cleared_point)} F${String(r.cleared_level).padStart(3, '0')}</span>` : '';
    return `<tr data-cs="${r.callsign}"><td><b>${r.callsign}</b><br><small class="c-dim">${esc(r.type)} → ${esc(r.dest || '')}</small></td>
      <td><small>RWY ${esc(r.runway || '')}<br>${esc(r.sid || '')}</small></td><td><small>dep ~${eta} min</small></td>
      <td><select data-pt>${opts}</select></td><td>F<input data-lv type="number" step="10" min="20" max="450" value="${r.cleared_level || r.level}" style="width:54px"></td>
      <td>${live ? `<button class="yes" data-ok title="start-up approved: leaves the TMA at the point, cleared to the level">${r.state === 'cleared' ? 'Change' : 'Approve'}</button>` : ''}
      ${live && r.state !== 'cleared' ? `<button class="no" data-no title="start-up refused: approach asks again in 5 minutes">Refuse</button>` : ''} ${st}</td></tr>`;
  }
  async function send(cs, approve, tr) {
    const pt = tr && tr.querySelector('[data-pt]'), lv = tr && tr.querySelector('[data-lv]');
    try {
      await api(`/sessions/${SID}/startup/answer`, { method: 'POST', body: JSON.stringify({ callsigns: [cs], approve,
        point: pt ? pt.value : null, level: lv && lv.value ? +lv.value : null }) });
      tr && (tr.dataset.done = '1', tr.style.opacity = .45);
    } catch (e) { const m = document.querySelector('#suWin [data-msg]'); if (m) m.textContent = e.message; }
  }
  let lastH = '';
  function render(force) {
    if (!picked) return;
    const all = (state.startups || []).filter(r => r.group === picked);
    const open = all.filter(r => r.state === 'requested');
    if (!open.length) { answered.add(picked); picked = null; atcCloseWin('suWin'); lastH = ''; return; }      // the call ends
    const ad = open[0].aerodrome;
    const el = atcWin('suWin', `☎ ${esc(ad)} approach · start-up request`, 360, 90, 620);
    const html = `<div class="c-dim" style="font-size:12px;margin-bottom:6px">${open.length} flight${open.length > 1 ? 's' : ''} for start-up: give the point where each leaves the ${esc(ad)} TMA and its level, or refuse (approach asks again later).</div>
      <table style="width:100%;border-collapse:collapse;font-size:12px">${open.map(r => row(r, true)).join('')}</table>
      <div style="margin-top:8px;display:flex;gap:6px"><button class="yes" data-all>Approve all</button><span data-msg class="c-warn" style="font-size:12px"></span></div>`;
    const wb = el.querySelector('.wb');
    if (force || html !== lastH && !wb.contains(document.activeElement)) { wb.innerHTML = html; lastH = html; }
  }
  function bind() {
    document.addEventListener('mousedown', e => {
      const w = e.target.closest('#suWin, #suList'); if (!w) return;
      const tr = e.target.closest('tr[data-cs]');
      if (e.target.closest('[data-ok]')) { e.preventDefault(); send(tr.dataset.cs, true, tr); }
      else if (e.target.closest('[data-no]')) { e.preventDefault(); send(tr.dataset.cs, false, tr); }
      else if (e.target.closest('[data-all]')) { e.preventDefault(); w.querySelectorAll('tr[data-cs]').forEach(t => { if (!t.dataset.done) send(t.dataset.cs, true, t); }); }
    }, true);                                  // (capture: the windows stop the event from reaching the page)
    document.addEventListener('keydown', e => { if (e.target.closest && e.target.closest('#suWin, #suList')) e.stopPropagation(); }, true);
  }
  // ---------------------------------------------------------------- Tools ▸ Start-ups: the clearances given (change them until take-off)
  function list() {
    const rs = (state.startups || []).filter(r => r.state !== 'requested');
    const el = atcWin('suList', 'Start-up clearances', 420, 120, 620);
    el.querySelector('.wb').innerHTML = rs.length ? `<table style="width:100%;border-collapse:collapse;font-size:12px">${rs.map(r => row(r, r.state === 'cleared')).join('')}</table>`
      : '<div class="empty">No start-up clearance waiting for take-off.</div>';
  }
  let seen = false;                    // the Start-ups button only once the exercise has start-up traffic
  function tick() {
    if (!mine() || !state) return;
    const b = document.getElementById('suBtn');
    if ((state.startups || []).length) seen = true;
    if (b) b.style.display = seen ? '' : 'none';
    const g = groups(), ids = Object.keys(g);
    if (!picked && ids.length) {
      const gid = ids[0], ad = g[gid][0].aerodrome;
      badge.innerHTML = `☎ <b>${esc(ad)} approach</b> calling · start-up for ${g[gid].map(r => r.callsign).join(', ')} <button class="yes" data-pick="${esc(gid)}" style="margin-left:8px">Pick up</button>`;
      badge.style.display = ''; ring(true);
    } else if (!ids.length || picked) { badge.style.display = 'none'; ring(false); }
    render(false);
  }
  function init() {
    if (!mine()) { setTimeout(init, 1500); return; }
    bind();
    const host = document.getElementById('toolsMenu');
    if (host && !document.getElementById('suBtn')) {
      const b = document.createElement('button'); b.id = 'suBtn'; b.textContent = 'Start-ups';
      b.title = 'Start-up clearances given to the aerodromes\' departures: change the point or the level until take-off';
      b.style.display = 'none'; b.onclick = () => atcIsOpen('suList') ? atcCloseWin('suList') : list(); host.appendChild(b);
    }
    setInterval(() => { tick(); if (atcIsOpen('suList') && !document.querySelector('#suList').contains(document.activeElement)) list(); }, 1000);
  }
  setTimeout(init, 1200);
})();

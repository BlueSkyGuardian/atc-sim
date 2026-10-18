// Radar page chrome: the HUD switch, the simulation control bar (instructor, solo radar, sandbox) and, on the solo
// radar, the radio log - what the simulated pilots say (first calls, read-backs, "unable", requests to answer).
// Uses the radar page's globals (state, SID, SEAT, EXID, EXINFO, eventsBuf, api, selected).
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = s => document.querySelector(s);
  const solo = () => !!(EXINFO && EXINFO.solo);

  // ------------------------------------------------------------------ HUD on / off (per browser)
  const hudBtn = $('#hudBtn');
  let hudOn = true; try { hudOn = localStorage.getItem('atc_hud') !== '0'; } catch (e) {}
  function setHud(on) { hudOn = on; document.body.classList.toggle('hud-off', !on); if (hudBtn) hudBtn.classList.toggle('on', on); try { localStorage.setItem('atc_hud', on ? '1' : '0'); } catch (e) {} }
  if (hudBtn) hudBtn.onclick = () => setHud(!hudOn);
  setHud(hudOn);

  // ------------------------------------------------------------------ timeline on / off (per browser); solo: Replay / Live
  const tlBtn = $('#tlBtn');
  let tlOn = true; try { tlOn = localStorage.getItem('atc_tl') !== '0'; } catch (e) {}
  function setTl(on) { tlOn = on; document.body.classList.toggle('tl-off', !on); if (tlBtn) tlBtn.classList.toggle('on', on); try { localStorage.setItem('atc_tl', on ? '1' : '0'); } catch (e) {}
    if (!on && typeof replayMode !== 'undefined' && replayMode && $('#rpLive')) $('#rpLive').click(); }   // hidden: back to live
  if (tlBtn) tlBtn.onclick = () => setTl(!tlOn);
  setTl(tlOn);
  const tlPlay = $('#tlPlay'), tlLive = $('#tlLive');
  if (tlPlay) tlPlay.onclick = () => $('#rpPlay') && $('#rpPlay').click();
  if (tlLive) tlLive.onclick = () => $('#rpLive') && $('#rpLive').click();
  setInterval(() => { if (!tlPlay) return; tlPlay.textContent = $('#rpPlay') ? $('#rpPlay').textContent : '▶ Replay'; tlLive.classList.toggle('on', typeof replayMode === 'undefined' || !replayMode); }, 400);

  // ------------------------------------------------------------------ control bar
  const bar = $('#ctrlRow');
  const canControl = () => !EXID || SEAT === 'instructor' || solo();
  async function control(body) {
    try { await api(`/sessions/${SID}/control`, { method: 'POST', body: JSON.stringify(body) }); if (typeof pollState === 'function') pollState(); } catch (e) { if (typeof msg === 'function') msg(e.message, true); }
  }
  function renderBar() {
    if (!bar || !state || !state.session) return;
    const s = state.session, st = s.status;
    bar.classList.toggle('running', st === 'running');
    const sb = $('#sbSt'); sb.textContent = { running: 'RUNNING', paused: 'PAUSED', created: 'READY', finished: 'FINISHED' }[st] || st.toUpperCase(); sb.className = 'sb-st ' + st;
    bar.querySelectorAll('[data-tf]').forEach(b => b.classList.toggle('on', +b.dataset.tf === +s.time_factor));
    $('#sbT').textContent = `T+ ${fmtT(state.sim_time_s)} / ${fmtT(state.duration_s)}`;
  }
  setInterval(renderBar, 500);
  const sbEnd = $('#sbEnd');
  if (sbEnd) sbEnd.onclick = async () => {
    if (!EXID || !confirm('End this solo run? It is kept in your exercises and statistics, with its recording.')) return;
    try { await api(`/api/exercises/${EXID}/end`, { method: 'POST' }); if (window.exShowEnded) exShowEnded(); } catch (e) { alert(e.message); }
  };
  const typing = () => { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') && a.type !== 'checkbox'; };
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing() || !SID || !state || !state.session || !canControl()) return;
    if (e.key === 'p' || e.key === 'P') { e.preventDefault(); control({ action: state.session.status === 'running' ? 'pause' : 'resume' }); }
  });

  // ------------------------------------------------------------------ solo radar: the radio log
  const log = $('#radioLog');
  const said = [];                 // [{t, cls, html}] what the pilots said, newest last
  let seenSeq = 0;
  const KEEP_S = 75;
  function ingest() {
    if (!solo() || !Array.isArray(eventsBuf)) return;
    for (const e of eventsBuf) {
      if (e.seq <= seenSeq) continue;
      seenSeq = e.seq;
      if (e.type === 'checkin' && !e.assumed) said.push({ t: e.time_s, cls: 'call', cs: e.callsign, html: `<b>${esc(e.callsign)}</b> ${esc(e.text.replace(e.callsign + ' ', ''))}` });
      else if (e.type === 'pilot_readback') said.push({ t: e.time_s, cls: e.ok ? '' : 'bad', html: esc(e.text) });
      else if (e.type === 'cpdlc' && /^[↑↓]/.test(e.text || '') && e.direction === 'down') said.push({ t: e.time_s, cls: '', html: `<span class="c-dim">CPDLC</span>${esc(e.text.slice(2))}` });
    }
    while (said.length > 40) said.shift();
  }
  let lastH = '';
  function renderLog() {
    if (!log || !solo() || !state) return;
    ingest();
    const now = state.sim_time_s;
    const dl = state.cpdlc || {};
    const byDl = new Set(Object.values(dl).flatMap(d => d.open.filter(m => m.request_id).map(m => m.request_id)));
    const reqs = (state.pending_requests || []).filter(q => q.kind !== 'checkin' && !byDl.has(q.id));
    const calling = new Set((state.aircraft || []).filter(a => a.on_frequency && !a.assumed && a.status === 'active').map(a => a.callsign));
    const recent = said.filter(x => now - x.t <= KEEP_S && (x.cls !== 'call' || calling.has(x.cs))).slice(-6);
    const rows = recent.map(x => `<div class="rl ${x.cls}"><span class="c-dim">${fmtT(x.t)}</span>${x.html}${x.cls === 'call' ? ` <span class="rb"><button data-asm="${esc(x.cs)}" title="assume it (A)">Assume</button></span>` : ''}</div>`)
      .concat(reqs.slice(-4).map(q => `<div class="rl req"><span class="c-dim">${fmtT(q.time_s)}</span>${esc(q.phraseology)}${q.reason ? ` <span class="c-dim">(${esc(q.reason)})</span>` : ''}
        <span class="rb">${q.type === 'report' ? `<button data-ans="ROG" data-cs="${esc(q.callsign)}">Roger</button>` : `<button class="primary" data-ans="APP" data-cs="${esc(q.callsign)}">Approve</button><button data-ans="UNA" data-cs="${esc(q.callsign)}">Unable</button>`}</span></div>`));
    const html = rows.length ? `<div class="hd">RADIO</div>` + rows.join('') : '';
    if (html !== lastH) { log.innerHTML = html; lastH = html; }
  }
  if (log) log.addEventListener('mousedown', async e => {
    const a = e.target.closest('[data-asm]');
    if (a) { e.preventDefault(); if (typeof assume === 'function') assume(a.dataset.asm); return; }
    const b = e.target.closest('[data-ans]'); if (!b) return;
    e.preventDefault();
    try { await api(`/sessions/${SID}/cpdlc/command`, { method: 'POST', body: JSON.stringify({ text: b.dataset.ans, callsign: b.dataset.cs, voice: true }) }); if (typeof pollState === 'function') pollState(); }
    catch (err) { if (typeof msg === 'function') msg(err.message, true); }
  });
  setInterval(renderLog, 700);
})();

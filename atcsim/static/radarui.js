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
  const tlRewind = $('#tlRewind'), tlCtl = $('#tlCtl');
  setInterval(() => {
    if (!tlPlay) return;
    tlPlay.textContent = $('#rpPlay') ? $('#rpPlay').textContent : '▶ Replay';
    const rp = typeof replayMode !== 'undefined' && replayMode;
    tlLive.classList.toggle('on', !rp);
    tlCtl.classList.toggle('replay', rp && canControl());
    if (rp && tlRewind) tlRewind.textContent = `⤺ Resume from T+${fmtT(Math.floor(replayT / 10) * 10)}`;
  }, 300);
  if (tlRewind) tlRewind.onclick = async () => {
    if (!SID || typeof replayT === 'undefined') return;
    const t = replayT;
    if (!confirm(`Put the simulation back to T+${fmtT(Math.floor(t / 10) * 10)} for everyone? What happened after it is undone; it stays paused until Run.`)) return;
    try { await api(`/sessions/${SID}/control`, { method: 'POST', body: JSON.stringify({ action: 'rewind', seconds: t }) });
      if ($('#rpLive')) $('#rpLive').click(); if (typeof pollState === 'function') pollState(); }
    catch (e) { alert(e.message); }
  };


  // ------------------------------------------------------------------ COMPLY (simulated pilots): no refusal for weather or performance
  const fbtn = $('#forceBtn');
  function setForce(on) {
    window.FORCE_CLEARANCE = on;
    if (fbtn) { fbtn.classList.toggle('on', on); fbtn.textContent = on ? '⚠ COMPLY' : 'Comply'; }
    try { localStorage.setItem('atc_force', on ? '1' : '0'); } catch (e) {}
  }
  let fOn = false; try { fOn = localStorage.getItem('atc_force') === '1'; } catch (e) {}
  setForce(fOn);
  if (fbtn) fbtn.onclick = () => setForce(!window.FORCE_CLEARANCE);

  // ------------------------------------------------------------------ control bar
  const bar = $('#ctrlRow');
  const canControl = () => !EXID || SEAT === 'instructor' || SEAT === 'pilot' || solo();
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
  // bookmarks for the debrief: ⚑ Mark, a note, Enter
  const mBtn = $('#sbMarkBtn'), mIn = $('#sbMarkIn');
  function markOpen() { if (!mIn || !SID) return; mIn.hidden = false; mIn.value = ''; mIn.focus(); }
  if (mBtn) mBtn.onclick = markOpen;
  if (mIn) mIn.addEventListener('keydown', async e => {
    e.stopPropagation();
    if (e.key === 'Escape') { mIn.hidden = true; mIn.blur(); return; }
    if (e.key !== 'Enter') return;
    const note = mIn.value; mIn.hidden = true; mIn.blur();
    try { const b = await api(`/sessions/${SID}/bookmark`, { method: 'POST', body: JSON.stringify({ note }) }); if (typeof msg === 'function') msg(`⚑ marked at T+${fmtT(b.t)}: ${b.note}`); }
    catch (err) { if (typeof msg === 'function') msg(err.message, true); }
  });
  const typing = () => { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') && a.type !== 'checkbox'; };
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing() || !SID || !state || !state.session || !canControl()) return;
    if (e.key === 'p' || e.key === 'P') { e.preventDefault(); control({ action: state.session.status === 'running' ? 'pause' : 'resume' }); }
    else if ((e.key === 'k' || e.key === 'K') && EXID) { e.preventDefault(); markOpen(); }
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
      else if (e.type === 'assumed' && !/again/.test(e.text || '')) said.push({ t: e.time_s, cls: 'atc', html: `<span class="c-dim">ATC</span><b>${esc(e.callsign)}</b> identified` });
      else if (e.type === 'released') said.push({ t: e.time_s, cls: 'atc', html: `<span class="c-dim">ATC</span><b>${esc(e.callsign)}</b> ${e.unit ? 'contact ' + esc(e.unit) + ', ' : ''}good day` });
      else if (e.type === 'pilot_readback') said.push({ t: e.time_s, cls: e.ok ? '' : 'bad', cs: e.ok ? null : e.callsign, html: esc(e.text) });
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
    const refused = window.lastRefused || {};
    const rows = recent.map(x => `<div class="rl ${x.cls}"><span class="c-dim">${fmtT(x.t)}</span>${x.html}${x.cls === 'call' ? ` <span class="rb"><button data-asm="${esc(x.cs)}" title="assume it (A)">Assume</button></span>`
        : x.cls === 'bad' && x.cs && refused[x.cs] ? ` <span class="rb"><button class="frc" data-force="${esc(x.cs)}" title="The pilot flies it anyway (weather, performance): to see what it would have given">Force</button></span>` : ''}</div>`)
      .concat(reqs.slice(-4).map(q => `<div class="rl req"><span class="c-dim">${fmtT(q.time_s)}</span>${esc(q.phraseology)}${q.reason ? ` <span class="c-dim">(${esc(q.reason)})</span>` : ''}
        <span class="rb">${q.type === 'report' ? `<button data-ans="ROG" data-cs="${esc(q.callsign)}">Roger</button>` : `<button class="primary" data-ans="APP" data-cs="${esc(q.callsign)}">Approve</button><button data-ans="UNA" data-cs="${esc(q.callsign)}">Unable</button>`}</span></div>`));
    const html = rows.length ? `<div class="hd">RADIO</div>` + rows.join('') : '';
    if (html !== lastH) { log.innerHTML = html; lastH = html; }
  }
  if (log) log.addEventListener('mousedown', async e => {
    const fb = e.target.closest('[data-force]');
    if (fb) {                                          // fly the refused clearance anyway
      e.preventDefault();
      const cs = fb.dataset.force, r = (window.lastRefused || {})[cs]; if (!r) return;
      delete window.lastRefused[cs];
      try {
        const body = { ...r.body, force: true };
        const out = await api(`/sessions/${SID}/${r.kind === 'cpdlc' ? 'cpdlc/command' : 'entries'}`, { method: 'POST', body: JSON.stringify(body) });
        if (typeof msg === 'function') msg(`${cs}: forced`);
      } catch (err) { if (typeof msg === 'function') msg(err.message, true); }
      lastH = ''; renderLog(); return;
    }
    const a = e.target.closest('[data-asm]');
    if (a) { e.preventDefault(); if (typeof assume === 'function') assume(a.dataset.asm); return; }
    const b = e.target.closest('[data-ans]'); if (!b) return;
    e.preventDefault();
    try { await api(`/sessions/${SID}/cpdlc/command`, { method: 'POST', body: JSON.stringify({ text: b.dataset.ans, callsign: b.dataset.cs, voice: true }) }); if (typeof pollState === 'function') pollState(); }
    catch (err) { if (typeof msg === 'function') msg(err.message, true); }
  });
  // a rewind (or a restart) by anyone: the recording, the event log and what was derived from them start again
  let seenRw = null;
  setInterval(() => {
    if (!state || !state.session) return;
    const rw = state.session.rewinds || 0;
    if (seenRw === null) { seenRw = rw; return; }
    if (rw === seenRw) return;
    seenRw = rw;
    try { rec = null; lastSeq = 0; eventsBuf = []; replayMode = false; replayPlaying = false; } catch (e) {}
    said.length = 0; seenSeq = 0;
    if (typeof pollEvents === 'function' && !DBID && !AIW) pollEvents();      // (the debrief's stream sends the recent events)
    window.dispatchEvent(new Event('sim-rewind'));
  }, 400);
  setInterval(renderLog, 700);
})();

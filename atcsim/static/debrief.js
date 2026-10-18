// Debrief on the simulator (/debrief/<exercise>): the radar page replays the whole exercise with its markers.
// Debrief session (started by the instructor): an open voice call; the instructor leads - what is shown, the replay
// time, the view - and everybody sees the others' cursors. "Try from here" branches the run into a live simulation
// (simulated pilots fly what is entered) to show other solutions; the original run is never changed and every try
// is kept with the exercise. Afterwards, the members open the same view to go through it again.
// Uses the radar page's globals (state, sector, rec, SID, SEAT, EXINFO, replayMode, replayT, replayPlaying, view,
// userZoom, attach, setReplay, pollRecording, toPx, fromPx, api, fmtT, ...).
(function () {
  if (!DBID) return;
  const h = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = s => document.querySelector(s);
  let INFO = null, voice = null, follow = true, lastView = null, applying = false, pending = null, opening = false;
  const run = { src: null, sid: null, writable: false, kind: null };
  const cursors = {};                   // conn -> {x, y, name, leader, at}

  // ------------------------------------------------------------------ start
  async function init() {
    document.body.classList.add('exercise', 'seat-atc', 'solo', 'debrief');
    SEAT = 'atc';
    try { INFO = await api(`/api/debriefs/${DBID}`); }
    catch (e) { document.body.innerHTML = `<p style="padding:30px;font:15px system-ui;color:#c9d6e2">${h(e.message)} · <a style="color:#7fb8ff" href="/">home</a></p>`; return; }
    EXINFO = Object.assign({}, INFO, { solo: true, debrief: true });
    document.title = `Debrief · ${INFO.name}`;
    document.body.classList.toggle('db-lead', !!INFO.leader);
    build();
    resize();
    if (INFO.debrief_status === 'none') { banner('This exercise ended before debriefs on the simulator existed: only its <a href="/debrief/' + DBID + '/report">report</a> is available.'); return; }
    await openRun('orig', { t: 0, rp: true });
    if (INFO.debrief_status === 'live') joinCall();
    render();
    setInterval(refresh, 5000);
    setInterval(leaderSync, 150);
    setInterval(render, 400);
    requestAnimationFrame(drawCursors);
  }
  async function refresh() {
    try {
      const was = INFO.debrief_status;
      INFO = await api(`/api/debriefs/${DBID}`);
      Object.assign(EXINFO, INFO, { solo: true, debrief: true });
      if (INFO.debrief_status === 'live' && !voice) joinCall();
      if (was === 'live' && INFO.debrief_status !== 'live') {
        if (voice) { voice.closed = true; try { voice.ws.close(); } catch (e) {} voice = null; }
        banner(INFO.leader ? 'Debrief ended. It stays with the exercise: the controller can go through it again here. <a href="/#evaluations/' + DBID + '">Evaluate the controller now</a>'
          : 'The instructor ended the debrief. It stays with the exercise: open it again from your exercises whenever you want.', 9000);
        if (run.kind === 'branch' && !INFO.leader) openRun(run.src, { rp: replayMode, t: replayT });     // read-only now
      }
      render();
    } catch (e) {}
  }

  // ------------------------------------------------------------------ the runs: the original, the tries
  async function openRun(src, o) {
    o = o || {};
    if (opening) return;
    opening = true;
    try {
      const keep = sector ? { cx: view.cx, cy: view.cy, z: userZoom } : null;
      const r = await api(`/api/debriefs/${DBID}/open`, { method: 'POST', body: JSON.stringify({ src: String(src) }) });
      Object.assign(run, { src: String(src), sid: r.sid, writable: r.writable, kind: r.kind });
      if (r.kind === 'branch' && !(INFO.branches || []).some(b => String(b.n) === String(src))) refresh();     // a try just made
      if (SID !== r.sid) await attach(r.sid);
      if (keep) { view.cx = keep.cx; view.cy = keep.cy; setZoom(keep.z); }
      await pollRecording();
      document.body.classList.toggle('db-ro', !r.writable);
      document.body.classList.toggle('db-orig', r.kind === 'orig');
      if (r.kind === 'orig' || o.rp) { replayPlaying = !!o.play; setReplay(true, o.t != null ? o.t : 0); }
      else { replayPlaying = false; setReplay(false); }
      render();
    } catch (e) { banner(h(e.message), 5000); }
    finally { opening = false; }
  }

  // ------------------------------------------------------------------ the page's additions
  function build() {
    const bar = document.createElement('div'); bar.id = 'dbBar'; $('#scope').appendChild(bar);
    const cur = document.createElement('div'); cur.id = 'dbCursors'; $('#scope').appendChild(cur);
    const msg = document.createElement('div'); msg.id = 'dbMsg'; $('#scope').appendChild(msg);
    const ctl = $('#tlCtl');
    ctl.insertAdjacentHTML('beforeend', `<select id="dbSpd" title="Replay speed">${[1, 5, 10, 30, 60].map(v => `<option value="${v}" ${v === 10 ? 'selected' : ''}>×${v}</option>`).join('')}</select>
      <button id="dbTry" class="db-lead-only" title="Branch the run here into a live simulation to show another solution (the original run is not changed)">⑂ Try from here</button>
      <span id="dbTryBox" hidden><input id="dbTryName" placeholder="what you try, e.g. turn RAM12 earlier" maxlength="60"><button id="dbTryGo" class="primary">Start the try</button></span>`);
    $('#dbSpd').onchange = e => { $('#rpSpeed').value = e.target.value; };
    $('#dbSpd').addEventListener('keydown', e => e.stopPropagation());
    $('#dbTry').onclick = () => { const b = $('#dbTryBox'); b.hidden = !b.hidden; if (!b.hidden) $('#dbTryName').focus(); };
    $('#dbTryName').addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') $('#dbTryGo').click(); if (e.key === 'Escape') $('#dbTryBox').hidden = true; });
    $('#dbTryGo').onclick = tryHere;
    bar.addEventListener('click', onBarClick);
    // the controller leaves the instructor's moment by moving the timeline themselves
    $('#tl').addEventListener('pointerdown', () => { if (followMode()) { follow = false; render(); } }, true);
    // everybody's cursor on the scope, for the others
    let lastCur = 0;
    $('#cv').addEventListener('mousemove', e => {
      if (!voice || Date.now() - lastCur < 60) return;
      lastCur = Date.now();
      const r = $('#cv').getBoundingClientRect(), [x, y] = fromPx((e.clientX - r.left) * devicePixelRatio, (e.clientY - r.top) * devicePixelRatio);
      voice.send({ op: 'cursor', x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 });
    });
    $('#cv').addEventListener('mouseleave', () => { if (voice) voice.send({ op: 'cursor', x: null, y: null }); });
  }
  const followMode = () => !!voice && !INFO.leader && follow;
  function banner(html, ms) {
    const m = $('#dbMsg'); m.innerHTML = html; m.style.display = 'block';
    clearTimeout(banner.t); if (ms) banner.t = setTimeout(() => { m.style.display = 'none'; }, ms);
  }
  let lastBar = '';
  function render() {
    if (!INFO) return;
    if (replayMode && state && typeof renderSide === 'function') renderSide();     // the information box follows the replay
    const st = INFO.debrief_status, live = st === 'live';
    const pill = live ? '<span class="db-pill live">● LIVE DEBRIEF</span>' : st === 'done' ? '<span class="db-pill">DEBRIEF</span>' : '<span class="db-pill ready">NOT HELD YET</span>';
    const tabs = [`<button data-src="orig" class="${run.src === 'orig' ? 'on' : ''}" title="The exercise as it was flown">Exercise</button>`]
      .concat((INFO.branches || []).map(b => `<button data-src="${b.n}" class="${run.src === String(b.n) ? 'on' : ''}" title="${h(b.name)} · from T+${fmtT(b.t0)} · ${h(b.by)}">⑂ ${h(b.name)} <span class="c-dim">T+${fmtT(b.t0)}</span></button>`));
    let call = '';
    if (voice) {
      const me = voice.me ? voice.me.conn : null;
      call = `<span class="db-call"><button data-act="mute" class="${voice.muted ? 'muted' : 'onair'}" title="Your microphone: everybody hears you unless muted">${voice.muted ? '🔇 Muted' : voice.node ? '🎙 On' : '🎙 Click to talk'}</button>` +
        (voice.roster || []).filter(r => r.conn !== me).map(r => `<span class="db-who ${voice.speaking(r.conn) ? 'talk' : ''}" title="${h(r.seat)}${r.leader ? ', leading' : ''}">${r.muted ? '🔇' : '●'} ${h(r.name)}${r.leader ? ' ★' : ''}</span>`).join('') + '</span>';
      if (!INFO.leader) call += follow ? `<button data-act="unfollow" class="db-follow on" title="Your screen follows the instructor's (what is shown, the time, the view)">Following the instructor</button>`
        : `<button data-act="follow" class="db-follow" title="Back to what the instructor shows">↺ Follow the instructor</button>`;
    }
    const lead = INFO.leader ? (live ? `<button data-act="end" class="db-end">End debrief</button>`
      : `<button data-act="start" class="primary">● ${st === 'done' ? 'Debrief again' : 'Start the debrief session'}</button>`) : '';
    const links = `<a href="/debrief/${DBID}/report" target="_blank" title="The written report: workload, losses and what led to them, requests, read-backs">Report ↗</a>` +
      (INFO.leader && !INFO.solo ? `<a href="/#evaluations/${DBID}">Evaluate</a>` : '') + `<a href="/#exercises">Home</a>`;
    const html = `<div class="db-row"><b>Debrief</b><span class="db-name">${h(INFO.name)}</span>${pill}${lead}${call}<span class="db-links">${links}</span></div>
      <div class="db-row db-tabs">${tabs.join('')}</div>`;
    if (html !== lastBar) { $('#dbBar').innerHTML = html; lastBar = html; }
    const tb = $('#dbTry');
    if (tb) {
      tb.textContent = `⑂ Try from T+${fmtT(Math.floor((replayMode ? replayT : (state ? state.sim_time_s : 0)) / 10) * 10)}`;
      tb.style.display = INFO.leader && run.kind === 'orig' ? '' : 'none';
    }
    if ($('#dbSpd') && $('#dbSpd').value !== $('#rpSpeed').value) $('#dbSpd').value = $('#rpSpeed').value;
  }
  async function onBarClick(e) {
    const t = e.target.closest('[data-src]');
    if (t) { if (followMode()) follow = false; openRun(t.dataset.src, t.dataset.src === 'orig' ? { t: replayMode ? replayT : 0, rp: true } : {}); return; }
    const a = e.target.closest('[data-act]'); if (!a) return;
    const act = a.dataset.act;
    if (act === 'mute' && voice) { if (!voice.node) { await voice.ensureMic(); render(); return; } voice.mute(!voice.muted); render(); }
    else if (act === 'follow') { follow = true; lastView = null; if (voice && voice.lastSync) onSync(voice.lastSync); render(); }
    else if (act === 'unfollow') { follow = false; render(); }
    else if (act === 'start') {
      try { INFO = await api(`/api/debriefs/${DBID}/start`, { method: 'POST' }); joinCall(); render();
        banner('Debrief session on: the members see it live on their home page and join here. They follow your screen and hear you.', 7000); }
      catch (err) { banner(h(err.message), 5000); }
    } else if (act === 'end') {
      if (!confirm('End the debrief session? The call closes. The debrief (the exercise and every try) stays with the exercise.')) return;
      try { await api(`/api/debriefs/${DBID}/end`, { method: 'POST' }); refresh(); } catch (err) { banner(h(err.message), 5000); }
    }
  }
  async function tryHere() {
    const t = replayMode ? replayT : (state ? state.sim_time_s : 0);
    try {
      const b = await api(`/api/debriefs/${DBID}/branches`, { method: 'POST', body: JSON.stringify({ t, name: $('#dbTryName').value }) });
      $('#dbTryBox').hidden = true; $('#dbTryName').value = '';
      INFO.branches = (INFO.branches || []).concat([b]);
      await openRun(String(b.n));
      banner(`⑂ ${h(b.name)}: the simulation from T+${fmtT(b.t0)}, paused. Enter clearances in the labels or the command line (the pilots fly them), then ▶ Run. The exercise itself is not changed.`, 9000);
    } catch (e) { banner(h(e.message), 5000); }
  }

  // ------------------------------------------------------------------ the session: open call, sync, cursors
  function joinCall() {
    if (voice) return;
    voice = new Voice(null, {
      onHello: m => { if (m.sync && !INFO.leader) { voice.lastSync = m.sync; onSync(m.sync); } render(); },
      onRoster: render,
      onSync: s => { voice.lastSync = s; onSync(s); },
      onCursor: m => { cursors[m.conn] = { ...m, at: Date.now() }; },
      onClosed: () => { voice = null; refresh(); },
      onError: e => banner(h(e), 6000),
    }, { url: `/debriefs/${DBID}/comms`, open: true });
    const pre = () => { if (voice) voice.ensureMic().then(render); window.removeEventListener('pointerdown', pre); };
    window.addEventListener('pointerdown', pre);
  }
  let lastSent = '', lastAt = 0;
  function leaderSync() {
    if (!voice || !INFO || !INFO.leader || !run.sid || opening) return;
    const s = { src: run.src, rp: replayMode, t: Math.round(replayT * 10) / 10, play: replayPlaying, spd: +$('#rpSpeed').value,
                cx: Math.round(view.cx * 100) / 100, cy: Math.round(view.cy * 100) / 100, z: Math.round(userZoom * 1000) / 1000 };
    const sig = JSON.stringify({ ...s, t: s.play ? 0 : s.t }), now = Date.now();
    if (sig !== lastSent || now - lastAt > (s.play ? 1000 : 3000)) { voice.send({ op: 'sync', s }); lastSent = sig; lastAt = now; }
  }
  async function onSync(s) {
    if (!followMode() || !s) return;
    if (applying) { pending = s; return; }
    applying = true;
    try {
      if (s.src !== run.src) await openRun(s.src, { t: s.t, rp: s.rp, play: s.play });
      if (s.rp) {
        $('#rpSpeed').value = String(s.spd || 10);
        replayPlaying = !!s.play;
        if (!replayMode || Math.abs(replayT - s.t) > (s.play ? 2 : 0.05)) setReplay(true, s.t); else setReplay(true);
      } else if (replayMode) { replayPlaying = false; setReplay(false); }
      const v = `${s.cx},${s.cy},${s.z}`;
      if (s.cx != null && v !== lastView) { lastView = v; view.cx = s.cx; view.cy = s.cy; setZoom(s.z); }
    } finally {
      applying = false;
      if (pending) { const p = pending; pending = null; onSync(p); }
    }
  }
  function drawCursors() {
    const box = $('#dbCursors'), now = Date.now(), dpr = devicePixelRatio;
    for (const [id, c] of Object.entries(cursors)) {
      let el = box.querySelector(`[data-c="${id}"]`);
      const gone = !voice || c.x == null || now - c.at > 5000 || !sector || !(voice.roster || []).some(r => String(r.conn) === id);
      if (gone) { if (el) el.style.display = 'none'; continue; }
      if (!el) {
        el = document.createElement('div'); el.className = 'db-cur' + (c.leader ? ' lead' : ''); el.dataset.c = id;
        el.innerHTML = `<svg width="18" height="22" viewBox="0 0 18 22"><path d="M1 1 L1 17 L5.5 13 L8.5 20.5 L11.5 19.2 L8.6 12 L15 12 Z"/></svg><span>${h(c.name)}</span>`;
        box.appendChild(el);
      }
      const [px, py] = toPx(c.x, c.y);
      el.style.display = ''; el.style.transform = `translate(${px / dpr}px, ${py / dpr}px)`;
    }
    requestAnimationFrame(drawCursors);
  }
  init();
})();

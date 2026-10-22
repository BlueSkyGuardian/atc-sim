// Debrief on the simulator (/debrief/<exercise>). The radar shows a *player*: the exercise re-simulated 1:1 (control
// states, label entries, coordination, alerts as they were), with play / pause / seek / speed. Any action on it
// (assume, decontrol, a level, a heading... in the labels or the command line) creates a new timeline from that
// second, flown by a synthetic pseudo-pilot and extrapolated to the end straight away; timelines can be branched
// again and switched between (the panel on the left), each with its outcome.
// Debrief session (the instructor starts it): one shared player, an open voice call, the instructor runs the player,
// everybody may act on it; views and cursors are shared. Outside a session, every member has their own player.
// Uses the radar page's globals (state, sector, rec, SID, SEAT, EXINFO, recOffline, view, userZoom, attach, toPx,
// fromPx, api, fmtT, setZoom, drawTimeline...).
(function () {
  if (!DBID) return;
  const h = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = s => document.querySelector(s);
  let INFO = null, voice = null, follow = true, lastView = null, P = { sid: null, can_write: false, can_control: false, shared: false };
  let TL = null, tlId = null, tlFetched = 0, known = new Set(), opening = false;
  const cursors = {};
  const dbg = () => (state && state.session && state.session.debrief) || {};

  // ------------------------------------------------------------------ start
  async function init() {
    document.body.classList.add('exercise', 'seat-atc', 'solo', 'debrief');
    SEAT = 'atc';
    try { INFO = await api(`/api/debriefs/${DBID}`); }
    catch (e) { document.body.innerHTML = `<p style="padding:30px;font:15px system-ui;color:#c9d6e2">${h(e.message)} · <a style="color:#7fb8ff" href="/">home</a></p>`; return; }
    EXINFO = Object.assign({}, INFO, { solo: true, debrief: true });
    document.title = `Debrief · ${INFO.name}`;
    document.body.classList.toggle('db-lead', !!INFO.leader);
    recOffline = true;                                   // no replay in the browser: the player is the replay
    build();
    resize();
    if (INFO.debrief_status === 'none') { banner('This exercise ended before debriefs on the simulator existed: only its <a href="/debrief/' + DBID + '/report">report</a> is available.'); return; }
    INFO.timelines.forEach(t => known.add(String(t.id)));
    await openPlayer();
    if (INFO.debrief_status === 'live') joinCall();
    setInterval(refresh, 3000);
    setInterval(tick, 250);
    setInterval(leaderSync, 150);
    requestAnimationFrame(drawCursors);
  }
  async function openPlayer() {
    try {
      P = await api(`/api/debriefs/${DBID}/player`, { method: 'POST' });
      document.body.classList.toggle('db-ro', !P.can_write);
      document.body.classList.toggle('db-ctl', !!P.can_control);
      if (SID !== P.sid) { const keep = sector ? { cx: view.cx, cy: view.cy, z: userZoom } : null; await attach(P.sid); if (keep) { view.cx = keep.cx; view.cy = keep.cy; setZoom(keep.z); } }
      recOffline = true;
      render();
    } catch (e) { banner(h(e.message), 6000); }
  }
  async function refresh() {
    try {
      const was = INFO.debrief_status;
      INFO = await api(`/api/debriefs/${DBID}`);
      Object.assign(EXINFO, INFO, { solo: true, debrief: true });
      if (was === 'live' && INFO.debrief_status !== 'live') {
        if (voice) { voice.closed = true; try { voice.ws.close(); } catch (e) {} voice = null; }
        banner(INFO.leader ? 'Debrief ended. It stays with the exercise: everybody can open it again and go on with their own timelines. <a href="/#evaluations/' + DBID + '">Evaluate the controller now</a>'
          : 'The instructor ended the debrief. It stays with the exercise: play it again here on your own, and try your own changes.', 12000);
      }
      if (INFO.player_sid !== SID && !opening) { opening = true; try { await openPlayer(); } finally { opening = false; } }
      if (INFO.debrief_status === 'live' && !voice) joinCall();
      for (const t of INFO.timelines) if (!known.has(String(t.id))) known.add(String(t.id));
      render();
    } catch (e) {}
  }

  // ------------------------------------------------------------------ the page's additions
  function build() {
    $('#scope').insertAdjacentHTML('beforeend', `<div id="dbBar"></div><div id="dbCursors"></div><div id="dbMsg"></div>
      <div id="dbTls"><div class="hd"><b>Timelines</b><span class="c-dim">click to show · any action branches a new one</span><button id="dbTlFold" title="Fold">–</button></div><div id="dbTlList"></div></div>
      <div id="dbCtl">
        <button data-c="start" title="To the start of this timeline">⏮</button><button data-c="back" title="30 s back">−30 s</button>
        <button data-c="play" id="dbPlay" class="primary" title="Play / pause (P)">▶ Play</button><button data-c="fwd" title="30 s forward">+30 s</button>
        <span class="sb-seg">${[1, 2, 5, 10, 30].map(v => `<button data-tf="${v}">×${v}</button>`).join('')}</span>
        <button data-c="wx" id="dbWx" title="Do the simulated pilots deviate around weather by themselves? Off: they fly what they are cleared to and only ask (you clear deviations). Switching it is a change of the timeline shown (or the setting for the timelines you make, on the exercise as flown).">WX avoid</button>
        <span id="dbT" class="mono"></span><span id="dbTlName"></span>
      </div>`);
    $('#dbBar').addEventListener('click', onBarClick);
    $('#dbTlList').addEventListener('click', onTlClick);
    $('#dbTlList').addEventListener('dblclick', onTlRename);
    $('#dbTlFold').onclick = () => { $('#dbTls').classList.toggle('fold'); $('#dbTlFold').textContent = $('#dbTls').classList.contains('fold') ? '+' : '–'; };
    $('#dbCtl').addEventListener('click', onCtl);
    // the time bar seeks the player (no replay in the browser)
    const tlc = $('#tl');
    let drag = false, lastSeek = 0;
    const tAt = e => tlTime(e);
    tlc.addEventListener('mousedown', e => { e.stopImmediatePropagation(); e.preventDefault(); if (!P.can_control) return; drag = true; seek(tAt(e)); }, true);
    tlc.addEventListener('mousemove', e => { if (drag && Date.now() - lastSeek > 80) { lastSeek = Date.now(); seek(tAt(e)); } }, true);
    window.addEventListener('mouseup', e => { if (drag) { drag = false; seek(tAt(e)); } }, true);
    tlc.addEventListener('wheel', e => { e.stopImmediatePropagation(); e.preventDefault(); if (P.can_control && state) seek(state.sim_time_s + (e.deltaY > 0 ? 30 : -30)); }, { capture: true, passive: false });
    // cursors on the scope, for the others
    let lastCur = 0;
    $('#cv').addEventListener('mousemove', e => {
      if (!voice || Date.now() - lastCur < 60) return;
      lastCur = Date.now();
      const r = $('#cv').getBoundingClientRect(), [x, y] = fromPx((e.clientX - r.left) * devicePixelRatio, (e.clientY - r.top) * devicePixelRatio);
      voice.send({ op: 'cursor', x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 });
    });
    $('#cv').addEventListener('mouseleave', () => { if (voice) voice.send({ op: 'cursor', x: null, y: null }); });
    window.tlExtra = drawTlExtra;
  }
  function banner(html, ms) {
    const m = $('#dbMsg'); m.innerHTML = html; m.style.display = 'block';
    clearTimeout(banner.t); if (ms) banner.t = setTimeout(() => { m.style.display = 'none'; }, ms);
  }
  const tlById = id => (INFO.timelines || []).find(t => String(t.id) === String(id));

  // ------------------------------------------------------------------ the player's controls
  async function control(body) { try { await api(`/sessions/${SID}/control`, { method: 'POST', body: JSON.stringify(body) }); } catch (e) { banner(h(e.message), 4000); } }
  let seeking = false, nextSeek = null;
  async function seek(t, timeline) {                 // one at a time; while one runs only the latest is kept
    if (!P.can_control) return;
    if (seeking) { nextSeek = [t, timeline]; return; }
    seeking = true;
    try { await api(`/api/debriefs/${DBID}/seek`, { method: 'POST', body: JSON.stringify({ sid: SID, t: t == null ? null : Math.max(0, t), timeline: timeline == null ? null : String(timeline) }) }); }
    catch (e) { banner(h(e.message), 4000); }
    finally { seeking = false; if (nextSeek) { const n = nextSeek; nextSeek = null; seek(n[0], n[1]); } }
  }
  function onCtl(e) {
    const b = e.target.closest('button'); if (!b || !state) return;
    if (b.dataset.tf) { control({ action: 'set_speed', time_factor: +b.dataset.tf }); return; }
    const c = b.dataset.c, now = state.sim_time_s, d = dbg();
    if (c === 'play') control({ action: state.session.status === 'running' ? 'pause' : 'resume' });
    else if (c === 'wx') control({ action: 'auto_wx', value: !d.wx_avoid });
    else if (c === 'start') seek(d.timeline === 'orig' ? 0 : d.t0);
    else if (c === 'back') seek(now - 30);
    else if (c === 'fwd') seek(now + 30);
  }
  function tick() {
    if (!state || !state.session) return;
    const d = dbg(), s = state.session, run = s.status === 'running';
    $('#dbPlay').textContent = run ? '❚❚ Pause' : '▶ Play';
    const wb = $('#dbWx'); if (wb) { const on = d.wx_avoid !== false; wb.textContent = on ? 'WX avoid: on' : 'WX avoid: OFF'; wb.classList.toggle('off', !on); }
    $('#dbCtl').querySelectorAll('[data-tf]').forEach(b => b.classList.toggle('on', +b.dataset.tf === +s.time_factor));
    $('#dbT').textContent = `T+${fmtT(state.sim_time_s)} / ${fmtT(Math.max(d.end_t || 0, d.frontier || 0))}`;
    const t = tlById(d.timeline);
    $('#dbTlName').innerHTML = t ? `${d.timeline === 'orig' ? '' : '⑂ '}${h(t.name)}${d.computing ? ' <span class="c-dim">· extrapolating…</span>' : ''}` : '';
    // a new timeline (somebody acted): say so, show it
    if (d.timeline != null && String(d.timeline) !== String(tlId)) {
      const isNew = !known.has(String(d.timeline));
      tlId = d.timeline; tlFetched = 0;
      if (isNew) { known.add(String(d.timeline)); refresh().then(() => { const n = tlById(d.timeline);
        banner(`⑂ New timeline from T+${fmtT(d.t0)}: <b>${h(n ? n.name : '')}</b>. A synthetic pilot flies the clearances; it is extrapolated to the end of the exercise (the other aircraft do what they did). Your next actions within a minute are added to it (one solution, several clearances); later ones branch again.`, 9000); }); }
      render();
    }
    if (Date.now() - tlFetched > (d.computing ? 2500 : 30000)) loadTl(d.timeline);
    render();
  }
  function setRec() {
    if (!TL) return;
    rec = { frames: [{ t: 0, ac: [], req: [], los: [], areas: [] }, { t: TL.frontier, ac: [], req: [], los: [], areas: [] }], events: TL.events, meta: {}, duration_s: Math.max(TL.end_t || 0, (state && state.duration_s) || 0) };
  }
  window.addEventListener('sim-rewind', () => { recOffline = true; setRec(); });          // a seek: keep the time bar's markers
  async function loadTl(id) {
    if (id == null) return;
    tlFetched = Date.now();
    try {
      TL = await api(`/api/debriefs/${DBID}/timelines/${id}`);
      setRec();
    } catch (e) {}
  }
  function drawTlExtra(c, X, W, H, dpr) {
    const d = dbg(); if (!TL || d.timeline == null) return;
    if (d.timeline !== 'orig') {
      c.fillStyle = 'rgba(95, 211, 163, .10)'; c.fillRect(X(d.t0), 6 * dpr, X(TL.frontier) - X(d.t0), H - 18 * dpr);   // extrapolated
      c.fillStyle = '#5fd3a3'; c.fillRect(X(d.t0) - dpr, 0, 2 * dpr, H - 10 * dpr);
      c.font = `${9 * dpr}px ui-monospace,monospace`; c.fillText('⑂', X(d.t0) + 3 * dpr, H - 14 * dpr);
      if (d.computing) { c.fillStyle = 'rgba(242, 184, 75, .5)'; c.fillRect(X(TL.frontier) - dpr, 6 * dpr, 2 * dpr, H - 18 * dpr); }
    }
    for (const t of INFO.timelines || []) {        // where the other timelines branch off this one
      if (t.parent != null && String(t.parent) === String(d.timeline)) { c.fillStyle = 'rgba(95, 211, 163, .55)'; c.fillRect(X(t.t0), H - 12 * dpr, dpr, 4 * dpr); }
    }
  }

  // ------------------------------------------------------------------ top bar and timelines panel
  let lastBar = '', lastList = '';
  function render() {
    if (!INFO) return;
    const st = INFO.debrief_status, live = st === 'live';
    const pill = live ? '<span class="db-pill live">● LIVE DEBRIEF</span>' : st === 'done' ? '<span class="db-pill">DEBRIEF</span>' : '<span class="db-pill ready">NOT HELD YET</span>';
    let call = '';
    if (voice) {
      const me = voice.me ? voice.me.conn : null;
      call = `<span class="db-call"><button data-act="mute" class="${voice.muted ? 'muted' : 'onair'}" title="Your microphone: everybody hears you unless muted">${voice.muted ? '🔇 Muted' : voice.node ? '🎙 On' : '🎙 Click to talk'}</button>` +
        (voice.roster || []).filter(r => r.conn !== me).map(r => `<span class="db-who ${voice.speaking(r.conn) ? 'talk' : ''}" title="${h(r.seat)}${r.leader ? ', leading' : ''}">${r.muted ? '🔇' : '●'} ${h(r.name)}${r.leader ? ' ★' : ''}</span>`).join('') + '</span>';
      if (!INFO.leader) call += follow ? `<button data-act="unfollow" class="db-follow on" title="Your view follows the instructor's (pan and zoom); the timeline and the moment are the session's">Following the instructor</button>`
        : `<button data-act="follow" class="db-follow" title="Back to the instructor's view">↺ Follow the instructor</button>`;
    }
    const lead = INFO.leader ? (live ? `<button data-act="end" class="db-end">End debrief</button>`
      : `<button data-act="start" class="primary">● ${st === 'done' ? 'Debrief again' : 'Start the debrief session'}</button>`)
      + `<button data-act="invite" title="Invite people to this debrief, or copy its join link">＋ Invite</button>` : '';
    const mode = live ? (P.can_control ? 'you run the session' : 'the instructor runs it · your actions branch timelines') : 'your own player';
    const links = `<a href="/debrief/${DBID}/report" target="_blank" title="The written report">Report ↗</a>` +
      (INFO.leader && !INFO.solo ? `<a href="/#evaluations/${DBID}">Evaluate</a>` : '') + `<a href="/#debriefs">Home</a>`;
    const html = `<div class="db-row"><b>Debrief</b><span class="db-name">${h(INFO.name)}</span>${pill}<span class="c-dim" style="font-size:11.5px">${mode}</span>${lead}${call}<span class="db-links">${links}</span></div>`;
    if (html !== lastBar) { $('#dbBar').innerHTML = html; lastBar = html; }
    // the timelines, as a tree
    const cur = String(dbg().timeline), by = {};
    for (const t of INFO.timelines || []) (by[t.parent == null ? '' : String(t.parent)] = by[t.parent == null ? '' : String(t.parent)] || []).push(t);
    const row = (t, depth) => {
      const s = t.summary || {}, comp = t.status === 'computing';
      const out = comp ? `<span class="warn">extrapolating… T+${fmtT(t.frontier)}</span>` : t.status === 'failed' ? '<span class="c-los">failed</span>'
        : s.losses ? `<span class="c-los">${s.losses} loss${s.losses > 1 ? 'es' : ''} · first T+${fmtT(s.first_loss_s)}</span>` : '<span class="c-ok">no loss</span>';
      const sub = t.id === 'orig' ? `T+00:00 – T+${fmtT(t.end_t)}` : `from T+${fmtT(t.t0)} · ${h(t.by || '')}`;
      return `<div class="tlr ${String(t.id) === cur ? 'on' : ''}" data-tl="${t.id}" style="padding-left:${8 + depth * 14}px" title="${h((t.changes || []).map(c => `T+${fmtT(c.t)} ${c.text}`).join('\n'))}">
        <div class="n">${t.id === 'orig' ? '●' : '⑂'} <span>${h(t.name)}</span></div><div class="s">${sub} · ${out}</div></div>` +
        (by[String(t.id)] || []).map(c => row(c, depth + 1)).join('');
    };
    const list = (by[''] || []).map(t => row(t, 0)).join('') + (INFO.timelines.length < 2 ? `<div class="c-dim" style="font-size:11.5px;padding:6px 8px">${P.can_write ? 'Act on a label (assume, level, heading…), playing or paused: a new timeline starts there.' : ''}</div>` : '');
    if (list !== lastList) { $('#dbTlList').innerHTML = list; lastList = list; }
  }
  function onTlClick(e) {
    const r = e.target.closest('[data-tl]'); if (!r || e.target.tagName === 'INPUT') return;
    if (!P.can_control) { banner('The instructor chooses the timeline during the session.', 3000); return; }
    const t = tlById(r.dataset.tl), now = state ? state.sim_time_s : 0;
    seek(t && t.id !== 'orig' && now < t.t0 ? t.t0 : null, r.dataset.tl);
  }
  function onTlRename(e) {
    const r = e.target.closest('[data-tl]'); if (!r || r.dataset.tl === 'orig') return;
    const span = r.querySelector('.n span'), t = tlById(r.dataset.tl);
    span.innerHTML = `<input value="${h(t.name)}" maxlength="80" style="width:170px;font-size:12px">`;
    const i = span.querySelector('input'); i.focus(); i.select();
    i.addEventListener('keydown', async ev => {
      ev.stopPropagation();
      if (ev.key === 'Enter') { try { await api(`/api/debriefs/${DBID}/timelines/${t.id}`, { method: 'PATCH', body: JSON.stringify({ name: i.value }) }); } catch (er) {} lastList = ''; refresh(); }
      if (ev.key === 'Escape') { lastList = ''; render(); }
    });
  }
  // ------------------------------------------------------------------ invitations (the leader)
  const SEATS = { instructor: 'instructor', atc: 'controller', pilot: 'pseudo-pilot', guest: 'invited' };
  let invTimer = null;
  async function openInvite() {
    const el = window.atcWin('dbInvite', '<b>Invite to the debrief</b>', 80, 80, 460);
    el.querySelector('.wb').innerHTML = `<div class="c-dim" style="font-size:11.5px;margin-bottom:6px">Invited people see this debrief on their home page and in their debriefs, and open it like the exercise's members.</div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px"><input id="dbInvQ" placeholder="name, e-mail or licence" autocomplete="off" style="flex:1"></div>
      <div id="dbInvRes"></div>
      <div class="sub">Join link <span class="c-dim">(anyone with an account who opens it joins as a guest)</span></div>
      <div style="display:flex;gap:6px;align-items:center"><input id="dbInvLink" readonly style="flex:1;font-size:11px">
        <button id="dbInvCopy">Copy</button><button id="dbInvNew" title="A new link: the previous one stops working">New link</button></div>
      <div class="sub">In this debrief</div><div id="dbInvPeople" class="c-dim">…</div>`;
    const q = el.querySelector('#dbInvQ');
    q.addEventListener('input', () => { clearTimeout(invTimer); invTimer = setTimeout(search, 250); });
    q.addEventListener('keydown', e => e.stopPropagation());
    el.querySelector('#dbInvCopy').onclick = async () => { const l = el.querySelector('#dbInvLink'); l.select();
      try { await navigator.clipboard.writeText(l.value); banner('Join link copied', 2500); } catch (e) { document.execCommand('copy'); } };
    el.querySelector('#dbInvNew').onclick = async () => {
      if (!confirm('Make a new join link? The current one stops working.')) return;
      try { const r = await api(`/api/debriefs/${DBID}/link`, { method: 'POST' }); el.querySelector('#dbInvLink').value = r.link; } catch (e) { banner(h(e.message), 4000); } };
    el.querySelector('#dbInvRes').onclick = async e => { const b = e.target.closest('[data-add]'); if (!b) return;
      try { const r = await api(`/api/debriefs/${DBID}/invite`, { method: 'POST', body: JSON.stringify({ user_ids: [+b.dataset.add] }) });
        people(r.people); search(); } catch (err) { banner(h(err.message), 4000); } };
    el.querySelector('#dbInvPeople').onclick = async e => { const b = e.target.closest('[data-rm]'); if (!b) return;
      try { const r = await api(`/api/debriefs/${DBID}/invite/${b.dataset.rm}`, { method: 'DELETE' }); people(r.people); } catch (err) { banner(h(err.message), 4000); } };
    try { const r = await api(`/api/debriefs/${DBID}/invite`); el.querySelector('#dbInvLink').value = r.link; people(r.people); }
    catch (e) { el.querySelector('#dbInvPeople').textContent = e.message; }
    q.focus();
  }
  function people(L) {
    const el = document.getElementById('dbInvPeople'); if (!el) return;
    el.innerHTML = L.map(p => `<div style="display:flex;gap:6px;align-items:center;padding:2px 0"><span style="flex:1;color:#cfe0ee">${h(p.name)} <span class="c-dim">${h(p.email)}</span></span>
      <span class="c-dim">${SEATS[p.seat] || h(p.seat)}${p.owner ? ' · leads' : ''}</span>${p.seat === 'guest' ? `<button class="small" data-rm="${p.id}" title="Remove the invitation">✕</button>` : ''}</div>`).join('') || 'nobody yet';
  }
  async function search() {
    const el = document.getElementById('dbInvRes'), q = document.getElementById('dbInvQ'); if (!el || !q) return;
    const v = q.value.trim(); if (v.length < 2) { el.innerHTML = ''; return; }
    try { const L = await api(`/api/debriefs/${DBID}/invitable?q=${encodeURIComponent(v)}`);
      el.innerHTML = L.map(p => `<div style="display:flex;gap:6px;align-items:center;padding:2px 0"><span style="flex:1">${h(p.name)} <span class="c-dim">${h(p.email)}${p.licence ? ' · ' + h(p.licence) : ''}</span></span><button class="small primary" data-add="${p.id}">Invite</button></div>`).join('')
        || '<div class="c-dim">nobody found (or already in it)</div>'; }
    catch (e) { el.textContent = e.message; }
  }
  async function onBarClick(e) {
    const a = e.target.closest('[data-act]'); if (!a) return;
    const act = a.dataset.act;
    if (act === 'mute' && voice) { if (!voice.node) { await voice.ensureMic(); render(); return; } voice.mute(!voice.muted); lastBar = ''; render(); }
    else if (act === 'follow') { follow = true; lastView = null; if (voice && voice.lastSync) onSync(voice.lastSync); lastBar = ''; render(); }
    else if (act === 'unfollow') { follow = false; lastBar = ''; render(); }
    else if (act === 'start') {
      try { INFO = await api(`/api/debriefs/${DBID}/start`, { method: 'POST' }); await openPlayer(); joinCall(); render();
        banner('Debrief session on: one shared player. The members see it live on their home page and join; they follow your view and hear you. You run the player; anybody may act on it (each action branches a timeline).', 8000); }
      catch (err) { banner(h(err.message), 5000); }
    } else if (act === 'invite') { openInvite();
    } else if (act === 'end') {
      if (!confirm('End the debrief session? The call closes. Every timeline stays with the exercise.')) return;
      try { await api(`/api/debriefs/${DBID}/end`, { method: 'POST' }); refresh(); } catch (err) { banner(h(err.message), 5000); }
    }
  }

  // ------------------------------------------------------------------ the session: open call, shared view, cursors
  function joinCall() {
    if (voice) return;
    voice = new Voice(null, {
      onHello: m => { if (m.sync && !INFO.leader) { voice.lastSync = m.sync; onSync(m.sync); } lastBar = ''; render(); },
      onRoster: render,
      onSync: s => { voice.lastSync = s; onSync(s); },
      onCursor: m => { cursors[m.conn] = { ...m, at: Date.now() }; },
      onClosed: () => { voice = null; refresh(); },
      onError: e => banner(h(e), 6000),
    }, { url: `/debriefs/${DBID}/comms`, open: true });
    const pre = () => { if (voice) voice.ensureMic().then(() => { lastBar = ''; render(); }); window.removeEventListener('pointerdown', pre); };
    window.addEventListener('pointerdown', pre);
    setInterval(() => { if (voice) render(); }, 400);                         // who is speaking (redrawn only when it changes)
  }
  let lastSent = '', lastAt = 0;
  function leaderSync() {
    if (!voice || !INFO || !INFO.leader) return;
    const s = { cx: Math.round(view.cx * 100) / 100, cy: Math.round(view.cy * 100) / 100, z: Math.round(userZoom * 1000) / 1000 };
    const sig = JSON.stringify(s), now = Date.now();
    if (sig !== lastSent || now - lastAt > 3000) { voice.send({ op: 'sync', s }); lastSent = sig; lastAt = now; }
  }
  function onSync(s) {
    if (!voice || INFO.leader || !follow || !s || s.cx == null) return;
    const v = `${s.cx},${s.cy},${s.z}`;
    if (v !== lastView) { lastView = v; view.cx = s.cx; view.cy = s.cy; setZoom(s.z); }
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

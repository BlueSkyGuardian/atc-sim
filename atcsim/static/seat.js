// Connected exercise (/x/<id>): the controller's, pseudo-pilot's and instructor's seats on top of the radar page.
// Uses the radar page's globals (state, sector, SID, SEAT, EXID, selected, api, attach, ...).
(function () {
  if (!EXID) return;
  const SEATN = { instructor: 'Instructor', atc: 'Controller', pilot: 'Pseudo-pilot' };
  const h = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let voice = null, pilotSel = null, ended = false, userList = null;
  const rows = {};

  // ------------------------------------------------------------------ start
  async function init() {
    try { EXINFO = await api(`/api/exercises/${EXID}`); }
    catch (e) { document.body.innerHTML = `<p style="padding:30px;font:15px system-ui;color:#c9d6e2">${h(e.message)} · <a style="color:#7fb8ff" href="/">home</a></p>`; return; }
    SEAT = EXINFO.seat;
    document.body.classList.add('exercise', 'seat-' + SEAT);
    document.title = `${EXINFO.name} · ${SEATN[SEAT]}`;
    resize();
    if (!EXINFO.live) { showEnded(); return; }
    buildUnits();
    voice = window.__voice = new Voice(EXID, {
      onCalls: renderCalls, onRoster: renderMembers, onHello: renderMembers, onPtt: renderRx, onTx: renderTx,
      onClosed: showEnded, onError: e => note(e, true),
    });
    // ask for the microphone at the first click/key, so the first transmission is not cut
    const pre = () => { voice.ensureMic(); window.removeEventListener('pointerdown', pre); window.removeEventListener('keydown', pre); };
    window.addEventListener('pointerdown', pre); window.addEventListener('keydown', pre);
    setupPTT();
    if (SEAT === 'instructor') setupInstructor();
    await attach(EXID);
    setInterval(tick, 500);
  }
  function note(t, bad) {
    const el = SEAT === 'atc' ? $('#rxBox') : $('#cmdmsg');
    if (SEAT === 'atc') { el.innerHTML = `<span class="rx" style="background:${bad ? '#5a1d1d' : '#1d3a5c'}">${h(t)}</span>`; setTimeout(renderRx, 4000); }
    else { el.textContent = t; el.className = bad ? 'c-los' : 'c-ok'; }
  }
  function showEnded() {
    if (ended) return; ended = true;
    if (voice) { voice.tone(null); voice.closed = true; }
    SID = null;                                   // stop polling the finished session
    if (ws) { try { ws.onclose = null; ws.close(); } catch (e) {} }
    const b = $('#pauseBanner'); b.style.display = 'block'; b.style.color = '#c9d6e2';
    b.innerHTML = 'This exercise has ended. <a href="/" style="pointer-events:auto;color:#7fb8ff">Back to the home page</a>';
    b.style.pointerEvents = 'auto';
  }
  function tick() {
    if (ended || !state || !state.session) return;
    const st = state.session.status, b = $('#pauseBanner');
    if (st !== 'running') {
      b.style.display = 'block';
      b.textContent = st === 'finished' ? 'Scenario finished' : st === 'created' ? (SEAT === 'instructor' ? 'Ready · press Run to start' : 'Waiting for the instructor to start the exercise') : 'PAUSED';
    } else b.style.display = 'none';
    if (SEAT !== 'atc') renderPilot();
    if (SEAT === 'atc' && $('#lblMenu').classList.contains('on')) refreshMenuHead();
  }

  // ------------------------------------------------------------------ push-to-talk
  function typing() { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') && a.type !== 'checkbox'; }
  function setupPTT() {
    window.addEventListener('keydown', e => {
      if (e.code !== 'Space' || typing()) return;
      e.preventDefault(); if (!e.repeat) voice.ptt(true);
    });
    window.addEventListener('keyup', e => { if (e.code === 'Space' && voice.tx) { e.preventDefault(); voice.ptt(false); } });
    window.addEventListener('blur', () => voice.ptt(false));
    const k = $('#pttKey');
    k.addEventListener('pointerdown', e => { e.preventDefault(); voice.ptt(true); });
    for (const ev of ['pointerup', 'pointerleave']) k.addEventListener(ev, () => voice.ptt(false));
    // the space bar must not press a focused button
    document.addEventListener('keyup', e => { if (e.code === 'Space' && !typing()) e.preventDefault(); }, true);
  }
  function renderTx(on) {
    const k = $('#pttKey');
    k.classList.toggle('tx', on);
    k.textContent = on ? '● TRANSMITTING' : (voice.micError ? '🎙 ' + voice.micError : '🎙 hold SPACE to transmit');
  }
  function renderRx() {
    if (!voice) return;
    const on = voice.roster.filter(r => r.ptt && (!voice.me || r.conn !== voice.me.conn));
    let html = on.map(r => `<span class="rx">◉ ${h(r.name)} <span style="opacity:.7">${SEATN[r.seat]}</span></span>`).join('');
    if (SEAT === 'instructor') html += voice.calls.filter(c => c.state === 'active').map(c => `<span class="rx ph">☎ ${c.unit} ${c.direction === 'atc_to_unit' ? '← ATC' : '→ ATC'}</span>`).join('');
    $('#rxBox').innerHTML = html;
    renderMembers();
  }

  // ------------------------------------------------------------------ telephone
  function buildUnits() {
    const units = EXINFO.units || ['GMMM', 'LPPC', 'GCCC', 'LECS', 'MADEIRA'], names = EXINFO.unit_names || {};
    $('#atcUnits').innerHTML = units.map(u => `<button data-unit="${u}">${u}<small>${h(names[u] || '')}</small></button>`).join('');
    $('#pilotUnits').innerHTML = units.map(u => `<button data-unit="${u}" title="Call the controller as ${h(names[u] || u)}">${u}</button>`).join('');
    document.querySelectorAll('[data-unit]').forEach(b => b.onclick = () => { voice.call(b.dataset.unit); $('#phonePanel').classList.remove('on'); });
    $('#phoneBtn').onclick = () => $('#phonePanel').classList.toggle('on');
  }
  const mine = c => voice.me && ((c.caller && c.caller.conn === voice.me.conn) || (c.answerer && c.answerer.conn === voice.me.conn));
  function canAnswer(c) {
    if (c.state !== 'ringing' || mine(c)) return false;
    return c.direction === 'unit_to_atc' ? (SEAT === 'atc' || (SEAT === 'instructor' && !voice.roster.some(r => r.seat === 'atc'))) : SEAT !== 'atc';
  }
  function since(t) { return fmtT(Date.now() / 1000 - t); }
  function renderCalls() {
    if (!voice) return;
    const calls = voice.calls, hud = [];
    for (const c of calls) {
      const other = c.direction === 'unit_to_atc' ? (SEAT === 'atc' ? `${c.unit} · ${h(c.unit_name)}` : `ATC ← ${c.unit}`) : (SEAT === 'atc' ? `${c.unit} · ${h(c.unit_name)}` : `ATC → ${c.unit}`);
      if (canAnswer(c)) hud.push(`<div class="callbox ring"><div class="u">☎ ${other}</div><div class="d">${c.direction === 'unit_to_atc' ? 'is calling you' : `the controller is calling ${c.unit} – answer as ${c.unit}`}</div>
        <button class="ans" data-ans="${c.id}">Answer</button><button class="hang" data-hang="${c.id}">Decline</button></div>`);
      else if (mine(c) && c.state === 'ringing') hud.push(`<div class="callbox"><div class="u">☎ ${other}</div><div class="d">calling…</div><button class="hang" data-hang="${c.id}">Cancel</button></div>`);
      else if (mine(c)) hud.push(`<div class="callbox act"><div class="u">☎ ${other}</div><div class="d">on the line · <span data-since="${c.since}">${since(c.since)}</span></div><button class="hang" data-hang="${c.id}">Hang up</button></div>`);
      else if (SEAT === 'instructor') hud.push(`<div class="callbox"><div class="d" style="margin:0">☎ ${c.unit} ${c.direction === 'atc_to_unit' ? '← ATC' : '→ ATC'} · ${c.state === 'active' ? `listening in · <span data-since="${c.since}">${since(c.since)}</span>` : 'ringing'}</div></div>`);
    }
    $('#callHud').innerHTML = hud.join('');
    $('#callHud').querySelectorAll('[data-ans]').forEach(b => b.onclick = () => voice.answer(+b.dataset.ans));
    $('#callHud').querySelectorAll('[data-hang]').forEach(b => b.onclick = () => voice.hangup(+b.dataset.hang));
    const ring = calls.some(canAnswer), back = calls.some(c => mine(c) && c.state === 'ringing');
    voice.tone(ring ? 'ring' : back ? 'back' : null);
    $('#callList').innerHTML = calls.length ? '' : '<span class="c-dim">no call</span>';
    renderRx();
  }
  setInterval(() => document.querySelectorAll('[data-since]').forEach(el => el.textContent = since(+el.dataset.since)), 1000);

  // ------------------------------------------------------------------ controller: label input
  window.onSelectAircraft = (cs, e, isLabel) => {
    if (SEAT === 'atc') { openMenu(cs, e); return true; }
    pilotSel = cs; renderPilot(true);
    return false;
  };
  let menuCs = null;
  function menuPos(e) {
    const r = $('#scope').getBoundingClientRect(), m = $('#lblMenu');
    let x = e.clientX - r.left + 14, y = e.clientY - r.top - 20;
    if (x + 250 > r.width) x = e.clientX - r.left - 260;
    if (y + 330 > r.height) y = r.height - 335;
    m.style.left = Math.max(4, x) + 'px'; m.style.top = Math.max(40, y) + 'px';
  }
  function refreshMenuHead() {
    const a = state.aircraft.find(x => x.callsign === menuCs); if (!a) { closeMenu(); return; }
    const calling = a.on_frequency && !a.assumed;
    $('#lmHead').innerHTML = `${a.callsign} <span class="c-dim" style="font-weight:400">${a.type} · FL${a.fl}${a.vertical_speed_fpm > 0 ? '↑' : a.vertical_speed_fpm < 0 ? '↓' : ''} · ${a.flight_plan && a.flight_plan.departure || ''}→${a.flight_plan && a.flight_plan.destination || ''}</span>`;
    $('#lmAssume').style.display = calling ? '' : 'none';
  }
  function openMenu(cs, e) {
    const a = state && state.aircraft.find(x => x.callsign === cs); if (!a) return;
    menuCs = cs; const ent = (state.entries || {})[cs] || {}, m = $('#lblMenu');
    const base = Math.round((ent.cfl != null ? ent.cfl : a.cleared_fl) / 10) * 10, lv = [];
    for (let d = -40; d <= 40; d += 10) if (d && base + d >= 100 && base + d <= 460) lv.push(base + d);
    const route = a.route.slice(Math.max(0, a.route.indexOf(a.next_waypoint))).filter(n => !n.startsWith('@')).concat(a.route_after_exit || []);
    m.innerHTML = `<div class="h"><span id="lmHead"></span><button id="lmX" style="padding:0 6px">✕</button></div>
      <div id="lmAssume" style="margin-bottom:7px"><button class="on" id="lmAsBtn" style="width:100%;padding:4px">ASSUME ${cs}</button></div>
      <div class="f"><span class="c-dim">CFL</span><input data-f="cfl" value="${ent.cfl != null ? ent.cfl : a.cleared_fl}" inputmode="numeric"><button data-ok="cfl">OK</button></div>
      <div class="lv">${lv.map(l => `<button data-lv="${l}">${l}</button>`).join('')}</div>
      <div class="f"><span class="c-dim">Speed</span><input data-f="speed" value="${h(ent.speed || '')}" placeholder="M78 · 280 · SN"><button data-ok="speed">OK</button></div>
      <div class="f"><span class="c-dim">Heading</span><input data-f="heading" value="${ent.heading || ''}" placeholder="e.g. 250" inputmode="numeric"><button data-ok="heading">OK</button></div>
      <div class="f"><span class="c-dim">Direct</span><input data-f="direct" value="${h(ent.direct || '')}" list="lmPts" placeholder="point"><button data-ok="direct">OK</button></div>
      <div class="lv">${[...new Set(route)].slice(0, 10).map(n => `<button data-dct="${n}">${n}</button>`).join('')}</div>
      <div class="f"><span class="c-dim">Note</span><input data-f="note" value="${h(ent.note || '')}" style="text-transform:none"><button data-ok="note">OK</button></div>
      <div class="row" style="margin:6px 0 0"><button id="lmClr">clear speed/heading/direct</button></div>
      <div id="lmMsg" style="min-height:14px;font-size:11px;margin-top:4px"></div>
      <datalist id="lmPts">${Object.keys(sector.waypoints).map(n => `<option value="${n}">`).join('')}</datalist>`;
    refreshMenuHead(); menuPos(e); m.classList.add('on');
    const send = async (f, v) => {
      try { await api(`/sessions/${SID}/entries`, { method: 'POST', body: JSON.stringify({ callsign: cs, ...(typeof f === 'object' ? f : { [f]: v }) }) });
        $('#lmMsg').className = 'c-ok'; $('#lmMsg').textContent = '✓ entered'; if (state.entries) state.entries[cs] = { ...(state.entries[cs] || {}), ...(typeof f === 'object' ? f : { [f]: v }) };
        if (f === 'heading' && v) m.querySelector('[data-f=direct]').value = ''; }
      catch (er) { $('#lmMsg').className = 'c-los'; $('#lmMsg').textContent = er.message; }
    };
    m.querySelectorAll('[data-ok]').forEach(b => b.onclick = () => send(b.dataset.ok, m.querySelector(`[data-f=${b.dataset.ok}]`).value.trim()));
    m.querySelectorAll('input[data-f]').forEach(i => i.onkeydown = ev => { ev.stopPropagation(); if (ev.key === 'Enter') send(i.dataset.f, i.value.trim()); if (ev.key === 'Escape') closeMenu(); });
    m.querySelectorAll('[data-lv]').forEach(b => b.onclick = () => { m.querySelector('[data-f=cfl]').value = b.dataset.lv; send('cfl', +b.dataset.lv); });
    m.querySelectorAll('[data-dct]').forEach(b => b.onclick = () => { m.querySelector('[data-f=direct]').value = b.dataset.dct; m.querySelector('[data-f=heading]').value = ''; send({ direct: b.dataset.dct, heading: '' }); });
    $('#lmClr').onclick = () => { for (const f of ['speed', 'heading', 'direct']) m.querySelector(`[data-f=${f}]`).value = ''; send({ speed: '', heading: '', direct: '' }); };
    $('#lmAsBtn').onclick = async () => { try { await api(`/sessions/${SID}/assume/${cs}`, { method: 'POST' }); $('#lmAssume').style.display = 'none'; } catch (er) { $('#lmMsg').textContent = er.message; } };
    $('#lmX').onclick = closeMenu;
    const f = m.querySelector('[data-f=cfl]'); f.focus(); f.select();
  }
  function closeMenu() { $('#lblMenu').classList.remove('on'); menuCs = null; document.activeElement && document.activeElement.blur(); }
  window.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  $('#cv').addEventListener('mousedown', () => { if (SEAT === 'atc') closeMenu(); });
  // the controller assumes by double-click too; it goes through /assume
  window.assume = async cs => { try { await api(`/sessions/${SID}/assume/${cs}`, { method: 'POST' }); } catch (e) { note(e.message, true); } };

  // ------------------------------------------------------------------ pseudo-pilot panel (also the instructor's)
  function speedInstr(v) {
    v = v.toUpperCase().replace(/\s/g, '');
    if (v === 'SN' || v === 'RESUME') return { type: 'speed', resume_normal: true };
    let m = v.match(/^M?0?\.?(\d{2})$/); if (m && (v.startsWith('M') || v.startsWith('.') || v.startsWith('0'))) return { type: 'speed', mach: +('0.' + m[1]) };
    m = v.match(/^K(\d{3})$/); if (m) return { type: 'speed', conversion_kt: +m[1] };
    m = v.match(/^S?(\d{3})(KT)?$/); if (m) return { type: 'speed', ias_kt: +m[1] };
    return null;
  }
  async function fly(cs, instr, out) {
    try {
      const r = (await api(`/sessions/${SID}/instructions`, { method: 'POST', body: JSON.stringify({ callsign: cs, ...instr }) }))[0];
      if (out) { out.className = r.status === 'rejected' ? 'c-los' : r.warnings && r.warnings.length ? 'c-warn' : 'c-ok';
        out.textContent = r.status === 'rejected' ? r.reason : '✓ ' + (r.warnings && r.warnings.length ? r.warnings.join(' | ') : 'executing'); }
      return r;
    } catch (e) { if (out) { out.className = 'c-los'; out.textContent = e.message; } }
  }
  function pilotCtl(cs, a) {
    const pts = [...new Set(a.route.slice(Math.max(0, a.route.indexOf(a.next_waypoint))).filter(n => !n.startsWith('@')).concat(a.route_after_exit || []))];
    const el = document.createElement('div'); el.className = 'ctl';
    el.innerHTML = `<span class="c-dim">FL</span><span><input data-i="fl" placeholder="${a.cleared_fl}" inputmode="numeric"> <button data-go="fl">Climb/descend</button></span>
      <span class="c-dim">HDG</span><span><input data-i="hdg" placeholder="${String(Math.round(a.heading)).padStart(3, '0')}" inputmode="numeric"> <button data-go="hl">Left</button><button data-go="hr">Right</button><button data-go="h">Turn</button></span>
      <span class="c-dim">SPD</span><span><input data-i="spd" placeholder="M78/280/K280/SN"> <button data-go="spd">Set</button></span>
      <span class="c-dim">DCT</span><span><input data-i="dct" list="plPts" placeholder="point"> <button data-go="dct">Direct</button></span>
      <span></span><span>${pts.slice(0, 8).map(n => `<button data-d="${n}" style="font-family:var(--mono)">${n}</button>`).join(' ')}</span>
      <span></span><span><button data-go="res">Resume own navigation</button> <button data-go="dev">Deviate (weather)</button></span>
      <span></span><span data-out style="font-family:system-ui;font-size:11px"></span>`;
    const out = el.querySelector('[data-out]'), val = k => el.querySelector(`[data-i=${k}]`).value.trim();
    const go = async k => {
      if (k === 'fl') { const v = parseInt(val('fl').replace(/^F(L)?/i, ''), 10); if (!v) return; await fly(cs, { type: 'altitude', fl: v }, out); }
      else if (k === 'h' || k === 'hl' || k === 'hr') { const v = parseInt(val('hdg'), 10); if (!v) return; await fly(cs, { type: 'heading', heading: v, turn: k === 'hl' ? 'left' : k === 'hr' ? 'right' : null }, out); }
      else if (k === 'spd') { const s = speedInstr(val('spd')); if (!s) { out.className = 'c-los'; out.textContent = 'speed: M78, 280 (knots now), K280 (on conversion) or SN'; return; } await fly(cs, s, out); }
      else if (k === 'dct') { const v = val('dct').toUpperCase(); if (v) await fly(cs, { type: 'direct', waypoint: v }, out); }
      else if (k === 'res') await fly(cs, { type: 'resume' }, out);
      else if (k === 'dev') await fly(cs, { type: 'deviation', side: 'either' }, out);
    };
    el.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(b.dataset.go));
    el.querySelectorAll('[data-d]').forEach(b => b.onclick = () => fly(cs, { type: 'direct', waypoint: b.dataset.d }, out));
    el.querySelectorAll('input').forEach(i => i.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') go(i.dataset.i === 'fl' ? 'fl' : i.dataset.i === 'hdg' ? 'h' : i.dataset.i); });
    return el;
  }
  function renderPilot(scroll) {
    if (!state || !$('#pilotList')) return;
    const box = $('#pilotList');
    if (!$('#plPts') && sector) { const dl = document.createElement('datalist'); dl.id = 'plPts'; dl.innerHTML = Object.keys(sector.waypoints).map(n => `<option value="${n}">`).join(''); document.body.appendChild(dl); }
    const reqs = state.pending_requests || [], pend = state.pending_instructions || [];
    const rank = a => { const calling = a.on_frequency && !a.assumed, rq = reqs.some(r => r.callsign === a.callsign && r.kind !== 'checkin');
      return calling ? 0 : rq ? 1 : a.on_frequency ? 2 : 3; };
    const list = state.aircraft.slice().sort((a, b) => rank(a) - rank(b) || a.callsign.localeCompare(b.callsign));
    const seen = new Set();
    let order = 0;
    for (const a of list) {
      const cs = a.callsign; seen.add(cs);
      let r = rows[cs];
      if (!r) {
        const el = document.createElement('div'); el.className = 'pl';
        el.innerHTML = '<div class="ph"></div><div data-says></div>';
        el.querySelector('.ph').onclick = () => { pilotSel = pilotSel === cs ? null : cs; selected = pilotSel; renderPilot(); };
        r = rows[cs] = { el, ph: el.querySelector('.ph'), says: el.querySelector('[data-says]'), ctl: null, phH: '', saysH: '' };
      }
      if (box.children[order] !== r.el) box.insertBefore(r.el, box.children[order] || null);
      order++;
      const calling = a.on_frequency && !a.assumed;
      const spd = a.speed_mode === 'mach' ? 'M' + String(Math.round(a.mach * 100)).padStart(2, '0') : a.ias_kt + 'kt';
      const nav = a.nav_mode === 'heading' ? 'H' + String(a.assigned_heading).padStart(3, '0') : a.nav_mode === 'wxdev' ? 'DEV' : '>' + (a.next_waypoint || '');
      const phH = `<b>${cs}</b><span>${String(a.fl).padStart(3, '0')}${a.vertical_speed_fpm > 0 ? '↑' : a.vertical_speed_fpm < 0 ? '↓' : ' '}${a.cleared_fl !== a.fl ? a.cleared_fl : ''}</span><span>${spd}</span><span>${nav}</span><span class="grow"></span>` +
        (!a.on_frequency ? '<span class="badge prev">prev sector</span>' : calling ? '<span class="badge call">CALLING</span>' : '<span class="badge asm">on freq</span>');
      if (phH !== r.phH) { r.ph.innerHTML = phH; r.phH = phH; }
      const says = [];
      for (const q of reqs.filter(q => q.callsign === cs)) {
        if (q.kind === 'checkin') says.push(`<div class="say call"><span>Call the controller: <b>“${h(q.phraseology)}”</b></span></div>`);
        else says.push(`<div class="say"><span><b>“${h(q.phraseology)}”</b>${q.reason ? ` <span style="opacity:.7">(${h(q.reason)})</span>` : ''}</span><span style="white-space:nowrap">${q.type === 'report' ? `<button data-rep="${q.id}" data-dec="roger">Acknowledged</button>` :
          `<button data-rep="${q.id}" data-dec="approved" title="the controller approved it: do it">Approved</button><button data-rep="${q.id}" data-dec="unable">Refused</button>`}</span></div>`);
      }
      for (const p of pend.filter(p => p.text && p.text.startsWith(cs))) says.push(`<div class="say" style="background:#1a2633;color:#9fc3e6;border-color:#24384c"><span>executing: ${h(p.text)}</span></div>`);
      const saysH = says.join('');
      if (saysH !== r.saysH) {
        r.says.innerHTML = saysH; r.saysH = saysH;
        r.says.querySelectorAll('[data-rep]').forEach(b => b.onclick = () => fly(cs, { type: 'reply', request_id: b.dataset.rep, decision: b.dataset.dec }, $('#cmdmsg')));
      }
      const sel = pilotSel === cs;
      r.el.classList.toggle('sel', sel);
      if (sel && !r.ctl) { r.ctl = pilotCtl(cs, a); r.el.appendChild(r.ctl); }
      if (!sel && r.ctl) { r.ctl.remove(); r.ctl = null; }
      if (sel && scroll) r.el.scrollIntoView({ block: 'nearest' });
    }
    for (const cs of Object.keys(rows)) if (!seen.has(cs)) { rows[cs].el.remove(); delete rows[cs]; if (pilotSel === cs) pilotSel = null; }
    if (!list.length) box.innerHTML = '<div class="empty">no aircraft in the sector yet</div>';
    else { const e = box.querySelector('.empty'); if (e) e.remove(); }
  }

  // ------------------------------------------------------------------ instructor
  async function setupInstructor() {
    try {
      const ex = await api('/api/exercises');
      const live = ex.filter(e => e.live && e.seat === 'instructor');
      $('#exSwitch').innerHTML = live.map(e => `<option value="${e.id}" ${e.id === EXID ? 'selected' : ''}>${h(e.name)} · ${e.id} · ${e.status}${e.losses && e.losses.length ? ' · LOSS' : ''}</option>`).join('');
      $('#exSwitch').onchange = e => { location.href = '/x/' + e.target.value; };
    } catch (e) {}
    try { userList = await api('/api/users'); } catch (e) { userList = []; }
    $('#invUser').innerHTML = '<option value="">— invite someone —</option>' + userList.filter(u => u.profile_complete).map(u => `<option value="${u.id}" data-role="${u.role}">${h(u.name)} · ${h(u.licence)} (${({ atc: 'controller', pilot: 'pseudo-pilot', instructor: 'instructor', admin: 'admin' })[u.role]})</option>`).join('');
    $('#invUser').onchange = () => { const o = $('#invUser').selectedOptions[0]; const r = o && o.dataset.role; if (r === 'atc' || r === 'pilot') $('#invSeat').value = r; };
    $('#invBtn').onclick = async () => {
      if (!$('#invUser').value) return;
      try { EXINFO.members = await api(`/api/exercises/${EXID}/members`, { method: 'POST', body: JSON.stringify({ user_id: +$('#invUser').value, seat: $('#invSeat').value }) });
        $('#exMsg').textContent = 'invited: the exercise is now on their home page'; renderMembers(); } catch (e) { $('#exMsg').textContent = e.message; }
    };
    document.querySelectorAll('[data-link]').forEach(b => b.onclick = async () => {
      const url = EXINFO.links[b.dataset.link];
      try { await navigator.clipboard.writeText(url); $('#exMsg').textContent = 'link copied: ' + url; } catch (e) { prompt('Invitation link', url); }
    });
    $('#endBtn').onclick = async () => {
      if (!confirm('End this exercise for everybody? The recording (radar + all audio) is then turned into an .mp4 on the home page.')) return;
      try { await api(`/api/exercises/${EXID}/end`, { method: 'POST' }); showEnded(); setTimeout(() => location.href = '/', 1500); } catch (e) { $('#exMsg').textContent = e.message; }
    };
    renderMembers();
    setInterval(async () => { try { const d = await api(`/api/exercises/${EXID}`); EXINFO.members = d.members; renderMembers(); } catch (e) {} }, 6000);
  }
  function renderMembers() {
    if (SEAT !== 'instructor' || !EXINFO) return;
    const online = voice ? voice.roster : [];
    const on = uid => online.filter(r => r.user_id === uid);
    const rowsH = (EXINFO.members || []).map(m => { const o = on(m.user_id), tx = o.some(r => r.ptt);
      return `<div class="mem"><span><span class="dot ${o.length ? 'on' : ''}"></span>${h(m.first_name)} ${h(m.last_name)} <span class="c-dim">${h(m.licence)}</span>${tx ? ' <span style="color:#fff;background:#b32d2d;border-radius:3px;padding:0 4px;font-size:10px">TX</span>' : ''}</span>
        <span><span class="c-dim">${SEATN[m.seat]}</span> <button data-rm="${m.user_id}" style="padding:0 5px;font-size:10px" title="remove">✕</button></span></div>`; }).join('');
    $('#exMembers').innerHTML = `<div class="c-dim" style="font-size:12px;margin-bottom:3px">${h(EXINFO.name)} · ${h(EXINFO.scenario)}</div>` + (rowsH || '<div class="empty">nobody invited yet</div>');
    $('#exMembers').querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => { EXINFO.members = await api(`/api/exercises/${EXID}/members/${b.dataset.rm}`, { method: 'DELETE' }); renderMembers(); });
  }

  init();
})();

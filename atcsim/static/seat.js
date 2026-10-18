// Connected exercise (/x/<id>): the controller's, pseudo-pilot's and instructor's seats on top of the radar page.
// Uses the radar page's globals (state, sector, SID, SEAT, EXID, selected, api, attach, ...).
(function () {
  if (!EXID) return;
  const allAc = () => state ? state.aircraft.concat(state.transferred || []) : [];   // incl. those gone to the next sectors
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
    setupTabs();
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
    b.innerHTML = 'This exercise has ended. ' + (SEAT === 'pilot' ? '' : '<a href="/#evaluations" style="pointer-events:auto;color:#7fb8ff">Evaluate it</a> · ') +
      '<a href="/" style="pointer-events:auto;color:#7fb8ff">Home</a>';
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
    if (SEAT === 'atc' && $('#lblMenu').classList.contains('on') && !$('#lblMenu').classList.contains('fm')) refreshMenuHead();
  }

  // ------------------------------------------------------------------ side panel tabs (instructor: Aircraft · Exercise · Monitor)
  function showTab(t) {
    document.querySelectorAll('aside [data-tab]').forEach(el => el.classList.toggle('tab-on', el.dataset.tab === t));
    document.querySelectorAll('#asideTabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
    try { localStorage.setItem('atc_tab', t); } catch (e) {}
  }
  window.showTab = showTab;
  function setupTabs() {
    document.querySelectorAll('#asideTabs button').forEach(b => b.onclick = () => showTab(b.dataset.t));
    let t = 'pilot'; try { t = localStorage.getItem('atc_tab') || 'pilot'; } catch (e) {}
    showTab(SEAT === 'instructor' ? t : 'pilot');
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
  window.onSelectAircraft = (cs, e, isLabel, field) => {
    if (isLabel && field === 'type' && window.openTypeWin) { openTypeWin(cs); if (SEAT === 'atc') { closeMenu(); return true; } }
    if (SEAT === 'atc') {                 // a click selects; a click on a value of the label opens that value's menu
      closeMenu();
      if (isLabel && field && field !== 'type') openFieldMenu(cs, field, e);
      return true;
    }
    pilotSel = cs; renderPilot(true);
    if (SEAT === 'instructor') showTab('pilot');
    return false;
  };
  let menuCs = null;
  function menuPos(e) {
    const r = $('#scope').getBoundingClientRect(), m = $('#lblMenu');
    let x = e.clientX - r.left + 14, y = e.clientY - r.top - 20;
    if (x + 270 > r.width) x = e.clientX - r.left - 280;
    if (y + 470 > r.height) y = r.height - 475;
    m.style.left = Math.max(4, x) + 'px'; m.style.top = Math.max(40, y) + 'px';
  }
  function refreshMenuHead() {
    const a = allAc().find(x => x.callsign === menuCs); if (!a) { closeMenu(); return; }
    const calling = !a.assumed;          // any track can be assumed: first call, early (forced) assume, taking it back
    $('#lmRelease').style.display = a.assumed ? '' : 'none';
    $('#lmHead').innerHTML = `${a.callsign} <span class="c-dim" style="font-weight:400">${a.type} · FL${a.fl}${a.vertical_speed_fpm > 0 ? '↑' : a.vertical_speed_fpm < 0 ? '↓' : ''} · ${a.flight_plan && a.flight_plan.departure || ''}→${a.flight_plan && a.flight_plan.destination || ''}</span>`;
    $('#lmAssume').style.display = calling ? '' : 'none';
    const co = (state.coord || {})[a.callsign], el = $('#lmCoord');
    if (el) el.innerHTML = !co ? '' : !a.assumed ? (co.entry_level ? `coordinated in: F${co.entry_level} from ${h(a.prev_unit)} (${co.in_state.toUpperCase()})` : '')
      : `→ ${h(co.next_unit || '?')}: ${co.state === 'act' ? `ACT F${co.coordinated}` + (co.coordinated === co.xfl ? ' ✓' : ' · REV due') : co.state === 'abi' ? `ABI F${co.coordinated}` + (co.act_in_s != null ? ` · ACT in ${Math.ceil(co.act_in_s / 60)} min` : '') : co.state === 'transferred' ? 'transferred' : `nothing sent yet (XFL F${co.xfl}${co.act_in_s != null ? ', ACT in ' + Math.ceil(co.act_in_s / 60) + ' min' : ''})`}`
        + (co.manual ? `<br><span class="c-warn">${h(co.manual)}</span> <button id="lmPhone" style="font-size:10px;padding:0 5px">✓ coordinated by phone</button>` : '')
        + (co.warn ? `<br><span class="c-warn">${h(co.warn)}</span>` : '');
    const pb = $('#lmPhone');
    if (pb) pb.onclick = async () => { try { await api(`/sessions/${SID}/entries`, { method: 'POST', body: JSON.stringify({ callsign: a.callsign, coord: 'phone' }) }); } catch (e) {} };
  }
  function openMenu(cs, e, field) {
    const a = state && allAc().find(x => x.callsign === cs); if (!a) return;
    menuCs = cs; const ent = (state.entries || {})[cs] || {}, m = $('#lblMenu');
    const sys = ent.cfl != null ? ent.cfl : (state.system_cfl && state.system_cfl[cs] != null ? state.system_cfl[cs] : a.cleared_fl);
    const base = Math.round(sys / 10) * 10, lv = [];
    for (let d = -40; d <= 40; d += 10) if (d && base + d >= 100 && base + d <= 460) lv.push(base + d);
    const route = realPoints(a.route.slice(Math.max(0, a.route.indexOf(a.next_waypoint))).concat(a.route_after_exit || []));
    m.innerHTML = `<div class="h"><span id="lmHead"></span><span><button id="lmFpl" style="padding:0 6px" title="flight plan (F)">FPL</button> <button id="lmX" style="padding:0 6px">✕</button></span></div>
      <div id="lmAssume" style="margin-bottom:7px"><button class="on" id="lmAsBtn" style="width:100%;padding:4px">ASSUME ${cs}</button></div>
      <div id="lmRelease" style="margin-bottom:7px"><button id="lmRelBtn" style="width:100%;padding:4px" title="hand it off: it keeps flying as another sector's traffic">DECONTROL ${cs}</button></div>
      <div class="f"><span class="c-dim">CFL</span><input data-f="cfl" value="${sys}" inputmode="numeric"><button data-ok="cfl">OK</button></div>
      <div class="lv">${lv.map(l => `<button data-lv="${l}">${l}</button>`).join('')}</div>
      <div class="f"><span class="c-dim" title="exit level for the coordination with the next unit (OLDI); empty = the cleared level">XFL</span><input data-f="xfl" value="${ent.xfl != null ? ent.xfl : ''}" placeholder="= CFL" inputmode="numeric"><button data-ok="xfl">OK</button></div>
      <div id="lmCoord" class="c-dim" style="font-size:11px;margin:-2px 0 6px 59px"></div>
      <div class="f"><span class="c-dim">Speed</span><input data-f="speed" value="${h(ent.speed || '')}" placeholder="M78 · 280 · SN"><button data-ok="speed">OK</button></div>
      <div class="f"><span class="c-dim">Heading</span><input data-f="heading" value="${ent.heading || ''}" placeholder="e.g. 250" inputmode="numeric"><button data-ok="heading">OK</button></div>
      <div class="f"><span class="c-dim">Direct</span><input data-f="direct" value="${h(ent.direct || '')}" list="lmPts" placeholder="point"><button data-ok="direct">OK</button></div>
      <div class="lv">${[...new Set(route)].slice(0, 10).map(n => `<button data-dct="${n}">${n}</button>`).join('')}</div>
      <div class="f"><span class="c-dim">WX dev</span><input data-f="wx" value="${h(ent.wx || '')}" placeholder="L · R · L20 · Y"><button data-ok="wx">OK</button></div>
      <div class="lv wxb"><button data-wx="L" class="${ent.wx === 'L' ? 'on' : ''}">L</button><button data-wx="R" class="${ent.wx === 'R' ? 'on' : ''}">R</button><button data-wx="Y" class="${ent.wx === 'Y' ? 'on' : ''}">either</button><button data-wx="">end</button></div>
      <div class="f"><span class="c-dim">Note</span><input data-f="note" value="${h(ent.note || '')}" style="text-transform:none"><button data-ok="note">OK</button></div>
      <div class="row" style="margin:6px 0 0"><button id="lmClr">clear speed/heading/direct/WX</button><button id="lmProbe" title="conflicts (20 min) with the level / heading / direct typed above, before clearing it">Probe</button></div>
      <div id="lmMsg" style="min-height:14px;font-size:11px;margin-top:4px"></div>
      <datalist id="lmPts">${realPoints(Object.keys(sector.waypoints)).map(n => `<option value="${n}">`).join('')}</datalist>`;
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
    $('#lmClr').onclick = () => { for (const f of ['speed', 'heading', 'direct', 'wx']) m.querySelector(`[data-f=${f}]`).value = ''; send({ speed: '', heading: '', direct: '', wx: '' }); };
    m.querySelectorAll('[data-wx]').forEach(b => b.onclick = () => { m.querySelector('[data-f=wx]').value = b.dataset.wx; send('wx', b.dataset.wx);
      m.querySelectorAll('[data-wx]').forEach(x => x.classList.toggle('on', x === b && !!b.dataset.wx)); });
    $('#lmFpl').onclick = () => { if (window.openFpl) openFpl(cs); };
    // level probe: each proposed level coloured green (no conflict in 20 min) or red (conflict), from the system trajectories
    api(`/sessions/${SID}/mtcd`, { method: 'POST', body: JSON.stringify({ callsign: cs, levels: lv }) }).then(r => {
      if (menuCs !== cs) return;
      m.querySelectorAll('[data-lv]').forEach(b => { const c = (r.levels || {})[b.dataset.lv]; if (!c) return;
        b.classList.toggle('cf', c.length > 0); b.classList.toggle('ok', c.length === 0);
        b.title = c.length ? 'conflict: ' + c.map(x => `${x.with} in ${Math.round(x.in_s / 60)} min (${x.min_nm} NM)`).join(', ') : 'no conflict in the next 20 minutes'; });
    }).catch(() => {});
    $('#lmProbe').onclick = async () => {
      const v = k => m.querySelector(`[data-f=${k}]`).value.trim(), body = { callsign: cs };
      if (v('cfl')) body.cfl = +v('cfl'); if (v('heading')) body.heading = +v('heading'); else if (v('direct')) body.direct = v('direct').toUpperCase();
      try { const r = await api(`/sessions/${SID}/mtcd`, { method: 'POST', body: JSON.stringify(body) }), c = r.conflicts || [];
        $('#lmMsg').className = c.length ? 'c-los' : 'c-ok';
        $('#lmMsg').textContent = c.length ? 'probe: ' + c.map(x => `${x.aircraft.find(y => y !== cs)} in ${Math.round(x.time_to_conflict_s / 60)} min, ${x.min_horizontal_nm} NM / ${x.vertical_ft} ft`).join(' · ') : 'probe: no conflict in the next 20 minutes';
      } catch (er) { $('#lmMsg').className = 'c-los'; $('#lmMsg').textContent = er.message; } };
    $('#lmRelBtn').onclick = async () => { try { await api(`/sessions/${SID}/release/${cs}`, { method: 'POST' }); closeMenu(); } catch (er) { $('#lmMsg').textContent = er.message; } };
    $('#lmAsBtn').onclick = async () => { try { await api(`/sessions/${SID}/assume/${cs}`, { method: 'POST' }); $('#lmAssume').style.display = 'none'; } catch (er) { $('#lmMsg').textContent = er.message; } };
    $('#lmX').onclick = closeMenu;
    const f = m.querySelector(`[data-f=${field || 'cfl'}]`) || m.querySelector('[data-f=cfl]'); f.focus(); f.select();
  }
  window.openLabelMenu = (cs, field) => {            // keyboard: open the selected aircraft's label input on a field
    const a = allAc().find(x => x.callsign === cs); if (!a) return;
    const r = $('#scope').getBoundingClientRect(), [px, py] = toPx(a.x_nm, a.y_nm);
    openMenu(cs, { clientX: r.left + px / devicePixelRatio + 20, clientY: r.top + py / devicePixelRatio }, field);
  };
  function closeMenu() { $('#lblMenu').classList.remove('on', 'fm'); menuCs = null; document.activeElement && document.activeElement.blur(); }
  // long press on a value of the label: the full label menu, on that value
  window.onLongPressLabel = (cs, field, e) => {
    if (SEAT !== 'atc') return false;
    const map = { cfl: 'cfl', xfl: 'xfl', spd: 'speed', hdg: 'heading', wx: 'wx', note: 'note' };
    openMenu(cs, e, map[field] || 'cfl'); return true;
  };
  // ---- small menus, one per value of the label (flat buttons in the label)
  const sendEntry = async (cs, f) => {
    await api(`/sessions/${SID}/entries`, { method: 'POST', body: JSON.stringify({ callsign: cs, ...f }) });
    if (state.entries && !f.route) state.entries[cs] = { ...(state.entries[cs] || {}), ...f };
  };
  const p3 = v => String(Math.round(v)).padStart(3, '0');
  function openFieldMenu(cs, f, e) {
    const a = allAc().find(x => x.callsign === cs); if (!a) return;
    const m = $('#lblMenu'), ent = (state.entries || {})[cs] || {}, co = (state.coord || {})[cs] || {};
    const sys = ent.cfl != null ? ent.cfl : (co.cfl != null ? co.cfl : a.cleared_fl);
    menuCs = cs;
    const title = { cs: a.type + ' · ' + ((a.flight_plan || {}).departure || '') + '→' + ((a.flight_plan || {}).destination || ''), cfl: 'Cleared level', xfl: 'Exit level (coordination)', spd: 'Speed', hdg: 'Heading', route: 'Route', wx: 'Weather deviation', note: 'Note' }[f] || f;
    let body = '';
    const levels = base => { const out = []; for (let l = Math.round(base / 10) * 10 + 60; l >= Math.round(base / 10) * 10 - 60; l -= 10) if (l >= 50 && l <= 600) out.push(l); return out; };
    if (f === 'cs') {                                    // the callsign: control and data of the aircraft
      body = `<div class="fl">${a.assumed ? `<button data-c="rel">Decontrol (U)</button>` : `<button data-c="asm" class="cur">Assume (A)</button>`}
        <button data-c="fpl">Flight plan (F)</button><button data-c="info">Track info (I)</button><button data-c="type">Type performance</button>
        <button data-c="edit">Edit route… (E)</button><button data-c="show">Show route (R)</button><button data-c="full">Full label menu</button></div>`;
    } else if (f === 'cfl' || f === 'xfl') {
      const cur = f === 'cfl' ? sys : (co.xfl != null ? co.xfl : sys);
      body = `<input data-v value="${f === 'xfl' && !co.xfl_entered ? '' : cur}" placeholder="${f === 'xfl' ? '= CFL ' + sys : ''}" inputmode="numeric">
        <div class="fl">${f === 'xfl' ? `<button data-x="">= CFL (F${p3(sys)})</button>` : ''}${levels(cur).map(l => `<button data-x="${l}" class="${l === cur ? 'cur' : ''}">F${p3(l)}</button>`).join('')}</div>`;
    } else if (f === 'spd') {
      const mach = Math.round(a.mach * 100), ias = Math.round(a.ias_kt / 10) * 10;
      body = `<input data-v value="${h(ent.speed || '')}" placeholder="M78 · 280 · SN">
        <div class="fg">${[-3, -2, -1, 0, 1, 2, 3].map(d => `<button data-x="M${mach + d}">M${mach + d}</button>`).join('')}<button data-x="SN">SN</button>
        ${[-30, -20, -10, 0, 10, 20, 30].map(d => `<button data-x="${ias + d}">${ias + d}</button>`).join('')}<button data-x="">clear</button></div>`;
    } else if (f === 'hdg') {
      const h0 = Math.round(a.heading / 5) * 5;
      body = `<input data-v value="${ent.heading || ''}" placeholder="${p3(a.heading)}" inputmode="numeric">
        <div class="fg">${[-40, -30, -20, -10, 10, 20, 30, 40].map(d => { const v = ((h0 + d) % 360 + 360) % 360 || 360; return `<button data-x="${v}">${d < 0 ? 'L' : 'R'}${p3(v)}</button>`; }).join('')}</div>
        <div class="fl"><button data-x="">own navigation (clear)</button></div>`;
    } else if (f === 'route') {
      const pts = realPoints(a.route.slice(Math.max(0, a.route.indexOf(a.next_waypoint || a.fp_next))).concat(a.route_after_exit || []));
      body = `<div class="c-dim" style="font-size:11px">direct to</div><div class="fg">${[...new Set(pts)].slice(0, 12).map(n => `<button data-x="${n}">${n}</button>`).join('')}</div>
        <div class="fl"><button data-edit>Edit route… (E)</button><button data-show>Show route (long press)</button>${ent.direct || ent.route ? '<button data-x="">back to the filed route</button>' : ''}</div>`;
    } else if (f === 'wx') {
      body = `<div class="fg"><button data-x="L">L</button><button data-x="R">R</button><button data-x="Y">either</button><button data-x="">end</button></div><input data-v value="${h(ent.wx || '')}" placeholder="L20 · R15" style="margin-top:4px">`;
    } else { openMenu(cs, e, f); return; }
    m.innerHTML = `<div class="h"><span>${cs} <span class="c-dim" style="font-weight:400">${title}</span></span><button id="fmX" style="padding:0 6px">✕</button></div>${body}<div id="lmMsg" style="min-height:12px;font-size:11px;margin-top:3px"></div>`;
    m.classList.add('on', 'fm'); menuPos(e);
    const key = { cfl: 'cfl', xfl: 'xfl', spd: 'speed', hdg: 'heading', wx: 'wx' }[f];
    const go = async v => { try {
        if (f === 'route') await sendEntry(cs, v ? { direct: v, heading: '' } : { direct: '', route: '' });
        else if (f === 'hdg' && v) await sendEntry(cs, { heading: +v, direct: '' });
        else await sendEntry(cs, { [key]: v === '' ? '' : (f === 'cfl' || f === 'xfl') ? +v : v });
        closeMenu(); } catch (er) { $('#lmMsg').className = 'c-los'; $('#lmMsg').textContent = er.message; } };
    m.querySelectorAll('[data-x]').forEach(b => b.onclick = () => go(b.dataset.x));
    const inp = m.querySelector('[data-v]');
    if (inp) { inp.onkeydown = ev => { ev.stopPropagation(); if (ev.key === 'Enter') { let v = inp.value.trim().toUpperCase(); if (f === 'cfl' || f === 'xfl') v = v.replace(/^FL?/, ''); go(v); } if (ev.key === 'Escape') closeMenu(); };
      setTimeout(() => { inp.focus(); inp.select(); }, 0); }
    m.querySelectorAll('[data-c]').forEach(b => b.onclick = async () => {
      const c = b.dataset.c; closeMenu();
      try {
        if (c === 'asm') await api(`/sessions/${SID}/assume/${cs}`, { method: 'POST' });
        else if (c === 'rel') await api(`/sessions/${SID}/release/${cs}`, { method: 'POST' });
        else if (c === 'fpl') openFpl(cs);
        else if (c === 'info') pinInfo(cs);
        else if (c === 'type') openTypeWin(cs);
        else if (c === 'edit') editRoute(cs);
        else if (c === 'show') routeShow = { cs, until: performance.now() + 8000 };
        else if (c === 'full') openMenu(cs, e, 'cfl');
      } catch (er) { note(er.message, true); } });
    const ed = m.querySelector('[data-edit]'); if (ed) ed.onclick = () => { closeMenu(); editRoute(cs); };
    const sh = m.querySelector('[data-show]'); if (sh) sh.onclick = () => { closeMenu(); routeShow = { cs, until: performance.now() + 8000 }; };
    $('#fmX').onclick = closeMenu;
    if (f === 'cfl') api(`/sessions/${SID}/mtcd`, { method: 'POST', body: JSON.stringify({ callsign: cs, levels: [...m.querySelectorAll('[data-x]')].map(b => +b.dataset.x).filter(Boolean) }) }).then(r => {
      if (menuCs !== cs) return;
      m.querySelectorAll('[data-x]').forEach(b => { const c = (r.levels || {})[b.dataset.x]; if (!c) return;
        b.style.borderColor = c.length ? '#a33' : '#2f7a5f'; b.style.color = c.length ? '#ffb3b3' : '#bff5dc';
        b.title = c.length ? 'conflict: ' + c.map(x => `${x.with} in ${Math.round(x.in_s / 60)} min (${x.min_nm} NM)`).join(', ') : 'no conflict in the next 20 minutes'; }); }).catch(() => {});
  }
  window.openFieldMenuAt = (cs, f) => {                // keyboard: the value's menu at the label
    const r = $('#scope').getBoundingClientRect(), d = devicePixelRatio;
    const fr = fieldRects.find(x => x.cs === cs && x.f === f) || fieldRects.find(x => x.cs === cs);
    let px, py;
    if (fr) { px = fr.x0 / d; py = fr.y1 / d; } else { const a = allAc().find(x => x.callsign === cs); if (!a) return; [px, py] = toPx(a.x_nm, a.y_nm).map(v => v / d); }
    openFieldMenu(cs, f, { clientX: r.left + px, clientY: r.top + py + 6 });
  };
  // re-routing: the controller enters the new route in the system; a pseudo-pilot / instructor makes the aircraft fly it
  window.editRoute = cs => {
    if (SEAT === 'atc') openRouteEditor(cs, { title: 'system route (re-routing)', apply: 'Enter route',
      onApply: pts => sendEntry(cs, { route: pts, direct: '' }), onReset: () => sendEntry(cs, { route: '', direct: '' }) });
    else openRouteEditor(cs, { title: 'the aircraft flies it', apply: 'Fly this route',
      onApply: async pts => { const r = await fly(cs, { type: 'reroute', points: pts }, $('#deck [data-out]')); if (r && r.status === 'rejected') throw new Error(r.reason); } });
  };
  window.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  $('#cv').addEventListener('mousedown', () => { if (SEAT === 'atc') closeMenu(); });
  // the controller assumes by double-click too; it goes through /assume
  window.assume = async cs => { try { await api(`/sessions/${SID}/assume/${cs}`, { method: 'POST' }); } catch (e) { note(e.message, true); } };

  // ------------------------------------------------------------------ pseudo-pilot workspace (also the instructor's)
  // To do (calls to make, requests to read) · the selected aircraft's control deck · the list of aircraft
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
      if (out) { out.className = 'dk-out ' + (r.status === 'rejected' ? 'bad' : r.warnings && r.warnings.length ? 'warn' : 'ok');
        out.textContent = r.status === 'rejected' ? '✗ ' + r.reason : '✓ ' + (r.warnings && r.warnings.length ? r.warnings.join(' | ') : 'done — the aircraft is executing'); }
      return r;
    } catch (e) { if (out) { out.className = 'dk-out bad'; out.textContent = e.message; } }
  }
  const reqsOf = cs => (state.pending_requests || []).filter(q => q.callsign === cs);
  const statusOf = a => { if (a.status && a.status !== 'active') return ['out', a.assumed ? 'left · still assumed' : 'next sector'];
    if (a.released) return ['out', 'decontrolled'];
    if (!a.on_frequency) return ['prev', 'prev sector'];
    if (!a.assumed) return ['call', 'CALLING'];
    if (reqsOf(a.callsign).some(q => q.kind !== 'checkin')) return ['req', 'REQUEST'];
    return ['ok', 'on frequency']; };
  const fmtSpd = a => a.speed_mode === 'mach' ? 'M' + String(Math.round(a.mach * 100)).padStart(2, '0') : a.ias_kt + ' kt';
  const fmtNav = a => a.nav_mode === 'heading' ? 'HDG ' + String(a.assigned_heading).padStart(3, '0') : a.nav_mode === 'wxdev' ? 'deviating' : '→ ' + nextReal(a);
  function select(cs) { pilotSel = cs; selected = cs; renderPilot(true); }

  // ---- to do
  let todoH = '';
  const DL = () => (state && state.cpdlc) || {};
  const dlOpenFor = rid => Object.values(DL()).some(d => d.open.some(m => m.dir === 'down' && m.request_id === rid));
  function cpdlcTodo() {
    const out = [];
    for (const [cs, d] of Object.entries(DL())) {
      for (const m of d.open) {
        const ag = Math.round(state.sim_time_s - m.t);
        if (m.dir === 'up') {
          const b = m.resp === 'WU' ? `<button class="yes" data-dlid="${m.id}" data-ans="WILCO" data-cs="${cs}" title="the aircraft executes it">WILCO</button><button data-dlid="${m.id}" data-ans="STANDBY" data-cs="${cs}">Standby</button><button class="no" data-dlid="${m.id}" data-ans="UNABLE" data-cs="${cs}">Unable</button><button class="no small" data-dlid="${m.id}" data-ans="UNABLE" data-why="weather" data-cs="${cs}" title="UNABLE / DUE TO WEATHER">…wx</button><button class="no small" data-dlid="${m.id}" data-ans="UNABLE" data-why="performance" data-cs="${cs}" title="UNABLE / DUE TO AIRCRAFT PERFORMANCE">…perf</button>`
            : m.resp === 'AN' ? `<button class="yes" data-dlid="${m.id}" data-ans="AFFIRM" data-cs="${cs}">Affirm</button><button class="no" data-dlid="${m.id}" data-ans="NEGATIVE" data-cs="${cs}">Negative</button>`
            : `<button class="yes" data-dlid="${m.id}" data-ans="ROGER" data-cs="${cs}">Roger</button><button class="no" data-dlid="${m.id}" data-ans="UNABLE" data-cs="${cs}">Unable</button>`;
          out.push([m.t, `<div class="todo dl${state.sim_time_s - m.t < 12 ? ' new' : ''}" data-cs="${cs}"><span class="tg">CPDLC</span><div class="tx"><b>${cs}</b> · ${h(m.text)}<small>data link from the controller${m.status === 'standby' ? ' · you sent STANDBY' : ''} · ${ag} s${m.late ? ' · <span class="c-warn">answer it</span>' : ''}</small></div><div class="bt">${b}</div></div>`]);
        } else out.push([m.t, `<div class="todo dl sent" data-cs="${cs}"><span class="tg">SENT</span><div class="tx"><b>${cs}</b> · ${h(m.text)}<small>by data link · waiting for the controller${m.status === 'standby' ? ' (STANDBY)' : ''} · ${ag} s</small></div></div>`]);
      }
      for (const m of d.recent.filter(m => m.dir === 'up' && m.resp === 'N' && /^(UNABLE|STANDBY|ROGER|AFFIRM|NEGATIVE)/.test(m.text)))
        out.push([m.t, `<div class="todo dl info" data-cs="${cs}"><span class="tg">CPDLC</span><div class="tx"><b>${cs}</b> · ${h(m.text)}<small>the controller's answer to your request</small></div></div>`]);
    }
    return out.sort((a, b) => a[0] - b[0]).map(x => x[1]);
  }
  function renderTodo() {
    const conn = cs => (DL()[cs] || {}).state === 'cda';
    const items = [].concat((state.pending_requests || []).slice().sort((a, b) => a.time_s - b.time_s).map(q => {
      const age = Math.round(state.sim_time_s - q.time_s);
      if (q.kind === 'checkin') return `<div class="todo call" data-cs="${q.callsign}"><span class="tg">CALL</span><div class="tx"><b>${q.callsign}</b> · say <i>“${h(q.phraseology)}”</i><small>first call · the controller assumes it · ${age} s</small></div></div>`;
      const btns = q.type === 'report' ? `<button data-rep="${q.id}" data-dec="roger" data-cs="${q.callsign}">Acknowledged</button>`
        : `<button class="yes" data-rep="${q.id}" data-dec="approved" data-cs="${q.callsign}" title="the controller approved: the aircraft does it">Approved</button><button class="no" data-rep="${q.id}" data-dec="unable" data-cs="${q.callsign}">Refused</button>`;
      const sent = dlOpenFor(q.id);
      const dlb = conn(q.callsign) && !sent ? `<button data-dlreq="${q.id}" data-cs="${q.callsign}" title="send this request by data link instead of saying it">By CPDLC</button>` : '';
      return `<div class="todo req" data-cs="${q.callsign}"><span class="tg">${q.type === 'report' ? 'REPORT' : 'REQ'}</span><div class="tx"><b>${q.callsign}</b> · ${sent ? 'sent by CPDLC: ' + h(q.phraseology) : `say <i>“${h(q.phraseology)}”</i>`}<small>${q.reason ? h(q.reason) + ' · ' : ''}${age} s${sent ? ' · the controller answers by data link (or on the frequency)' : ''}</small></div><div class="bt">${dlb}${btns}</div></div>`;
    }));
    // aircraft dealing with weather (they asked for a deviation, were cleared one, or deviate): report clear of it
    for (const a of allAc().filter(x => x.wx_handling)) items.push(`<div class="todo req" data-cs="${a.callsign}"><span class="tg">WX</span><div class="tx"><b>${a.callsign}</b> is dealing with weather · when clear, say <i>“${a.callsign} clear of weather”</i><small>its weather is not counted as a penetration until then</small></div><div class="bt"><button class="yes" data-wxclear="${a.callsign}">Clear of weather</button></div></div>`);
    const html = items.join('') || '<div class="empty">Nothing to say right now. Fly what the controller gives you.</div>';
    if (html !== todoH) { $('#todo').innerHTML = html; todoH = html; }
    $('#todoN').textContent = items.length ? `(${items.length})` : '';
  }
  // ---- CPDLC: the uplinks to answer, the requests sent, the message log
  let dlH = '', dlLog = [], dlLast = -1;
  async function renderDl() {
    const box = $('#dlBox'); if (!box) return;
    const sec = $('#dlSec'), conns = Object.values(DL());
    sec.style.display = conns.length ? '' : 'none';
    const items = cpdlcTodo();
    const nUp = Object.values(DL()).reduce((n, d) => n + d.open.filter(m => m.dir === 'up').length, 0);
    $('#dlN').textContent = nUp ? `(${nUp} to answer)` : '';
    sec.querySelector('h2').classList.toggle('hot', nUp > 0);
    if ((state.cpdlc_last || 0) !== dlLast) { dlLast = state.cpdlc_last || 0;
      try { dlLog = (await api(`/sessions/${SID}/cpdlc`)).messages.filter(m => !/^(NEXT DATA AUTHORITY|END SERVICE)/.test(m.text)).slice(-14).reverse(); } catch (e) {} }
    const log = dlLog.length ? `<div class="dl-log2">${dlLog.map(m => `<div class="${m.dir}" data-cs="${m.callsign}"><span class="c-dim">${h(m.utc.slice(0, 5))}</span> <b>${m.callsign}</b> ${m.dir === 'up' ? '↓ from ATC' : '↑ sent'}: ${h(m.text)} <span class="st-${m.status}">${m.resp === 'N' ? '' : m.status.toUpperCase()}</span></div>`).join('')}</div>` : '';
    const html = (items.join('') || `<div class="empty">No message to answer. Uplinks from the controller appear here with WILCO / UNABLE / STANDBY; ${conns.filter(d => d.state === 'cda').length} aircraft connected.</div>`) + log;
    if (html !== dlH) { box.innerHTML = html; dlH = html; }
  }
  // one listener each (the lists are re-drawn every second; mousedown survives a re-draw between press and release)
  function bindLists() {
    $('#dlBox').addEventListener('mousedown', e => {
      const da = e.target.closest('[data-dlid]');
      if (da) { e.preventDefault(); dlAnswer(da.dataset.cs, +da.dataset.dlid, da.dataset.ans, da.dataset.why); return; }
      const c = e.target.closest('[data-cs]'); if (c) select(c.dataset.cs);
    });
    $('#todo').addEventListener('mousedown', e => {
      const da = e.target.closest('[data-dlid]');
      if (da) { e.preventDefault(); dlAnswer(da.dataset.cs, +da.dataset.dlid, da.dataset.ans, da.dataset.why); return; }
      const dr = e.target.closest('[data-dlreq]');
      if (dr) { e.preventDefault(); dlSend(dr.dataset.cs, { request_id: dr.dataset.dlreq }); select(dr.dataset.cs); return; }
      const wc = e.target.closest('[data-wxclear]');
      if (wc) { e.preventDefault(); fly(wc.dataset.wxclear, { type: 'wx_clear' }, $('#deck .dk-out')); select(wc.dataset.wxclear); return; }
      const b = e.target.closest('[data-rep]');
      if (b) { e.preventDefault(); const out = $('#deck .dk-out'); fly(b.dataset.cs, { type: 'reply', request_id: b.dataset.rep, decision: b.dataset.dec }, out); select(b.dataset.cs); return; }
      const c = e.target.closest('[data-cs]'); if (c) select(c.dataset.cs);
    });
    $('#fleet').addEventListener('mousedown', e => { const r = e.target.closest('[data-cs]'); if (r) select(r.dataset.cs); });
  }

  // ---- CPDLC: answers to uplinks, downlink requests
  async function dlAnswer(cs, id, ans, why) {
    select(cs);
    const out = $('#deck [data-out]');
    try { await api(`/sessions/${SID}/cpdlc/respond`, { method: 'POST', body: JSON.stringify({ message_id: id, answer: ans, reason: why || null }) });
      if (out) { out.className = 'dk-out ok'; out.textContent = `✓ ${ans}${why ? ' due to ' + why : ''} sent by data link` + (ans === 'WILCO' ? ' — the aircraft is executing' : ''); } }
    catch (e) { if (out) { out.className = 'dk-out bad'; out.textContent = '✗ ' + e.message; } }
    if (typeof pollState === 'function') pollState();
  }
  async function dlSend(cs, body) {
    const out = $('#deck [data-out]');
    try { const m = await api(`/sessions/${SID}/cpdlc/downlink`, { method: 'POST', body: JSON.stringify({ callsign: cs, ...body }) });
      if (out) { out.className = 'dk-out ok'; out.textContent = '✓ sent by data link: ' + m.text; } }
    catch (e) { if (out) { out.className = 'dk-out bad'; out.textContent = '✗ ' + e.message; } }
    if (typeof pollState === 'function') pollState();
  }

  // ---- aircraft list
  let fleetH = '';
  function renderFleet() {
    const rank = { call: 0, req: 1, ok: 2, prev: 3, out: 4 };
    const list = allAc().slice().sort((a, b) => rank[statusOf(a)[0]] - rank[statusOf(b)[0]] || a.callsign.localeCompare(b.callsign));
    const html = list.length ? `<table class="fleet"><tr><th>Callsign</th><th>Level</th><th>Speed</th><th>Nav</th><th></th></tr>` + list.map(a => {
      const [k, t] = statusOf(a), vs = a.vertical_speed_fpm > 0 ? '↑' : a.vertical_speed_fpm < 0 ? '↓' : '';
      return `<tr data-cs="${a.callsign}" class="${a.callsign === pilotSel ? 'sel' : ''} st-${k}"><td><b>${a.callsign}</b> <span class="c-dim">${a.type}</span></td>
        <td>${String(a.fl).padStart(3, '0')}${vs}${a.cleared_fl !== a.fl ? ' ' + a.cleared_fl : ''}</td><td>${fmtSpd(a)}</td><td>${fmtNav(a)}</td><td><span class="badge b-${k}">${t}</span></td></tr>`; }).join('') + '</table>'
      : '<div class="empty">No aircraft in the sector yet.</div>';
    if (html !== fleetH) { $('#fleet').innerHTML = html; fleetH = html; }
  }

  // ---- control deck of the selected aircraft
  let deckCs = null;
  async function buildDeck(cs) {
    deckCs = cs;
    const a = allAc().find(x => x.callsign === cs), box = $('#deck');
    if (!a) { box.innerHTML = '<div class="empty">Select an aircraft (in the list, in To do, or on the radar) to fly it.</div>'; return; }
    const fp = a.flight_plan || {};
    const route = [...new Set(realPoints(a.route.slice(Math.max(0, a.route.indexOf(a.next_waypoint))).concat(a.route_after_exit || [])))];
    box.innerHTML = `<div class="dk-head"><div><span class="dk-cs">${cs}</span> <span class="c-dim">${a.type} · ${h(fp.departure || '')} → ${h(fp.destination || '')}</span></div><button class="dk-x" title="close">✕</button></div>
      <div class="dk-now" data-now></div>
      <div class="dk-lim c-dim" data-lim>what this aircraft can do: loading…</div>
      <div class="dk-row"><label>Level</label><div class="dk-ctl">
        <input data-i="fl" inputmode="numeric" value="${a.cleared_fl}" style="width:62px">
        <span class="dk-steps"><button data-step="-20">−20</button><button data-step="-10">−10</button><button data-step="10">+10</button><button data-step="20">+20</button></span>
        <select data-i="rate" title="rate"><option value="">any rate</option><option value="1000">≥1000 ft/min</option><option value="1500">≥1500</option><option value="2000">≥2000</option></select>
        <button class="go" data-go="fl">Climb / descend</button><div class="dk-hint" data-hint="fl"></div></div></div>
      <div class="dk-row"><label>Heading</label><div class="dk-ctl">
        <input data-i="hdg" inputmode="numeric" placeholder="${String(Math.round(a.heading)).padStart(3, '0')}" style="width:62px">
        <button data-go="hl">◀ Left</button><button data-go="h">Turn</button><button data-go="hr">Right ▶</button>
        <button data-go="res" title="back on the route">Resume own nav</button><div class="dk-hint" data-hint="hdg"></div></div></div>
      <div class="dk-row"><label>Speed</label><div class="dk-ctl">
        <input data-i="mach" placeholder="M.78" style="width:62px"><button data-go="mach">Mach</button>
        <input data-i="kt" placeholder="280" inputmode="numeric" style="width:56px"><button data-go="kt" title="fly this IAS now">Knots</button><button data-go="kconv" title="on conversion from Mach">On conversion</button>
        <button data-go="sn">Resume normal</button><div class="dk-hint" data-hint="spd"></div></div></div>
      <div class="dk-row"><label>Direct</label><div class="dk-ctl dk-wrap">
        ${route.slice(0, 10).map(n => `<button class="pt" data-d="${n}">${n}</button>`).join('')}
        <span class="dk-any"><input data-i="dct" list="plPts" placeholder="any point" style="width:96px"><button class="go" data-go="dct">Direct</button></span></div></div>
      <div class="dk-row"><label>Route</label><div class="dk-ctl">
        <input data-i="via" placeholder="via points, e.g. IBALU TERTO" style="width:170px">
        <button class="go" data-go="via" title="re-routing via these points, then the rest of the route">Fly via</button><button data-go="redit" title="pick the points on the map (fixes or positions)">Edit on map…</button></div></div>
      <div class="dk-row"><label>Weather</label><div class="dk-ctl">
        <select data-i="side"><option value="either">either side</option><option value="left">left</option><option value="right">right</option></select>
        <button data-go="dev">Deviate around weather</button><button data-go="wxclear" title="the pilot reports clear of the weather: from now on, weather it flies through counts as a penetration">Clear of weather</button></div></div>
      <div class="dk-row" data-dlrow><label>CPDLC</label><div class="dk-ctl">
        <span class="dk-dl" data-dlst></span>
        <select data-i="dlk"><option value="level">Request level</option><option value="direct">Request direct</option><option value="speed">Request speed</option><option value="deviation">Weather deviation</option><option value="text">Free text</option></select>
        <input data-i="dlv" placeholder="370 / ROLRO / M78 / L20 / text" style="width:150px">
        <select data-i="dlr" title="reason"><option value="">no reason</option><option value="weather">due to weather</option><option value="performance">due to performance</option></select>
        <button class="go" data-go="dl">Send</button></div></div>
      <div class="dk-out" data-out></div>`;
    const val = k => box.querySelector(`[data-i=${k}]`).value.trim(), out = box.querySelector('[data-out]');
    const flBtn = box.querySelector('[data-go=fl]'), flIn = box.querySelector('[data-i=fl]');
    const label = () => { const cur = allAc().find(x => x.callsign === cs); const v = parseInt(flIn.value, 10);
      flBtn.textContent = !cur || !v ? 'Climb / descend' : v * 100 > cur.altitude_ft + 50 ? `Climb FL${v}` : v * 100 < cur.altitude_ft - 50 ? `Descend FL${v}` : `Maintain FL${v}`; };
    flIn.oninput = label; label();
    box.querySelectorAll('[data-step]').forEach(b => b.onclick = () => { flIn.value = Math.max(100, (parseInt(flIn.value, 10) || a.cleared_fl) + +b.dataset.step); label(); });
    const go = async k => {
      if (k === 'fl') { const v = parseInt(val('fl').replace(/^F(L)?/i, ''), 10); if (!v) return; const r = val('rate');
        await fly(cs, { type: 'altitude', fl: v, ...(r ? { rate_fpm: +r } : {}) }, out); }
      else if (k === 'h' || k === 'hl' || k === 'hr') { const v = parseInt(val('hdg'), 10); if (!v) { out.className = 'dk-out bad'; out.textContent = 'type a heading first'; return; }
        await fly(cs, { type: 'heading', heading: v, turn: k === 'hl' ? 'left' : k === 'hr' ? 'right' : null }, out); }
      else if (k === 'res') await fly(cs, { type: 'resume' }, out);
      else if (k === 'mach') { const sp = speedInstr(val('mach').startsWith('M') || val('mach').startsWith('.') ? val('mach') : 'M' + val('mach')); if (sp && sp.mach) await fly(cs, sp, out); else { out.className = 'dk-out bad'; out.textContent = 'Mach like 78 or .78'; } }
      else if (k === 'kt' || k === 'kconv') { const v = parseInt(val('kt'), 10); if (!v) { out.className = 'dk-out bad'; out.textContent = 'type a speed in knots first'; return; }
        await fly(cs, k === 'kt' ? { type: 'speed', ias_kt: v } : { type: 'speed', conversion_kt: v }, out); }
      else if (k === 'sn') await fly(cs, { type: 'speed', resume_normal: true }, out);
      else if (k === 'dct') { const v = val('dct').toUpperCase(); if (v) await fly(cs, { type: 'direct', waypoint: v }, out); }
      else if (k === 'dev') await fly(cs, { type: 'deviation', side: val('side') }, out);
      else if (k === 'wxclear') await fly(cs, { type: 'wx_clear' }, out);
      else if (k === 'via') { const v = val('via').toUpperCase().split(/[\s,]+/).filter(x => x && x !== 'DCT'); if (v.length) await fly(cs, { type: 'reroute', points: v }, out); }
      else if (k === 'redit') editRoute(cs);
      else if (k === 'dl') { const v = val('dlv'); if (!v) { out.className = 'dk-out bad'; out.textContent = 'type what you request'; return; }
        await dlSend(cs, { kind: val('dlk'), value: v, reason: val('dlr') || null }); box.querySelector('[data-i=dlv]').value = ''; }
    };
    box.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(b.dataset.go));
    box.querySelectorAll('[data-d]').forEach(b => b.onclick = () => fly(cs, { type: 'direct', waypoint: b.dataset.d }, out));
    const enterMap = { fl: 'fl', hdg: 'h', mach: 'mach', kt: 'kt', dct: 'dct', via: 'via', dlv: 'dl' };
    box.querySelectorAll('input[data-i]').forEach(i => i.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter' && enterMap[i.dataset.i]) go(enterMap[i.dataset.i]); });
    box.querySelector('.dk-x').onclick = () => { pilotSel = null; selected = null; renderPilot(); };
    loadPerf(cs);
  }
  // what the aircraft can do now (it changes with level and weight): ranges the pseudo-pilot can give or request
  let perfCs = null, perfAt = 0;
  async function loadPerf(cs) {
    perfCs = cs; perfAt = Date.now();
    let d; try { d = await api(`/sessions/${SID}/aircraft/${cs}`); } catch (e) { return; }
    const box = $('#deck'), p = d.performance; if (deckCs !== cs || !p) return;
    const m = (p.assignable_mach || []).map(x => '.' + Math.round(x * 100)), kt = p.assignable_ias_kt || [], own = p.own_speed_schedule || {};
    const fl = d.assignable_fl || [100, 460], maxNow = Math.min(fl[1], p.max_fl_at_present_weight);
    const set = (k, t) => { const el = box.querySelector(`[data-hint=${k}]`); if (el) el.textContent = t; };
    box.querySelector('[data-lim]').innerHTML = `<b>Can do now:</b> FL${fl[0]}–FL${maxNow} · Mach ${m.join('–')} · ${kt.join('–')} kt · climb ${p.climb_rate_available_fpm} ft/min · descent up to ${Math.round(p.idle_descent_rate_fpm * 1.5 / 100) * 100} ft/min`;
    set('fl', `levels FL${fl[0]}–FL${maxNow} (max FL${p.max_fl_at_present_weight} at this weight) · climb now ≤ ${p.climb_rate_available_fpm} ft/min · idle descent ${p.idle_descent_rate_fpm} ft/min (up to ${Math.round(p.idle_descent_rate_fpm * 1.5 / 100) * 100} with speed brakes)`);
    set('hdg', `turn radius ${p.turn_radius_nm} NM at present speed`);
    set('spd', `Mach ${m.join('–')} · ${kt.join('–')} kt · own schedule M${own.mach ? '.' + Math.round(own.mach * 100) : '?'} / ${own.cas_kt || '?'} kt, conversion FL${own.conversion_fl || '?'} · a speed may still be refused at some levels (buffet, VMO/MMO)`);
    const rate = box.querySelector('[data-i=rate]');
    if (rate) for (const o of rate.options) if (o.value) o.disabled = +o.value > Math.max(p.climb_rate_available_fpm, p.idle_descent_rate_fpm * 1.5);
    const mi = box.querySelector('[data-i=mach]'), ki = box.querySelector('[data-i=kt]'), fi = box.querySelector('[data-i=fl]');
    if (mi) mi.placeholder = m.join('–'); if (ki) ki.placeholder = kt.join('–'); if (fi) fi.title = `FL${fl[0]}–FL${maxNow}`;
  }
  function refreshDeck() {
    if (deckCs && deckCs === perfCs && Date.now() - perfAt > 10000) loadPerf(deckCs);
    const a = deckCs && allAc().find(x => x.callsign === deckCs), el = $('#deck [data-now]');
    if (!a || !el) return;
    const [k, t] = statusOf(a), vs = a.vertical_speed_fpm;
    const pend = (state.pending_instructions || []).filter(p => p.text && p.text.startsWith(deckCs)).map(p => h(p.text.slice(deckCs.length + 1)));
    const co = (state.coord || {})[deckCs];
    const coTxt = co && a.assumed && co.state && co.state !== 'transferred' ? `<div class="dk-pend">coordinated with ${h(co.next_unit)}: F${co.coordinated} (${co.state.toUpperCase()})${co.manual ? ' · <span class="c-warn">phone coordination needed</span>' : ''}</div>` : '';
    const dd = DL()[deckCs], dst = $('#deck [data-dlst]'), drow = $('#deck [data-dlrow]');
    if (drow) {
      const on = dd && dd.state === 'cda';
      drow.style.display = dd ? '' : 'none';
      drow.querySelectorAll('select, input, button').forEach(x => x.disabled = !on);
      if (dst) dst.innerHTML = !dd ? '' : on ? `<span class="c-ok">connected</span> ${h(dd.cda || '')}${dd.nda ? ' · next ' + h(dd.nda) : ''}` : dd.state === 'logon' ? `logged on, CDA ${h(dd.cda || 'previous unit')}` : dd.state === 'transferred' ? `transferred to ${h(dd.cda || '')}` : 'not logged on yet';
    }
    el.innerHTML = coTxt + `<span><b>FL${String(a.fl).padStart(3, '0')}</b>${vs > 0 ? ' ↑' : vs < 0 ? ' ↓' : ''}${a.cleared_fl !== a.fl ? ' → ' + a.cleared_fl : ''}</span><span>${fmtSpd(a)}</span><span>HDG ${String(Math.round(a.heading)).padStart(3, '0')}</span><span>${fmtNav(a)}</span><span class="badge b-${k}">${t}</span>` +
      (pend.length ? `<div class="dk-pend">executing: ${pend.join(' · ')}</div>` : '');
  }
  function renderPilot(scroll) {
    if (!state || !$('#fleet')) return;
    if (!$('#plPts') && sector) { const dl = document.createElement('datalist'); dl.id = 'plPts'; dl.innerHTML = realPoints(Object.keys(sector.waypoints)).map(n => `<option value="${n}">`).join(''); document.body.appendChild(dl); }
    if (!$('#todo').dataset.bound) { $('#todo').dataset.bound = 1; bindLists(); }
    if (pilotSel && !allAc().some(a => a.callsign === pilotSel)) pilotSel = null;
    renderTodo(); renderDl(); renderFleet();
    if (pilotSel !== deckCs || !$('#deck').innerHTML) buildDeck(pilotSel);
    refreshDeck();
    if (scroll && pilotSel) $('#deckSec').scrollIntoView({ block: 'nearest' });
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
    const aw = $('#autoWx');
    if (aw) aw.onchange = async () => { try { await api(`/sessions/${SID}/control`, { method: 'POST', body: JSON.stringify({ action: 'auto_wx', value: aw.checked }) });
      $('#exMsg').textContent = aw.checked ? 'simulated pilots deviate around weather by themselves' : 'aircraft deviate only when a pseudo-pilot makes them'; } catch (e) { $('#exMsg').textContent = e.message; } };
    setInterval(() => { if (aw && state && state.session && document.activeElement !== aw) aw.checked = state.session.auto_wx !== false; }, 2000);
    $('#endBtn').onclick = async () => {
      if (!confirm('End this exercise for everybody? The recording (radar + all audio) is then turned into an .mp4 on the home page.')) return;
      try { await api(`/api/exercises/${EXID}/end`, { method: 'POST' }); showEnded(); setTimeout(() => location.href = '/#evaluations', 1500); } catch (e) { $('#exMsg').textContent = e.message; }
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

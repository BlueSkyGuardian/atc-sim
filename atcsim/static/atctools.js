// Controller tools on the radar page: flight plans (window, flight list), MTCD list and conflict display,
// keyboard shortcuts and their help, UTC clock. Uses the radar page's globals (state, sector, SID, SEAT, selected, ...).
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const wins = document.getElementById('wins');
  const allAc = () => state ? state.aircraft.concat(state.transferred || []) : [];

  // ------------------------------------------------------------------ floating windows
  function win(id, title, x, y, w) {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement('div'); el.className = 'win'; el.id = id;
      el.innerHTML = `<div class="wh"><span class="wt"></span><span><button data-close title="close (Esc)">✕</button></span></div><div class="wb"></div>`;
      const r = document.getElementById('scope').getBoundingClientRect();
      el.style.left = Math.max(8, Math.min(x, r.width - (w || 420) - 8)) + 'px'; el.style.top = y + 'px';
      if (w) el.style.width = w + 'px';
      wins.appendChild(el);
      el.querySelector('[data-close]').onclick = () => closeWin(id);
      const head = el.querySelector('.wh');
      head.addEventListener('mousedown', e => {
        if (e.target.closest('button')) return;
        const sx = e.clientX, sy = e.clientY, ox = el.offsetLeft, oy = el.offsetTop;
        const mv = ev => { el.style.left = ox + ev.clientX - sx + 'px'; el.style.top = Math.max(0, oy + ev.clientY - sy) + 'px'; };
        const up = () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
        window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up); e.preventDefault();
      });
      el.addEventListener('mousedown', e => e.stopPropagation());
      el.addEventListener('keydown', e => e.stopPropagation());
    }
    el.querySelector('.wt').innerHTML = title;
    return el;
  }
  function closeWin(id) { const el = document.getElementById(id); if (el) el.remove(); if (id === 'fpWin') fpCs = null; if (id === 'tyWin') tyCs = null; if (id === 'mtWin') mtFocus = null; }
  const isOpen = id => !!document.getElementById(id);
  window.atcWin = win; window.atcCloseWin = closeWin; window.atcIsOpen = isOpen;   // for cpdlc.js

  // ------------------------------------------------------------------ flight plan window
  let fpCs = null;
  async function openFpl(cs) {
    if (!SID || !cs) return;
    fpCs = cs;
    let d; try { d = await api(`/sessions/${SID}/flightplans/${cs}`); } catch (e) { return; }
    if (fpCs !== cs) return;                              // closed (or another one) meanwhile
    const el = win('fpWin', `<b>${esc(d.callsign)}</b> <span class="st ${d.status.split(',')[0].replace(' ', '')}">${esc(d.status)}</span> <span class="c-dim" style="font-weight:400">flight plan · ${esc(d.utc)} UTC</span>`, 60, 60, 520);
    const c = d.correlation, co = d.coordination, ent = d.entries || {};
    const fp = d.flight_plan || {};
    el.querySelector('.wb').innerHTML = `
      <div style="font-size:12px;margin-bottom:6px">${c.track ? '✓ <b>correlated</b> with the radar track' : '<span class="c-dim">no track yet (planned)</span>'} · SSR <b>A${esc(c.ssr)}</b> · Mode S <b>${esc(c.mode_s_address)}</b> · ${esc(c.surveillance)}${fp.registration ? ' · REG <b>' + esc(fp.registration) + '</b>' : ''}${fp.telephony ? ' · <span class="c-dim">telephony</span> ' + esc(fp.telephony) : ''}</div>
      <pre>${esc(d.message)}</pre>
      <div class="sub">Coordination</div>
      <table><tr><th></th><th>Point</th><th>ETO</th><th>Level</th><th>Unit</th></tr>
        <tr><td class="c-dim">Entry</td><td>${esc(co.entry.point)}</td><td>${esc(co.entry.eto)}</td><td>F${String(co.entry.level).padStart(3, '0')}</td><td>from ${esc(co.entry.from)}</td></tr>
        <tr><td class="c-dim">Exit</td><td>${esc(co.exit.point)}</td><td>${esc(co.exit.eto || '—')}</td><td>F${String(co.exit.level).padStart(3, '0')} <span class="c-dim">${co.exit.xfl_entered ? 'XFL' : '(= CFL F' + String(co.exit.cfl).padStart(3, '0') + ')'}</span></td><td>to ${esc(co.exit.to)} <span class="c-dim">${esc(co.exit.state || '')}</span></td></tr></table>
      ${(d.oldi || []).length ? `<div class="sub">OLDI</div><table>${d.oldi.map(m => `<tr><td>${esc(m.utc)}</td><td class="${m.dir}">${m.dir === 'out' ? '→' : '←'} ${esc(m.unit)}</td><td><b>${esc(m.type)}</b></td><td>${esc(m.point)} ${esc(m.eto)}</td><td>F${String(m.level).padStart(3, '0')}</td><td class="c-dim">${esc(m.status)}${m.note ? ' · ' + esc(m.note) : ''}</td></tr>`).join('')}</table>` : ''}
      <div class="sub">Estimates (points ahead)</div>
      <table><tr><th>Point</th><th>ETO</th><th>In</th><th>Dist</th><th></th></tr>${d.etos.map(e => `<tr><td>${esc(e.point)}</td><td>${esc(e.eto)}</td><td>${Math.max(0, Math.round(e.in_s / 60))}'</td><td>${e.dist_nm} NM</td><td class="c-dim">${esc(e.role)}</td></tr>`).join('') || '<tr><td colspan="5" class="c-dim">no point ahead</td></tr>'}</table>
      <div class="sub">Items</div>
      <div class="kv">${d.items.map(([n, k, v]) => `<span>${n}</span><span>${esc(k)}</span><span>${esc(v)}</span>`).join('')}</div>`;
  }
  window.openFpl = openFpl;
  setInterval(() => { if (fpCs && isOpen('fpWin')) openFpl(fpCs); }, 6000);

  // ------------------------------------------------------------------ flight list
  let flFilter = '';
  async function openFlightList(refresh) {
    if (!SID) return;
    let d; try { d = await api(`/sessions/${SID}/flightplans`); } catch (e) { return; }
    if (refresh && !isOpen('flWin')) return;
    const first = !isOpen('flWin');
    const el = win('flWin', `Flight plans <span class="c-dim" style="font-weight:400">${d.flights.length} · ${esc(d.utc)} UTC</span>`, 420, 50, 640);
    const body = el.querySelector('.wb');
    if (first) {
      body.innerHTML = `<input class="flt" placeholder="filter: callsign, SSR, aerodrome, point, unit, status" value="${esc(flFilter)}"><div class="tbl"></div>`;
      const inp = body.querySelector('.flt');
      inp.oninput = () => { flFilter = inp.value; render(); };
      inp.onkeydown = e => { e.stopPropagation(); if (e.key === 'Escape') closeWin('flWin'); };
    }
    const render = () => {
      const f = flFilter.trim().toUpperCase();
      const rows = d.flights.filter(r => !f || [r.callsign, r.ssr, r.dep, r.dest, r.entry, r.exit, r.next_unit, r.prev_unit, r.type, r.status].some(v => String(v || '').toUpperCase().includes(f)));
      body.querySelector('.tbl').innerHTML = `<table><tr><th>Callsign</th><th>SSR</th><th>Type</th><th>Dep</th><th>Dest</th><th>RFL</th><th>Entry</th><th>ETO</th><th>Exit</th><th>Units</th><th>Status</th></tr>` +
        rows.map(r => `<tr data-cs="${esc(r.callsign)}"><td><b>${esc(r.callsign)}</b>${r.military ? ' <span class="c-dim">MIL</span>' : ''}${r.rvsm !== 'approved' ? ' <span class="c-warn">NRVSM</span>' : ''}</td><td>${esc(r.ssr)}</td><td>${esc(r.type)}/${esc(r.wake)}</td><td>${esc(r.dep)}</td><td>${esc(r.dest)}</td><td>${esc(r.rfl)}</td><td>${esc(r.entry)}</td><td>${esc(r.eto_entry)}</td><td>${esc(r.exit)}</td><td class="c-dim">${esc(r.prev_unit)}→${esc(r.next_unit)}</td><td><span class="st ${r.status.split(',')[0].replace(' ', '')}">${esc(r.status)}</span></td></tr>`).join('') + '</table>';
      body.querySelectorAll('tr[data-cs]').forEach(tr => tr.onclick = () => { const cs = tr.dataset.cs; if (allAc().some(a => a.callsign === cs)) { selected = cs; } openFpl(cs); });
    };
    render();
  }
  setInterval(() => { if (isOpen('flWin')) openFlightList(true); }, 8000);

  // ------------------------------------------------------------------ MTCD list and display
  let mtFocus = null;                     // 'A|B' pair shown on the scope
  function renderMtcd() {
    const list = mtcdOn ? (state && state.mtcd) || [] : [];
    const b = document.getElementById('mtBtn');
    if (b) { b.textContent = !mtcdOn ? 'MTCD off' : list.length ? `MTCD ${list.length}` : 'MTCD on'; b.classList.toggle('on', mtcdOn); b.classList.toggle('hit', mtcdOn && list.length > 0); }
    if (!isOpen('mtWin') || !mtcdOn) return;
    const look = Math.round(((state && state.mtcd_look_s) || 1200) / 60);
    const el = win('mtWin', `MTCD <span class="c-dim" style="font-weight:400">conflicts in the next ${look} min · system trajectories</span>`, 60, 50, 380);
    const wb = el.querySelector('.wb');
    if (!wb.querySelector('.mtl')) {                 // built once: the look-ahead field keeps its focus across refreshes
      const can = SEAT === 'atc' || SEAT === 'instructor';
      wb.innerHTML = `<div class="mtla"><label for="mtLook">Look-ahead</label>
        <button class="small" data-la="-5" title="5 min less" ${can ? '' : 'disabled'}>−</button><input id="mtLook" type="number" min="5" max="60" step="5" ${can ? '' : 'disabled title="the controller or the instructor sets it"'}><button class="small" data-la="5" title="5 min more" ${can ? '' : 'disabled'}>+</button>
        <span class="c-dim">min</span><span class="c-dim mtmsg"></span></div><div class="mtl"></div>`;
      const inp = wb.querySelector('#mtLook'), msg = wb.querySelector('.mtmsg');
      const send = async v => {
        v = Math.max(5, Math.min(60, Math.round(+v)));
        if (!isFinite(v)) return;
        inp.value = v; msg.textContent = '…';
        try { const r = await api(`/sessions/${SID}/mtcd/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ look_ahead_min: v }) });
          if (state) { state.mtcd_look_s = r.look_ahead_s; state.mtcd = r.conflicts; } msg.textContent = ''; renderMtcd(); }
        catch (e) { msg.textContent = e.message; }
      };
      inp.onchange = () => send(inp.value);
      inp.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') { send(inp.value); inp.blur(); } if (e.key === 'Escape') inp.blur(); };
      wb.querySelectorAll('[data-la]').forEach(b => b.onclick = () => send((+inp.value || look) + +b.dataset.la));
    }
    const inp = wb.querySelector('#mtLook');
    if (document.activeElement !== inp) inp.value = look;
    const body = wb.querySelector('.mtl');
    const html = list.length ? list.map(c => { const k = c.aircraft.join('|');
      return `<div class="mt ${mtFocus === k ? 'on' : ''}" data-k="${k}"><b>${c.aircraft.join(' × ')}</b> in ${Math.floor(c.time_to_conflict_s / 60)}' · min ${c.min_horizontal_nm} NM / ${c.vertical_ft} ft
        <small>FL${c.levels[0]} / FL${c.levels[1]} at the start · ${Math.round(c.duration_s / 60) || '<1'} min long · min ${c.required_horizontal_nm} NM / ${c.required_vertical_ft} ft${c.current ? ' · <b>now</b>' : ''}</small></div>`; }).join('')
      : `<div class="empty">No medium-term conflict in the next ${look} min between the system trajectories (cleared levels as entered, routes, headings and directs entered).</div>`;
    if (body.dataset.h !== html) { body.innerHTML = html; body.dataset.h = html;
      body.querySelectorAll('[data-k]').forEach(d => d.onclick = () => { mtFocus = mtFocus === d.dataset.k ? null : d.dataset.k; selected = mtFocus ? d.dataset.k.split('|')[0] : selected; body.dataset.h = ''; renderMtcd(); }); }
  }
  setInterval(renderMtcd, 1000);
  // on the scope: the focused pair (or the selected aircraft's conflicts): where each will be when the conflict starts
  function setMtcd(on) {               // the MTCD function on / off (tags, diamonds, conflict display and list)
    mtcdOn = on; try { localStorage.setItem('atc_mtcd', on ? '1' : '0'); } catch (e) {}
    if (on) { win('mtWin', 'MTCD', 60, 50, 380); } else closeWin('mtWin');
    renderMtcd();
  }
  const mtOff = window.mtOff = {}, mtRects = window.__mtRects = [];
  window.mtcdLabelAt = (x, y) => mtRects.find(r => x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1);
  window.drawMtcd = function (byCs) {
    mtRects.length = 0;
    if (!mtcdOn || !state || !state.mtcd || SEAT === 'pilot' || replayMode) return;
    const dpr = devicePixelRatio;
    for (const c of state.mtcd) {
      const k = c.aircraft.join('|');
      if (k !== mtFocus && !c.aircraft.includes(selected)) continue;
      const A = byCs[c.aircraft[0]], B = byCs[c.aircraft[1]]; if (!A || !B) continue;
      ctx.strokeStyle = TH.mtcd; ctx.fillStyle = TH.mtcd; ctx.lineWidth = 1.3 * dpr; ctx.setLineDash([5 * dpr, 4 * dpr]);
      const pts = [[A, c.at[0]], [B, c.at[1]]];
      for (const [P, q] of pts) { const [x1, y1] = toPx(P.x_nm, P.y_nm), [x2, y2] = toPx(q[0], q[1]);
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
      ctx.setLineDash([]);
      const [ax, ay] = toPx(...c.at[0]), [bx, by] = toPx(...c.at[1]);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      for (const [x, y] of [[ax, ay], [bx, by]]) { ctx.beginPath(); ctx.arc(x, y, 4 * dpr, 0, Math.PI * 2); ctx.stroke(); }
      ctx.font = `${10.5 * dpr}px ui-monospace,monospace`;
      const t = `MTCD ${Math.floor(c.time_to_conflict_s / 60)}' ${c.min_horizontal_nm} NM FL${c.levels[0]}/${c.levels[1]}`;
      const off = mtOff[k] || [0, 0];                       // the label can be dragged away (leader line)
      const cx0 = (ax + bx) / 2 + 8 * dpr, cy0 = (ay + by) / 2 - 8 * dpr, mx = cx0 + off[0] * dpr, my = cy0 + off[1] * dpr, w = ctx.measureText(t).width;
      if (off[0] || off[1]) { ctx.strokeStyle = TH.mtcd + '99'; ctx.lineWidth = 1 * dpr; ctx.setLineDash([2 * dpr, 3 * dpr]); ctx.beginPath(); ctx.moveTo(cx0 - 8 * dpr, cy0 + 8 * dpr); ctx.lineTo(mx, my - 4 * dpr); ctx.stroke(); ctx.setLineDash([]); }
      ctx.fillStyle = '#0b1016cc'; ctx.fillRect(mx - 3 * dpr, my - 11 * dpr, w + 6 * dpr, 15 * dpr);
      ctx.fillStyle = TH.mtcd; ctx.fillText(t, mx, my);
      mtRects.push({ k, x0: mx - 3 * dpr, y0: my - 11 * dpr, x1: mx + w + 3 * dpr, y1: my + 4 * dpr });
    }
  };

  // ------------------------------------------------------------------ OLDI: coordination messages with the adjacent units
  let olFilter = 'all';
  async function renderOldi(force) {
    const b = document.getElementById('olBtn');
    const man = state && state.coord ? Object.values(state.coord).filter(c => c.manual).length : 0;   // REV refused: phone
    if (b) { b.textContent = man ? `OLDI ⚠${man}` : 'OLDI'; b.classList.toggle('hit', man > 0); b.title = man ? `${man} coordination(s) to do by phone (O)` : 'OLDI: coordination messages with the adjacent units (O)'; }
    if (!isOpen('olWin') || !SID) return;
    let d; try { d = await api(`/sessions/${SID}/oldi`); } catch (e) { return; }
    if (!isOpen('olWin')) return;                       // closed while the request was out
    const el = win('olWin', `OLDI <span class="c-dim" style="font-weight:400">coordination with the adjacent units · ABI ${Math.round(d.abi_s / 60)}' · ACT ${Math.round(d.act_s / 60)}' · REV until ${Math.round(d.rev_limit_s / 60)}' before the point</span>`, 80, 60, 660);
    const body = el.querySelector('.wb');
    const ms = d.messages.slice().reverse().filter(m => olFilter === 'all' || m.dir === olFilter);
    const manual = Object.entries(d.coord).filter(([, c]) => c.manual);
    const html = `<div class="ol-filter">${[['all', 'All'], ['out', 'Sent (to next units)'], ['in', 'Received (from previous units)']].map(([k, t]) => `<button data-of="${k}" class="${olFilter === k ? 'on' : ''}">${t}</button>`).join('')}</div>` +
      (manual.length ? `<div class="sub" style="color:var(--warn)">Coordination by phone needed</div>` + manual.map(([cs, c]) => `<div class="c-warn" style="font-size:12px;margin-bottom:3px"><b>${esc(cs)}</b> ${esc(c.manual)}</div>`).join('') : '') +
      `<table><tr><th>UTC</th><th></th><th>Msg</th><th>Callsign</th><th>Unit</th><th>Point</th><th>ETO</th><th>Level</th><th></th></tr>` +
      ms.slice(0, 200).map(m => `<tr data-cs="${esc(m.callsign)}"><td>${esc(m.utc.slice(0, 2) + ':' + m.utc.slice(2))}</td><td class="${m.dir}">${m.dir === 'out' ? '→' : '←'}</td><td><b>${esc(m.type)}</b></td><td>${esc(m.callsign)}</td><td>${esc(m.unit)}</td><td>${esc(m.point)}</td><td>${esc(m.eto)}</td><td>F${String(m.level).padStart(3, '0')}</td><td class="c-dim">${esc(m.status)}${m.note ? ' · ' + esc(m.note) : ''}</td></tr>`).join('') + '</table>' +
      (ms.length ? '' : '<div class="empty">no message yet</div>');
    if (force || body.dataset.h !== html) { body.innerHTML = html; body.dataset.h = html;
      body.querySelectorAll('[data-of]').forEach(x => x.onclick = () => { olFilter = x.dataset.of; renderOldi(true); });
      body.querySelectorAll('tr[data-cs]').forEach(tr => tr.onclick = () => { if (allAc().some(a => a.callsign === tr.dataset.cs)) selected = tr.dataset.cs; openFpl(tr.dataset.cs); }); }
  }
  setInterval(() => renderOldi(false), 2000);
  function toggleOldi() { if (isOpen('olWin')) closeWin('olWin'); else { win('olWin', 'OLDI', 80, 60, 660); renderOldi(true); } }

  // ------------------------------------------------------------------ track info window (the aircraft under the mouse)
  let pinned = null, infoH = '', lastHover = null;
  const infoOn = () => isOpen('tiWin');
  function toggleInfo() { if (infoOn()) closeWin('tiWin'); else { infoH = ''; win('tiWin', 'Track info', 10, 150, 300); renderInfo(); } try { localStorage.setItem('atc_info', infoOn() ? '1' : '0'); } catch (e) {} }
  window.pinInfo = cs => { pinned = pinned === cs ? null : cs; if (!infoOn()) toggleInfo(); renderInfo(); };
  const P3 = v => v == null ? '—' : String(Math.round(v)).padStart(3, '0');
  function renderInfo() {
    if (!infoOn()) return;
    if (hoverAc) lastHover = hoverAc;
    // the pinned aircraft, else the one under the mouse, else the selected one, else the last one under the mouse
    const cs = pinned || hoverAc || selected || lastHover, a = cs && allAc().find(x => x.callsign === cs);
    const el = win('tiWin', a ? `<b>${esc(a.callsign)}</b> <span class="c-dim" style="font-weight:400">${pinned ? 'pinned' : hoverAc === cs ? 'under the mouse' : selected === cs ? 'selected' : 'last under the mouse'}</span> <button data-pin title="keep this aircraft (middle-click another)" style="padding:0 5px;font-size:10px" class="${pinned ? 'on' : ''}">📌</button>` : 'Track info', 10, 150, 300);
    let html;
    if (!a) html = '<div class="empty">Move the mouse over an aircraft or its label (middle-click one to keep it here).</div>';
    else {
      const fp = a.flight_plan || {}, ms = a.mode_s, co = (state.coord || {})[cs] || {}, ent = (state.entries || {})[cs] || {};
      const sp = (state.special || {})[cs] || [], mt = mtcdOn ? (state.mtcd || []).filter(c => c.aircraft.includes(cs)) : [];
      const row = (k, v) => `<span>${k}</span><span>${v}</span>`;
      const sec = t => `<div class="sub" style="grid-column:1/-1;margin:6px 0 1px">${t}</div>`;
      html = `<div class="ti">` +
        row('SSR / Mode S', `A${esc(a.squawk || fp.ssr || '----')} · ${esc((ms && ms.address) || fp.mode_s_address || '—')}`) +
        row('Type', `${esc(a.type)}/${esc(fp.wake || '')} · ${esc(fp.registration || '')}`) +
        row('Operator', esc(fp.operator || fp.telephony || '—')) +
        row('Flight', `${esc(fp.departure || '----')} → ${esc(fp.destination || '----')} · RFL ${esc((fp.rfl || fp.cruise || '').replace('F', ''))}`) +
        sec('Levels') +
        row('Current / cleared', `${P3(a.fl)}${a.vertical_speed_fpm > 0 ? ' ↑' : a.vertical_speed_fpm < 0 ? ' ↓' : ''} / ${P3(co.cfl != null ? co.cfl : a.cleared_fl)}`) +
        row('Exit level', co.xfl != null ? `${P3(co.xfl)} ${co.xfl_entered ? '' : '(= CFL)'} ${co.state ? '· ' + co.state.toUpperCase() + (co.coordinated != null ? ' F' + P3(co.coordinated) : '') : ''}` : '—') +
        (co.entry_level ? row('Entry (coord.)', `F${P3(co.entry_level)} from ${esc(a.prev_unit)}`) : '') +
        sec(ms ? (ms.ehs ? 'Mode S enhanced surveillance' : 'Mode S elementary surveillance') : 'Mode A/C (no Mode S)') +
        (ms && ms.ehs ? row('Selected level', `${P3(ms.selected_fl)} · ${esc(ms.baro_setting)}`) + row('Mag heading / track', `${P3(ms.mag_heading)} / ${P3(ms.track_deg)}`) +
          row('IAS / Mach', `${ms.ias_kt} kt / M${String(Math.round(ms.mach * 100)).padStart(2, '0')}`) + row('TAS / GS', `${ms.tas_kt} / ${ms.gs_kt} kt`) +
          row('Vertical rate', `${ms.baro_rate_fpm > 0 ? '+' : ''}${ms.baro_rate_fpm} ft/min`) + row('Roll / track rate', `${ms.roll_deg < -0.5 ? 'L' : ms.roll_deg > 0.5 ? 'R' : ''}${Math.abs(Math.round(ms.roll_deg))}° / ${ms.track_rate_deg_s.toFixed(1)}°/s`)
          : row('Radar', `GS ${a.gs_kt} kt · track ${P3(a.track_deg)}`)) +
        sec('Route and transfer') +
        row('Next points', esc(((ent.route && co.route_ahead) || (a.route || []).slice(Math.max(0, (a.route || []).indexOf(a.next_waypoint || a.fp_next))).concat(a.route_after_exit || []).filter(n => !COPS().has(n) && !n.startsWith('@'))).slice(0, 6).join(' '))) +
        row('Units', `${esc(a.prev_unit || '?')} → ${esc(a.next_unit || '?')}`) +
        (a.to_exit_s != null && a.status === 'active' ? row('Exit', `${esc(a.planned_exit)} in ${Math.round(a.to_exit_s / 60)}' (${a.to_exit_nm} NM)`) : '') +
        (sp.length || mt.length ? sec('Alerts') + sp.map(t => row(esc(t[0]), esc(t[2] || ''))).join('') + mt.map(c => row('MTCD', `${esc(c.aircraft.find(x => x !== cs))} in ${Math.floor(c.time_to_conflict_s / 60)}' · ${c.min_horizontal_nm} NM`)).join('') : '') +
        `</div>`;
    }
    if (html !== infoH) { el.querySelector('.wb').innerHTML = html; infoH = html; }
    const pb = el.querySelector('[data-pin]'); if (pb) pb.onclick = () => { pinned = pinned ? null : (a && a.callsign); infoH = ''; renderInfo(); };
  }
  setInterval(renderInfo, 300);
  try { if (localStorage.getItem('atc_info') === '1') setTimeout(() => { if (!infoOn()) toggleInfo(); }, 1500); } catch (e) {}

  // ------------------------------------------------------------------ aircraft type performance window
  let tyCs = null;
  async function openTypeWin(cs) {
    if (!SID || !cs) return;
    tyCs = cs;
    let d; try { d = await api(`/sessions/${SID}/aircraft/${cs}/performance`); } catch (e) { return; }
    if (tyCs !== cs) return;
    const t = d.type, n = d.now, a = allAc().find(x => x.callsign === cs), fl = a ? a.fl : null;
    const el = win('tyWin', `<b>${esc(t.icao)}</b> <span class="c-dim" style="font-weight:400">${esc(t.name)} · ${esc(cs)} · wake ${esc(d.wake || '')}</span>`, 380, 90, 470);
    el.querySelector('[data-close]').onclick = () => { tyCs = null; closeWin('tyWin'); };
    const row = (k, v) => `<span>${k}</span><span>${v}</span>`;
    const tbl = d.levels.map(r => `<tr class="${fl != null && Math.abs(r.fl - fl) < 5 ? 'cur' : ''}" title="${esc(r.why || '')}"><td>FL${String(r.fl).padStart(3, '0')}</td>
      <td>${r.mode === 'mach' ? 'M' + String(Math.round(r.mach * 100)).padStart(2, '0') : r.ias_kt + ' kt'}</td><td>${r.ias_kt}</td><td>${r.tas_kt}</td>
      <td>${r.climb_fpm > 0 ? r.climb_fpm : '—'}</td><td>${r.idle_descent_fpm}</td><td class="${r.available ? 'c-ok' : 'c-los'}">${r.available ? '✓' : '✗'}</td></tr>`).join('');
    el.querySelector('.wb').innerHTML = `<div class="ti">` +
      row('Category', `${esc(t.category)}${t.rvsm_by_default ? ' · RVSM' : ''} · ${esc(t.model_source)}`) +
      row('MTOW / OEW', `${Math.round(t.mtow_kg / 1000)} t / ${Math.round(t.oew_kg / 1000)} t`) +
      row('Mass now', `${(d.mass_kg / 1000).toFixed(1)} t (load ${Math.round(d.load * 100)} %)`) +
      row('Ceiling', `FL${t.ceiling_fl}`) + row('VMO / MMO', `${t.vmo_kt} kt / M${t.mmo.toFixed(2)}`) +
      row('Speed schedule', `${t.speed_schedule.cas_kt} kt / M${t.speed_schedule.mach.toFixed(2)}, crossover FL${t.speed_schedule.crossover_fl}`) +
      row('Assignable Mach', t.assignable_mach.map(x => x.toFixed(2)).join(' – ')) + row('Assignable IAS', t.assignable_ias_kt.map(x => Math.round(x)).join(' – ') + ' kt') +
      (n ? row('Max level now', `FL${n.max_fl_at_present_weight}`) + row('Climb now', `${n.climb_rate_available_fpm} ft/min`) +
        row('Idle descent now', `${n.idle_descent_rate_fpm} ft/min (×1.5 with speed brakes)`) + row('Turn radius', `${n.turn_radius_nm} NM`) : '') +
      `</div><div class="sub">By level, at the present weight (own speed schedule)</div>
      <table><tr><th>Level</th><th>Speed</th><th>IAS</th><th>TAS</th><th>Climb</th><th>Idle desc.</th><th>Avail.</th></tr>${tbl}</table>
      <div class="c-dim" style="font-size:11px;margin-top:4px">ft/min · ✗ = the level cannot be reached at this weight (hover for why)</div>`;
  }
  window.openTypeWin = openTypeWin;
  setInterval(() => { if (tyCs && isOpen('tyWin')) openTypeWin(tyCs); }, 15000);

  // ------------------------------------------------------------------ route editor (re-routing)
  // The route from the aircraft onwards as a list of points; click the map to insert a point after the cursor:
  // a fix when its name or symbol is clicked, else the position itself. Apply = the controller's system route
  // (or, on the pseudo-pilot's deck, the route the aircraft flies).
  let RE = null;
  window.routeEditActive = () => !!RE;
  function routeAhead(a) {
    const co = (state.coord || {})[a.callsign], ent = (state.entries || {})[a.callsign] || {};
    if (ent.route && co && co.route_ahead) return co.route_ahead.slice();
    const r = a.route || [], i = Math.max(0, r.indexOf(a.next_waypoint || a.fp_next));
    return [...new Set(r.slice(i).concat(a.route_after_exit || []).filter(n => !COPS().has(n) && !n.startsWith('@')))];
  }
  window.openRouteEditor = function (cs, opts) {
    const a = allAc().find(x => x.callsign === cs); if (!a) return;
    RE = { cs, pts: (opts.initial || routeAhead(a)).map(n => ({ n })), cur: 0, opts };
    renderRE();
  };
  function closeRE() { RE = null; closeWin('reWin'); }
  const ptName = p => p.n || `pos ${p.x.toFixed(0)},${p.y.toFixed(0)}`;
  function renderRE() {
    if (!RE) return;
    const el = win('reWin', `Route <b>${esc(RE.cs)}</b> <span class="c-dim" style="font-weight:400">${esc(RE.opts.title || 're-routing')}</span>`, 340, 60, 430);
    el.querySelector('[data-close]').onclick = closeRE;
    const chips = [`<span class="rc ${RE.cur === 0 ? 'cur' : ''}" data-c="0" title="insert at the start">▸ ${esc(RE.cs)}</span>`]
      .concat(RE.pts.map((p, i) => `<span class="rc ${RE.cur === i + 1 ? 'cur' : ''}" data-c="${i + 1}" title="insert after it">${esc(ptName(p))}<b data-del="${i}" title="remove">×</b></span>`));
    el.querySelector('.wb').innerHTML = `<div class="rcs">${chips.join('')}</div>
      <div class="c-dim" style="font-size:11px;margin:6px 0">Click on the map to insert a point after the highlighted one: a fix (click its symbol or its name) or any position. Click a point to move the insertion mark.</div>
      <div class="row" style="display:flex;gap:4px"><input class="flt" data-add placeholder="type a point and Enter" style="flex:1;margin:0"><button data-apply class="on">${esc(RE.opts.apply || 'Apply')}</button><button data-cancel>Cancel</button></div>
      ${RE.opts.onReset ? '<div style="margin-top:6px"><button data-reset>Back to the filed route</button></div>' : ''}<div data-msg class="c-los" style="font-size:11px;min-height:14px;margin-top:4px"></div>`;
    el.querySelectorAll('[data-c]').forEach(c => c.onclick = ev => { if (ev.target.dataset.del != null) { RE.pts.splice(+ev.target.dataset.del, 1); RE.cur = Math.min(RE.cur, RE.pts.length); } else RE.cur = +c.dataset.c; renderRE(); });
    const inp = el.querySelector('[data-add]');
    inp.onkeydown = ev => { ev.stopPropagation(); if (ev.key === 'Escape') closeRE();
      if (ev.key === 'Enter') { const v = inp.value.trim().toUpperCase(); if (!v) return;
        for (const n of v.split(/[\s,]+/)) { if (!(sector.waypoints[n])) { el.querySelector('[data-msg]').textContent = 'unknown point ' + n; return; } RE.pts.splice(RE.cur, 0, { n }); RE.cur++; }
        renderRE(); setTimeout(() => { const i2 = document.querySelector('#reWin [data-add]'); if (i2) i2.focus(); }, 0); } };
    el.querySelector('[data-apply]').onclick = async () => {
      if (!RE.pts.length) { el.querySelector('[data-msg]').textContent = 'the route is empty'; return; }
      const pts = RE.pts.map(p => p.n || [+p.x.toFixed(3), +p.y.toFixed(3)]);
      try { await RE.opts.onApply(pts); closeRE(); } catch (er) { el.querySelector('[data-msg]').textContent = er.message; } };
    el.querySelector('[data-cancel]').onclick = closeRE;
    const rs = el.querySelector('[data-reset]'); if (rs) rs.onclick = async () => { try { await RE.opts.onReset(); closeRE(); } catch (er) {} };
  }
  window.routeEditClick = function (e) {
    if (!RE) return;
    const [x, y] = canvasPt(e), dpr = devicePixelRatio;
    let pt = null;
    const nr = nameRects.find(r => x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1);
    if (nr) pt = { n: nr.n };
    else {
      const [wx, wy] = fromPx(x, y); let bd = 8 * dpr / view.s;
      for (const [n, p] of Object.entries(sector.waypoints)) { if (COPS().has(n) || n.startsWith('@')) continue; const d = Math.hypot(p[0] - wx, p[1] - wy); if (d < bd) { bd = d; pt = { n }; } }
      if (!pt) pt = { x: wx, y: wy };
    }
    RE.pts.splice(RE.cur, 0, pt); RE.cur++; renderRE();
  };
  window.drawRouteEdit = function (byCs) {
    if (!RE) return;
    const a = byCs[RE.cs]; if (!a) return;
    const dpr = devicePixelRatio, xy = p => p.n ? sector.waypoints[p.n] : [p.x, p.y];
    const pts = [[a.x_nm, a.y_nm]].concat(RE.pts.map(xy).filter(Boolean));
    ctx.strokeStyle = TH.routeShow; ctx.lineWidth = 2.2 * dpr; ctx.setLineDash([7 * dpr, 4 * dpr]); ctx.beginPath();
    pts.forEach((p, k) => { const [px, py] = toPx(p[0], p[1]); k ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }); ctx.stroke(); ctx.setLineDash([]);
    ctx.font = `bold ${(TH.nameFont + 0.5) * dpr}px ui-monospace,monospace`;
    RE.pts.forEach((p, i) => { const q = xy(p); if (!q) return; const [px, py] = toPx(q[0], q[1]);
      ctx.fillStyle = TH.routeShow;
      if (p.n) { ctx.beginPath(); ctx.arc(px, py, 4 * dpr, 0, Math.PI * 2); ctx.fill(); }
      else { ctx.strokeStyle = TH.routeShow; ctx.beginPath(); ctx.moveTo(px - 5 * dpr, py - 5 * dpr); ctx.lineTo(px + 5 * dpr, py + 5 * dpr); ctx.moveTo(px + 5 * dpr, py - 5 * dpr); ctx.lineTo(px - 5 * dpr, py + 5 * dpr); ctx.stroke(); }
      ctx.fillText(`${i + 1}${RE.cur === i + 1 ? '▸' : ''} ${p.n || ''}`, px + 7 * dpr, py - 7 * dpr); });
    if (mouseCv) { const [mx, my] = mouseCv, pv = RE.cur === 0 ? pts[0] : xy(RE.pts[RE.cur - 1]);   // where a click would go
      if (pv) { const [px, py] = toPx(pv[0], pv[1]); ctx.strokeStyle = TH.routeShow + '88'; ctx.lineWidth = 1 * dpr; ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(mx, my); ctx.stroke(); } }
  };

  // ------------------------------------------------------------------ help
  const KEYS = [
    ['click label', 'callsign: assume / decontrol / flight plan…; type: its performance; a value (level, XFL, speed, heading, route, WX): its menu'], ['long press', 'on a value: the full label menu; on the route: the route'],
    ['Tab / Shift+Tab', 'next / previous aircraft (calling first)'],
    ['C or L', 'cleared level of the selected aircraft'], ['X', 'exit level (XFL)'], ['H', 'heading'], ['S', 'speed'], ['D', 'direct / route'], ['W', 'weather deviation (WX tag)'], ['N', 'note (full menu)'],
    ['E', 'edit its route (re-routing)'], ['A', 'assume the selected aircraft'], ['U', 'decontrol it'], ['F', 'its flight plan'], ['I', 'track info window'],
    ['R', 'show its route for 8 s'], ['B', 'range/bearing line from the aircraft or point under the mouse; it follows the mouse, click (or B) to place it'],
    ['right-drag', 'range/bearing line between two aircraft or points'], ['Delete', 'remove the last RBL (Shift+Delete: all)'],
    ['G', 'flight plan list'], ['M', 'MTCD on / off'], ['O', 'OLDI messages'], ['+ / −', 'zoom'], ['arrows', 'move the picture'], ['0', 'reset the view'],
    ['Enter', 'command line: type a clearance for the selected aircraft (CPDLC, or Shift+Enter to enter a voice clearance)'], ['J', 'CPDLC window'], ['T', 'coordination chat with the other sectors and units'],
    ['Space (hold)', 'transmit on the frequency'], ['Esc', 'close the menu / windows, cancel'], ['?', 'this help'],
  ];
  function openHelp() {
    const el = win('helpWin', 'Keyboard shortcuts', 120, 60, 470);
    el.querySelector('.wb').innerHTML = `<div class="keys">${KEYS.map(([k, t]) => `<span><kbd>${esc(k)}</kbd></span><span>${esc(t)}</span>`).join('')}</div>
      <div class="c-dim" style="margin-top:8px;font-size:11px">Letters work in capitals too. The track info window (I) shows the aircraft under the mouse: SSR code, registration, flight plan, levels and coordination, and the Mode S downlinked parameters when it has enhanced surveillance; middle-click an aircraft to keep it there.</div>
      <div style="margin-top:8px;font-size:12px"><a href="/help/${SEAT === 'pilot' ? 'pilot' : SEAT === 'atc' ? 'controller' : 'instructor'}" target="_blank">Full manual ↗</a></div>`;
  }

  // ------------------------------------------------------------------ toolbar
  document.getElementById('flBtn').onclick = () => isOpen('flWin') ? closeWin('flWin') : openFlightList();
  document.getElementById('mtBtn').onclick = () => setMtcd(!mtcdOn);
  document.getElementById('infoBtn').onclick = toggleInfo;
  document.getElementById('olBtn').onclick = toggleOldi;
  document.getElementById('helpBtn').onclick = () => isOpen('helpWin') ? closeWin('helpWin') : openHelp();
  setInterval(() => {
    const el = document.getElementById('utcClock');
    if (!state || !state.utc) { el.textContent = ''; return; }
    el.textContent = `${state.utc}:${String(Math.floor(state.sim_time_s) % 60).padStart(2, '0')} UTC`;
  }, 500);

  // ------------------------------------------------------------------ keyboard shortcuts
  const typing = () => { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') && a.type !== 'checkbox'; };
  function cycle(dir) {
    const rank = a => a.on_frequency && !a.assumed ? 0 : a.assumed ? 1 : 2;
    const list = allAc().filter(a => !(a.status !== 'active' && !a.assumed)).sort((a, b) => rank(a) - rank(b) || a.callsign.localeCompare(b.callsign));
    if (!list.length) return;
    const i = list.findIndex(a => a.callsign === selected);
    selected = list[(i + dir + list.length) % list.length].callsign;
    if (typeof pollSelected === 'function') pollSelected();
  }
  function focusDeck(k) { const el = document.querySelector(`#deck [data-i=${k}]`); if (el) { el.focus(); el.select && el.select(); return true; } return false; }
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing()) return;
    const k = e.key, cs = selected, atc = SEAT === 'atc' && EXID;
    const low = k.length === 1 ? k.toLowerCase() : k;           // letters: capitals or not, Caps Lock or not
    if (k === 'Escape') { if (RE) { closeRE(); return; } for (const id of ['helpWin', 'tyWin', 'olWin', 'mtWin', 'dlWin', 'chatWin', 'flWin', 'fpWin']) if (isOpen(id)) { closeWin(id); return; } selected = null; return; }
    if (k === '?') { isOpen('helpWin') ? closeWin('helpWin') : openHelp(); e.preventDefault(); return; }
    if (k === 'Tab') { e.preventDefault(); cycle(e.shiftKey ? -1 : 1); return; }
    if (k === '+' || k === '=') { setZoom(userZoom * 1.25); return; }
    if (k === '-' || k === '_') { setZoom(userZoom / 1.25); return; }
    if (k === '0') { fitView(); setZoom(1); return; }
    if (k.startsWith('Arrow')) { e.preventDefault(); const st = 60 * devicePixelRatio / view.s;
      if (k === 'ArrowLeft') view.cx -= st; if (k === 'ArrowRight') view.cx += st; if (k === 'ArrowUp') view.cy += st; if (k === 'ArrowDown') view.cy -= st; return; }
    if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); if (e.shiftKey) rbls.length = 0; else rbls.pop(); return; }
    if (low === 'g' && SEAT !== 'pilot') { isOpen('flWin') ? closeWin('flWin') : openFlightList(); return; }
    if (low === 'm' && SEAT !== 'pilot') { setMtcd(!mtcdOn); return; }
    if (low === 'o') { toggleOldi(); return; }
    if (low === 'i') { toggleInfo(); return; }
    if (low === 'b') { rblFromKey(); return; }          // RBL from the aircraft / point under the mouse
    if (!cs) return;
    const field = { c: 'cfl', l: 'cfl', x: 'xfl', h: 'hdg', s: 'spd', d: 'route', w: 'wx', n: 'note' }[low];
    if (field) {
      e.preventDefault();
      if (atc && field === 'note' && window.openLabelMenu) openLabelMenu(cs, 'note');
      else if (atc && window.openFieldMenuAt) openFieldMenuAt(cs, field);
      else focusDeck({ cfl: 'fl', hdg: 'hdg', spd: 'mach', route: 'dct' }[field] || 'fl');
      return;
    }
    if (low === 'a' && SEAT !== 'pilot') { assume(cs); return; }
    if (low === 'u' && SEAT !== 'pilot') { release(cs); return; }
    if (low === 'f') { openFpl(cs); return; }
    if (low === 'e') { if (window.editRoute) editRoute(cs); return; }
    if (low === 'r') { routeShow = routeShow && routeShow.cs === cs ? null : { cs, until: performance.now() + 8000 }; return; }
  });
})();

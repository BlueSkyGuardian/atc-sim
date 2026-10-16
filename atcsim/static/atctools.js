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
  function closeWin(id) { const el = document.getElementById(id); if (el) el.remove(); if (id === 'fpWin') fpCs = null; if (id === 'mtWin') mtFocus = null; }
  const isOpen = id => !!document.getElementById(id);

  // ------------------------------------------------------------------ flight plan window
  let fpCs = null;
  async function openFpl(cs) {
    if (!SID || !cs) return;
    fpCs = cs;
    let d; try { d = await api(`/sessions/${SID}/flightplans/${cs}`); } catch (e) { return; }
    if (fpCs !== cs) return;
    const el = win('fpWin', `<b>${esc(d.callsign)}</b> <span class="st ${d.status.split(',')[0].replace(' ', '')}">${esc(d.status)}</span> <span class="c-dim" style="font-weight:400">flight plan · ${esc(d.utc)} UTC</span>`, 60, 60, 520);
    const c = d.correlation, co = d.coordination, ent = d.entries || {};
    const fp = d.flight_plan || {};
    el.querySelector('.wb').innerHTML = `
      <div style="font-size:12px;margin-bottom:6px">${c.track ? '✓ <b>correlated</b> with the radar track' : '<span class="c-dim">no track yet (planned)</span>'} · SSR <b>A${esc(c.ssr)}</b> · Mode S <b>${esc(c.mode_s_address)}</b> · ${esc(c.surveillance)}${fp.registration ? ' · REG <b>' + esc(fp.registration) + '</b>' : ''}${fp.telephony ? ' · <span class="c-dim">telephony</span> ' + esc(fp.telephony) : ''}</div>
      <pre>${esc(d.message)}</pre>
      <div class="sub">Coordination</div>
      <table><tr><th></th><th>Point</th><th>ETO</th><th>Level</th><th>Unit</th></tr>
        <tr><td class="c-dim">Entry</td><td>${esc(co.entry.point)}</td><td>${esc(co.entry.eto)}</td><td>F${String(co.entry.level).padStart(3, '0')}</td><td>from ${esc(co.entry.from)}</td></tr>
        <tr><td class="c-dim">Exit</td><td>${esc(co.exit.point)}</td><td>${esc(co.exit.eto || '—')}</td><td>F${String(co.exit.level).padStart(3, '0')}${ent.cfl != null ? ' <span class="c-dim">(CFL)</span>' : ''}</td><td>to ${esc(co.exit.to)}</td></tr></table>
      <div class="sub">Estimates (points ahead)</div>
      <table><tr><th>Point</th><th>ETO</th><th>In</th><th>Dist</th><th></th></tr>${d.etos.map(e => `<tr><td>${esc(e.point)}</td><td>${esc(e.eto)}</td><td>${Math.max(0, Math.round(e.in_s / 60))}'</td><td>${e.dist_nm} NM</td><td class="c-dim">${esc(e.role)}</td></tr>`).join('') || '<tr><td colspan="5" class="c-dim">no point ahead</td></tr>'}</table>
      <div class="sub">Items</div>
      <div class="kv">${d.items.map(([n, k, v]) => `<span>${n}</span><span>${esc(k)}</span><span>${esc(v)}</span>`).join('')}</div>`;
  }
  window.openFpl = openFpl;
  setInterval(() => { if (fpCs && isOpen('fpWin')) openFpl(fpCs); }, 6000);

  // ------------------------------------------------------------------ flight list
  let flFilter = '';
  async function openFlightList() {
    if (!SID) return;
    let d; try { d = await api(`/sessions/${SID}/flightplans`); } catch (e) { return; }
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
  setInterval(() => { if (isOpen('flWin')) openFlightList(); }, 8000);

  // ------------------------------------------------------------------ MTCD list and display
  let mtFocus = null;                     // 'A|B' pair shown on the scope
  function renderMtcd() {
    const list = (state && state.mtcd) || [];
    const b = document.getElementById('mtBtn');
    if (b) { b.textContent = list.length ? `MTCD ${list.length}` : 'MTCD'; b.classList.toggle('hit', list.length > 0); }
    if (!isOpen('mtWin')) return;
    const el = win('mtWin', `MTCD <span class="c-dim" style="font-weight:400">conflicts in the next 20 min · system trajectories</span>`, 60, 50, 380);
    const body = el.querySelector('.wb');
    const html = list.length ? list.map(c => { const k = c.aircraft.join('|');
      return `<div class="mt ${mtFocus === k ? 'on' : ''}" data-k="${k}"><b>${c.aircraft.join(' × ')}</b> in ${Math.floor(c.time_to_conflict_s / 60)}' · min ${c.min_horizontal_nm} NM / ${c.vertical_ft} ft
        <small>FL${c.levels[0]} / FL${c.levels[1]} at the start · ${Math.round(c.duration_s / 60) || '<1'} min long · min ${c.required_horizontal_nm} NM / ${c.required_vertical_ft} ft${c.current ? ' · <b>now</b>' : ''}</small></div>`; }).join('')
      : '<div class="empty">No medium-term conflict between the system trajectories (cleared levels as entered, routes, headings and directs entered).</div>';
    if (body.dataset.h !== html) { body.innerHTML = html; body.dataset.h = html;
      body.querySelectorAll('[data-k]').forEach(d => d.onclick = () => { mtFocus = mtFocus === d.dataset.k ? null : d.dataset.k; selected = mtFocus ? d.dataset.k.split('|')[0] : selected; body.dataset.h = ''; renderMtcd(); }); }
  }
  setInterval(renderMtcd, 1000);
  // on the scope: the focused pair (or the selected aircraft's conflicts): where each will be when the conflict starts
  window.drawMtcd = function (byCs) {
    if (!state || !state.mtcd || SEAT === 'pilot' || replayMode) return;
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
      const mx = (ax + bx) / 2 + 8 * dpr, my = (ay + by) / 2 - 8 * dpr, w = ctx.measureText(t).width;
      ctx.fillStyle = '#0b1016cc'; ctx.fillRect(mx - 3 * dpr, my - 11 * dpr, w + 6 * dpr, 15 * dpr);
      ctx.fillStyle = TH.mtcd; ctx.fillText(t, mx, my);
    }
  };

  // ------------------------------------------------------------------ help
  const KEYS = [
    ['click', 'select an aircraft (controller: opens its label input)'], ['Tab / Shift+Tab', 'next / previous aircraft (calling first)'],
    ['C or L', 'cleared level of the selected aircraft'], ['H', 'heading'], ['S', 'speed'], ['D', 'direct'], ['W', 'weather deviation (WX tag)'], ['N', 'note'],
    ['A', 'assume the selected aircraft'], ['X', 'decontrol it'], ['F', 'its flight plan'], ['E', 'pin its extended label (Shift+E: all)'],
    ['R', 'show its route for 8 s (or long-press the label)'], ['B', 'range/bearing line from it (then click the other end)'],
    ['right-drag', 'range/bearing line between two aircraft or points'], ['Delete', 'remove the last RBL (Shift+Delete: all)'],
    ['G', 'flight plan list'], ['M', 'MTCD list'], ['+ / −', 'zoom'], ['arrows', 'move the picture'], ['0', 'reset the view'],
    ['Space (hold)', 'transmit on the frequency'], ['Esc', 'close the menu / windows, cancel'], ['?', 'this help'],
  ];
  function openHelp() {
    const el = win('helpWin', 'Keyboard shortcuts', 120, 60, 470);
    el.querySelector('.wb').innerHTML = `<div class="keys">${KEYS.map(([k, t]) => `<span><kbd>${esc(k)}</kbd></span><span>${esc(t)}</span>`).join('')}</div>
      <div class="c-dim" style="margin-top:8px;font-size:11px">Hover a label (or middle-click it) for the extended label: SSR code, registration, flight-plan data and the Mode S downlinked parameters when the aircraft has enhanced surveillance.</div>`;
  }

  // ------------------------------------------------------------------ toolbar
  document.getElementById('flBtn').onclick = () => isOpen('flWin') ? closeWin('flWin') : openFlightList();
  document.getElementById('mtBtn').onclick = () => { if (isOpen('mtWin')) closeWin('mtWin'); else { win('mtWin', 'MTCD', 60, 50, 380); renderMtcd(); } };
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
    const field = { c: 'cfl', l: 'cfl', h: 'heading', s: 'speed', d: 'direct', w: 'wx', n: 'note' }[k.toLowerCase()];
    if (k === 'Escape') { for (const id of ['helpWin', 'mtWin', 'flWin', 'fpWin']) if (isOpen(id)) { closeWin(id); return; } selected = null; return; }
    if (k === '?') { isOpen('helpWin') ? closeWin('helpWin') : openHelp(); e.preventDefault(); return; }
    if (k === 'Tab') { e.preventDefault(); cycle(e.shiftKey ? -1 : 1); return; }
    if (k === '+' || k === '=') { setZoom(userZoom * 1.25); return; }
    if (k === '-' || k === '_') { setZoom(userZoom / 1.25); return; }
    if (k === '0') { fitView(); setZoom(1); return; }
    if (k.startsWith('Arrow')) { e.preventDefault(); const st = 60 * devicePixelRatio / view.s;
      if (k === 'ArrowLeft') view.cx -= st; if (k === 'ArrowRight') view.cx += st; if (k === 'ArrowUp') view.cy += st; if (k === 'ArrowDown') view.cy -= st; return; }
    if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); if (e.shiftKey) rbls.length = 0; else rbls.pop(); return; }
    const low = k.toLowerCase();
    if (low === 'g' && SEAT !== 'pilot') { isOpen('flWin') ? closeWin('flWin') : openFlightList(); return; }
    if (low === 'm' && SEAT !== 'pilot') { document.getElementById('mtBtn').click(); return; }
    if (low === 'e' && e.shiftKey) { extAll = !extAll; return; }
    if (!cs) return;
    if (field) {
      e.preventDefault();
      if (atc && window.openLabelMenu) openLabelMenu(cs, field);
      else focusDeck({ cfl: 'fl', heading: 'hdg', speed: 'mach', direct: 'dct' }[field] || 'fl');
      return;
    }
    if (low === 'a' && SEAT !== 'pilot') { assume(cs); return; }
    if (low === 'x' && SEAT !== 'pilot') { release(cs); return; }
    if (low === 'f') { openFpl(cs); return; }
    if (low === 'e') { extPinned.has(cs) ? extPinned.delete(cs) : extPinned.add(cs); return; }
    if (low === 'r') { routeShow = routeShow && routeShow.cs === cs ? null : { cs, until: performance.now() + 8000 }; return; }
    if (low === 'b') { setRblMode(true); rblPick(cs); return; }
  });
})();

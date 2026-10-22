// Solo radar (and sandbox): the controller's sequencing tool. "Sequence" on, click two or more aircraft, then the
// point where they must be in trail: the server sequences them first come, first served (POST /sequence) and the
// scope shows, for each follower, its sequencing ellipse - the turn points that put it exactly the spacing behind the
// one ahead when it crosses the point, wind included - with the headings that can be given on it (dots, labelled
// every 10 degrees), the one the plan takes drawn as heading leg + direct. Click a heading in the panel to see it and
// Apply it ("heading H for N NM, then direct P"); the plan's clearances (speed first, then the fan) can be applied as
// they are. Uses the radar page's globals (state, SID, EXID, EXINFO, DBID, AIW, api, msg, ctx, toPx, sector, ...).
(function () {
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const btn = $('#seqBtn'), box = $('#seqPanel');
  if (!btn || !box) return;
  const allowed = () => !DBID && !AIW && SID && (!EXID || (EXINFO && EXINFO.solo));
  const COLORS = ['#7fd18b', '#5ad1e6', '#ffb86b', '#e6b3ff', '#ffe08a', '#ff8fa3', '#9fd0ff', '#c9f29a'];
  let on = false, sel = [], point = null, data = null, err = '', busy = false, lastKey = '';
  const pick = {};                         // callsign -> heading shown (from the panel)
  const mmss = s => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.round(s) % 60).padStart(2, '0')}`;
  const colorOf = cs => { const i = (data && data.sequence || sel).indexOf(cs); return COLORS[(i < 0 ? sel.indexOf(cs) : i) % COLORS.length]; };

  function setOn(v) {
    on = v; btn.classList.toggle('on', on); box.hidden = !on;
    if (!on) { data = null; err = ''; }
    render();
  }
  btn.onclick = () => setOn(!on);
  window.seqToolActive = () => on && allowed();
  window.addEventListener('keydown', e => { if (on && e.key === 'Escape' && !e.target.closest('input,textarea')) setOn(false); });

  // ---------------------------------------------------------------- picking on the scope
  window.seqToolClick = function (e) {
    const cs = nearestAircraft(e);
    if (cs) {
      const i = sel.indexOf(cs);
      if (i >= 0) sel.splice(i, 1); else sel.push(cs);
      delete pick[cs]; compute(); return;
    }
    const [qx, qy] = canvasPt(e), dpr = devicePixelRatio;
    let best = null, bd = 16 * dpr;
    for (const [n, p] of Object.entries((sector && sector.waypoints) || {})) {
      if (n.startsWith('@')) continue;
      const [px, py] = toPx(p[0], p[1]), d = Math.hypot(px - qx, py - qy);
      if (d < bd) { bd = d; best = n; }
    }
    if (best) { point = best; compute(); }
  };

  async function compute(force) {
    if (!on || !allowed()) return render();
    if (sel.length < 2 || !point) { data = null; err = ''; return render(); }
    const key = `${sel.join(',')}|${point}|${state ? Math.floor(state.sim_time_s / 10) : 0}`;
    if (!force && key === lastKey && data) return render();
    if (busy) return;
    busy = true; render();
    try { data = await api(`/sessions/${SID}/sequence`, { method: 'POST', body: JSON.stringify({ callsigns: sel, point }) }); err = ''; lastKey = key; }
    catch (er) { err = er.message; data = null; }
    busy = false; render();
  }
  setInterval(() => { if (on && sel.length >= 2 && point) compute(); }, 2000);    // follows the traffic (every 10 s)

  // ---------------------------------------------------------------- panel
  let lastH = '';
  function render() {
    if (!on) { lastH = ''; return; }
    const now = state ? state.sim_time_s : 0;
    const chips = sel.map(cs => `<span class="sq-ac" style="background:${colorOf(cs)}">${esc(cs)}</span>`).join('') || '<span class="c-dim">none</span>';
    let body = `<div><span class="c-dim">aircraft</span> ${chips} <span class="c-dim">at</span> <b>${esc(point || '—')}</b></div>`;
    if (!allowed()) body += '<div class="c-dim">The sequencing tool is on the solo radar and in the sandbox.</div>';
    else if (sel.length < 2 || !point) body += `<div class="sq-hint">Click ${sel.length < 2 ? 'two or more aircraft' : ''}${sel.length < 2 && !point ? ', then ' : ''}${!point ? 'the point where they must be in trail' : ''} on the scope (click an aircraft again to remove it, Esc to leave).</div>`;
    else if (err) body += `<div class="warn">${esc(err)}</div>`;
    else if (!data) body += '<div class="c-dim">computing…</div>';
    else {
      const d = data;
      body += `<div>${d.ok ? '✓' : '<span class="warn">✗</span>'} in trail at <b>${esc(d.point)}</b> by ${d.spacing_nm} NM: ${d.sequence.map(c => `<b style="color:${colorOf(c)}">${esc(c)}</b>`).join(' → ')}
        ${d.closest_nm != null ? `<span class="c-dim">· closest ${d.closest_nm} NM</span>` : ''}</div>`;
      if (d.why) body += `<div class="warn">${esc(d.why)}</div>`;
      if ((d.not_via_point || []).length) body += `<div class="warn">${d.not_via_point.map(esc).join(', ')}: ${esc(d.point)} is not on the route ahead</div>`;
      body += (d.spacing || []).map(x => `<div class="c-dim">${esc(x.behind)} ${x.spacing_nm == null ? '—' : x.spacing_nm + ' NM'} behind ${esc(x.ahead)}${x.where ? ' at ' + esc(x.where) : ''}</div>`).join('');
      body += d.steps.map((s, i) => `<div class="sq-step ${s.at_s > now + 1 ? 'later' : ''}"><span>${s.at_s > now + 1 ? `<span class="c-dim">at T+${mmss(s.at_s)}</span> ` : ''}${esc(s.why)}</span>
        <button data-step="${i}">Apply</button></div>`).join('');
      if (!d.steps.length) body += '<div class="c-dim">No clearance needed: ' + esc((d.notes || []).join('; ') || 'already in trail') + '</div>';
      for (const cs of d.sequence) {
        const a = d.aircraft[cs]; if (!a || !a.headings) continue;
        const all = a.headings.filter(h => h.leg_nm != null), hs = all.filter(h => h.ok);
        const outN = all.length - hs.length;
        const pk = pick[cs];
        body += `<div><b style="color:${colorOf(cs)}">${esc(cs)}</b> <span class="c-dim">behind ${esc(a.ahead)} · its ellipse: headings that give the spacing (time on heading)</span>
          <div class="sq-hdgs">${hs.length ? '' : '<span class="warn">no heading within the rules</span>'}${hs.map(h => `<span class="sq-h ${h.ok ? '' : 'no'} ${pk === h.heading ? 'pick' : ''} ${a.chosen_heading === h.heading ? 'chosen' : ''}"
            ${h.ok ? `data-pick="${esc(cs)}:${h.heading}"` : ''} title="${h.ok ? `${h.side}${h.turn}°, ${h.leg_nm} NM, ${mmss(h.time_s)}` : 'not allowed: ' + esc({ direction: 'off its direction of flight', 'too long': 'more than 15 minutes on the heading', turn: 'turn too small or too large', fan: 'not on the side / not more than the one ahead', ellipse: 'cannot give the spacing' }[h.why_not] || h.why_not)}">${String(h.heading).padStart(3, '0')}<span class="c-dim"> ${Math.round(h.time_s / 60)}'</span></span>`).join('')}${outN ? `<span class="c-dim" title="on the ellipse but off the direction of flight, over 15 minutes, too small or too large a turn">+${outN} outside the rules</span>` : ''}</div>`;
        if (pk != null) {
          const h = hs.find(x => x.heading === pk);
          if (h) body += `<div class="sq-step"><span>${esc(cs)} heading ${String(h.heading).padStart(3, '0')} for ${Math.round(h.leg_nm)} NM, then direct ${esc(d.point)} <span class="c-dim">(geometry: not checked against the traffic)</span></span><button data-hdg="${esc(cs)}:${h.heading}">Apply</button></div>`;
        }
        body += '</div>';
      }
    }
    const html = `<div class="hd"><span>SEQUENCE ${data ? '<span class="c-dim">T+' + mmss(data.sim_time_s) + '</span>' : ''}${busy ? ' …' : ''}</span>
      <span><button data-act="re" title="compute again now">↻</button> <button data-act="clr" title="clear the aircraft and the point">Clear</button> <button data-act="off" title="leave the tool (Esc)">✕</button></span></div>${body}`;
    if (html !== lastH) { box.innerHTML = html; lastH = html; }
  }

  async function enter(a) {
    const { type, limit, ...f } = a;
    if (f.heading != null) { f.direct = ''; if (limit) f.hlim = limit; }
    if (f.speed != null && limit) f.slim = limit;
    const rr = await api(`/sessions/${SID}/entries`, { method: 'POST', body: JSON.stringify({ ...f, ...(window.FORCE_CLEARANCE ? { force: true } : {}) }) });
    if (window.noteRefused) window.noteRefused(f.callsign, 'entries', f, rr && rr._readback);
    const bad = (rr && rr._readback || []).find(r => r.status === 'rejected');
    if (bad) throw new Error(bad.text);
  }
  box.addEventListener('mousedown', async e => {
    const b = e.target.closest('[data-act]');
    if (b) { e.preventDefault(); const k = b.dataset.act;
      if (k === 'off') setOn(false); else if (k === 'clr') { sel = []; point = null; data = null; for (const c in pick) delete pick[c]; render(); } else compute(true);
      return; }
    const pk = e.target.closest('[data-pick]');
    if (pk) { e.preventDefault(); const [cs, h] = pk.dataset.pick.split(':'); pick[cs] = pick[cs] === +h ? undefined : +h; if (pick[cs] === undefined) delete pick[cs]; lastH = ''; render(); return; }
    const st = e.target.closest('[data-step]');
    if (st && data) {
      e.preventDefault();
      const s = data.steps[+st.dataset.step]; if (!s) return;
      try { await enter(s.action); msg(`✓ ${s.why}`); setTimeout(() => compute(true), 800); } catch (er) { msg(er.message, true); }
      return;
    }
    const hd = e.target.closest('[data-hdg]');
    if (hd && data) {
      e.preventDefault();
      const [cs, h] = hd.dataset.hdg.split(':'), a = data.aircraft[cs], x = a && a.headings.find(q => q.heading === +h);
      if (!x) return;
      try { await enter({ callsign: cs, heading: +h, limit: `${Math.max(1, Math.round(x.leg_nm))}NM`, then: data.point });
        msg(`✓ ${cs} heading ${h} for ${Math.round(x.leg_nm)} NM, then direct ${data.point}`); delete pick[cs]; setTimeout(() => compute(true), 800); }
      catch (er) { msg(er.message, true); }
    }
  });

  // ---------------------------------------------------------------- drawing on the scope
  window.drawSeqTool = function (byCs) {
    if (!on || !allowed()) return;
    const dpr = devicePixelRatio;
    ctx.save();
    ctx.font = `${10 * dpr}px ui-monospace,monospace`;
    for (const cs of sel) {                                    // the aircraft chosen: a ring in its colour
      const a = byCs[cs]; if (!a) continue;
      const [x, y] = toPx(a.x_nm, a.y_nm);
      ctx.strokeStyle = colorOf(cs); ctx.lineWidth = 2 * dpr;
      ctx.beginPath(); ctx.arc(x, y, 11 * dpr, 0, Math.PI * 2); ctx.stroke();
      const k = data && data.sequence ? data.sequence.indexOf(cs) : -1;
      if (k >= 0) { ctx.fillStyle = colorOf(cs); ctx.fillText(String(k + 1), x - 3 * dpr, y - 14 * dpr); }
    }
    const wp = sector && sector.waypoints;
    if (point && wp && wp[point]) {                           // the sequencing point: a diamond
      const [x, y] = toPx(...wp[point]), r = 8 * dpr;
      ctx.strokeStyle = '#5ad1e6'; ctx.lineWidth = 2 * dpr;
      ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath(); ctx.stroke();
    }
    if (data && data.aircraft) {
      const T = data.point_xy;
      for (const cs of data.sequence) {
        const a = data.aircraft[cs]; if (!a) continue;
        const col = colorOf(cs);
        if (a.path && a.path.length > 1) {                    // as planned (thin): to the point
          ctx.strokeStyle = col + '66'; ctx.lineWidth = 1 * dpr; ctx.setLineDash([]);
          ctx.beginPath(); a.path.forEach((p, i) => { const [x, y] = toPx(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke();
        }
        if (!a.ellipse) continue;
        ctx.strokeStyle = col; ctx.lineWidth = 1.2 * dpr; ctx.setLineDash([5 * dpr, 4 * dpr]);   // the ellipse
        ctx.beginPath(); let pen = false;
        a.ellipse.forEach(p => { if (!p) { pen = false; return; } const [x, y] = toPx(p[0], p[1]); if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y); pen = true; });
        ctx.stroke(); ctx.setLineDash([]);
        const [ax, ay] = toPx(a.x, a.y);
        for (const h of a.headings || []) {                   // the headings that can be given: dots at their turn points
          if (!h.ok || !h.turn_point) continue;
          const [x, y] = toPx(...h.turn_point);
          ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, 2.2 * dpr, 0, Math.PI * 2); ctx.fill();
          if (h.heading % 10 === 0) { ctx.fillStyle = col + 'cc'; ctx.fillText(String(h.heading).padStart(3, '0'), x + 4 * dpr, y - 3 * dpr); }
        }
        const show = pick[cs] != null ? pick[cs] : a.chosen_heading;   // the heading shown: picked, else the plan's
        const h = show != null && (a.headings || []).find(q => q.heading === show && q.turn_point);
        if (h) {
          const [mx, my] = toPx(...h.turn_point), [tx, ty] = toPx(T[0], T[1]);
          ctx.strokeStyle = col; ctx.lineWidth = (pick[cs] != null ? 2.4 : 1.8) * dpr;
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(mx, my); ctx.lineTo(tx, ty); ctx.stroke();
          ctx.fillStyle = col;
          ctx.fillText(`${String(h.heading).padStart(3, '0')} ${Math.round(h.leg_nm)}NM ${Math.round(h.time_s / 60)}'`, (ax + mx) / 2 + 6 * dpr, (ay + my) / 2);
        }
      }
    }
    ctx.restore();
  };
  window.addEventListener('sim-rewind', () => { data = null; lastKey = ''; });
})();

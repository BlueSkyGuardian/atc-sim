// Solo radar, sandbox and the controller's seat in an exercise: the controller's sequencing tool. "Sequence" on, click two or more aircraft, then the
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
  // the point can also be typed (a waypoint under a label is hard to click): a field that the panel's refresh leaves alone
  box.innerHTML = `<div class="pwh"><span>Sequencing</span><button data-pclose title="close (Esc)">✕</button></div><div class="sq-pt"><span class="c-dim">point</span> <input id="sqPt" list="sqPts" placeholder="e.g. TERTO" autocomplete="off" spellcheck="false">
    <datalist id="sqPts"></datalist></div><div id="sqInner"></div>`;
  const inner = box.querySelector('#sqInner'), ptIn = box.querySelector('#sqPt');
  const fillPts = () => { const dl = box.querySelector('#sqPts'); if (dl.options.length || !sector) return;
    dl.innerHTML = Object.keys(sector.waypoints || {}).filter(n => !n.startsWith('@')).sort().map(n => `<option value="${n}">`).join(''); };
  ptIn.addEventListener('focus', fillPts);
  ptIn.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') ptIn.dispatchEvent(new Event('change')); });
  ptIn.addEventListener('change', () => { const v = ptIn.value.trim().toUpperCase(); if (sector && (sector.waypoints || {})[v]) { point = v; compute(true); } else if (v) msg(`unknown point ${v}`, true); });
  // the solo radar, the sandbox and the controller's (or instructor's) seat in an exercise; not the pseudo-pilot, a
  // debrief or an AI run being watched
  const allowed = () => !DBID && !AIW && !LIVETR && SID && SEAT !== 'pilot';
  setInterval(() => document.body.classList.toggle('seq-ok', !!allowed()), 1000);
  const COLORS = ['#8fcfa0', '#7fc4dc', '#e2b07e', '#c3a6e0', '#d9cc86', '#e09aa6', '#9db8e0', '#b5d38e'];   // muted
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
  box.querySelector('[data-pclose]').onclick = () => setOn(false);
  if (window.makeMovable) makeMovable(box, box.querySelector('.pwh'));
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
    if (best) { point = best; ptIn.value = best; compute(); }
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
    if (!allowed()) body += '<div class="c-dim">The sequencing tool is for the controller (solo radar, sandbox, the controller\'s seat in an exercise).</div>';
    else if (sel.length < 2 || !point) body += `<div class="sq-hint">Click ${sel.length < 2 ? 'two or more aircraft' : ''}${sel.length < 2 && !point ? ', then ' : ''}${!point ? 'the point where they must be in trail (or type it above)' : ''} on the scope (click an aircraft again to remove it, Esc to leave).</div>`;
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
        body += delayRow(cs, a);
        const all = a.headings.filter(h => h.leg_nm != null), hs = all.filter(h => h.ok);
        const outN = all.length - hs.length;
        const pk = pick[cs];
        const now_ = a.spacing_now_nm != null ? ` · ${a.spacing_now_nm >= 0 ? a.spacing_now_nm + ' NM' : 'ahead of it'} as cleared now` : '';
        if (a.ellipse_none) {
          body += `<div data-cs="${esc(cs)}"><b style="color:${colorOf(cs)}">${esc(cs)}</b> <span class="c-dim">behind ${esc(a.ahead)}${now_}: already far enough behind, no vector needed</span></div>`;
          continue;
        }
        body += `<div data-cs="${esc(cs)}"><b style="color:${colorOf(cs)}">${esc(cs)}</b> <span class="c-dim">behind ${esc(a.ahead)}${now_}${a.direct ? ' · direct ' + esc(d.point) : ''} · headings that give the spacing (time on heading)</span>
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
    if (html !== lastH) { inner.innerHTML = html; lastH = html; }
  }

  // the delay table of a follower: the delay it must lose for its slot, what each family of clearances can absorb for
  // it, the ones that fit (✓), the best of them in bold, the one the plan took marked
  const FAM = { speed: 'speed', dogleg: 'one vector', 'dogleg+speed': 'vector + speed', 'two legs': 'two vectors',
    'two legs+speed': 'two vectors + speed', '3 legs': 'zigzag 3', '4 legs': 'zigzag 4', '5 legs': 'zigzag 5',
    '6 legs': 'zigzag 6', hold: 'hold' };
  const SOL = { speed: 'speed', fan: 'dogleg' };
  function delayRow(cs, a) {
    if (!a.options || !a.options.length || a.delay_s == null) return '';
    const took = SOL[a.solution] || a.solution || null;
    const cells = a.options.map(o => {
      const rng = o.advisory ? `${Math.round(o.lo_s / 60)}' laps` : `${mmss(o.lo_s)}–${mmss(o.hi_s)}`;
      const cls = `sq-fam ${o.fits ? 'fit' : 'no'} ${a.best === o.family ? 'best' : ''} ${took === o.family ? 'took' : ''}`;
      const tip = `${FAM[o.family] || o.family}: absorbs ${rng} · ${o.advisory ? 'advisory (coordinate)' : o.instructions + ' clearance' + (o.instructions > 1 ? 's' : '')}`
        + (o.fits ? ` · fits${o.off_axis_nm != null ? ', ' + o.off_axis_nm + ' NM off the axis' : ''}${o.slack != null ? ', slack ' + Math.round(o.slack * 100) + '%' : ''}` : ' · does not fit');
      return `<span class="${cls}" title="${esc(tip)}">${o.fits ? '✓ ' : ''}${esc(FAM[o.family] || o.family)} <span class="c-dim">${rng}</span></span>`;
    }).join('');
    return `<div class="sq-delay"><span class="c-dim">${esc(cs)} to lose</span> <b>${a.delay_s > 8 ? mmss(a.delay_s) : 'nothing'}</b> ${a.delay_s > 8 ? cells : ''}</div>`;
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
      if (k === 'off') setOn(false); else if (k === 'clr') { sel = []; point = null; ptIn.value = ''; data = null; for (const c in pick) delete pick[c]; render(); } else compute(true);
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
  // Drawing: quiet by default - the whole ellipse a faint hairline, only the arc of the headings that can be given
  // drawn plainly (ticks every 10 degrees, the end headings labelled), the heading the plan takes (or the one picked)
  // as a thin leg + direct with a small label. The follower in focus (picked or under the mouse in the panel) is drawn
  // in full, the others dimmed.
  let focus = null;
  box.addEventListener('mouseover', e => { const r = e.target.closest('[data-cs]'); const f = r ? r.dataset.cs : null; if (f !== focus) focus = f; });
  box.addEventListener('mouseleave', () => { focus = null; });
  const hex2 = v => Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, '0');
  function label(txt, x, y, col, dpr) {
    ctx.lineWidth = 3 * dpr; ctx.strokeStyle = (typeof TH !== 'undefined' && TH.bg) || '#0b1724'; ctx.lineJoin = 'round';
    ctx.strokeText(txt, x, y); ctx.fillStyle = col; ctx.fillText(txt, x, y);
  }
  function runs(hs) {                       // consecutive allowed headings (5 degree steps), across 360 too
    const ok = hs.filter(h => h.ok && h.turn_point).map(h => h.heading % 360).sort((a, b) => a - b);
    const out = []; let cur = [];
    for (const h of ok) { if (cur.length && h - cur[cur.length - 1] > 5) { out.push(cur); cur = []; } cur.push(h); }
    if (cur.length) out.push(cur);
    if (out.length > 1 && out[0][0] === 0 && out[out.length - 1][out[out.length - 1].length - 1] === 355) out[0] = out.pop().concat(out[0]);
    return out;
  }
  window.drawSeqTool = function (byCs) {
    if (!on || !allowed()) return;
    // in focus: the follower under the mouse - its label or symbol on the scope, or its row in the panel - else the
    // one whose heading was picked; it is drawn stronger, the others stay visible, a little dimmed
    const hov = (typeof hoverLbl !== 'undefined' && hoverLbl) || (typeof hoverAc !== 'undefined' && hoverAc) || null;
    const dpr = devicePixelRatio, focusCs = focus || (hov && data && data.aircraft && data.aircraft[hov] ? hov : null)
      || Object.keys(pick).find(c => pick[c] != null) || null;
    ctx.save();
    ctx.font = `${9.5 * dpr}px ui-monospace,monospace`; ctx.textBaseline = 'middle';
    for (const cs of sel) {                                    // the aircraft chosen: a thin ring, its rank
      const a = byCs[cs]; if (!a) continue;
      const [x, y] = toPx(a.x_nm, a.y_nm), col = colorOf(cs);
      ctx.strokeStyle = col + (focusCs === cs ? 'ff' : 'b0'); ctx.lineWidth = (focusCs === cs ? 2 : 1.2) * dpr;
      ctx.beginPath(); ctx.arc(x, y, 9 * dpr, 0, Math.PI * 2); ctx.stroke();
      const k = data && data.sequence ? data.sequence.indexOf(cs) : -1;
      if (k >= 0) label(String(k + 1), x - 12 * dpr, y - 12 * dpr, col, dpr);
    }
    const wp = sector && sector.waypoints;
    if (point && wp && wp[point]) {                           // the sequencing point: a small diamond
      const [x, y] = toPx(...wp[point]), r = 6 * dpr;
      ctx.strokeStyle = '#9fd0e6'; ctx.lineWidth = 1.2 * dpr;
      ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath(); ctx.stroke();
    }
    if (data && data.aircraft) {
      const T = data.point_xy;
      for (const cs of data.sequence) {
        const a = data.aircraft[cs]; if (!a || !a.ellipse) continue;
        const col = colorOf(cs), hot = focusCs === cs, dim = focusCs && !hot ? 0.55 : 1;
        const pt = hd => a.ellipse[Math.round(((hd % 360) + 360) % 360 / 2) % a.ellipse.length];
        // the whole ellipse: a faint hairline
        ctx.strokeStyle = col + hex2((hot ? 0.6 : 0.35) * dim); ctx.lineWidth = (hot ? 1.4 : 1) * dpr;
        ctx.beginPath(); let pen = false;
        a.ellipse.forEach(p => { if (!p) { pen = false; return; } const [x, y] = toPx(p[0], p[1]); if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y); pen = true; });
        ctx.stroke();
        // the arc of the headings that can be given, with its ticks
        for (const r of runs(a.headings || [])) {
          ctx.strokeStyle = col + hex2((hot ? 1 : 0.75) * dim); ctx.lineWidth = (hot ? 2.2 : 1.6) * dpr;
          ctx.beginPath(); pen = false;
          for (let k = 0; k < r.length; k++) {
            const h0 = r[k], h1 = k + 1 < r.length ? r[k + 1] : r[k];
            const span = ((h1 - h0) + 360) % 360;
            for (let d = 0; d <= Math.max(span, 0); d += 1) {
              const p = pt(h0 + d); if (!p) continue;
              const [x, y] = toPx(p[0], p[1]); if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y); pen = true;
            }
          }
          ctx.stroke();
          for (const h of r) {
            if (h % 10) continue;
            const p = pt(h), q = pt(h + 2); if (!p || !q) continue;
            const [x, y] = toPx(p[0], p[1]), [qx, qy] = toPx(q[0], q[1]);
            const L = Math.hypot(qx - x, qy - y) || 1, nx = -(qy - y) / L * 3.5 * dpr, ny = (qx - x) / L * 3.5 * dpr;
            ctx.beginPath(); ctx.moveTo(x - nx, y - ny); ctx.lineTo(x + nx, y + ny); ctx.stroke();
          }
          if (!focusCs || hot) for (const h of [r[0], r[r.length - 1]]) {      // the two ends of the arc labelled
            const p = pt(h); if (!p) continue;
            const [x, y] = toPx(p[0], p[1]);
            label(String(h || 360).padStart(3, '0'), x + 5 * dpr, y, col + 'cc', dpr);
          }
        }
        if (/ legs$/.test(a.solution || '') && pick[cs] == null && a.path && a.path.length > 1) {   // several vectors: the planned path
          ctx.strokeStyle = col + hex2(0.9 * dim); ctx.lineWidth = 1.3 * dpr; ctx.beginPath();
          a.path.forEach((p, i) => { const [x, y] = toPx(p[0], p[1]); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
          ctx.stroke();
          if (!focusCs || hot) { const [ax, ay] = toPx(a.x, a.y);
            const hs = (a.legs && a.legs.length ? a.legs.map(l => l[0]) : [a.chosen_heading, a.chosen_heading2]).filter(h => h != null);
            label(hs.map(h => String(h).padStart(3, '0')).join(' → '), ax + 12 * dpr, ay + 12 * dpr, col, dpr); }
          continue;
        }
        const show = pick[cs] != null ? pick[cs] : a.chosen_heading;   // the heading shown: picked, else the plan's
        const h = show != null && (a.headings || []).find(q => q.heading === show && q.turn_point);
        if (h) {
          const [ax, ay] = toPx(a.x, a.y), [mx, my] = toPx(...h.turn_point), [tx, ty] = toPx(T[0], T[1]);
          ctx.strokeStyle = col + hex2(0.9 * dim); ctx.lineWidth = 1.3 * dpr;
          ctx.setLineDash(pick[cs] != null ? [6 * dpr, 4 * dpr] : []);
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(mx, my); ctx.lineTo(tx, ty); ctx.stroke(); ctx.setLineDash([]);
          ctx.fillStyle = col + hex2(dim); ctx.beginPath(); ctx.arc(mx, my, 2.5 * dpr, 0, Math.PI * 2); ctx.fill();
          if (!focusCs || hot) label(`${String(h.heading).padStart(3, '0')} · ${Math.round(h.leg_nm)} NM · ${Math.round(h.time_s / 60)}'`,
            (ax + mx) / 2 + 8 * dpr, (ay + my) / 2, col, dpr);
        }
      }
    }
    ctx.restore();
  };
  window.addEventListener('sim-rewind', () => { data = null; lastKey = ''; });
})();

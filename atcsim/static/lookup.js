// Lookup ("FIND", Ctrl+F): an aircraft by callsign or SSR code, a point, an aerodrome, an airway, an area, a sector, a
// geographic coordinate or a point / bearing / distance. An aircraft is selected, centred on the scope and ringed for
// a few seconds; everything else is centred and drawn (marker, highlighted airway or area) until removed.
// Uses the radar page's globals (state, sector, arrInfo, view, toPx, ctx, selected, centerOn, atcWin, ...).
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const marks = [];                     // {kind, label, x, y, pts?, area?, t0}
  let hiAc = null;                      // {cs, until}
  const COL = '#ffd84d';

  // ---------------------------------------------------------------- geography
  function lcc() {
    const p = sector && sector.projection; if (!p) return null;
    const R = p.earth_radius_nm || 3440.065, rad = Math.PI / 180, [l1, l2] = p.standard_parallels;
    const t = f => Math.tan(Math.PI / 4 + f * rad / 2);
    const n = Math.abs(l1 - l2) < 1e-9 ? Math.sin(l1 * rad) : Math.log(Math.cos(l1 * rad) / Math.cos(l2 * rad)) / Math.log(t(l2) / t(l1));
    const F = Math.cos(l1 * rad) * Math.pow(t(l1), n) / n, rho0 = R * F / Math.pow(t(p.lat0), n);
    return (lat, lon) => { const rho = R * F / Math.pow(t(lat), n), th = n * (lon - p.lon0) * rad; return [rho * Math.sin(th), rho0 - rho * Math.cos(th)]; };
  }
  function lccInv() {                    // plane (NM) -> lat, lon
    const p = sector && sector.projection; if (!p) return null;
    const R = p.earth_radius_nm || 3440.065, rad = Math.PI / 180, [l1, l2] = p.standard_parallels;
    const t = f => Math.tan(Math.PI / 4 + f * rad / 2);
    const n = Math.abs(l1 - l2) < 1e-9 ? Math.sin(l1 * rad) : Math.log(Math.cos(l1 * rad) / Math.cos(l2 * rad)) / Math.log(t(l2) / t(l1));
    const F = Math.cos(l1 * rad) * Math.pow(t(l1), n) / n, rho0 = R * F / Math.pow(t(p.lat0), n);
    return (x, y) => { const dy = rho0 - y, rho = Math.sign(n) * Math.hypot(x, dy), th = Math.atan2(x, dy);
      return [(2 * Math.atan(Math.pow(R * F / rho, 1 / n)) - Math.PI / 2) / rad, p.lon0 + th / n / rad]; };
  }
  const dmsFine = (v, w, pos, neg) => { const a = Math.abs(v); let d = Math.floor(a), m = Math.floor((a - d) * 60), s = ((a - d) * 60 - m) * 60;
    if (s >= 59.95) { s = 0; m += 1; } if (m >= 60) { m = 0; d += 1; }
    return `${String(d).padStart(w, '0')}°${String(m).padStart(2, '0')}'${s.toFixed(1).padStart(4, '0')}"${v >= 0 ? pos : neg}`; };
  const dmsTxt = (v, w, pos, neg) => { const a = Math.abs(v), d = Math.floor(a), m = Math.floor((a - d) * 60), s = Math.round(((a - d) * 60 - m) * 60);
    return `${String(d).padStart(w, '0')}°${String(m).padStart(2, '0')}'${String(s).padStart(2, '0')}"${v >= 0 ? pos : neg}`; };
  // 334512N 0073015W · 3345N00730W · N33 45 12 W007 30 15 · 33°45'12"N 7°30'15"W · 33.75 -7.5 · 33.75N 7.5W
  function parseCoord(q) {
    const s = q.toUpperCase().replace(/[°'"’”′″]/g, ' ').replace(/\s+/g, ' ').trim();
    const comp = (dg, w) => { const [i, f] = dg.split('.'); const deg = +i.slice(0, w), mi = +(i.slice(w, w + 2) || 0), se = +((i.slice(w + 2) || '0') + (f ? '.' + f : ''));
      return deg + mi / 60 + se / 3600; };
    let m = s.match(/^(\d{4,6}(?:\.\d+)?) ?([NS]) ?(\d{5,7}(?:\.\d+)?) ?([EW])$/) || s.match(/^([NS]) ?(\d{4,6}(?:\.\d+)?) ?([EW]) ?(\d{5,7}(?:\.\d+)?)$/);
    if (m) { const [la, hn, lo, he] = /^[NS]/.test(m[1]) ? [m[2], m[1], m[4], m[3]] : [m[1], m[2], m[3], m[4]];
      return [comp(la, 2) * (hn === 'S' ? -1 : 1), comp(lo, 3) * (he === 'W' ? -1 : 1)]; }
    m = s.match(/^(-?\d+(?:\.\d+)?)[ ,;]+(-?\d+(?:\.\d+)?)$/);
    if (m && Math.abs(+m[1]) <= 90 && Math.abs(+m[2]) <= 180 && (m[1].includes('.') || m[2].includes('.') || m[2].startsWith('-'))) return [+m[1], +m[2]];
    m = s.match(/^([NS])? ?(\d+(?:\.\d+)?)(?: (\d+(?:\.\d+)?))?(?: (\d+(?:\.\d+)?))? ?([NS])?[ ,;/]*([EW])? ?(\d+(?:\.\d+)?)(?: (\d+(?:\.\d+)?))?(?: (\d+(?:\.\d+)?))? ?([EW])?$/);
    if (m && (m[1] || m[5]) && (m[6] || m[10])) {
      const v = (a, b, c) => +a + (+b || 0) / 60 + (+c || 0) / 3600;
      return [v(m[2], m[3], m[4]) * ((m[1] || m[5]) === 'S' ? -1 : 1), v(m[7], m[8], m[9]) * ((m[6] || m[10]) === 'W' ? -1 : 1)];
    }
    return null;
  }

  // ---------------------------------------------------------------- search
  const allAc = () => state ? state.aircraft.concat(state.transferred || []) : [];
  function search(q0) {
    const q = q0.trim().toUpperCase(), out = []; if (!q || !sector) return out;
    const wp = sector.waypoints || {}, geo = sector.waypoints_geo || {};
    const ll = parseCoord(q0), fwd = lcc();
    if (ll && fwd) { const [x, y] = fwd(ll[0], ll[1]); out.push({ kind: 'coord', label: `${dmsTxt(ll[0], 2, 'N', 'S')} ${dmsTxt(ll[1], 3, 'E', 'W')}`, sub: 'coordinate', x, y }); }
    let m = q.match(/^([A-Z0-9]{2,5})[\/ ](\d{1,3})[\/ ](\d{1,3}(?:\.\d+)?)$/);         // point / bearing / distance (true)
    if (m && wp[m[1]]) { const b = +m[2] * Math.PI / 180, d = +m[3], [px, py] = wp[m[1]];
      out.push({ kind: 'coord', label: `${m[1]} ${m[2].padStart(3, '0')}° ${d} NM`, sub: 'bearing / distance from the point (true)', x: px + Math.sin(b) * d, y: py + Math.cos(b) * d }); }
    m = q.match(/^A?([0-7]{4})$/);                                                         // SSR (Mode A) code
    if (m) for (const a of allAc()) if (String(a.squawk || '') === m[1]) out.push({ kind: 'ac', cs: a.callsign, label: a.callsign, sub: `SSR A${m[1]} · ${a.type} · FL${a.fl}` });
    const acs = allAc().filter(a => a.callsign.includes(q)).sort((a, b) => (b.callsign.startsWith(q) - a.callsign.startsWith(q)) || a.callsign.localeCompare(b.callsign));
    for (const a of acs.slice(0, 6)) if (!out.some(r => r.cs === a.callsign)) out.push({ kind: 'ac', cs: a.callsign, label: a.callsign, sub: `aircraft · ${a.type} · FL${a.fl} · A${a.squawk || '----'}` });
    for (const ad of (arrInfo && arrInfo.aerodromes) || []) if (ad.id.startsWith(q) || String(ad.name || '').toUpperCase().includes(q))
      out.push({ kind: 'point', label: ad.id, sub: `aerodrome · ${ad.name} · RWY ${ad.runway_in_use}`, x: ad.x_nm, y: ad.y_nm });
    const pts = Object.keys(wp).filter(n => !n.startsWith('@') && n.startsWith(q)).sort((a, b) => a.length - b.length || a.localeCompare(b));
    for (const n of pts.slice(0, 8)) out.push({ kind: 'point', label: n, sub: `point${geo[n] ? ' · ' + (geo[n].role || '') + ' · ' + (geo[n].aip || '') : ''}`, x: wp[n][0], y: wp[n][1] });
    for (const [name, aw] of Object.entries(sector.airways || {})) if (name.toUpperCase().startsWith(q) && aw.points.every(p => wp[p]))
      out.push({ kind: 'airway', label: name, sub: `airway · ${aw.points[0]} – ${aw.points[aw.points.length - 1]}`, pts: aw.points.map(p => wp[p]) });
    const qa = q.replace(/\s+/g, '');
    for (const ar of (state && state.areas) || []) if (String(ar.id).toUpperCase().replace(/\s+/g, '').includes(qa) || String(ar.name || '').toUpperCase().includes(q)) {
      const c = ar.shape.type === 'circle' ? ar.shape.center : [ar.shape.points.reduce((s, p) => s + p[0], 0) / ar.shape.points.length, ar.shape.points.reduce((s, p) => s + p[1], 0) / ar.shape.points.length];
      out.push({ kind: 'area', label: ar.id, sub: `${ar.kind} area${ar.active ? ' · active' : ''} · FL${ar.lower_fl ?? ''}–${ar.upper_fl ?? ''}`, area: ar.id, x: c[0], y: c[1] });
    }
    for (const sc of sector.sectors || []) if (String(sc.id).toUpperCase() === q || String(sc.name || '').toUpperCase().includes(q)) {
      const P = sc.polygon; out.push({ kind: 'sector', label: sc.name || sc.id, sub: `sector${sc.frequency ? ' · ' + sc.frequency : ''}`, pts: P.concat([P[0]]),
        x: P.reduce((s, p) => s + p[0], 0) / P.length, y: P.reduce((s, p) => s + p[1], 0) / P.length });
    }
    return out.slice(0, 16);
  }

  // ---------------------------------------------------------------- actions
  function act(r) {
    if (r.kind === 'ac') {
      selected = r.cs; if (typeof pollSelected === 'function') pollSelected();
      centerOn(r.cs); hiAc = { cs: r.cs, until: performance.now() + 8000 }; return;
    }
    const k = r.kind + ':' + r.label;
    if (!marks.some(m => m.key === k)) marks.push({ ...r, key: k, t0: performance.now() });
    else marks.find(m => m.key === k).t0 = performance.now();
    if (r.pts && r.x == null) { const xs = r.pts.map(p => p[0]), ys = r.pts.map(p => p[1]); centerOn([(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2]); }
    else centerOn([r.x, r.y]);
    render();
  }

  // ---------------------------------------------------------------- window
  let results = [], cur = 0;
  function open() {
    const el = atcWin('lkWin', `Find <span class="c-dim" style="font-weight:400">callsign, SSR code, point, aerodrome, airway, area, coordinates</span>`, 120, 46, 400);
    const wb = el.querySelector('.wb');
    if (!wb.querySelector('#lkIn')) {
      wb.innerHTML = `<input id="lkIn" class="flt" placeholder="RAM763 · 7421 · TERTO · GMMN · UN857 · GMP18 · 3345N00730W · TERTO/090/20" autocomplete="off" spellcheck="false">
        <div id="lkRes"></div><div id="lkMarks"></div>`;
      const inp = wb.querySelector('#lkIn');
      inp.oninput = () => { results = search(inp.value); cur = 0; render(); };
      inp.onkeydown = e => {
        e.stopPropagation();
        if (e.key === 'ArrowDown') { cur = Math.min(results.length - 1, cur + 1); render(); e.preventDefault(); }
        else if (e.key === 'ArrowUp') { cur = Math.max(0, cur - 1); render(); e.preventDefault(); }
        else if (e.key === 'Enter' && results[cur]) { act(results[cur]); }
        else if (e.key === 'Escape') atcCloseWin('lkWin');
      };
      wb.addEventListener('click', e => {
        const r = e.target.closest('[data-r]'); if (r) { act(results[+r.dataset.r]); return; }
        const x = e.target.closest('[data-x]'); if (x) { marks.splice(+x.dataset.x, 1); render(); return; }
        const g = e.target.closest('[data-g]'); if (g) { const m = marks[+g.dataset.g]; if (m) act(m); return; }
        if (e.target.closest('[data-clear]')) { marks.length = 0; render(); }
      });
    }
    render();
    const inp = wb.querySelector('#lkIn'); inp.focus(); inp.select();
  }
  function render() {
    const el = document.getElementById('lkWin'); if (!el) return;
    const q = el.querySelector('#lkIn').value.trim();
    el.querySelector('#lkRes').innerHTML = !q ? '' : results.length ? results.map((r, i) =>
      `<div class="lk-r${i === cur ? ' cur' : ''}" data-r="${i}"><b>${esc(r.label)}</b> <small>${esc(r.sub || '')}</small></div>`).join('') : '<div class="empty">nothing found</div>';
    el.querySelector('#lkMarks').innerHTML = marks.length ? `<div class="sub">On the scope <button data-clear style="float:right;padding:0 6px;font-size:10px">clear all</button></div>` +
      marks.map((m, i) => `<div class="lk-m"><span data-g="${i}"><b>${esc(m.label)}</b> <small>${esc(m.kind)}</small></span><button data-x="${i}" title="remove">✕</button></div>`).join('') : '';
  }
  const btn = document.createElement('button');
  btn.id = 'findBtn'; btn.textContent = 'FIND'; btn.title = 'Find an aircraft (callsign, SSR code), a point, an aerodrome, an airway, an area or a coordinate (Ctrl+F)';
  const al = document.getElementById('alBtn'); if (al) al.after(btn); else document.getElementById('viewbar').prepend(btn);
  btn.onclick = () => { if (atcIsOpen('lkWin')) atcCloseWin('lkWin'); else open(); };
  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); open(); }
  });
  let geoMode = false;
  const geoAt = (px, py) => { const inv = lccInv(); if (!inv) return null; const [x, y] = fromPx(px, py); return inv(x, y); };
  function setGeo(on) { geoMode = on; cv.style.cursor = on ? 'none' : ''; }
  window.geoCursorActive = () => geoMode;
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest('input,textarea,select,[contenteditable]')) return;
    if (e.key === 'q' || e.key === 'Q') { setGeo(!geoMode); e.preventDefault(); }
    else if (e.key === 'Escape' && geoMode) setGeo(false);
  });
  cv.addEventListener('mousedown', e => {
    if (!geoMode || e.button !== 0) return;
    e.stopImmediatePropagation(); e.preventDefault();
    const [px, py] = canvasPt(e), ll = geoAt(px, py); if (!ll) return;
    const [x, y] = fromPx(px, py), label = `${dmsFine(ll[0], 2, 'N', 'S')} ${dmsFine(ll[1], 3, 'E', 'W')}`;
    marks.push({ kind: 'coord', label, sub: 'dropped', x, y, key: 'coord:' + label + ':' + marks.length, t0: performance.now() });
    render(); if (!e.shiftKey) setGeo(false);
  }, true);
  const css = document.createElement('style');
  css.textContent = `.win .lk-r { padding: 4px 6px; border-radius: 4px; cursor: pointer; font-family: var(--mono); font-size: 12px; }
    .win .lk-r small, .win .lk-m small { color: var(--dim); font-family: system-ui; margin-left: 4px; }
    .win .lk-r.cur, .win .lk-r:hover { background: #1c3856; }
    .win .lk-m { display: flex; justify-content: space-between; align-items: center; padding: 2px 4px; font-family: var(--mono); font-size: 12px; }
    .win .lk-m span { cursor: pointer; } .win .lk-m button { padding: 0 6px; font-size: 10px; }`;
  document.head.appendChild(css);

  // ---------------------------------------------------------------- drawing (called by the radar's draw loop)
  // a ring with a fine cross through it (the exact position at the centre, a gap so the point itself stays visible)
  function crosshair(x, y, r, long) {
    const dpr = devicePixelRatio, g = 2.5 * dpr, L = long ? r * 1.8 : r * 1.35;
    ctx.lineWidth = 1.6 * dpr; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
    ctx.lineWidth = 1 * dpr; ctx.beginPath();
    ctx.moveTo(x - L, y); ctx.lineTo(x - g, y); ctx.moveTo(x + g, y); ctx.lineTo(x + L, y);
    ctx.moveTo(x, y - L); ctx.lineTo(x, y - g); ctx.moveTo(x, y + g); ctx.lineTo(x, y + L); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, 0.9 * dpr, 0, Math.PI * 2); ctx.fill();
  }
  window.drawLookup = byCs => {
    const dpr = devicePixelRatio, now = performance.now();
    const pulse = t0 => now - t0 < 4000 ? 1 + 0.6 * Math.abs(Math.sin((now - t0) / 220)) : 1;
    ctx.save(); ctx.strokeStyle = COL; ctx.fillStyle = COL; ctx.font = `bold ${11 * dpr}px ui-monospace,monospace`;
    for (const m of marks) {
      const k = pulse(m.t0);
      if (m.kind === 'airway' || m.kind === 'sector') {
        ctx.lineWidth = 3.5 * dpr * k; ctx.globalAlpha = 0.75; ctx.beginPath();
        m.pts.forEach((p, i) => { const [x, y] = toPx(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke(); ctx.globalAlpha = 1;
        const [lx, ly] = toPx(m.x != null ? m.x : m.pts[0][0], m.y != null ? m.y : m.pts[0][1]); ctx.fillText(m.label, lx + 6 * dpr, ly - 6 * dpr);
      } else if (m.kind === 'area') {
        const ar = ((typeof shown !== 'undefined' && shown) || state || {}).areas?.find(a => a.id === m.area); if (!ar) continue;
        ctx.lineWidth = 3 * dpr * k; ctx.beginPath();
        if (ar.shape.type === 'circle') { const [cx, cy] = toPx(...ar.shape.center); ctx.arc(cx, cy, ar.shape.radius_nm * view.s, 0, Math.PI * 2); }
        else { ar.shape.points.forEach((p, i) => { const [x, y] = toPx(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.closePath(); }
        ctx.stroke(); const [lx, ly] = toPx(m.x, m.y); ctx.fillText(m.label, lx, ly);
      } else {
        const [x, y] = toPx(m.x, m.y); ctx.lineWidth = 2 * dpr;
        crosshair(x, y, (m.kind === 'coord' ? 9 : 10) * dpr * k, m.kind === 'coord');
        ctx.fillText(m.label, x + 12 * dpr, y - 10 * dpr);
      }
    }
    if (hiAc && now < hiAc.until && byCs[hiAc.cs]) {           // the aircraft found: a shrinking ring around it
      const a = byCs[hiAc.cs], [x, y] = toPx(a.x_nm, a.y_nm), f = ((hiAc.until - now) % 1000) / 1000;
      ctx.lineWidth = 2.5 * dpr; ctx.globalAlpha = 0.4 + 0.6 * f; ctx.beginPath(); ctx.arc(x, y, (12 + 26 * f) * dpr, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1; ctx.beginPath(); ctx.arc(x, y, 12 * dpr, 0, Math.PI * 2); ctx.stroke();
    }
    if (geoMode && mouseCv) {                                    // the geographic cursor: crosshair and DMS label
      const [x, y] = mouseCv, ll = geoAt(x, y);
      ctx.strokeStyle = COL; ctx.fillStyle = COL; crosshair(x, y, 7 * dpr, true);
      if (ll) {
        const t = `${dmsFine(ll[0], 2, 'N', 'S')}  ${dmsFine(ll[1], 3, 'E', 'W')}`;
        ctx.font = `bold ${11 * dpr}px ui-monospace,monospace`; const w = ctx.measureText(t).width;
        const bx = x + 16 * dpr, by = y + 16 * dpr;
        ctx.fillStyle = '#0b1016e6'; ctx.fillRect(bx - 4 * dpr, by - 12 * dpr, w + 8 * dpr, 17 * dpr);
        ctx.strokeStyle = COL + '99'; ctx.lineWidth = 1 * dpr; ctx.strokeRect(bx - 4 * dpr, by - 12 * dpr, w + 8 * dpr, 17 * dpr);
        ctx.fillStyle = COL; ctx.fillText(t, bx, by);
      }
    }
    ctx.restore();
  };
})();

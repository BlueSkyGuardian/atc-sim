// The live traffic (/live): the real aircraft around the Agadir CTA (a public ADS-B network) on the radar, view only.
// The panel shows the feed's state and its credit, and the coverage map (where the network receives positions).
// Uses the radar page's globals (state, SID, SEAT, EXINFO, rec, recOffline, attach, api, toPx, ctx, view, fmtT...).
(function () {
  if (!LIVETR) return;
  const h = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = s => document.querySelector(s);
  let info = null, cov = null, covOn = false, covAt = 0, me = null, lastPanel = '';
  try { covOn = localStorage.getItem('lt_cov') === '1'; } catch (e) {}

  async function init() {
    document.body.classList.add('exercise', 'seat-atc', 'solo', 'debrief', 'db-ro', 'live');
    SEAT = 'atc';
    EXINFO = { solo: true, debrief: true, live: true, name: 'Live traffic' };
    document.title = 'Live traffic · ATC SIM';
    $('#scope').insertAdjacentHTML('beforeend', `<div id="ltPanel"></div><div id="dbMsg"></div>`);
    $('#ltPanel').addEventListener('click', onClick);
    try { const r = await fetch('/api/me', { credentials: 'same-origin' }); if (r.ok) me = await r.json(); } catch (e) {}
    resize();
    await poll();
    setInterval(poll, 5000);
    setInterval(render, 1000);
  }
  async function poll() {
    try { info = await api('/api/live'); }
    catch (e) { const m = $('#dbMsg'); if (m) { m.innerHTML = h(e.message); m.style.display = 'block'; } return; }
    if (SID !== info.sid) await attach(info.sid);
    recOffline = true;
    rec = { frames: [{ t: 0, ac: [], req: [], los: [], areas: [] }, { t: 1, ac: [], req: [], los: [], areas: [] }], events: [], meta: {}, duration_s: 1 };
    if (covOn && Date.now() - covAt > 60000) loadCoverage();
    render();
  }
  async function loadCoverage() {
    covAt = Date.now();
    try { cov = await api('/api/live/coverage'); } catch (e) { cov = null; }
    render();
  }
  async function onClick(e) {
    const b = e.target.closest('[data-a]'); if (!b) return;
    const a = b.dataset.a;
    if (a === 'cov') { covOn = !covOn; try { localStorage.setItem('lt_cov', covOn ? '1' : '0'); } catch (e2) {} if (covOn) loadCoverage(); }
    else if (a === 'measure') { try { await api('/api/live/measure', { method: 'POST', body: JSON.stringify({ hours: info.measure_until && info.measure_until * 1000 > Date.now() ? 0 : 24 }) }); } catch (e2) {} poll(); }
    else if (a === 'reset' && confirm('Start the coverage map again from nothing?')) { try { await api('/api/live/coverage/reset', { method: 'POST' }); } catch (e2) {} loadCoverage(); }
    lastPanel = ''; render();
  }
  const ago = t => { if (!t) return 'never'; const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; };
  function render() {
    if (!info) return;
    const fresh = info.last_ok && Date.now() / 1000 - info.last_ok < 30;
    const err = info.last_error && (!info.last_ok || info.last_error_t > info.last_ok);
    const pill = fresh ? '<span class="db-pill live">● LIVE</span>' : '<span class="db-pill ready">WAITING FOR THE FEED</span>';
    const meas = info.measure_until && info.measure_until * 1000 > Date.now();
    const admin = me && me.role === 'admin';
    let covTxt = '';
    if (covOn && cov && cov.counts) {
      const hrs = (Date.now() / 1000 - cov.since) / 3600, n = cov.counts.filter(c => c > 0).length;
      covTxt = `<div class="c-dim">${cov.polls} readings over ${hrs < 1 ? Math.round(hrs * 60) + ' min' : hrs.toFixed(1) + ' h'} · positions received in ${n} cells of 10 NM</div>
        <div class="cov-key"><span>few</span><i></i><span>many</span><b></b><span>inside the CTA, nothing received</span></div>`;
    }
    const html = `<div class="hd2"><b>Live traffic</b>${pill}</div><div class="bd">
      <div><b>${info.aircraft}</b> aircraft on the picture, <b>${info.in_cta}</b> in the CTA</div>
      <div class="c-dim">Read from ${h(info.source)} every ${info.every_s} s while somebody watches · last ${ago(info.last_ok)}${err ? ` · <span class="bad">${h(info.last_error)}</span>` : ''}</div>
      <div class="c-dim">The level selected in the cockpit (Mode S) shows as the cleared level. Flight plans are not public: the look-ahead (MTCD) flies each track straight ahead. View only.</div>
      <div class="row"><button data-a="cov" class="${covOn ? 'on' : ''}" title="Where the network receives positions: the readings since the measurement started, 10 NM cells">Coverage map</button>
        ${admin ? `<button data-a="measure" title="${meas ? 'Stop reading the feed when nobody watches' : 'Read the feed (once a minute) for 24 h even when nobody watches, for the coverage map'}">${meas ? 'Stop measuring' : 'Measure 24 h'}</button>${covOn ? '<button data-a="reset">Reset map</button>' : ''}` : ''}</div>
      ${meas ? `<div class="c-dim">Measuring the coverage until ${new Date(info.measure_until * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</div>` : ''}
      ${covTxt}</div><div class="credit">${h(info.credit)} · <a href="https://adsb.lol" target="_blank" rel="noopener">adsb.lol</a></div>`;
    if (html !== lastPanel) { $('#ltPanel').innerHTML = html; lastPanel = html; }
  }
  // the coverage map, under the traffic: a cell's colour grows with the positions received there; a cell of the CTA
  // where nothing was received is hatched red
  window.drawCoverage = () => {
    if (!covOn || !cov || !cov.counts || !state) return;
    const dpr = devicePixelRatio, c = cov.cell, max = Math.max(1, ...cov.counts), lm = Math.log(1 + max);
    const poly = (sector && sector.shape && sector.shape.polygon) || [];
    const inCta = (x, y) => { let ins = false; for (let a = 0, b = poly.length - 1; a < poly.length; b = a++) {     // point in polygon
      const [xa, ya] = poly[a], [xb, yb] = poly[b];
      if ((ya > y) !== (yb > y) && x < (xb - xa) * (y - ya) / (yb - ya) + xa) ins = !ins; } return ins; };
    for (let j = 0; j < cov.ny; j++) for (let i = 0; i < cov.nx; i++) {
      const n = cov.counts[j * cov.nx + i], x = cov.x0 + i * c, y = cov.y0 + j * c;
      const [px, py] = toPx(x, y + c), [qx, qy] = toPx(x + c, y), w = qx - px, hh = qy - py;
      if (qx < 0 || px > ctx.canvas.width || qy < 0 || py > ctx.canvas.height) continue;
      if (n > 0) { const k = Math.log(1 + n) / lm; ctx.fillStyle = `rgba(${Math.round(42 + 53 * k)}, ${Math.round(111 + 100 * k)}, ${Math.round(154 + 9 * k)}, ${0.18 + 0.5 * k})`; ctx.fillRect(px, py, w + 0.5, hh + 0.5); }
      else if (cov.polls > 3 && inCta(x + c / 2, y + c / 2)) {
        ctx.strokeStyle = 'rgba(255, 107, 107, 0.45)'; ctx.lineWidth = 1 * dpr; ctx.beginPath();
        for (let k = 0; k <= 2; k++) { ctx.moveTo(px + w * k / 2, qy); ctx.lineTo(px, qy - hh * k / 2); }
        ctx.stroke();
      }
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

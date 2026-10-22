// Controller's seat: sends what happens on the screen to the exercise recording (through the voice WebSocket; on the
// solo radar, which has no radio, by HTTP POST /api/exercises/{id}/ui):
//   'v' scope view (size, centre, zoom, display options, weather product, label positions, selection, RBLs)
//   'o' on-screen panels (toolbar, HUD, alarms, label menu with what is being typed, telephone, push-to-talk)
//   'm' mouse position (20 per second while it moves), 'c' / 'd' clicks and double-clicks (and what was clicked)
// Positions are CSS pixels relative to the scope. Times are this page's clock; the server converts them.
(function () {
  if (!EXID || DBID || AIW) return;
  const scope = document.getElementById('scope'), q = [];
  let mouse = null, sentMouse = null, lastV = '', lastO = '', started = false;
  const now = () => Math.round(performance.now());
  const push = (k, d) => { if (q.length < 6000) q.push([now(), k, d]); };
  const rel = e => { const r = scope.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top;
    return x < 0 || y < 0 || x > r.width || y > r.height ? null : [Math.round(x), Math.round(y)]; };
  function describe(e) {
    const t = e.target, el = t.closest && t.closest('button, input, select, label');
    if (el && scope.contains(el)) {
      if (el.tagName === 'INPUT' && el.type !== 'checkbox') return 'field ' + (el.dataset.f || el.id || '');
      return (el.innerText || el.value || el.title || '').replace(/\s+/g, ' ').trim().slice(0, 38);
    }
    if (t === cv) { const l = labelAt(e); if (l) return 'label ' + l.cs; const a = nearestAircraft(e); return a ? 'aircraft ' + a : 'scope'; }
    return '';
  }
  function hex(c) {
    const m = c && c.match(/rgba?\(([^)]+)\)/); if (!m) return '';
    const p = m[1].split(',').map(s => parseFloat(s)), a = p.length > 3 ? Math.round(p[3] * 255) : 255;
    if (!a) return '';
    return '#' + [p[0], p[1], p[2], a].map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  }
  function visible(el) {
    if (!el || !el.getClientRects().length) return false;
    const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0.05;
  }
  // a flat list of boxes, widgets and texts: [x, y, w, h, type, text, fg, bg, border, fontPx, flags]
  function overlay() {
    const R = scope.getBoundingClientRect(), out = [];
    const box = (r) => [Math.round(r.left - R.left), Math.round(r.top - R.top), Math.round(r.width), Math.round(r.height)];
    function item(el, type, text, r) {
      const s = getComputedStyle(el), fl = (s.fontFamily.includes('mono') ? 'm' : '') + (+s.fontWeight >= 600 ? 'b' : '');
      return [...box(r || el.getBoundingClientRect()), type, text, hex(s.color), hex(s.backgroundColor), hex(s.borderTopColor) && parseFloat(s.borderTopWidth) ? hex(s.borderTopColor) : '', parseFloat(s.fontSize), fl];
    }
    function walk(el) {
      if (!visible(el) || out.length > 400) return;
      const tag = el.tagName;
      if (tag === 'CANVAS' || tag === 'DATALIST' || tag === 'OPTION' || tag === 'SCRIPT') return;
      if (tag === 'INPUT' && el.type === 'checkbox') { const it = item(el, 'x', '', null); it[10] += el.checked ? 'c' : ''; out.push(it); return; }
      if (tag === 'INPUT') { const it = item(el, 'i', el.value || el.placeholder || ''); if (!el.value) it[10] += 'h'; out.push(it); return; }
      if (tag === 'SELECT') { out.push(item(el, 'i', (el.selectedOptions[0] || {}).text || '')); return; }
      if (tag === 'BUTTON') { out.push(item(el, 'b', (el.innerText || '').replace(/\n+/g, '\n').trim())); return; }
      const s = getComputedStyle(el);
      if (hex(s.backgroundColor) || (parseFloat(s.borderTopWidth) && hex(s.borderTopColor))) out.push(item(el, 'p', ''));
      for (const n of el.childNodes) {
        if (n.nodeType === 3) {
          const t = n.textContent.replace(/\s+/g, ' ').trim(); if (!t) continue;
          const rg = document.createRange(); rg.selectNodeContents(n); const r = rg.getBoundingClientRect();
          if (r.width) out.push(item(el, 't', t, r));
        } else if (n.nodeType === 1) walk(n);
      }
    }
    for (const sel of ['#viewbar', '#hud', '#alerts', '#pauseBanner', '#ptt', '#phoneBtn', '#phonePanel', '#callHud', '#lblMenu', '#wins', '#dispPanel',
                       '#ctrlRow', '#solvePanel', '#seqPanel', '#radioLog', '#cmdBar'])   // + the solo radar's control bar, panels,
                                                                             // radio log; the command line (what is typed)
      walk(document.querySelector(sel));
    return out;
  }
  function selFl() { const a = state && selected && state.aircraft.find(x => x.callsign === selected); return a ? a.fl : (+$('#wxFl').value || 350); }
  function viewState() {
    return { w: cv.clientWidth, h: cv.clientHeight, cx: +view.cx.toFixed(3), cy: +view.cy.toFixed(3), s: +(view.s / devicePixelRatio).toFixed(5),
      V: { ...V }, wx: [$('#wxMode').value, $('#wxMode').value === 'sel' ? selFl() : (+$('#wxFl').value || 350), $('#wxCells').checked],
      lo: labelOff, sel: selected || null,
      rbl: rbls.map(r => [r.a, r.b].map(t => typeof t === 'string' ? t : [+t.x.toFixed(2), +t.y.toFixed(2), t.name || '']).concat([[Math.round(r.ox || 0), Math.round(r.oy || 0)]])),
      rs: routeShow && routeShow.until > performance.now() ? routeShow.cs : null, th: TH };
  }
  function start() {
    if (started) return; started = true;
    window.addEventListener('mousemove', e => { mouse = rel(e); }, { passive: true });
    document.addEventListener('mouseleave', () => { mouse = null; });
    window.addEventListener('pointerdown', e => { const p = rel(e); if (p) push('c', { x: p[0], y: p[1], b: e.button, what: describe(e) }); }, true);
    window.addEventListener('dblclick', e => { const p = rel(e); if (p) push('d', { x: p[0], y: p[1], b: 0, what: describe(e) }); }, true);
    setInterval(() => { const m = JSON.stringify(mouse); if (m !== sentMouse) { sentMouse = m; push('m', mouse); } }, 50);
    setInterval(() => {
      if (!sector) return;
      const v = viewState(), sv = JSON.stringify(v); if (sv !== lastV) { lastV = sv; push('v', v); }
      const o = overlay(), so = JSON.stringify(o); if (so !== lastO) { lastO = so; push('o', o); }
    }, 200);
    // the labels exactly as drawn (lines of [text, colour], extended labels, alert tags): changes only, every second
    let lastL = {};
    setInterval(() => {
      const cur = window.__drawnLabels; if (!cur) return;
      const diff = {}; let n = 0;
      for (const [cs, v] of Object.entries(cur)) { const sv = JSON.stringify(v); if (lastL[cs] !== sv) { diff[cs] = v; lastL[cs] = sv; n++; } }
      for (const cs of Object.keys(lastL)) if (!(cs in cur)) { diff[cs] = null; delete lastL[cs]; n++; }
      if (n) push('L', diff);
    }, 1000);
    let posting = false;
    setInterval(() => {
      const voice = window.__voice;
      if (!q.length) return;
      if (voice && voice.ws && voice.ws.readyState === 1) { voice.send({ op: 'ui', now: now(), ev: q.splice(0, q.length) }); return; }
      if (voice || posting) return;                // (a seat with the radio: wait for it to reconnect)
      // the solo radar has no radio connection: the same events by HTTP, about once a second
      posting = true;
      const ev = q.splice(0, q.length);
      fetch(`/api/exercises/${EXID}/ui`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ now: now(), ev }) })
        .then(r => { if (!r.ok && r.status >= 500) q.unshift(...ev); })
        .catch(() => { q.unshift(...ev.slice(-3000)); })
        .finally(() => { setTimeout(() => { posting = false; }, 700); });
    }, 300);
  }
  const wait = setInterval(() => { if (typeof SEAT !== 'undefined' && document.body.classList.contains('exercise')) { clearInterval(wait); if (SEAT === 'atc') start(); } }, 300);
})();

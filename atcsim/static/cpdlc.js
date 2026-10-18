// CPDLC on the radar page: the controller's command line (shortcut typing, sent by data link or entered for a voice
// clearance), the CPDLC window (requests, dialogues, connections) and the short CPDLC tags of the labels.
// Uses the radar page's globals (state, selected, SID, SEAT, EXID, api, sector) and atctools.js (atcWin ...).
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ctl = () => (SEAT === 'atc' && EXID) || !EXID;          // the controller's seat, or the solo radar
  const dl = cs => (state && state.cpdlc && state.cpdlc[cs]) || null;
  const age = t => { const s = Math.max(0, Math.round(state.sim_time_s - t)); return s < 60 ? s + ' s' : Math.floor(s / 60) + ' min'; };

  // ------------------------------------------------------------------ short forms for the labels
  function short(text) {
    const t = text.split(' / ')[0];
    let m;
    if ((m = t.match(/^(?:REQUEST )?(?:CLIMB TO|DESCEND TO|DESCENT TO|MAINTAIN|REQUEST) FL(\d{3})/))) return (t.startsWith('REQUEST') ? 'RQ' : t.startsWith('CLIMB') ? 'CL' : t.startsWith('DESC') ? 'DE' : 'FL') + m[1];
    if ((m = t.match(/DIRECT TO (\S+)/))) return (t.startsWith('REQUEST') ? 'RQ ' : 'D ') + m[1];
    if ((m = t.match(/HEADING (\d{3})/))) return 'H' + m[1];
    if ((m = t.match(/^(?:REQUEST )?(?:MAINTAIN )?M\.(\d{2})/))) return (t.startsWith('REQUEST') ? 'RQ ' : '') + 'M' + m[1];
    if ((m = t.match(/(\d{3}) KT/))) return (t.startsWith('REQUEST') ? 'RQ ' : 'S') + m[1];
    if (/DEVIATE|DEVIATION/.test(t)) return t.startsWith('REQUEST') ? 'RQ WX' : 'DEV';
    if (/^CONTACT|^MONITOR/.test(t)) return t.startsWith('CONTACT') ? 'CT' : 'MON';
    if (/^CLEARED TO/.test(t)) return 'RTE';
    if (/^RESUME/.test(t)) return t.includes('SPEED') ? 'SN' : 'RES';
    return t.startsWith('REQUEST') ? 'RQ' : 'TXT';
  }
  // label segments: [text, colour] after the callsign (controller, instructor, solo)
  window.cpdlcTag = function (cs, blink) {
    const d = dl(cs); if (!d) return [];
    if (SEAT === 'pilot') {                                   // the pseudo-pilot: an uplink to answer
      const up = d.open.find(m => m.dir === 'up');
      return up ? [['  CPDLC↓', blink ? '#5ad1e6' : '#5ad1e688']] : [];
    }
    const out = [];
    if (d.state === 'cda') out.push([' D', '#6fa8dc']);
    else if (d.state === 'logon') out.push([' d', '#93a7ba']);
    else return out;
    const down = d.open.find(m => m.dir === 'down');
    if (down) out.push(['↓' + short(down.text) + (down.status === 'standby' ? ' SBY' : ''), blink ? '#5ad1e6' : '#5ad1e6aa']);
    const up = d.open.filter(m => m.dir === 'up').pop();
    if (up) out.push(['↑' + short(up.text) + (up.status === 'standby' ? ' SBY' : up.late ? '?' : ''), up.late ? (blink ? '#f2b84b' : '#f2b84b88') : up.status === 'standby' ? '#f2b84b' : '#e9eef2']);
    if (!up && !down) {
      const r = d.recent.filter(m => m.dir === 'up' && m.closed_t != null).pop();
      if (r && r.status === 'unable') out.push(['↑UNABLE', '#ff6b6b']);
      else if (r && r.status === 'wilco' && state.sim_time_s - r.closed_t < 12) out.push(['✓', '#5fd3a3']);
    }
    return out;
  };

  // the seat is known once the exercise is loaded (seat.js); the solo radar is a controller's seat
  const wait = setInterval(() => { if (EXID && !EXINFO) return; clearInterval(wait); if (ctl()) setup(); }, 200);

  function setup() {
  // ------------------------------------------------------------------ command line
  const bar = document.createElement('div');
  bar.id = 'cmdBar';
  bar.innerHTML = `<div class="cb-row"><span class="cb-cs" id="cbCs"></span><span class="cb-dl" id="cbDl"></span>
      <input id="cbIn" autocomplete="off" spellcheck="false" placeholder="Enter: type a clearance · CL350 · D ROLRO · H270 · M78 · CT">
      <span class="cb-k" title="Enter sends by CPDLC (aircraft connected), Shift+Enter enters it in the system only (voice clearance)">⏎ CPDLC · ⇧⏎ voice</span></div>
    <div class="cb-pv" id="cbPv"></div><div class="cb-sg" id="cbSg"></div>`;
  (document.getElementById('scope') || document.body).appendChild(bar);
  const inp = bar.querySelector('#cbIn'), pv = bar.querySelector('#cbPv'), sg = bar.querySelector('#cbSg');
  let sugg = [], sIdx = 0, lastPv = null, pvTimer = null, result = null;

  const KEYWORDS = [
    ['CL', 'CL350', 'climb to FL350'], ['DES', 'DES240', 'descend to FL240'], ['FL', 'FL330', 'climb / descend / maintain'],
    ['H', 'H270', 'fly heading 270'], ['L', 'L270', 'turn left heading 270'], ['R', 'R270', 'turn right heading 270'],
    ['D', 'D ROLRO', 'proceed direct'], ['M', 'M78', 'maintain Mach .78'], ['S', 'S280', 'maintain 280 kt'], ['K', 'K280', '280 kt on conversion'],
    ['SN', 'SN', 'resume normal speed'], ['VIA', 'VIA IBALU TERTO', 'cleared via points'], ['DEV', 'DEVL20', 'cleared to deviate (L / R / either, NM)'],
    ['RES', 'RES', 'resume own navigation'], ['CT', 'CT', 'contact the next unit (or CT GCCC)'], ['MON', 'MON', 'monitor the next unit'],
    ['APP', 'APP', 'approve the request as asked'], ['UNA', 'UNA', 'unable'], ['SBY', 'SBY', 'standby'], ['ROG', 'ROG', 'roger'],
    ['TXT', 'TXT WHEN READY…', 'free text'], ['NDA', 'NDA GCCC', 'next data authority'], ['EOS', 'EOS', 'end service'],
  ];
  const pts = () => (sector ? Object.keys(sector.waypoints).filter(n => !n.startsWith('@')) : []);
  function suggest() {
    const v = inp.value.toUpperCase(), toks = v.split(/\s+/), last = toks[toks.length - 1] || '', prev = toks[toks.length - 2] || '';
    sugg = [];
    if (!last) { sugg = KEYWORDS.slice(0, 10).map(k => ({ ins: null, show: k[1], what: k[2] })); }
    else {
      const ptMode = ['D', 'DCT', 'VIA'].includes(prev) || toks.includes('VIA');
      const ahead = () => { const a = state && state.aircraft.find(x => x.callsign === cs()); if (!a) return [];
        const i = a.route.indexOf(a.next_waypoint); return [...new Set(a.route.slice(Math.max(0, i)).concat(a.route_after_exit || []).filter(n => !n.startsWith('@')))].slice(0, 7); };
      const unitMode = ['CT', 'CONTACT', 'MON', 'MONITOR', 'NDA'].includes(prev);
      if (last === 'D' || last === 'DCT') sugg = ahead().map(n => ({ ins: last + ' ' + n, show: last + ' ' + n, what: 'route point' }));
      else if (unitMode) sugg = ((EXINFO && EXINFO.units) || ['GMMM', 'LPPC', 'GCCC', 'LECS']).filter(u => u.startsWith(last)).map(u => ({ ins: u, show: u, what: 'unit' }));
      else {
        const m = last.match(/^(DCT|D)?([A-Z]{2,})$/);
        const stem = m ? m[2] : '', pre = m && m[1] && !ptMode ? m[1] : '';
        if (stem) sugg = [...new Set(ahead().filter(n => n.startsWith(stem)).concat(pts().filter(n => n.startsWith(stem))))].filter(n => n !== stem).slice(0, 6).map(n => ({ ins: pre + n, show: pre + n, what: pre || ptMode ? 'point' : 'direct' }));
        if (!ptMode) sugg = sugg.concat(KEYWORDS.filter(k => k[0].startsWith(last) && k[0] !== last).slice(0, 5).map(k => ({ ins: null, show: k[1], what: k[2] })));
      }
    }
    sIdx = Math.min(sIdx, Math.max(0, sugg.length - 1));
    sg.innerHTML = sugg.slice(0, 8).map((s, i) => `<span class="${i === sIdx ? 'on' : ''}" data-i="${i}"><b>${esc(s.show)}</b> ${esc(s.what)}</span>`).join('');
  }
  function accept(i) {
    const s = sugg[i]; if (!s || !s.ins) return false;
    const toks = inp.value.split(/(\s+)/); toks[toks.length - 1] = s.ins; inp.value = toks.join('') + ' '; refresh(); return true;
  }
  function cs() { return selected || null; }
  function header() {
    const c = cs(), d = c ? dl(c) : null, cb = bar.querySelector('#cbCs'), cd = bar.querySelector('#cbDl');
    cb.textContent = c || 'no aircraft';
    cb.className = 'cb-cs' + (c ? '' : ' none');
    const st = !c ? '' : !d ? 'VOICE' : d.state === 'cda' ? 'CPDLC' : d.state === 'logon' ? 'LOGON' : 'VOICE';
    cd.textContent = st; cd.className = 'cb-dl ' + st.toLowerCase();
    cd.title = !c ? '' : st === 'CPDLC' ? `connected: we are the current data authority${d.nda ? ' · next data authority ' + d.nda : ''}` : st === 'LOGON' ? `logged on, not yet ours (CDA ${d.cda || 'previous unit'})` : 'no data link: Enter puts the clearance in the system, give it by voice';
  }
  function showPv(p) {
    const c = cs(), d = c ? dl(c) : null, down = d && d.open.find(m => m.dir === 'down');
    if (result && performance.now() < result.until) { pv.innerHTML = result.html; return; }
    let h = '';
    if (down) h += `<div class="cb-down">↓ ${esc(down.text)} <span class="c-dim">· ${age(down.t)}${down.status === 'standby' ? ' · STANDBY sent' : ''} · APP, UNA, SBY or a clearance</span></div>`;
    if (!inp.value.trim()) h += `<div class="c-dim">${c ? (d && d.state === 'cda' ? 'Type the clearance, Enter sends it by CPDLC.' : 'Type the clearance, Enter puts it in the system (no data link: give it by voice).') : 'Select an aircraft, or start with its callsign.'}</div>`;
    else if (p && p.ok) h += `<div class="cb-up">${p.connected ? '↑ ' : '✎ '}${p.elements.map(esc).join(' · ')}</div>` +
      `<div class="c-dim">${p.connected ? 'Enter: send to ' + p.callsign + ' by CPDLC · Shift+Enter: voice, enter only' : 'Enter: put it in the system for ' + p.callsign + ' (' + (p.equipped ? 'no active connection' : 'not CPDLC equipped') + ', give it by voice)'}</div>`;
    else if (p) h += `<div class="${/\?$/.test(p.error) ? 'c-dim' : 'cb-err'}">${esc(p.error)}</div>`;   // a question: not finished yet
    pv.innerHTML = h;
  }
  function refresh() {
    suggest(); header();
    clearTimeout(pvTimer);
    const text = inp.value.trim();
    if (!text || !SID) { lastPv = null; showPv(null); return; }
    pvTimer = setTimeout(async () => {
      try { lastPv = await api(`/sessions/${SID}/cpdlc/parse`, { method: 'POST', body: JSON.stringify({ text, callsign: cs() }) }); } catch (e) { lastPv = { ok: false, error: e.message }; }
      if (inp.value.trim() === text) showPv(lastPv);
    }, 90);
  }
  async function send(voice) {
    const text = inp.value.trim(); if (!text || !SID) return;
    try {
      const r = await api(`/sessions/${SID}/cpdlc/command`, { method: 'POST', body: JSON.stringify({ text, callsign: cs(), voice }) });
      const html = r.kind === 'uplink' ? `<div class="cb-ok">↑ sent to ${esc(r.callsign)}: ${esc(r.message.text)}</div>`
        : r.kind === 'entry' ? `<div class="cb-ok">✎ ${esc(r.callsign)}: ${esc(r.note)}</div>` : `<div class="cb-ok">${esc(r.callsign)}: connection updated</div>`;
      result = { html, until: performance.now() + 3500 };
      inp.value = ''; inp.blur();                      // done: shortcuts again, Enter opens the next one
      if (window.uiLog) uiLog('cpdlc', { cs: r.callsign, text, voice });
      if (typeof pollState === 'function') pollState();
    } catch (e) { result = { html: `<div class="cb-err">${esc(e.message)}</div>`, until: performance.now() + 5000 }; }
    refresh();
  }
  inp.addEventListener('input', () => { sIdx = 0; result = null; refresh(); });
  inp.addEventListener('focus', () => { bar.classList.add('open'); refresh(); });
  inp.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== inp) bar.classList.remove('open'); }, 150));
  inp.addEventListener('keydown', e => {
    e.stopPropagation();                                     // letters and space are text here, not shortcuts / PTT
    if (e.key === 'Enter') { e.preventDefault(); send(e.shiftKey); return; }
    if (e.key === 'Escape') { inp.value = ''; inp.blur(); return; }
    if (e.key === 'Tab') { e.preventDefault(); if (!accept(sIdx)) accept(0); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (sugg.length) { sIdx = (sIdx + (e.key === 'ArrowDown' ? 1 : -1) + Math.min(8, sugg.length)) % Math.min(8, sugg.length); suggest(); } }
  });
  inp.addEventListener('keyup', e => e.stopPropagation());
  sg.addEventListener('mousedown', e => { const s = e.target.closest('[data-i]'); if (s) { e.preventDefault(); if (!accept(+s.dataset.i)) { inp.value = (inp.value.replace(/\S*$/, '') + sugg[+s.dataset.i].show.replace('…', '')).trimStart(); refresh(); } inp.focus(); } });
  const typing = () => { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') && a.type !== 'checkbox'; };
  window.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing()) return;
    if (e.key === 'Enter') { e.preventDefault(); inp.focus(); return; }
    if ((e.key === 'j' || e.key === 'J')) { toggleWin(); }
  });
  let lastSel = null;
  setInterval(() => { if (selected !== lastSel) { lastSel = selected; refresh(); } else { header(); if (document.activeElement === inp || result) showPv(lastPv); } }, 400);
  window.cpdlcCommand = (c, text) => { selected = c; inp.value = text; inp.focus(); refresh(); };

  // ------------------------------------------------------------------ CPDLC window
  const btn = document.createElement('button');
  btn.id = 'dlBtn'; btn.title = 'CPDLC: requests, dialogues and connections (J)'; btn.textContent = 'CPDLC';
  const help = document.getElementById('helpBtn'); if (help) help.parentNode.insertBefore(btn, help);
  btn.onclick = toggleWin;
  let logCs = null, logLast = -1, logMsgs = [];
  let wantWin = false;
  function toggleWin() { if (atcIsOpen('dlWin')) { atcCloseWin('dlWin'); wantWin = false; } else { wantWin = true; logLast = -1; renderWin(); } }
  async function loadLog(c) {
    try { const r = await api(`/sessions/${SID}/cpdlc?callsign=${c}`); logMsgs = r.messages; } catch (e) { logMsgs = []; }
  }
  async function renderWin() {
    const all = (state && state.cpdlc) || {};
    const downs = [], ups = [];
    let nConn = 0;
    for (const [c, d] of Object.entries(all)) {
      if (d.state === 'cda') nConn++;
      for (const m of d.open) (m.dir === 'down' ? downs : ups).push([c, m]);
    }
    btn.textContent = downs.length ? `CPDLC ${downs.length}` : 'CPDLC';
    btn.classList.toggle('hit', downs.length > 0);
    if (!wantWin) return;
    if (!atcIsOpen('dlWin') && wantWin === 'shown') { wantWin = false; return; }   // closed with ✕ or Esc
    const own = (state && state.cpdlc_own) || '';
    const el = atcWin('dlWin', `CPDLC <span class="c-dim" style="font-weight:400">${nConn} connected${downs.length ? ' · ' + downs.length + ' request' + (downs.length > 1 ? 's' : '') : ''}</span>`, 80, 70, 470);
    const c = selected && all[selected] ? selected : null;
    const last = (state && state.cpdlc_last) || 0;
    if (c && (c !== logCs || last !== logLast)) { logCs = c; logLast = last; await loadLog(c); }
    const row = (cs_, m, btns) => `<div class="dl-r ${m.late ? 'late' : ''}" data-cs="${cs_}"><b>${cs_}</b> ${m.dir === 'down' ? '↓' : '↑'} ${esc(m.text)} <span class="c-dim">${age(m.t)}${m.status === 'standby' ? ' · STANDBY' : ''}${m.late ? ' · no answer' : ''}</span>${btns || ''}</div>`;
    const d = c ? all[c] : null;
    const html = `
      <div class="sub">Requests</div>${downs.map(([x, m]) => row(x, m, `<span class="dl-b"><button class="small primary" data-a="APP" data-cs="${x}" ${m.kind === 'text' ? 'disabled title="free text: ROGER, UNABLE or a clearance"' : ''}>Approve</button><button class="small" data-a="SBY" data-cs="${x}">Standby</button><button class="small" data-a="UNA" data-cs="${x}">Unable</button>${m.kind === 'text' ? `<button class="small" data-a="ROG" data-cs="${x}">Roger</button>` : ''}</span>`)).join('') || '<div class="c-dim">No request.</div>'}
      <div class="sub">Waiting for the pilot</div>${ups.map(([x, m]) => row(x, m)).join('') || '<div class="c-dim">Nothing waiting.</div>'}
      <div class="sub">${c ? esc(c) : 'Selected aircraft'}</div>
      ${!c ? '<div class="c-dim">Select an aircraft to see its connection and dialogue.</div>' : `
        <div class="dl-conn">${d.state === 'cda' ? `<span class="ok">connected</span> · current data authority <b>${esc(d.cda || '')}</b>${d.nda ? ' · next <b>' + esc(d.nda) + '</b>' : ''}` : d.state === 'logon' ? `logged on · current data authority <b>${esc(d.cda || 'previous unit')}</b>` : d.state === 'transferred' ? `transferred to <b>${esc(d.cda || '')}</b>` : 'equipped, not logged on yet'}</div>
        <div class="dl-log">${logMsgs.slice(-14).map(m => `<div class="dl-m ${m.dir}"><span class="c-dim">${esc(m.utc.slice(0, 5))}</span> ${m.dir === 'down' ? '↓' : '↑'} ${esc(m.text)} <span class="st-${m.status}">${m.resp === 'N' ? '' : m.status.toUpperCase()}</span></div>`).join('') || '<div class="c-dim">No message yet.</div>'}</div>
        <div class="c-dim" style="font-size:11px;margin-top:4px">Command line: NDA GCCC sets the next data authority, CT transfers it, EOS ends the service.</div>`}`;
    wantWin = 'shown';
    const wb = el.querySelector('.wb');
    if (wb.dataset.h !== html) { wb.innerHTML = html; wb.dataset.h = html; }
  }
  document.getElementById('wins').addEventListener('mousedown', async e => {
    const b = e.target.closest('#dlWin [data-a]');
    if (b) { e.preventDefault(); e.stopPropagation();
      try { await api(`/sessions/${SID}/cpdlc/command`, { method: 'POST', body: JSON.stringify({ text: b.dataset.a, callsign: b.dataset.cs }) }); if (typeof pollState === 'function') pollState(); }
      catch (err) { result = { html: `<div class="cb-err">${esc(err.message)}</div>`, until: performance.now() + 5000 }; inp.focus(); showPv(lastPv); }
      return; }
    const r = e.target.closest('#dlWin [data-cs]'); if (r) { selected = r.dataset.cs; if (typeof pollSelected === 'function') pollSelected(); }
  }, true);
  setInterval(renderWin, 1000);
  }
})();

// Coordination chat on the radar page. Every message shows its sender and its destination (stations: our sector,
// the other sectors, the adjacent units). The controller has a window (key T) and a toast for each new message; the
// pseudo-pilot and the instructor have a section in the side panel and write as another station.
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let info = null, msgs = [], last = 0, seen = 0, thread = null, ready = false;
  const isCtl = () => (SEAT === 'atc' && EXID) || !EXID;
  const SEEN_KEY = () => 'atc_chat_seen_' + (EXID || SID);
  const label = st => { const s = info && info.stations[st]; return s ? `${st}` : st; };
  const name = st => (info && info.stations[st] && info.stations[st].name) || st;
  const fmt = m => `<span class="ch-hd"><b>${esc(m.from)}</b> → <b>${esc(m.to)}</b> <span class="c-dim">${esc(m.utc.slice(0, 5))}</span>${m.callsign ? ` <span class="ch-cs">${esc(m.callsign)}</span>` : ''}</span>`;

  async function load() {
    if (!SID) return;
    try {
      const r = await api(`/sessions/${SID}/chat?since=${last}`);
      if (!info) { info = r; try { seen = +localStorage.getItem(SEEN_KEY()) || 0; } catch (e) {} }
      for (const m of r.messages) msgs.push(m);
      if (r.messages.length) {
        const fresh = r.messages.filter(m => m.id > last);
        last = msgs[msgs.length - 1].id;
        if (ready && isCtl()) for (const m of fresh) if (m.to === info.own) toast(m);
      }
      ready = true;
      render();
    } catch (e) {}
  }
  setInterval(() => { if (SID && state && (state.chat_last || 0) !== last) load(); else if (SID && !info) load(); }, 700);
  addEventListener('sim-rewind', () => { msgs = []; last = 0; ready = false; load(); });   // the chat is rewound with the simulation

  async function send(body, out) {
    try { const m = await api(`/sessions/${SID}/chat`, { method: 'POST', body: JSON.stringify(body) });
      if (out) { out.className = 'ch-out c-ok'; out.textContent = `✓ ${m.from} → ${m.to}`; }
      await load(); return m;
    } catch (e) { if (out) { out.className = 'ch-out c-los'; out.textContent = e.message; } }
  }

  // ------------------------------------------------------------------ the controller: toast, window
  function toast(m) {
    let box = document.getElementById('chatToast');
    if (!box) { box = document.createElement('div'); box.id = 'chatToast'; (document.getElementById('scope') || document.body).appendChild(box); }
    const el = document.createElement('div'); el.className = 'ch-toast';
    el.innerHTML = fmt(m) + `<div>${esc(m.text)}</div>`;
    el.onclick = () => { thread = m.from; openWin(); el.remove(); };
    box.appendChild(el); setTimeout(() => el.remove(), 9000);
  }
  let btn = null, wantWin = false;
  function setupCtl() {
    btn = document.createElement('button'); btn.id = 'chatBtn'; btn.textContent = 'CHAT';
    btn.title = 'Coordination chat with the other sectors and the adjacent units (T)';
    const help = document.getElementById('helpBtn'), host = document.getElementById('commsMenu'); if (host) host.appendChild(btn); else if (help) help.parentNode.insertBefore(btn, help);
    btn.onclick = () => wantWin && atcIsOpen('chatWin') ? closeWin() : openWin();
    const typing = () => { const a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT') && a.type !== 'checkbox'; };
    window.addEventListener('keydown', e => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing()) return;
      if (e.key === 't' || e.key === 'T') { e.preventDefault(); wantWin && atcIsOpen('chatWin') ? closeWin() : openWin(true); }
    });
  }
  function closeWin() { wantWin = false; atcCloseWin('chatWin'); }
  function openWin(focus) {
    wantWin = true;
    const el = atcWin('chatWin', 'Coordination chat', 120, 90, 520);
    if (!el.querySelector('.ch-wrap')) {
      el.querySelector('.wb').innerHTML = `<div class="ch-wrap"><div class="ch-threads"></div><div class="ch-main"><div class="ch-log"></div>
        <div class="ch-new"><div class="ch-row"><label>To <select data-to></select></label><label>about <input data-cs placeholder="callsign" style="width:84px"></label><span class="ch-out"></span></div>
        <div class="ch-row"><input data-tx placeholder="Type the message, Enter sends it" style="flex:1"><button class="primary small" data-send>Send</button></div></div></div></div>`;
      const to = el.querySelector('[data-to]'), tx = el.querySelector('[data-tx]'), csIn = el.querySelector('[data-cs]'), out = el.querySelector('.ch-out');
      const go = async () => { if (!tx.value.trim()) return; const m = await send({ to: to.value, text: tx.value, callsign: csIn.value || null }, out); if (m) { tx.value = ''; thread = m.to; render(); } };
      el.querySelector('[data-send]').onclick = go;
      tx.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') go(); if (e.key === 'Escape') tx.blur(); };
      csIn.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') tx.focus(); };
      to.onchange = () => { thread = to.value; render(); };
      el.querySelector('.ch-threads').addEventListener('mousedown', e => { const t = e.target.closest('[data-th]'); if (t) { e.preventDefault(); thread = t.dataset.th || null; render(); } });
      el.querySelector('.ch-log').addEventListener('mousedown', e => {
        const q = e.target.closest('[data-q]'); if (!q) return; e.preventDefault();
        const m = msgs.find(x => x.id === +q.dataset.ref); if (m) { thread = m.from; send({ to: m.from, text: q.dataset.q, ref: m.id, callsign: m.callsign }, out); }
      });
    }
    const csIn = el.querySelector('[data-cs]');
    if (selected && !csIn.value) csIn.value = selected;
    render();
    if (focus) setTimeout(() => el.querySelector('[data-tx]').focus(), 0);
  }
  function renderCtl() {
    if (!btn || !info) return;
    const unread = msgs.filter(m => m.id > seen && m.to === info.own).length;
    btn.textContent = unread ? `CHAT ${unread}` : 'CHAT'; btn.classList.toggle('hit', unread > 0);
    if (!wantWin || !atcIsOpen('chatWin')) { if (wantWin && !atcIsOpen('chatWin')) wantWin = false; return; }
    seen = last; try { localStorage.setItem(SEEN_KEY(), String(seen)); } catch (e) {}
    const el = document.getElementById('chatWin');
    const others = Object.keys(info.stations).filter(s => s !== info.own);
    const sel = el.querySelector('[data-to]');
    if (sel.options.length !== others.length) sel.innerHTML = others.map(s => `<option value="${esc(s)}">${esc(s)} · ${esc(name(s))}</option>`).join('');
    const lastIn = msgs.filter(m => m.to === info.own).pop();
    const dflt = thread && others.includes(thread) ? thread : lastIn ? lastIn.from : null;   // default: whoever wrote last
    if (dflt && document.activeElement !== sel) sel.value = dflt;
    const count = s => msgs.filter(m => m.from === s || m.to === s).length;
    el.querySelector('.ch-threads').innerHTML = `<div data-th="" class="${!thread ? 'on' : ''}">All <span class="c-dim">${msgs.length}</span></div>` +
      others.map(s => `<div data-th="${esc(s)}" class="${thread === s ? 'on' : ''}">${esc(s)} <span class="c-dim">${count(s) || ''}</span></div>`).join('');
    const list = msgs.filter(m => !thread || m.from === thread || m.to === thread).slice(-60);
    const answered = new Set(msgs.filter(m => m.ref).map(m => m.ref));
    const log = el.querySelector('.ch-log');
    const html = list.map(m => `<div class="ch-m ${m.from === info.own ? 'out' : 'in'}">${fmt(m)}<div class="ch-t">${esc(m.text)}</div>
      ${m.to === info.own && !m.ref && !answered.has(m.id) ? `<div class="ch-q">${info.quick.slice(0, 4).map(q => `<button class="small" data-q="${q}" data-ref="${m.id}">${q}</button>`).join('')}</div>` : ''}</div>`).join('')
      || `<div class="c-dim">No message yet. Messages show their sender and destination: you are <b>${esc(info.own)}</b>.</div>`;
    if (log.dataset.h !== html) { log.innerHTML = html; log.dataset.h = html; log.scrollTop = log.scrollHeight; }
  }

  // ------------------------------------------------------------------ pseudo-pilot / instructor: side panel
  function setupPilot() {
    const phone = document.getElementById('phoneSec'); if (!phone) return;
    const sec = document.createElement('section'); sec.id = 'chatSec'; sec.className = 'ex-only'; sec.dataset.tab = 'pilot';
    sec.innerHTML = `<h2>Coordination chat <span class="c-dim" style="text-transform:none;letter-spacing:0">write as another sector or unit</span></h2>
      <div class="ch-row"><label>As <select data-as></select></label><label>to <select data-to></select></label><label>about <input data-cs placeholder="callsign" style="width:80px"></label></div>
      <div class="ch-row"><input data-tx placeholder="e.g. REQUEST FL360 FOR RAM201" style="flex:1"><button class="primary small" data-send>Send</button></div>
      <div class="ch-out"></div><div class="ch-log ch-plog"></div>`;
    if (phone.classList.contains('tab-on')) sec.classList.add('tab-on');    // the side panel tabs (seat.js)
    phone.parentNode.insertBefore(sec, phone.nextSibling);
    const as = sec.querySelector('[data-as]'), to = sec.querySelector('[data-to]'), tx = sec.querySelector('[data-tx]'), csIn = sec.querySelector('[data-cs]'), out = sec.querySelector('.ch-out');
    const go = async () => { if (!tx.value.trim()) return; const m = await send({ from: as.value, to: to.value, text: tx.value, callsign: csIn.value || null }, out); if (m) tx.value = ''; };
    sec.querySelector('[data-send]').onclick = go;
    for (const i of [tx, csIn]) i.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') { if (i === tx) go(); else tx.focus(); } };
    let lastSel = null;                               // about: follows the selected aircraft until edited
    setInterval(() => { if (selected !== lastSel) { lastSel = selected; if (document.activeElement !== csIn && selected) csIn.value = selected; } }, 500);
    sec.querySelector('.ch-log').addEventListener('mousedown', e => {
      const q = e.target.closest('[data-q]'); if (!q) return; e.preventDefault();
      const m = msgs.find(x => x.id === +q.dataset.ref); if (m) send({ from: m.to, to: m.from, text: q.dataset.q, ref: m.id, callsign: m.callsign }, out);
    });
  }
  function renderPilot() {
    const sec = document.getElementById('chatSec'); if (!sec || !info) return;
    const all = Object.keys(info.stations), others = all.filter(s => s !== info.own);
    const as = sec.querySelector('[data-as]'), to = sec.querySelector('[data-to]');
    const asList = SEAT === 'instructor' ? all : others;
    if (as.options.length !== asList.length) { as.innerHTML = asList.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join(''); }
    if (to.options.length !== all.length) { to.innerHTML = all.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join(''); to.value = info.own; }
    const answered = new Set(msgs.filter(m => m.ref).map(m => m.ref));
    const html = msgs.slice(-30).reverse().map(m => `<div class="ch-m ${m.to === info.own ? 'out' : 'in'}">${fmt(m)}<div class="ch-t">${esc(m.text)}</div>
      ${m.from === info.own && !m.ref && !answered.has(m.id) ? `<div class="ch-q">${info.quick.slice(0, 4).map(q => `<button class="small" data-q="${q}" data-ref="${m.id}" title="answer as ${esc(m.to)}">${q}</button>`).join('')}</div>` : ''}</div>`).join('')
      || '<div class="c-dim">No message yet.</div>';
    const log = sec.querySelector('.ch-log');
    if (log.dataset.h !== html) { log.innerHTML = html; log.dataset.h = html; }
  }
  function render() { if (isCtl()) renderCtl(); else renderPilot(); }

  const wait = setInterval(() => {
    if (EXID && !EXINFO) return; clearInterval(wait);
    if (isCtl()) setupCtl(); else setupPilot();
    setInterval(render, 1000);
  }, 200);
})();

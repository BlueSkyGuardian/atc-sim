// Solo radar (and sandbox): the conflict solver's suggestions. For each predicted conflict with an assumed aircraft,
// the clearances that leave everyone separated - speed first, then a delaying vector or a direct, then a level - the
// aircraft with priority in its level left alone where possible; the best ones checked by running the simulation
// ahead. "Apply" enters the clearance in the label as the controller would; a delaying vector leaves a "Resume"
// reminder (a vector given with its limit ends by itself: the pilot proceeds as cleared). Uses the radar page's globals (state, SID, EXID, EXINFO, DBID, AIW, api, msg, selected, pollSelected).
(function () {
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const btn = $('#solveBtn'), box = $('#solvePanel');
  if (!btn || !box) return;
  const allowed = () => !DBID && !AIW && SID && (!EXID || (EXINFO && EXINFO.solo));
  let on = false; try { on = localStorage.getItem('atc_solver') === '1'; } catch (e) {}
  let data = null, busy = false, lastKey = '', err = '';
  const resumes = {};                         // callsign -> {at (sim s), heading}
  const KIND = { speed: 'SPEED', direct: 'DIRECT', heading: 'VECTOR', level: 'LEVEL', pair: 'TWO' };

  function setOn(v) {
    on = v; btn.classList.toggle('on', on); box.hidden = !on;
    try { localStorage.setItem('atc_solver', on ? '1' : '0'); } catch (e) {}
    if (on) { lastKey = ''; poll(); }
  }
  btn.onclick = () => setOn(!on);

  async function poll() {
    document.body.classList.toggle('solver-ok', !!allowed());
    if (!on || busy || !allowed() || !state) { render(); return; }
    const key = `${Math.floor(state.sim_time_s / 5)}|${(state.session || {}).rewinds || 0}`;
    if (key === lastKey) { render(); return; }
    busy = true;
    try { data = await api(`/sessions/${SID}/solve`); err = ''; lastKey = key; }
    catch (e) { err = e.message; }
    busy = false; render();
  }

  const mmss = s => s == null ? '' : `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.round(s) % 60).padStart(2, '0')}`;
  let lastH = '';
  function render() {
    if (!on) return;
    const now = state ? state.sim_time_s : 0;
    const rs = Object.entries(resumes).map(([cs, r]) => {
      const due = r.at - now;
      return `<div class="sv-res ${due <= 0 ? 'due' : ''}"><b>${esc(cs)}</b> on heading ${String(r.heading).padStart(3, '0')} — resume ${due <= 0 ? 'now' : 'in ' + mmss(due)}
        <span class="rb"><button data-resume="${esc(cs)}">Resume</button><button data-forget="${esc(cs)}" title="forget this reminder">×</button></span></div>`;
    }).join('');
    let body;
    if (!allowed()) body = '<div class="c-dim">The solver is on the solo radar and in the sandbox.</div>';
    else if (err) body = `<div class="c-los">${esc(err)}</div>`;
    else if (!data) body = '<div class="c-dim">computing…</div>';
    else if (!data.conflicts.length) body = (data.requests || []).length ? '' : `<div class="c-dim">No predicted conflict with your traffic in the next ${Math.round(data.horizon_s / 60)} min.</div>`;
    else body = data.conflicts.map((c, ci) => {
      const [a, b] = c.pair;
      const prio = c.move ? `<div class="sv-prio">${esc(c.priority || '')}</div>` : '';
      const noTurn = Object.entries(c.turn_enough || {}).filter(([, ok]) => !ok).map(([m]) => `<div class="sv-warn">${esc(m)}: too close for a turn to separate it in time</div>`).join('');
      const opt = new Set(c.optimal);
      const sols = c.solutions.slice(0, 6).map((s, si) => `<div class="sv-sol ${opt.has(s.text) ? 'best' : ''}">
          <span class="sv-k k-${esc(s.kind)}">${KIND[s.kind] || esc(s.kind)}</span>
          <span class="sv-t">${esc(s.text)}${s.needs_ask ? ' <span class="sv-ask" title="the pilot did not ask for this level: ask if it is acceptable first">ask first</span>' : ''}${s.priority_moved ? ' <span class="sv-ask" title="moves the aircraft that has priority in its level">priority</span>' : ''}</span>
          <span class="sv-d" title="${s.confirmed ? 'checked by running the simulation ahead: closest distance afterwards' : 'predicted closest distance afterwards'}">${s.confirmed ? '✓ ' + s.confirmed_min_nm : (s.d_min != null ? s.d_min : '—')} NM</span>
          <button data-apply="${ci}:${si}">Apply</button></div>`).join('')
        || '<div class="sv-warn">No single clearance (or pair) keeps everyone separated: combine, or act on the other traffic.</div>';
      const head = c.kind === 'exit'
        ? `EXIT ${esc(c.exit)}: <a data-sel="${esc(b)}">${esc(b)}</a> behind <a data-sel="${esc(a)}">${esc(a)}</a>
           <span class="c-dim">${c.d_min_nm} NM at the exit (LoA ${c.required_nm} NM) in ${mmss(c.t_first_s)}</span>`
        : `<a data-sel="${esc(a)}">${esc(a)}</a> × <a data-sel="${esc(b)}">${esc(b)}</a>
           <span class="c-dim">${c.d_min_nm} NM in ${mmss(c.t_first_s)}</span>`;
      return `<div class="sv-c ${c.kind === 'exit' ? 'exit' : ''}"><div class="sv-h">${head}</div>${prio}${noTurn}${sols}</div>`;
    }).join('');
    const rq = (data && data.requests || []).map((q, qi) => `<div class="sv-c req"><div class="sv-h">REQUEST ${esc(q.request_id)} <a data-sel="${esc(q.callsign)}">${esc(q.callsign)}</a> <span class="c-dim">${esc(q.text)}</span></div>
        <div class="sv-sol ${q.possible ? 'best' : ''}"><span class="sv-k k-${q.as_asked ? 'speed' : q.possible ? 'heading' : 'level'}">${q.as_asked ? 'AS ASKED' : q.possible ? 'WITH' : 'NOT NOW'}</span>
        <span class="sv-t">${esc(q.as_asked ? 'separated as asked: approve' : q.how)}</span><span></span>${q.possible ? `<button data-req="${qi}">Apply</button>` : '<span></span>'}</div></div>`).join('');
    body = rq + body;
    const html = `<div class="hd">SOLVER <span class="c-dim">${data ? 'T+' + mmss(data.sim_time_s) : ''}${busy ? ' …' : ''}</span></div>${rs}${body}`;
    if (html !== lastH) { box.innerHTML = html; lastH = html; }
  }

  async function enter(body) {
    const rr = await api(`/sessions/${SID}/entries`, { method: 'POST', body: JSON.stringify({ ...body, ...(window.FORCE_CLEARANCE ? { force: true } : {}) }) });
    if (window.noteRefused) window.noteRefused(body.callsign, 'entries', body, rr && rr._readback);
    const bad = (rr && rr._readback || []).find(r => r.status === 'rejected');
    if (bad) throw new Error(bad.text);
  }
  box.addEventListener('mousedown', async e => {
    const sel = e.target.closest('[data-sel]');
    if (sel) { e.preventDefault(); selected = sel.dataset.sel; if (typeof pollSelected === 'function') pollSelected(); return; }
    const fg = e.target.closest('[data-forget]');
    if (fg) { e.preventDefault(); delete resumes[fg.dataset.forget]; lastH = ''; render(); return; }
    const rsm = e.target.closest('[data-resume]');
    if (rsm) {
      e.preventDefault();
      const cs = rsm.dataset.resume;
      try { await enter({ callsign: cs, heading: '' }); delete resumes[cs]; msg(`${cs}: resume own navigation`); lastKey = ''; poll(); }
      catch (er) { msg(er.message, true); }
      return;
    }
    const rqb = e.target.closest('[data-req]');
    if (rqb && data) {
      e.preventDefault();
      const q = (data.requests || [])[+rqb.dataset.req]; if (!q) return;
      try {
        for (const a of q.actions) {
          const { type, limit, ...f } = a;
          if (f.heading != null) { f.direct = ''; if (limit) f.hlim = limit; }
          if (f.speed != null && limit) f.slim = limit;
          await enter(f);
        }
        msg(`✓ ${q.request_id}: ${q.as_asked ? 'approved' : q.how}`); lastKey = ''; data = null; setTimeout(poll, 800);
      } catch (er) { msg(er.message, true); }
      return;
    }
    const ap = e.target.closest('[data-apply]'); if (!ap || !data) return;
    e.preventDefault();
    const [ci, si] = ap.dataset.apply.split(':').map(Number);
    const s = data.conflicts[ci] && data.conflicts[ci].solutions[si]; if (!s) return;
    if (s.needs_ask && !confirm(`${s.text}: the pilot has not asked for this level. Has the pilot said it is acceptable?`)) return;
    try {
      for (const a of s.actions) {
        const { type, limit, ...f } = a;
        if (f.heading != null) { f.direct = ''; if (limit) f.hlim = limit; }
        if (f.speed != null && limit) f.slim = limit;
        await enter(f);
        if (f.heading != null && !limit) {
          const prm = s.kind === 'pair' ? (s.params[a.callsign] || {}) : s.params;
          if (prm.for_s) resumes[a.callsign] = { at: state.sim_time_s + prm.for_s, heading: f.heading };
        }
      }
      msg(`✓ ${s.text}`);
      lastKey = ''; data = null; setTimeout(poll, 800);
    } catch (er) { msg(er.message, true); }
  });
  window.addEventListener('sim-rewind', () => { for (const k in resumes) delete resumes[k]; lastKey = ''; data = null; });
  setOn(on);
  setInterval(poll, 1000);
})();

// Watch an AI run live (/ai/watch/<run id>): the radar shows the world the headless run publishes while somebody
// watches (read-only), paced at the speed chosen here; the panel shows the model's latest decisions and whether it
// is thinking. Uses the radar page's globals (state, SID, SEAT, EXINFO, rec, recOffline, attach, api, fmtT...).
(function () {
  if (!AIW) return;
  const h = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = s => document.querySelector(s);
  let factor = 4, info = null, lastPanel = '', lastBar = '';
  try { const f = localStorage.getItem('aiw_factor'); if (f != null) factor = +f; } catch (e) {}

  async function init() {
    document.body.classList.add('exercise', 'seat-atc', 'solo', 'debrief', 'db-ro', 'aiw');
    SEAT = 'atc';
    EXINFO = { solo: true, debrief: true, ai: true, name: 'AI run' };
    $('#scope').insertAdjacentHTML('beforeend', `<div id="dbBar"></div><div id="aiwPanel"></div><div id="dbMsg"></div>`);
    $('#aiwPanel').addEventListener('click', e => {
      const b = e.target.closest('[data-f]'); if (!b) return;
      factor = +b.dataset.f; try { localStorage.setItem('aiw_factor', String(factor)); } catch (e2) {}
      poll();
    });
    const tlc = $('#tl');                                   // the time bar only shows where the run is
    ['mousedown', 'wheel'].forEach(ev => tlc.addEventListener(ev, e => { e.stopImmediatePropagation(); e.preventDefault(); }, { capture: true, passive: false }));
    resize();
    await poll();
    setInterval(poll, 2500);
    setInterval(render, 500);
  }
  async function poll() {
    try { info = await api(`/api/ai/runs/${AIW}/watch`, { method: 'POST', body: JSON.stringify({ factor }) }); }
    catch (e) { const m = $('#dbMsg'); if (m) { m.innerHTML = h(e.message); m.style.display = 'block'; } return; }
    Object.assign(EXINFO, { name: info.name });
    document.title = `Live · ${info.name}`;
    if (SID !== info.sid) { await attach(info.sid); }
    recOffline = true;
    const dur = (state && state.duration_s) || (info.progress && info.progress.duration) || 2700;
    rec = { frames: [{ t: 0, ac: [], req: [], los: [], areas: [] }, { t: dur, ac: [], req: [], los: [], areas: [] }], events: [], meta: {}, duration_s: dur };
    render();
  }
  function render() {
    if (!info) return;
    const p = info.progress || {}, st = info.status;
    const running = st === 'running' || st === 'queued';
    const pill = st === 'queued' ? '<span class="db-pill ready">QUEUED</span>' : running ? '<span class="db-pill live">● LIVE</span>'
      : `<span class="db-pill">${h(st.toUpperCase())}${info.score != null ? ' · score ' + info.score : ''}</span>`;
    const links = (running ? '' : `<a href="/debrief/${info.exercise_id}">Replay in the debrief</a>`) + `<a href="/ai">AI lab</a>`;
    const bar = `<div class="db-row"><b>AI run</b><span class="db-name">${h(info.name)}</span>${pill}<span class="c-dim" style="font-size:11.5px">look only · the model controls</span><span class="db-links">${links}</span></div>`;
    if (bar !== lastBar) { $('#dbBar').innerHTML = bar; lastBar = bar; }
    const thinking = running && p.thinking;
    const t = state ? state.sim_time_s : p.t || 0;
    const speeds = running ? `<div class="aiw-sp"><span class="c-dim">Speed while you watch</span><span class="sb-seg">${(info.factors || [0, 1, 2, 4, 8, 16]).map(f =>
      `<button data-f="${f}" class="${+f === +factor ? 'on' : ''}" title="${f ? f + ' simulated seconds per second' : 'as fast as the model answers (the picture jumps)'}">${f ? '×' + f : 'Full'}</button>`).join('')}</span></div>` : '';
    const status = st === 'queued' ? 'Waiting for its turn in the queue…'
      : !running ? `Run ${h(st)}. ${info.live ? '' : 'It ran before anybody watched: '}<a href="/debrief/${info.exercise_id}">replay it in the debrief</a>.`
      : !info.live ? 'Starting… the picture appears at the next second.'
      : thinking ? `<span class="aiw-think">● the model is ${p.querying ? 'asking the simulator (round ' + p.querying + ')' : 'deciding'}</span> <span class="c-dim">(the simulation waits)</span>` : 'Flying…';
    const steps = (info.steps || []).slice().reverse().map(s => {
      const res = (s.results || []).map(r => `<div class="${r.ok ? 'ok' : 'ko'}">${r.ok ? '✓' : '✗'} ${h(r.text)}</div>`).join('');
      const err = s.error ? `<div class="ko">no answer: ${h(s.error)}</div>` : s.parse_error ? `<div class="ko">unreadable answer: ${h(s.parse_error)}</div>` : '';
      const qs = (s.queries || []).map(q => q.kind === 'correction' ? '<div class="ko">↺ refused, sent back for a correction</div>'
        : (q.results || []).map(x => `<div class="c-dim" style="font-size:11.5px">? ${h(x.result)}</div>`).join('')).join('');
      return `<div class="aiw-st"><div class="hd"><b class="mono">T+${fmtT(s.t)}</b><span class="c-dim">${s.latency_s != null ? s.latency_s + ' s' : ''}</span>${s.label ? `<span class="lb ${h(s.label)}">${h(s.label)} ${s.reward > 0 ? '+' : ''}${s.reward}</span>` : ''}</div>` +
        qs + (s.reasoning ? `<div class="rs">${h(s.reasoning)}</div>` : '') + (res || (!err ? '<div class="c-dim">no instruction</div>' : '')) + err + `</div>`;
    }).join('') || '<div class="c-dim">No decision yet.</div>';
    const html = `<div class="hd2"><b>The model</b><span class="mono">T+${fmtT(t)} / ${fmtT((state && state.duration_s) || p.duration || 0)} · ${p.decisions || 0} decisions</span></div>
      <div class="aiw-status">${status}</div>${speeds}<div class="aiw-list">${steps}</div>`;
    if (html !== lastPanel) { $('#aiwPanel').innerHTML = html; lastPanel = html; }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

// Voice for a connected exercise: radio (push-to-talk) and telephone lines, over /sessions/{id}/comms.
// Debriefs (opts.open, over /debriefs/{id}/comms): an open call - the microphone is live unless muted - that also
// carries the debrief's sync (onSync) and everybody's cursor (onCursor).
// Audio is 16 kHz mono G.711 mu-law. The microphone is resampled in an AudioWorklet; every speaker gets its own
// little jitter buffer on playback.
(function () {
  const RATE = 16000, FRAME = 640;              // 40 ms per frame
  // capture: resample the context's rate to 16 kHz (linear, continuous across the 128-sample blocks)
  const WORKLET = `
    class Cap extends AudioWorkletProcessor {
      constructor() { super(); this.r = sampleRate / ${RATE}; this.p = 0; this.prev = 0; this.buf = new Int16Array(${FRAME}); this.n = 0; this.on = false;
        this.port.onmessage = e => { this.on = e.data; if (!this.on) this.n = 0; }; }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0]; if (!ch) return true;
        if (!this.on) { this.p = 0; this.prev = ch[ch.length - 1]; return true; }
        // this.p: position in this block, -1 = the last sample of the previous block
        for (; this.p < ch.length - 1; this.p += this.r) {
          const i = Math.floor(this.p), f = this.p - i, a = i < 0 ? this.prev : ch[i], b = ch[i + 1];
          let v = a + (b - a) * f; v = Math.max(-1, Math.min(1, v));
          this.buf[this.n++] = v * 32767;
          if (this.n === ${FRAME}) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
        }
        this.p -= ch.length; this.prev = ch[ch.length - 1];
        return true;
      }
    }
    try { registerProcessor('cap', Cap); } catch (e) {}`;
  function lin2ulaw(s) {
    let sign = (s >> 8) & 0x80; if (sign) s = -s; if (s > 32635) s = 32635; s += 0x84;
    let exp = 7; for (let m = 0x4000; (s & m) === 0 && exp > 0; exp--, m >>= 1);
    return ~(sign | (exp << 4) | ((s >> (exp + 3)) & 0x0F)) & 0xFF;
  }
  const ULAW = new Float32Array(256);
  for (let i = 0; i < 256; i++) { const u = ~i & 0xFF, sign = u & 0x80, exp = (u >> 4) & 7, man = u & 0x0F;
    const mag = (((man << 3) + 0x84) << exp) - 0x84; ULAW[i] = (sign ? -mag : mag) / 32768; }

  class Voice {
    constructor(sid, h, opts) {
      this.opts = opts || {}; this.open = !!this.opts.open; this.muted = false; this.heard = {};
      this.sid = sid; this.h = h || {}; this.ws = null; this.me = null; this.calls = []; this.roster = [];
      this.tx = false; this.ctx = null; this.mic = null; this.node = null; this.next = {}; this.micError = null;
      this.ringing = null; this.closed = false;
      this.connect();
      const unlock = () => { this.audio(); };
      window.addEventListener('pointerdown', unlock, { once: false }); window.addEventListener('keydown', unlock, { once: false });
    }
    connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = this.ws = new WebSocket(`${proto}://${location.host}${this.opts.url || `/sessions/${this.sid}/comms`}`);
      ws.binaryType = 'arraybuffer';
      ws.onmessage = ev => {
        if (typeof ev.data !== 'string') { this.play(ev.data); return; }
        const m = JSON.parse(ev.data);
        if (m.type === 'sync') { this.h.onSync && this.h.onSync(m.s); return; }
        if (m.type === 'cursor') { this.h.onCursor && this.h.onCursor(m); return; }
        if (m.type === 'hello') { if (this.open && this.muted) this.send({ op: 'mute', on: true }); this.me = m.me; this.roster = m.roster; this.calls = m.calls; this.h.onHello && this.h.onHello(m); this.h.onCalls && this.h.onCalls(this.calls); }
        else if (m.type === 'roster') { this.roster = m.roster; this.h.onRoster && this.h.onRoster(m.roster); }
        else if (m.type === 'ptt') { const r = this.roster.find(x => x.conn === m.conn); if (r) r.ptt = m.on; this.h.onPtt && this.h.onPtt(m); }
        else if (m.type === 'calls') { this.calls = m.calls; this.h.onCalls && this.h.onCalls(m.calls); this.updateMic(); }
        else if (m.type === 'closed') { this.closed = true; this.h.onClosed && this.h.onClosed(); }
        else if (m.type === 'error') { this.h.onError && this.h.onError(m.error); }
      };
      ws.onclose = ev => { if (this.closed || ev.code === 4403) { this.h.onClosed && this.h.onClosed(); return; } setTimeout(() => this.connect(), 2000); };
    }
    send(o) { if (this.ws && this.ws.readyState === 1) this.ws.send(typeof o === 'string' || o instanceof ArrayBuffer || ArrayBuffer.isView(o) ? o : JSON.stringify(o)); }
    audio() {
      if (!this.ctx) {
        try { this.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; }
        this.out = this.ctx.createGain(); this.out.gain.value = 1; this.out.connect(this.ctx.destination);
        // radio and telephone sound: band-limited
        const mk = (lo, hi) => { const a = this.ctx.createBiquadFilter(); a.type = 'highpass'; a.frequency.value = lo;
          const b = this.ctx.createBiquadFilter(); b.type = 'lowpass'; b.frequency.value = hi; a.connect(b); b.connect(this.out); return a; };
        this.radioIn = mk(250, 3600); this.phoneIn = mk(300, 3400);
      }
      if (this.ctx.state === 'suspended') this.ctx.resume().then(() => this.h.onSound && this.h.onSound(true)).catch(() => {});
      if (!this._sw) { this._sw = true; this.ctx.onstatechange = () => this.h.onSound && this.h.onSound(this.ctx.state === 'running'); }
      return this.ctx;
    }
    // The microphone is opened once per page: one stream, one capture node, whatever asks for it (the seat at its
    // start, push-to-talk, a call) and however many times; a failed attempt can be tried again.
    ensureMic() {
      if (this.node) return Promise.resolve(true);
      if (this._micP) return this._micP;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { this.micError = 'the microphone needs an HTTPS connection'; this.h.onError && this.h.onError(this.micError); return Promise.resolve(false); }
      const ctx = this.audio(); if (!ctx) return Promise.resolve(false);
      this._micP = (async () => {
        let stream = null;
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
          if (!this._wk) {
            const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
            this._wk = ctx.audioWorklet.addModule(url).catch(e => { this._wk = null; throw e; });
          }
          await this._wk;
          this.stream = stream;
          this.mic = ctx.createMediaStreamSource(stream);
          this.node = new AudioWorkletNode(ctx, 'cap');
          this.node.port.onmessage = e => this.frame(e.data);
          const sink = ctx.createGain(); sink.gain.value = 0;
          this.mic.connect(this.node); this.node.connect(sink); sink.connect(ctx.destination);
          this.micError = null;
          if (ctx.state === 'suspended') ctx.resume().catch(() => {});   // a page with a live microphone may play sound
          this.updateMic(); this.h.onMic && this.h.onMic(true);
          return true;
        } catch (e) {
          if (stream) stream.getTracks().forEach(t => t.stop());
          this.micError = e && e.name === 'NotAllowedError' ? 'microphone blocked: allow it in the address bar' : 'microphone: ' + ((e && (e.message || e.name)) || e);
          this.h.onError && this.h.onError(this.micError); this.h.onMic && this.h.onMic(false);
          return false;
        } finally { this._micP = null; }
      })();
      return this._micP;
    }
    // sound needs a click or a key on the page first (the browser's rule), unless the microphone is already live
    soundBlocked() { return !this.ctx || this.ctx.state !== 'running'; }
    myCall() { return this.me && this.calls.find(c => c.state === 'active' && ((c.caller && c.caller.conn === this.me.conn) || (c.answerer && c.answerer.conn === this.me.conn))); }
    live() { return this.open ? !this.muted : (this.tx || !!this.myCall()); }
    mute(on) { this.muted = !!on; this.send({ op: 'mute', on: this.muted }); this.updateMic(); }
    speaking(conn) { return this.ctx && (this.heard[conn] || 0) > this.ctx.currentTime - 0.35; }
    updateMic() {
      const on = this.live();
      if (on && !this.node) { this.ensureMic(); return; }            // (updates again once it is open)
      if (this.node) this.node.port.postMessage(on);
    }
    frame(pcm) {
      // after push-to-talk is released, the frames the microphone had already captured still go (the transmission's
      // last syllable): the frequency is released 150 ms later
      if (!this.live() && !(this._offT && performance.now() < this._tail)) return;
      const u = new Uint8Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) u[i] = lin2ulaw(pcm[i]);
      this.send(u.buffer);
    }
    async ptt(on) {
      if (on === this.tx) return;
      this.tx = on;
      if (on) {
        this.audio();
        if (this._offT) { clearTimeout(this._offT); this._offT = null; }      // pressed again within the tail: still on
        else this.send({ op: 'ptt', on: true });
      } else {
        this._tail = performance.now() + 150;
        this._offT = setTimeout(() => { this._offT = null; if (!this.tx) this.send({ op: 'ptt', on: false }); }, 150);
      }
      this.updateMic();
      this.h.onTx && this.h.onTx(on);
    }
    call(unit) { this.audio(); this.ensureMic(); this.send({ op: 'call', unit }); }
    answer(id) { this.audio(); this.ensureMic(); this.send({ op: 'answer', call: id }); }
    hangup(id) { this.send({ op: 'hangup', call: id }); }
    // Playback: each speaker's frames are queued back to back behind a jitter buffer of 150 ms; a frame that comes
    // too late (the stream ran dry) restarts it, and every restart lengthens that speaker's buffer (up to 400 ms),
    // so a connection that stutters gets a little more delay instead of a choppy voice.
    // The delay is bounded: after a burst (the server or the network held the frames, then let them all go) the
    // voice would otherwise stay that much late - and an open call (the debrief) never pauses to catch up. Behind
    // the buffer by more than 50 ms it plays 8 % faster until it is back; by more than 100 ms quiet frames are
    // skipped; by more than 250 ms, any frame.
    play(buf) {
      const ctx = this.ctx; if (!ctx || ctx.state !== 'running') return;
      const dv = new DataView(buf), chan = dv.getUint8(0), conn = dv.getUint16(1, true), n = buf.byteLength - 3;
      if (n <= 0) return;
      this.rxFrames = (this.rxFrames || 0) + 1;
      const key = chan * 65536 + conn, now = ctx.currentTime;
      this.jb = this.jb || {};
      let t = this.next[key] || 0, jb = this.jb[key] || 0.15;
      const u = new Uint8Array(buf, 3);
      const over = t - (now + jb);
      if (over > 0.1) {                                              // behind: catch up
        let e = 0; for (let i = 0; i < n; i += 4) { const v = ULAW[u[i]]; e += v * v; }
        if (over > 0.25 || e / (n / 4) < 4e-4) { this.skipped = (this.skipped || 0) + 1; return; }
      }
      const ab = ctx.createBuffer(1, n, RATE), d = ab.getChannelData(0);
      for (let i = 0; i < n; i++) d[i] = ULAW[u[i]];
      if (t < now + 0.005) {                                         // ran dry (or the first frame): (re)start
        if (t) { jb = Math.min(0.4, jb + 0.05); this.underruns = (this.underruns || 0) + 1; }
        t = now + jb;
      }
      this.jb[key] = jb;
      const rate = t - (now + jb) > 0.05 ? 1.08 : 1, dur = ab.duration / rate;
      const src = ctx.createBufferSource(); src.buffer = ab; src.playbackRate.value = rate;
      src.connect(chan === 0 ? this.radioIn : chan === 1 ? this.phoneIn : this.out); src.start(t);
      this.heard[conn] = t + dur;
      this.next[key] = t + dur;
    }
    // ring (incoming) and ring-back (outgoing) tones, 425 Hz
    tone(kind) {
      if (kind === this.ringing) return;
      if (this.toneTimer) { clearInterval(this.toneTimer); this.toneTimer = null; }
      this.ringing = kind; if (!kind) return;
      const ctx = this.audio(); if (!ctx) return;
      const burst = () => {
        const o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime;
        o.frequency.value = kind === 'ring' ? 425 : 425; g.gain.setValueAtTime(0, t);
        const lvl = kind === 'ring' ? 0.12 : 0.05;
        if (kind === 'ring') { for (const s of [0, 0.4]) { g.gain.setValueAtTime(lvl, t + s); g.gain.setValueAtTime(0, t + s + 0.3); } }
        else { g.gain.setValueAtTime(lvl, t); g.gain.setValueAtTime(0, t + 1); }
        o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 1.1);
      };
      burst(); this.toneTimer = setInterval(burst, kind === 'ring' ? 2500 : 4000);
    }
  }
  window.Voice = Voice;
})();

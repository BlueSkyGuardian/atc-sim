// Voice for a connected exercise: radio (push-to-talk) and telephone lines, over /sessions/{id}/comms.
// Debriefs (opts.open, over /debriefs/{id}/comms): an open call - the microphone is live unless muted - that also
// carries the debrief's sync (onSync) and everybody's cursor (onCursor).
// Audio is 16 kHz mono G.711 mu-law. The microphone is resampled in an AudioWorklet; every speaker gets its own
// little jitter buffer on playback.
(function () {
  const RATE = 16000, FRAME = 640;              // 40 ms per frame
  const WORKLET = `
    class Cap extends AudioWorkletProcessor {
      constructor() { super(); this.r = sampleRate / ${RATE}; this.p = 0; this.buf = new Int16Array(${FRAME}); this.n = 0; this.on = false;
        this.port.onmessage = e => { this.on = e.data; if (!this.on) this.n = 0; }; }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0]; if (!ch) return true;
        if (!this.on) { this.p = 0; return true; }
        for (; this.p < ch.length; this.p += this.r) {
          const i = Math.floor(this.p), f = this.p - i, a = ch[i], b = i + 1 < ch.length ? ch[i + 1] : a;
          let v = a + (b - a) * f; v = Math.max(-1, Math.min(1, v));
          this.buf[this.n++] = v * 32767;
          if (this.n === ${FRAME}) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
        }
        this.p -= ch.length;
        return true;
      }
    }
    registerProcessor('cap', Cap);`;
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
      if (this.ctx.state === 'suspended') this.ctx.resume();
      return this.ctx;
    }
    async ensureMic() {
      if (this.node) return true;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { this.micError = 'the microphone needs an HTTPS connection'; this.h.onError && this.h.onError(this.micError); return false; }
      const ctx = this.audio(); if (!ctx) return false;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
        const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
        await ctx.audioWorklet.addModule(url);
        this.mic = ctx.createMediaStreamSource(stream);
        this.node = new AudioWorkletNode(ctx, 'cap');
        this.node.port.onmessage = e => this.frame(e.data);
        const sink = ctx.createGain(); sink.gain.value = 0;
        this.mic.connect(this.node); this.node.connect(sink); sink.connect(ctx.destination);
        this.micError = null; this.updateMic(); return true;
      } catch (e) { this.micError = 'microphone: ' + (e.message || e.name); this.h.onError && this.h.onError(this.micError); return false; }
    }
    myCall() { return this.me && this.calls.find(c => c.state === 'active' && ((c.caller && c.caller.conn === this.me.conn) || (c.answerer && c.answerer.conn === this.me.conn))); }
    live() { return this.open ? !this.muted : (this.tx || !!this.myCall()); }
    mute(on) { this.muted = !!on; this.send({ op: 'mute', on: this.muted }); this.updateMic(); }
    speaking(conn) { return this.ctx && (this.heard[conn] || 0) > this.ctx.currentTime - 0.35; }
    updateMic() {
      const on = this.live();
      if (on && !this.node) { this.ensureMic(); return; }
      if (this.node) this.node.port.postMessage(on);
    }
    frame(pcm) {
      if (!this.live()) return;
      const u = new Uint8Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) u[i] = lin2ulaw(pcm[i]);
      this.send(u.buffer);
    }
    async ptt(on) {
      if (on === this.tx) return;
      this.tx = on;
      if (on) this.audio();
      this.send({ op: 'ptt', on });
      this.updateMic();
      this.h.onTx && this.h.onTx(on);
    }
    call(unit) { this.audio(); this.ensureMic(); this.send({ op: 'call', unit }); }
    answer(id) { this.audio(); this.ensureMic(); this.send({ op: 'answer', call: id }); }
    hangup(id) { this.send({ op: 'hangup', call: id }); }
    play(buf) {
      const ctx = this.ctx; if (!ctx || ctx.state !== 'running') return;
      const dv = new DataView(buf), chan = dv.getUint8(0), conn = dv.getUint16(1, true), n = buf.byteLength - 3;
      if (n <= 0) return;
      this.rxFrames = (this.rxFrames || 0) + 1;
      const u = new Uint8Array(buf, 3), ab = ctx.createBuffer(1, n, RATE), d = ab.getChannelData(0);
      for (let i = 0; i < n; i++) d[i] = ULAW[u[i]];
      const key = chan * 65536 + conn, now = ctx.currentTime;
      let t = this.next[key] || 0;
      if (t < now + 0.03 || t > now + 0.8) t = now + 0.12;          // (re)start with a 120 ms jitter buffer
      const src = ctx.createBufferSource(); src.buffer = ab; src.connect(chan === 0 ? this.radioIn : chan === 1 ? this.phoneIn : this.out); src.start(t);
      this.heard[conn] = t + ab.duration;
      this.next[key] = t + ab.duration;
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

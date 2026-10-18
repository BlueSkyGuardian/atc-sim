// Sign-in page background: the Agadir CTA in 3D (outline and its FL195–FL460 volume, sector lines, airways, RAD
// routings, points), with traffic flying the published routes at semicircular levels and a few storm echoes.
// The camera orbits slowly; with "reduce motion" a single still frame is drawn.
import * as THREE from '/static/vendor/three.module.min.js';

const C = { chart: 0x6fa8dc, magenta: 0xd1478f, dim: 0x3c5a78, paper: 0xe9eef2, stalk: 0x7fc4e8, land: 0x15293d, coast: 0x4a7aa3 };
const FL_SCALE = 0.25;                 // world units per flight level (vertical exaggeration ~15x)
const SPEED = 450 / 3600 * 55;         // NM per second of animation: 450 kt, 55x time compression
const canvas = document.getElementById('scene');
// 'landing' (sign-in page) or 'ambient' (behind the dashboards: no labels, fewer aircraft, slower, centred)
const AMBIENT = canvas.dataset.mode === 'ambient';
let reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
renderer.setPixelRatio(AMBIENT ? 1 : Math.min(devicePixelRatio || 1, 1.75));   // behind the dashboards: light on the GPU
renderer.setClearColor(0x0d1b2a, 1);
// software WebGL (no GPU: SwiftShader, llvmpipe): behind the dashboards, one still frame instead of an animation
try {
  const gl = renderer.getContext(), ext = gl.getExtension('WEBGL_debug_renderer_info');
  const name = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : '';
  if (AMBIENT && /swiftshader|llvmpipe|software/i.test(name)) reduce = true;
} catch (e) { /* keep animating */ }
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x0d1b2a, 520, 1200);
const camera = new THREE.PerspectiveCamera(38, 1, 1, 3000);

function textSprite(text, { size = 26, color = '#e9eef2', alpha = 0.9, weight = 600, width = 22 } = {}) {
  const c = document.createElement('canvas'), g = c.getContext('2d');
  const font = `${weight} ${size}px Archivo, system-ui, sans-serif`;
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 8, h = size + 10;
  c.width = w; c.height = h;
  g.font = font; g.fillStyle = color; g.globalAlpha = alpha; g.textBaseline = 'middle'; g.fillText(text, 4, h / 2);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.minFilter = THREE.LinearFilter;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, fog: true }));
  s.scale.set(width, width * h / w, 1);
  return s;
}
function radialTexture(stops) {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'), gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  for (const [o, col] of stops) gr.addColorStop(o, col);
  g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

let geo = null, center = [0, 0];
const W = (p, fl = 0) => new THREE.Vector3(p[0] - center[0], fl * FL_SCALE, -(p[1] - center[1]));

function line(points, material) { const g = new THREE.BufferGeometry().setFromPoints(points); const l = new THREE.Line(g, material); l.computeLineDistances(); return l; }

function buildMap() {
  const xs = geo.outline.map(p => p[0]), ys = geo.outline.map(p => p[1]);
  center = [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
  // land and coastlines on the ground (Morocco, Iberia, the Canaries, Madeira)
  if (geo.coast) {
    const landM = new THREE.MeshBasicMaterial({ color: C.land, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false });
    for (const ring of geo.coast.land) {
      const sh = new THREE.Shape(ring.map(p => new THREE.Vector2(p[0] - center[0], p[1] - center[1])));
      const m = new THREE.Mesh(new THREE.ShapeGeometry(sh), landM); m.rotation.x = -Math.PI / 2; m.position.y = -0.6; m.renderOrder = -2; scene.add(m);
    }
    const coastM = new THREE.LineBasicMaterial({ color: C.coast, transparent: true, opacity: 0.75 });
    for (const ln of geo.coast.lines) scene.add(line(ln.map(p => W(p, -0.2)), coastM));
  }
  // controlled volume: outline on the ground, at FL195 and FL460, and faint walls between
  const out = geo.outline.concat([geo.outline[0]]);
  scene.add(line(out.map(p => W(p)), new THREE.LineBasicMaterial({ color: C.chart, transparent: true, opacity: 0.9 })));
  scene.add(line(out.map(p => W(p, 460)), new THREE.LineBasicMaterial({ color: C.chart, transparent: true, opacity: 0.22 })));
  scene.add(line(out.map(p => W(p, 195)), new THREE.LineBasicMaterial({ color: C.chart, transparent: true, opacity: 0.12 })));
  const pos = [];
  for (let i = 0; i < out.length - 1; i++) {
    const a0 = W(out[i], 195), a1 = W(out[i], 460), b0 = W(out[i + 1], 195), b1 = W(out[i + 1], 460);
    pos.push(...a0.toArray(), ...b0.toArray(), ...b1.toArray(), ...a0.toArray(), ...b1.toArray(), ...a1.toArray());
  }
  const wg = new THREE.BufferGeometry(); wg.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  scene.add(new THREE.Mesh(wg, new THREE.MeshBasicMaterial({ color: C.chart, transparent: true, opacity: 0.045, side: THREE.DoubleSide, depthWrite: false })));
  // sector lines (dashed) and names
  for (const s of geo.sectors) {
    const p = s.polygon.concat([s.polygon[0]]);
    scene.add(line(p.map(q => W(q, 0.4)), new THREE.LineDashedMaterial({ color: C.chart, dashSize: 4, gapSize: 4, transparent: true, opacity: 0.35 })));
    const cx = s.polygon.reduce((u, q) => u + q[0], 0) / s.polygon.length, cy = s.polygon.reduce((u, q) => u + q[1], 0) / s.polygon.length;
    if (!AMBIENT) { const t = textSprite(s.name, { size: 30, color: '#6fa8dc', alpha: 0.55, weight: 500, width: 50 }); t.position.copy(W([cx, cy], 6)); scene.add(t); }
  }
  // airways (solid) and RAD routings (dashed magenta)
  const awM = new THREE.LineBasicMaterial({ color: 0x4f7aa3, transparent: true, opacity: 0.55 });
  for (const a of geo.airways) scene.add(line(a.points.filter(n => geo.points[n]).map(n => W(geo.points[n], 0.2)), awM));
  const radM = new THREE.LineDashedMaterial({ color: C.magenta, dashSize: 3, gapSize: 3, transparent: true, opacity: 0.6 });
  for (const r of geo.rad) scene.add(line(r.filter(n => geo.points[n]).map(n => W(geo.points[n], 0.3)), radM));
  // points: small triangles, names of the main ones
  const tri = new THREE.BufferGeometry();
  tri.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, -2.2, -1.9, 0, 1.3, 1.9, 0, 1.3], 3));
  const triM = new THREE.MeshBasicMaterial({ color: C.chart, transparent: true, opacity: 0.85, side: THREE.DoubleSide });
  const named = new Set(['IBALU', 'SAMAR', 'VEDOD', 'TIGGI', 'BEXAL', 'KORNO', 'TERTO', 'MARJA', 'SALCA', 'NIDEB', 'ERMED', 'ROLRO',
    'VASTO', 'KONBA', 'AKUDA', 'RUNAX', 'BAROK', 'KUBIL', 'OSDIV', 'NIPRI', 'DESUM', 'TAKAV', 'SUNID', 'DIMSA']);
  for (const [n, p] of Object.entries(geo.points)) {
    const m = new THREE.Mesh(tri, triM); m.position.copy(W(p, 0.5)); scene.add(m);
    if (named.has(n) && !AMBIENT) { const t = textSprite(n, { size: 24, color: '#93a7ba', alpha: 0.8, weight: 500, width: 19 }); t.position.copy(W(p, 9)); scene.add(t); }
  }
}

// ---------------------------------------------------------------- storms: drifting radar echoes
const storms = [];
function buildStorms() {
  const tex = radialTexture([[0, 'rgba(230,70,60,0.85)'], [0.18, 'rgba(240,200,60,0.75)'], [0.42, 'rgba(70,170,90,0.55)'], [1, 'rgba(70,170,90,0)']]);
  const col = radialTexture([[0, 'rgba(180,200,220,0.16)'], [1, 'rgba(180,200,220,0)']]);
  const seeds = [[-60, -95, 26], [-35, -120, 18], [-90, -60, 22], [-20, -70, 15], [-120, -110, 20], [10, -100, 14]];
  for (const [x, y, r] of seeds) {
    const m = new THREE.Mesh(new THREE.CircleGeometry(r, 40), new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
    m.rotation.x = -Math.PI / 2; m.position.copy(W([x, y], 0.6)); scene.add(m);
    const tower = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.25, r * 0.55, 95, 24, 1, true),
      new THREE.MeshBasicMaterial({ map: col, transparent: true, opacity: 0.5, depthWrite: false, side: THREE.DoubleSide }));
    tower.position.copy(W([x, y], 0)); tower.position.y = 47; scene.add(tower);
    storms.push({ m, tower, x, y, ph: Math.random() * 6.28 });
  }
}

// ---------------------------------------------------------------- traffic
const OPS = ['RAM', 'RYR', 'EZY', 'IBE', 'TAP', 'AFR', 'KLM', 'BAW', 'EXS', 'TOM', 'DLH', 'VLG', 'AZU', 'TVF', 'BEL', 'SWR', 'EWG'];
const traffic = [];
const chevron = new THREE.BufferGeometry();
chevron.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, -4.2, -2.6, 0, 2.6, 0, 0, 1.2, 0, 0, -4.2, 0, 0, 1.2, 2.6, 0, 2.6], 3));
const acM = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide });
const stalkM = new THREE.LineBasicMaterial({ color: C.stalk, transparent: true, opacity: 0.28 });
const shadowM = new THREE.MeshBasicMaterial({ color: C.stalk, transparent: true, opacity: 0.35, depthWrite: false });
const trailM = new THREE.LineBasicMaterial({ color: 0xbfd7ee, transparent: true, opacity: 0.35 });

function routeList() {
  const out = [];
  for (const a of geo.airways) { const p = a.points.filter(n => geo.points[n]); if (p.length > 1) { out.push(p); if (!a.one_way) out.push([...p].reverse()); } }
  for (const r of geo.routes) if (r.length > 1) out.push(r);
  return out;
}
function newFlight(f, routes, fresh) {
  const names = routes[Math.floor(Math.random() * routes.length)], pts = names.map(n => geo.points[n]);
  const a = pts[0], b = pts[pts.length - 1], brg = (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180 / Math.PI + 360) % 360;
  const south = brg >= 90 && brg < 270, lv = south ? [310, 330, 350, 370, 390] : [300, 320, 340, 360, 380, 400];
  const fl = lv[Math.floor(Math.random() * lv.length)];
  const segs = []; let L = 0;
  for (let i = 0; i < pts.length - 1; i++) { const d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]); segs.push([pts[i], pts[i + 1], L, d]); L += d; }
  Object.assign(f, { segs, L, fl, s: fresh ? Math.random() * L : -Math.random() * 60, speed: SPEED * (0.9 + Math.random() * 0.25), trail: [] });
  const cs = OPS[Math.floor(Math.random() * OPS.length)] + (100 + Math.floor(Math.random() * 8900));
  if (f.label) { scene.remove(f.label); f.label.material.map.dispose(); }
  f.label = textSprite(`${cs}  ${fl}`, { size: 24, color: '#e9eef2', alpha: 0.85, weight: 600, width: 30 }); f.label.visible = !AMBIENT; if (!AMBIENT) scene.add(f.label);
}
function at(f, s) {
  s = Math.max(0, Math.min(f.L, s));
  for (const [a, b, s0, d] of f.segs) if (s <= s0 + d || d === 0) { const k = d ? (s - s0) / d : 0; return [[a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k], Math.atan2(b[0] - a[0], b[1] - a[1])]; }
  const l = f.segs[f.segs.length - 1]; return [l[1], Math.atan2(l[1][0] - l[0][0], l[1][1] - l[0][1])];
}
function buildTraffic(n) {
  const routes = routeList();
  for (let i = 0; i < n; i++) {
    const f = { mesh: new THREE.Mesh(chevron, acM), shadow: new THREE.Mesh(new THREE.CircleGeometry(1.4, 12), shadowM) };
    f.shadow.rotation.x = -Math.PI / 2;
    f.stalk = line([new THREE.Vector3(), new THREE.Vector3(0, 1, 0)], stalkM);
    f.trailLine = new THREE.Line(new THREE.BufferGeometry(), trailM);
    scene.add(f.mesh, f.shadow, f.stalk, f.trailLine);
    newFlight(f, routes, true); f.routes = routes; traffic.push(f);
  }
}
function stepTraffic(dt) {
  for (const f of traffic) {
    f.s += f.speed * dt;
    if (f.s > f.L) { newFlight(f, f.routes, false); }
    const vis = f.s >= 0;
    for (const o of [f.mesh, f.shadow, f.stalk, f.trailLine]) o.visible = vis; f.label.visible = vis && !AMBIENT;
    if (!vis) continue;
    const [p, hdg] = at(f, f.s), v = W(p, f.fl);
    f.mesh.position.copy(v); f.mesh.rotation.y = -hdg;            // nose (-z) along the track: east +x, north -z
    f.shadow.position.copy(W(p, 0.7));
    f.stalk.geometry.setFromPoints([W(p, 0.7), v]);
    f.label.position.set(v.x + 13, v.y + 6, v.z);
    f.trail.push(v.clone()); if (f.trail.length > 28) f.trail.shift();
    f.trailLine.geometry.setFromPoints(f.trail);
  }
}

// ---------------------------------------------------------------- camera, resize, loop
let angle = -0.6, offsetX = 0;
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  offsetX = w > 860 && !AMBIENT ? -w * 0.19 : 0;            // keep the map on the right of the text column on wide screens
  if (offsetX) camera.setViewOffset(w, h, offsetX, 0, w, h); else camera.clearViewOffset();
  camera.updateProjectionMatrix();
}
function placeCamera() {
  const r = AMBIENT ? 600 : innerWidth > 860 ? 640 : 760, el = AMBIENT ? 0.5 : 0.64;
  camera.position.set(Math.sin(angle) * r * Math.cos(el), r * Math.sin(el), Math.cos(angle) * r * Math.cos(el));
  camera.lookAt(0, 20, 0);
}
let last = performance.now(), running = true;
const MIN_FRAME_MS = AMBIENT ? 40 : 0;        // ~25 frames/s behind the dashboards
function frame(now) {
  if (now - last < MIN_FRAME_MS) { requestAnimationFrame(frame); return; }
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (running) {
    angle += dt * (AMBIENT ? 0.012 : 0.022);
    stepTraffic(dt);
    for (const s of storms) { s.x += dt * 0.35; s.y += dt * 0.2; s.ph += dt * 0.6;
      s.m.position.copy(W([s.x, s.y], 0.6)); s.tower.position.x = s.m.position.x; s.tower.position.z = s.m.position.z;
      s.m.material.opacity = 0.5 + 0.15 * Math.sin(s.ph); }
    placeCamera();
    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);
}
document.addEventListener('visibilitychange', () => { running = !document.hidden; last = performance.now(); });
addEventListener('resize', () => { resize(); if (reduce) { placeCamera(); renderer.render(scene, camera); } });

try {
  geo = await (await fetch('/api/landing')).json();
  await document.fonts.ready.catch(() => {});
  buildMap(); buildStorms(); buildTraffic(AMBIENT ? 16 : innerWidth > 860 ? 26 : 14);
  resize(); placeCamera();
  if (reduce) { stepTraffic(0); renderer.render(scene, camera); }
  else requestAnimationFrame(frame);
} catch (e) {
  console.warn('landing scene unavailable', e);
}

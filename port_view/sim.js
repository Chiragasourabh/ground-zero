import { ukc, maxSafeSpeed, RED_PCT } from './ukc.js';
import {
  METRES_PER_PX, CELL_PX, buildWaterMask, distanceToLand, synthBathymetry, synthTide, tideAt,
} from './data.js';

// Illustrative presets; the real build derives these from the Cerema AIS CSVs.
const PRESETS = {
  bulk: { label: 'Bulk carrier', draft: 10.5, loa: 190, beam: 32, Cb: 0.82 },
  container: { label: 'Container ship', draft: 11, loa: 260, beam: 40, Cb: 0.65 },
  tanker: { label: 'Product tanker', draft: 11.5, loa: 183, beam: 32, Cb: 0.8 },
  ferry: { label: 'Ro-Pax ferry', draft: 6.5, loa: 186, beam: 28, Cb: 0.6 },
};

const KN_TO_MS = 0.5144;
const TIME_SCALE = 30;       // 1 s on screen = 30 s at sea, so a transit fits a demo
const MAX_AHEAD_KN = 14, MAX_ASTERN_KN = -4;
const ACCEL_KN_S = 1.5;
const LOOKAHEAD_M = 2000, SAMPLE_M = 20;
const START = { x: 1150, y: 1250, heading: 352 };
const BRIDGE_ZOOM = 3.5;

const COLORS = {
  green: [46, 170, 90], amber: [240, 170, 40], red: [220, 50, 47], aground: [120, 0, 26],
};
const css = (level, a = 1) => `rgba(${COLORS[level].join(',')},${a})`;

const $ = (id) => document.getElementById(id);
const mapCanvas = $('map'), ctx = mapCanvas.getContext('2d');
const keelCanvas = $('keel'), kctx = keelCanvas.getContext('2d');

const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
const tide = synthTide(midnight.getTime());
const minutesNow = () => Math.round((Date.now() - midnight.getTime()) / 60000 / 10) * 10;

const state = {
  vessel: { ...PRESETS.bulk },
  tideMin: minutesNow(),
  view: 'port',
  seed: 1 + Math.floor(Math.random() * 99999),
  ship: { ...START, speed: 0, aground: false },
};
const keys = new Set();

let mask, dist, bathy, img;
const overlay = document.createElement('canvas');
let overlayKey = '';

// --- seabed lookups ---------------------------------------------------------------------------

function cellAt(px, py) {
  const c = Math.floor(px / CELL_PX), r = Math.floor(py / CELL_PX);
  if (r < 0 || c < 0 || r >= mask.rows || c >= mask.cols) return null;
  return { depth: bathy.depth[r][c], confined: bathy.confined[r][c] };
}

const tideM = (min = state.tideMin) => tideAt(tide, midnight.getTime() + min * 60000);

// Local hull frame (along, across) in metres -> map pixels.
function toMap(x, y, heading, along, across) {
  const h = (heading * Math.PI) / 180;
  const fx = Math.sin(h), fy = -Math.cos(h);
  return [x + (fx * along - fy * across) / METRES_PER_PX, y + (fy * along + fx * across) / METRES_PER_PX];
}

// Minimum depth under the hull: bow, midship, stern and both beam edges, not the centreline only.
function footprint(x, y, heading) {
  const { loa, beam } = state.vessel;
  const pts = [[loa / 2, 0], [0, 0], [-loa / 2, 0], [0, beam / 2], [0, -beam / 2],
    [loa / 2 - beam, beam / 2], [loa / 2 - beam, -beam / 2]];
  let depth = Infinity, confined = false;
  for (const [a, b] of pts) {
    const cell = cellAt(...toMap(x, y, heading, a, b));
    if (!cell || cell.depth === null) return { land: true };
    depth = Math.min(depth, cell.depth);
    confined ||= cell.confined;
  }
  return { land: false, depth, confined };
}

// Seabed geometry along the track from the stern to 2 km past the bow, stopping at the shore.
// Tide-free, so the tide-window scan can re-evaluate it cheaply.
function trackProfile() {
  const { x, y, heading } = state.ship;
  const { loa, beam } = state.vessel;
  const samples = [];
  let shoreAt = null;
  for (let d = -loa; d <= LOOKAHEAD_M; d += SAMPLE_M) {
    const along = loa / 2 + d;
    let depth = Infinity, confined = false, land = false;
    for (const across of [0, beam / 2, -beam / 2]) {
      const cell = cellAt(...toMap(x, y, heading, along, across));
      if (!cell || cell.depth === null) { land = true; break; }
      depth = Math.min(depth, cell.depth);
      confined ||= cell.confined;
    }
    if (land) { if (d >= 0) shoreAt = d; break; }
    samples.push({ d, depth, confined });
  }
  return { samples, shoreAt };
}

function evaluateTrack(samples, tideHeight, speed) {
  const { draft, Cb } = state.vessel;
  let worst = null, maxSafe = Infinity;
  for (const s of samples) {
    if (s.d < 0) continue;
    const r = ukc(s.depth, tideHeight, draft, speed, Cb, s.confined);
    if (!worst || r.metres < worst.r.metres) worst = { s, r };
    maxSafe = Math.min(maxSafe, maxSafeSpeed(s.depth, tideHeight, draft, Cb, s.confined));
  }
  return { worst, maxSafe };
}

function nextSafeWindow(samples, speed) {
  const { draft, Cb } = state.vessel;
  const ahead = samples.filter((s) => s.d >= 0);
  if (!ahead.length) return null;
  const end = (tide.time.length - 1) * 10;
  for (let m = state.tideMin; m <= Math.min(end, state.tideMin + 24 * 60); m += 10) {
    const t = tideM(m);
    if (ahead.every((s) => ukc(s.depth, t, draft, speed, Cb, s.confined).pct >= RED_PCT)) return m;
  }
  return -1;
}

// --- simulation -------------------------------------------------------------------------------

function step(dt) {
  const ship = state.ship;
  if (ship.aground) return;
  const ahead = keys.has('w') || keys.has('arrowup');
  const astern = keys.has('s') || keys.has('arrowdown');
  if (ahead) ship.speed = Math.min(MAX_AHEAD_KN, ship.speed + ACCEL_KN_S * dt);
  if (astern) ship.speed = Math.max(MAX_ASTERN_KN, ship.speed - ACCEL_KN_S * dt);
  if (keys.has(' ')) ship.speed -= Math.sign(ship.speed) * Math.min(Math.abs(ship.speed), 4 * dt);

  // Rudder needs water flowing past it: no turning on the spot.
  const turn = 22 * Math.min(1, Math.abs(ship.speed) / 4) * Math.sign(ship.speed || 1);
  if (keys.has('a') || keys.has('arrowleft')) ship.heading -= turn * dt;
  if (keys.has('d') || keys.has('arrowright')) ship.heading += turn * dt;
  ship.heading = (ship.heading + 360) % 360;

  const metres = ship.speed * KN_TO_MS * dt * TIME_SCALE;
  const [nx, ny] = toMap(ship.x, ship.y, ship.heading, metres, 0);
  ship.x = nx; ship.y = ny;

  const fp = footprint(ship.x, ship.y, ship.heading);
  const { draft, Cb } = state.vessel;
  if (fp.land || ukc(fp.depth, tideM(), draft, ship.speed, Cb, fp.confined).metres < 0) {
    ship.aground = true;
    ship.speed = 0;
  }
}

function resetShip() {
  state.ship = { ...START, speed: 0, aground: false };
  const start = nearestWater(START.x, START.y);
  state.ship.x = start[0]; state.ship.y = start[1];
}

function nearestWater(x, y) {
  for (let rad = 0; rad < 400; rad += CELL_PX) {
    for (let a = 0; a < 2 * Math.PI; a += Math.PI / 18) {
      const px = x + rad * Math.cos(a), py = y + rad * Math.sin(a);
      const fp = footprint(px, py, START.heading);
      if (!fp.land && fp.depth > state.vessel.draft + 2) return [px, py];
    }
  }
  return [x, y];
}

// --- rendering --------------------------------------------------------------------------------

function rebuildOverlay() {
  const { draft, Cb } = state.vessel;
  const t = tideM();
  const speed = state.ship.speed;
  const key = `${state.seed}|${t.toFixed(2)}|${draft}|${Cb}|${Math.abs(speed).toFixed(1)}`;
  if (key === overlayKey) return;
  overlayKey = key;
  const { cols, rows } = mask;
  overlay.width = cols; overlay.height = rows;
  const octx = overlay.getContext('2d');
  const out = octx.createImageData(cols, rows);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const depth = bathy.depth[r][c];
      if (depth === null) continue;
      const lvl = ukc(depth, t, draft, speed, Cb, bathy.confined[r][c]).level;
      const i = (r * cols + c) * 4;
      out.data.set(COLORS[lvl], i);
      out.data[i + 3] = 165;
    }
  }
  octx.putImageData(out, 0, 0);
}

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  return { w, h, dpr };
}

function drawMap(status) {
  const { w, h, dpr } = fitCanvas(mapCanvas);
  const ship = state.ship;
  let scale, ox, oy;
  if (state.view === 'port') {
    scale = Math.min(w / img.width, h / img.height);
    ox = (w - img.width * scale) / 2; oy = (h - img.height * scale) / 2;
  } else {
    scale = BRIDGE_ZOOM;
    ox = w / 2 - ship.x * scale; oy = h / 2 - ship.y * scale;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = '#bac2cc';
  ctx.fillRect(0, 0, mapCanvas.width, mapCanvas.height);
  ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * ox, dpr * oy);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(img, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(overlay, 0, 0, mask.cols * CELL_PX, mask.rows * CELL_PX);

  if (state.view === 'bridge') {
    ctx.strokeStyle = 'rgba(0,0,0,.12)';
    ctx.lineWidth = 1 / scale;
    ctx.beginPath();
    for (let c = 0; c <= mask.cols; c++) { ctx.moveTo(c * CELL_PX, 0); ctx.lineTo(c * CELL_PX, mask.rows * CELL_PX); }
    for (let r = 0; r <= mask.rows; r++) { ctx.moveTo(0, r * CELL_PX); ctx.lineTo(mask.cols * CELL_PX, r * CELL_PX); }
    ctx.stroke();
  }

  // Track ahead, with the worst point marked.
  const { loa, beam } = state.vessel;
  const px = 1 / scale;
  const end = status.shoreAt ?? LOOKAHEAD_M;
  const [bx, by] = toMap(ship.x, ship.y, ship.heading, loa / 2, 0);
  const [ex, ey] = toMap(ship.x, ship.y, ship.heading, loa / 2 + end, 0);
  ctx.setLineDash([6 * px, 5 * px]);
  ctx.strokeStyle = 'rgba(15,23,32,.75)';
  ctx.lineWidth = 2 * px;
  ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(ex, ey); ctx.stroke();
  ctx.setLineDash([]);
  if (status.worst) {
    const [wx, wy] = toMap(ship.x, ship.y, ship.heading, loa / 2 + status.worst.s.d, 0);
    ctx.fillStyle = css(status.worst.r.level);
    ctx.strokeStyle = '#0f1720';
    ctx.beginPath(); ctx.arc(wx, wy, 5 * px, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }

  // Ship at real length and beam, coloured by its own clearance.
  const level = status.now ? status.now.level : 'aground';
  const hull = [[-loa / 2, -beam / 2], [loa / 2 - beam, -beam / 2], [loa / 2, 0], [loa / 2 - beam, beam / 2], [-loa / 2, beam / 2]]
    .map(([a, b]) => toMap(ship.x, ship.y, ship.heading, a, b));
  ctx.fillStyle = css(level);
  ctx.strokeStyle = '#0f1720';
  ctx.lineWidth = 1.5 * px;
  ctx.beginPath();
  hull.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath(); ctx.fill(); ctx.stroke();

  // At harbour zoom the true-scale hull is a few pixels, so ring it.
  if (state.view === 'port') {
    ctx.lineWidth = 3 * px;
    ctx.strokeStyle = '#0f1720';
    ctx.beginPath(); ctx.arc(ship.x, ship.y, 13 * px, 0, Math.PI * 2); ctx.stroke();
    ctx.lineWidth = 2 * px;
    ctx.strokeStyle = css(level);
    ctx.beginPath(); ctx.arc(ship.x, ship.y, 13 * px, 0, Math.PI * 2); ctx.stroke();
  }
}

function drawKeel(profile, status) {
  const { w, h, dpr } = fitCanvas(keelCanvas);
  kctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  kctx.clearRect(0, 0, w, h);
  const { draft, loa, Cb } = state.vessel;
  const t = tideM();
  const speed = state.ship.speed;
  const samples = profile.samples;
  const pad = { l: 8, r: 8, t: 14, b: 16 };
  const maxDepth = Math.max(20, ...samples.map((s) => s.depth + t)) + 2;
  const X = (d) => pad.l + ((d + loa) / (LOOKAHEAD_M + loa)) * (w - pad.l - pad.r);
  const Y = (z) => pad.t + (z / maxDepth) * (h - pad.t - pad.b);   // z: metres below the surface

  kctx.fillStyle = '#123049';
  kctx.fillRect(X(-loa), Y(0), X(LOOKAHEAD_M) - X(-loa), Y(maxDepth) - Y(0));

  // Seabed, each segment coloured by the clearance it would leave.
  for (const s of samples) {
    const lvl = ukc(s.depth, t, draft, speed, Cb, s.confined).level;
    const x0 = X(s.d), x1 = X(s.d + SAMPLE_M) + 0.5;
    kctx.fillStyle = '#5b4a36';
    kctx.fillRect(x0, Y(s.depth + t), x1 - x0, Y(maxDepth) - Y(s.depth + t));
    kctx.fillStyle = css(lvl);
    kctx.fillRect(x0, Y(s.depth + t) - 1.5, x1 - x0, 3);
  }
  if (profile.shoreAt !== null) {
    kctx.fillStyle = '#7a6a55';
    kctx.fillRect(X(profile.shoreAt), Y(-1), X(LOOKAHEAD_M) - X(profile.shoreAt), Y(maxDepth) - Y(-1));
    kctx.fillStyle = '#e6edf3'; kctx.font = '10px system-ui';
    kctx.fillText('shore', X(profile.shoreAt) + 3, Y(0) + 11);
  }

  // Water surface at the current tide.
  kctx.strokeStyle = '#4aa3ff'; kctx.lineWidth = 1.5;
  kctx.beginPath(); kctx.moveTo(X(-loa), Y(0)); kctx.lineTo(X(LOOKAHEAD_M), Y(0)); kctx.stroke();

  // Hull at draft, plus squat, and the keel line projected along the track.
  const sq = status.now ? status.now.squat : 0;
  kctx.fillStyle = '#c9d3dd';
  kctx.fillRect(X(-loa), Y(-2), X(0) - X(-loa), Y(draft) - Y(-2));
  kctx.fillStyle = 'rgba(240,170,40,.75)';
  kctx.fillRect(X(-loa), Y(draft), X(0) - X(-loa), Y(draft + sq) - Y(draft));
  kctx.setLineDash([4, 3]); kctx.strokeStyle = 'rgba(201,211,221,.7)'; kctx.lineWidth = 1;
  kctx.beginPath(); kctx.moveTo(X(0), Y(draft + sq)); kctx.lineTo(X(profile.shoreAt ?? LOOKAHEAD_M), Y(draft + sq)); kctx.stroke();
  kctx.setLineDash([]);

  if (status.worst) {
    const { s, r } = status.worst;
    const x = X(s.d + SAMPLE_M / 2);
    kctx.strokeStyle = '#fff'; kctx.lineWidth = 1;
    kctx.beginPath(); kctx.moveTo(x, Y(draft + r.squat)); kctx.lineTo(x, Y(s.depth + t)); kctx.stroke();
    kctx.fillStyle = '#fff'; kctx.font = '600 11px system-ui';
    const label = `${r.metres.toFixed(1)} m @ ${Math.round(s.d)} m`;
    kctx.fillText(label, Math.min(x + 4, w - kctx.measureText(label).width - 4), Math.max(Y(draft + r.squat) - 4, 11));
  }
  kctx.fillStyle = '#8b9aab'; kctx.font = '10px system-ui';
  kctx.fillText('ship', X(-loa) + 2, h - 4);
  kctx.fillText('2 km ahead', X(LOOKAHEAD_M) - 54, h - 4);
}

const fmtTime = (min) => {
  const day = min >= 1440 ? 'Tomorrow ' : '';
  const m = ((min % 1440) + 1440) % 1440;
  return `${day}${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
const pill = (level, text) => `<span class="pill" style="background:${css(level)};color:${level === 'aground' ? '#fff' : '#111'}">${text}</span>`;
const setHTML = (id, html) => { const el = $(id); if (el.innerHTML !== html) el.innerHTML = html; };

function updatePanel(status) {
  const ship = state.ship;
  const { draft } = state.vessel;
  $('hudSpeed').textContent = ship.speed.toFixed(1);
  $('hudHdg').textContent = String(Math.round(ship.heading) % 360).padStart(3, '0');
  setHTML('hudView', state.view === 'port'
    ? 'Port view · whole harbour, risk for this vessel'
    : 'Bridge view · following the ship');
  $('banner').style.display = ship.aground ? 'block' : 'none';
  $('viewPort').classList.toggle('on', state.view === 'port');
  $('viewBridge').classList.toggle('on', state.view === 'bridge');

  setHTML('draftVal', `${draft.toFixed(1)} m`);
  setHTML('tideVal', `${fmtTime(state.tideMin)} · ${tideM().toFixed(2)} m above LAT`);
  setHTML('seedVal', `seed ${state.seed}`);

  const n = status.now;
  setHTML('advNow', ship.aground || !n ? pill('aground', 'aground')
    : pill(n.level, `${n.metres.toFixed(2)} m · ${n.pct.toFixed(0)}%`));
  setHTML('advSquat', n ? `${n.squat.toFixed(2)} m · ${status.confinedNow ? 'confined' : 'open'}` : '–');
  setHTML('advWorst', status.worst
    ? `${pill(status.worst.r.level, `${status.worst.r.metres.toFixed(2)} m`)} in ${Math.round(status.worst.s.d)} m`
    : (status.shoreAt !== null ? `shore in ${Math.round(status.shoreAt)} m` : '–'));
  setHTML('advMax', !status.worst ? '–'
    : status.maxSafe <= 0 ? pill('red', 'none · wait for tide')
      : `${Math.min(status.maxSafe, MAX_AHEAD_KN).toFixed(1)} kn${status.maxSafe > MAX_AHEAD_KN ? '+' : ''}`);
  const win = status.window;
  setHTML('advWindow', win === null ? '–'
    : win === state.tideMin ? pill('green', 'now')
      : win < 0 ? pill('red', 'none in 24 h') : `from ${fmtTime(win)}`);
}

function vesselInfo() {
  const v = state.vessel;
  $('vesselInfo').innerHTML =
    `<span>Length</span><span>${v.loa} m</span><span>Beam</span><span>${v.beam} m</span><span>Block coeff. Cb</span><span>${v.Cb}</span>`;
}

// --- loop & input -----------------------------------------------------------------------------

let last = performance.now();
let advisoryAt = -Infinity, cached = null;
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  step(dt);

  const ship = state.ship;
  const profile = trackProfile();
  const fp = footprint(ship.x, ship.y, ship.heading);
  const { draft, Cb } = state.vessel;
  const nowR = fp.land ? null : ukc(fp.depth, tideM(), draft, ship.speed, Cb, fp.confined);
  const ev = evaluateTrack(profile.samples, tideM(), ship.speed);
  if (now - advisoryAt > 250) {   // the 24 h tide scan is the only costly bit
    cached = nextSafeWindow(profile.samples, ship.speed);
    advisoryAt = now;
  }
  const status = {
    now: ship.aground ? null : nowR, confinedNow: fp.confined,
    worst: ev.worst, maxSafe: ev.maxSafe, shoreAt: profile.shoreAt, window: cached,
  };

  rebuildOverlay();
  drawMap(status);
  drawKeel(profile, status);
  updatePanel(status);
  requestAnimationFrame(frame);
}

const GAME_KEYS = new Set(['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', ' ']);
window.addEventListener('keydown', (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) {
    if (!GAME_KEYS.has(e.key.toLowerCase())) return;
    e.target.blur();
  }
  const k = e.key.toLowerCase();
  if (GAME_KEYS.has(k)) { keys.add(k); e.preventDefault(); }
  if (k === 'r') resetShip();
  if (k === 'v') state.view = state.view === 'port' ? 'bridge' : 'port';
});
window.addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
window.addEventListener('blur', () => keys.clear());

function initControls() {
  const preset = $('preset');
  for (const [id, p] of Object.entries(PRESETS)) preset.add(new Option(p.label, id));
  preset.value = 'bulk';
  preset.addEventListener('change', () => {
    state.vessel = { ...PRESETS[preset.value] };
    $('draft').value = state.vessel.draft;
    vesselInfo();
  });
  const draft = $('draft');
  draft.value = state.vessel.draft;
  draft.addEventListener('input', () => { state.vessel.draft = Number(draft.value); });
  const tideInput = $('tide');
  tideInput.value = state.tideMin;
  tideInput.addEventListener('input', () => { state.tideMin = Number(tideInput.value); });
  $('tideNow').addEventListener('click', () => { state.tideMin = minutesNow(); tideInput.value = state.tideMin; });
  $('reseed').addEventListener('click', () => {
    state.seed = 1 + Math.floor(Math.random() * 99999);
    bathy = synthBathymetry(mask, dist, state.seed);
    overlayKey = '';
    resetShip();
  });
  $('viewPort').addEventListener('click', () => { state.view = 'port'; });
  $('viewBridge').addEventListener('click', () => { state.view = 'bridge'; });
  vesselInfo();
}

img = new Image();
img.src = 'corkmap.png';
img.onload = () => {
  const off = document.createElement('canvas');
  off.width = img.width; off.height = img.height;
  const octx = off.getContext('2d');
  octx.drawImage(img, 0, 0);
  mask = buildWaterMask(octx.getImageData(0, 0, img.width, img.height));
  dist = distanceToLand(mask);
  bathy = synthBathymetry(mask, dist, state.seed);
  initControls();
  resetShip();
  requestAnimationFrame(frame);
};
img.onerror = () => {
  document.body.innerHTML = '<p style="padding:20px">Could not load corkmap.png from the port_view folder.</p>';
};

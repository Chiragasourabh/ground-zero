// Synthetic stand-ins for bathymetry.json and tide.json, in the same shapes as the interface
// contract, so the real EMODnet / Marine Institute exports replace these without touching the UI.

export const METRES_PER_PX = 10; // corkmap.png spans roughly 18 km across 1802 px
export const CELL_PX = 10;       // 100 m cells, close to the ~115 m EMODnet grid

export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Water is the near-white fill of the base map, land the blue-grey. Other pixels (labels, AIS
// icons drawn on the screenshot) are ignored, so a vessel marker does not become an island.
// Only water connected to the open sea (bottom edge) counts, so inland ponds become land.
export function buildWaterMask(imageData) {
  const { width, height, data } = imageData;
  const cols = Math.floor(width / CELL_PX);
  const rows = Math.floor(height / CELL_PX);
  const raw = new Uint8Array(cols * rows);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let water = 0, land = 0;
      for (let y = r * CELL_PX; y < (r + 1) * CELL_PX; y++) {
        for (let x = c * CELL_PX; x < (c + 1) * CELL_PX; x++) {
          const i = (y * width + x) * 4;
          const R = data[i], G = data[i + 1], B = data[i + 2];
          const mn = Math.min(R, G, B), mx = Math.max(R, G, B);
          if (mn > 225) water++;
          else if (mx > 160 && mx <= 225 && B >= R && mx - mn < 30) land++;
        }
      }
      raw[r * cols + c] = water >= land ? 1 : 0;
    }
  }

  const water = new Uint8Array(cols * rows);
  const queue = [];
  for (let c = 0; c < cols; c++) {
    const i = (rows - 1) * cols + c;
    if (raw[i]) { water[i] = 1; queue.push(i); }
  }
  for (let q = 0; q < queue.length; q++) {
    const i = queue[q];
    const r = Math.floor(i / cols), c = i % cols;
    for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nr = r + dr, nc = c + dc;
      if (nr < 0 || nc < 0 || nr >= rows || nc >= cols) continue;
      const n = nr * cols + nc;
      if (raw[n] && !water[n]) { water[n] = 1; queue.push(n); }
    }
  }
  return { cols, rows, water };
}

// Cells from the nearest land cell (map edges are not land: the sea continues past them).
export function distanceToLand({ cols, rows, water }) {
  const dist = new Float32Array(cols * rows).fill(Infinity);
  const queue = [];
  for (let i = 0; i < water.length; i++) if (!water[i]) { dist[i] = 0; queue.push(i); }
  for (let q = 0; q < queue.length; q++) {
    const i = queue[q];
    const r = Math.floor(i / cols), c = i % cols;
    for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nr = r + dr, nc = c + dc;
      if (nr < 0 || nc < 0 || nr >= rows || nc >= cols) continue;
      const n = nr * cols + nc;
      if (dist[n] > dist[i] + 1) { dist[n] = dist[i] + 1; queue.push(n); }
    }
  }
  return dist;
}

function valueNoise(cols, rows, spacing, rand) {
  const gc = Math.ceil(cols / spacing) + 2, gr = Math.ceil(rows / spacing) + 2;
  const lattice = Array.from({ length: gc * gr }, () => rand() * 2 - 1);
  const smooth = (t) => t * t * (3 - 2 * t);
  return (r, c) => {
    const x = c / spacing, y = r / spacing;
    const x0 = Math.floor(x), y0 = Math.floor(y);
    const tx = smooth(x - x0), ty = smooth(y - y0);
    const v = (yy, xx) => lattice[yy * gc + xx];
    const top = v(y0, x0) * (1 - tx) + v(y0, x0 + 1) * tx;
    const bot = v(y0 + 1, x0) * (1 - tx) + v(y0 + 1, x0 + 1) * tx;
    return top * (1 - ty) + bot * ty;
  };
}

// Randomised but plausible seabed: shallow at the shore, deeper mid-channel and towards the
// harbour mouth, with noise and a few shoals. Returns the bathymetry.json shape plus a derived
// confined[][] flag (within 400 m of land counts as confined water for squat).
export function synthBathymetry(mask, dist, seed) {
  const { cols, rows, water } = mask;
  const rand = mulberry32(seed);
  const coarse = valueNoise(cols, rows, 14, rand);
  const fine = valueNoise(cols, rows, 5, rand);

  const shoals = [];
  const waterCells = [];
  for (let i = 0; i < water.length; i++) if (water[i] && dist[i] >= 3) waterCells.push(i);
  for (let k = 0; k < 9 && waterCells.length; k++) {
    const i = waterCells[Math.floor(rand() * waterCells.length)];
    shoals.push({ r: Math.floor(i / cols), c: i % cols, rad: 2.5 + rand() * 5, lift: 3 + rand() * 5 });
  }

  const depth = [], confined = [];
  for (let r = 0; r < rows; r++) {
    const drow = [], crow = [];
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c;
      if (!water[i]) { drow.push(null); crow.push(false); continue; }
      const d = dist[i];
      let z = 1.5 + 15 * (1 - Math.exp(-d / 4)) + 6 * (r / rows);
      z += 2.5 * coarse(r, c) + 1.0 * fine(r, c);
      for (const s of shoals) {
        const g = ((r - s.r) ** 2 + (c - s.c) ** 2) / (s.rad * s.rad);
        z -= s.lift * Math.exp(-g);
      }
      drow.push(Math.max(0.5, z));
      crow.push(d <= 4);
    }
    depth.push(drow);
    confined.push(crow);
  }
  return {
    bbox: [0, 0, cols * CELL_PX, rows * CELL_PX], // map pixels here; real export uses lon/lat
    resolution: CELL_PX * METRES_PER_PX,
    datum: 'LAT (synthetic)',
    depth,
    confined,
  };
}

// Semidiurnal tide over 48 h from local midnight, metres above LAT. Cork springs range ~4 m.
export function synthTide(midnightMs, hours = 48, stepMin = 10) {
  const time = [], height = [];
  for (let m = 0; m <= hours * 60; m += stepMin) {
    const h = m / 60;
    time.push(midnightMs + m * 60000);
    height.push(2.2 + 1.6 * Math.cos((2 * Math.PI * (h - 3.1)) / 12.42) + 0.35 * Math.cos((2 * Math.PI * (h - 1.0)) / 12));
  }
  return { time, height };
}

export function tideAt({ time, height }, ms) {
  if (ms <= time[0]) return height[0];
  if (ms >= time[time.length - 1]) return height[height.length - 1];
  const step = time[1] - time[0];
  const i = Math.floor((ms - time[0]) / step);
  const t = (ms - time[i]) / step;
  return height[i] * (1 - t) + height[i + 1] * t;
}

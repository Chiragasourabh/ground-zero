// Wave layer shared by the UKC what-if and Sail planning maps (inlined by common.render_map).
// Data: one time step of the Copernicus Marine global wave model (GLOBAL_ANALYSISFORECAST_WAV_001_027),
// u and v = deep-water group velocity in the direction the waves travel (m/s, east and north), null on land.
// Look: the sea is coloured by wave speed, and translucent squares with a dark leading edge (the crest)
// drift the way the waves travel, fading in and out.

// Velocity at a point, bilinear between the four surrounding model cells; null on land or off the grid.
function waveSampler(w) {
  return function (lat, lon) {
    const fx = (lon - w.lon0) / w.dlon, fy = (lat - w.lat0) / w.dlat;
    const x0 = Math.floor(fx), y0 = Math.floor(fy);
    if (x0 < 0 || y0 < 0 || x0 >= w.nx - 1 || y0 >= w.ny - 1) return null;
    const tx = fx - x0, ty = fy - y0;
    let u = 0, v = 0, sum = 0;
    for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
      const i = (y0 + dy) * w.nx + x0 + dx;
      if (w.u[i] === null) continue;
      const k = (dx ? tx : 1 - tx) * (dy ? ty : 1 - ty);
      u += w.u[i] * k; v += w.v[i] * k; sum += k;
    }
    return sum < 0.5 ? null : { u: u / sum, v: v / sum };
  };
}

// "heading SE (136°), 11.5 m/s" for a sampled velocity
function waveText(wv) {
  const to = (Math.atan2(wv.u, wv.v) * 180 / Math.PI + 360) % 360;
  const compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(to / 45) % 8];
  return `heading ${compass} (${to.toFixed(0)}°), ${Math.hypot(wv.u, wv.v).toFixed(1)} m/s`;
}

// Colour ramp for wave speed, from slow (0) to fast (1): blue, violet, magenta, pink, yellow, lime, green
const WAVE_STOPS = [[0, [43, 43, 217]], [0.23, [122, 43, 224]], [0.41, [214, 63, 217]], [0.55, [240, 154, 191]],
                    [0.68, [242, 233, 78]], [0.82, [183, 240, 58]], [1, [60, 194, 60]]];
function rampColor(t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < WAVE_STOPS.length; i++) {
    const [t1, c1] = WAVE_STOPS[i];
    if (t <= t1) {
      const [t0, c0] = WAVE_STOPS[i - 1], k = (t - t0) / (t1 - t0);
      return c0.map((c, j) => Math.round(c + (c1[j] - c) * k));
    }
  }
  return WAVE_STOPS[WAVE_STOPS.length - 1][1];
}

function addWaveLayer(map, w, opts = {}) {
  const waveAt = waveSampler(w);
  const rad = Math.PI / 180, merc = lat => Math.log(Math.tan(Math.PI / 4 + lat * rad / 2));
  // Stretch the colours over this time step (5th to 95th percentile of speed) so local differences show
  const speeds = w.u.map((u, i) => u === null ? null : Math.hypot(u, w.v[i])).filter(x => x !== null).sort((a, b) => a - b);
  const lo = speeds[Math.floor(speeds.length * 0.05)] || 0, hi = speeds[Math.floor(speeds.length * 0.95)] || 1;
  const waveColor = s => rampColor((s - lo) / ((hi - lo) || 1));
  const tick = t => (lo + (hi - lo) * t).toFixed(1);

  // ----- Key -----
  const style = document.createElement("style");
  style.textContent = `.wave-note { left:12px; bottom:${opts.bottom || 40}px; padding:8px 12px; font-size:12.5px;
      line-height:1.45; width:250px; }
    .wave-note b { font-weight:600; }
    .wave-note .ramp { height:9px; border-radius:2px; margin:5px 0 2px;
      background:linear-gradient(90deg, ${WAVE_STOPS.map(([t, c]) => `rgb(${c}) ${(t * 100).toFixed(0)}%`).join(", ")}); }
    .wave-note .ticks { display:flex; justify-content:space-between; font-size:11px; color:#5B7385; font-variant-numeric:tabular-nums; }
    .wave-note .src { color:#5B7385; font-size:11.5px; margin-top:3px; }
    .leaflet-waves-pane canvas { pointer-events:none; }`;
  document.head.appendChild(style);
  const note = document.createElement("div");
  note.className = "panel wave-note";
  note.innerHTML = `<b>Waves</b>: dashes are wave crests, drifting the way the waves travel.${w.field ? ` Colour is wave speed.
    <div class="ramp"></div><div class="ticks"><span>${tick(0)}</span><span>${tick(0.5)}</span><span>${tick(1)} m/s</span></div>` : ""}
    <div class="src">Copernicus Marine global wave model, ${w.label}</div>`;
  document.body.appendChild(note);

  // ----- The sea coloured by wave speed: one image, rows spaced evenly in Web Mercator like the map -----
  if (w.field) {
    map.createPane("wavefield").style.zIndex = 350;   // above the base map, below the UKC and area overlays
    const south = w.lat0, north = w.lat0 + (w.ny - 1) * w.dlat, west = w.lon0, east = w.lon0 + (w.nx - 1) * w.dlon;
    const IW = (w.nx - 1) * 4, IH = (w.ny - 1) * 4, img = document.createElement("canvas");
    img.width = IW; img.height = IH;
    const g = img.getContext("2d"), px = g.createImageData(IW, IH), yTop = merc(north), yBot = merc(south);
    for (let r = 0; r < IH; r++) {
      const y = yTop + (r + 0.5) / IH * (yBot - yTop), lat = (2 * Math.atan(Math.exp(y)) - Math.PI / 2) / rad;
      for (let c = 0; c < IW; c++) {
        const f = waveAt(lat, west + (c + 0.5) / IW * (east - west));
        if (!f) continue;
        const col = waveColor(Math.hypot(f.u, f.v)), o = (r * IW + c) * 4;
        px.data[o] = col[0]; px.data[o + 1] = col[1]; px.data[o + 2] = col[2]; px.data[o + 3] = 255;
      }
    }
    g.putImageData(px, 0, 0);
    L.imageOverlay(img.toDataURL(), [[south, west], [north, east]],
      { pane: "wavefield", opacity: opts.fieldOpacity || 0.7, interactive: false }).addTo(map);
  }

  // ----- Wave crests: many short dashes across the direction of travel, drifting slowly with the waves -----
  map.createPane("waves").style.zIndex = 420;   // above the coloured overlays, below routes and ships
  const cv = L.DomUtil.create("canvas", "", map.getPane("waves")), ctx = cv.getContext("2d");
  const PX_PER_MS = 0.055;                       // screen pixels per frame for each m/s of group speed (~30 px/s)
  const INK = w.field ? "255,255,255" : "13,43,69";   // white on the coloured sea, navy on the plain map
  const FADES = [0.25, 0.5, 0.75, 0.95];
  let dashes = [], zooming = false;

  // Dashes live in lat/lon, so they stay on the sea while the map pans (including Sail's follow view).
  function spawn(p) {
    const size = map.getSize();
    for (let tries = 0; tries < 20; tries++) {
      const pt = map.containerPointToLatLng([Math.random() * size.x, Math.random() * size.y]);
      if (waveAt(pt.lat, pt.lng)) {
        p.ll = pt; p.age = 0; p.life = 60 + Math.floor(Math.random() * 100); p.size = 0.7 + Math.random() * 0.6;
        return p;
      }
    }
    p.ll = null; return p;
  }
  function respawnAll() {
    const size = map.getSize();
    dashes = Array.from({ length: Math.round(Math.min(4500, size.x * size.y / 240)) }, () => {
      const p = spawn({}); if (p.ll) p.age = Math.floor(Math.random() * p.life); return p;
    });
  }

  function frame() {
    requestAnimationFrame(frame);
    const size = map.getSize();
    if (cv.width !== size.x || cv.height !== size.y) { cv.width = size.x; cv.height = size.y; }
    L.DomUtil.setPosition(cv, map.containerPointToLayerPoint([0, 0]));
    ctx.clearRect(0, 0, size.x, size.y);
    if (zooming) return;
    const paths = FADES.map(() => new Path2D()), tails = new Path2D();
    for (const p of dashes) {
      if (!p.ll || p.age++ >= p.life) { spawn(p); continue; }
      const f = waveAt(p.ll.lat, p.ll.lng), a = map.latLngToContainerPoint(p.ll);
      if (!f || a.x < -10 || a.y < -10 || a.x > size.x + 10 || a.y > size.y + 10) { spawn(p); continue; }
      const s = Math.hypot(f.u, f.v) || 1, tx = f.u / s, ty = -f.v / s;      // travel direction on screen
      const half = (2 + s * 0.22) * p.size;                                 // crest half-length, longer for faster swell
      // fade in and out over the dash's life (clamped: sin(pi) can come out a hair below zero)
      const fade = Math.max(0, Math.sin(Math.PI * p.age / p.life)), path = paths[Math.min(3, Math.floor(fade * 4))];
      path.moveTo(a.x + ty * half, a.y - tx * half); path.lineTo(a.x - ty * half, a.y + tx * half);
      if (fade > 0.5) {   // a faint copy just behind the crest reads as motion
        const bx = a.x - tx * 2.5, by = a.y - ty * 2.5;
        tails.moveTo(bx + ty * half, by - tx * half); tails.lineTo(bx - ty * half, by + tx * half);
      }
      p.ll = map.containerPointToLatLng([a.x + tx * s * PX_PER_MS, a.y + ty * s * PX_PER_MS]);
    }
    ctx.lineCap = "round";
    ctx.lineWidth = 1.4; ctx.strokeStyle = `rgba(${INK},0.22)`; ctx.stroke(tails);
    ctx.lineWidth = 1.6;
    paths.forEach((path, k) => { ctx.strokeStyle = `rgba(${INK},${FADES[k]})`; ctx.stroke(path); });
  }

  map.on("zoomstart", () => { zooming = true; });
  map.on("zoomend", () => { zooming = false; respawnAll(); });
  map.on("resize", respawnAll);
  respawnAll();
  requestAnimationFrame(frame);
  return waveAt;
}

// Wave layer shared by the UKC what-if and Sail planning maps (inlined by common.render_map).
// Data: one time step of the Copernicus Marine global wave model (GLOBAL_ANALYSISFORECAST_WAV_001_027),
// u and v = deep-water group velocity in the direction the waves travel (m/s, east and north), null on land.

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

// Particles that drift across the sea the way the waves travel, with fading trails. Returns the sampler.
function addWaveLayer(map, w, opts = {}) {
  const waveAt = waveSampler(w);

  const style = document.createElement("style");
  style.textContent = `.wave-note { left:12px; bottom:${opts.bottom || 40}px; padding:8px 12px; font-size:12.5px;
      line-height:1.45; max-width:260px; }
    .wave-note b { font-weight:600; }
    .wave-note .src { color:#5B7385; font-size:11.5px; }
    .leaflet-waves-pane canvas { pointer-events:none; }`;
  document.head.appendChild(style);
  const note = document.createElement("div");
  note.className = "panel wave-note";
  note.innerHTML = `<b>Waves</b>: lines drift the way the waves travel, faster where they travel faster.
    <div class="src">Copernicus Marine global wave model, ${w.label}</div>`;
  document.body.appendChild(note);

  map.createPane("waves").style.zIndex = 420;   // above the coloured overlays, below routes and ships
  const cv = L.DomUtil.create("canvas", "", map.getPane("waves")), ctx = cv.getContext("2d");
  const MAX_AGE = 100, PX_PER_MS = 0.16;          // screen pixels per frame for each m/s of group speed
  let parts = [], zooming = false, shifted = true;

  // Particles live in lat/lon, so they stay on the sea while the map pans (including Sail's follow view).
  function spawn(p) {
    const size = map.getSize();
    for (let tries = 0; tries < 20; tries++) {
      const pt = map.containerPointToLatLng([Math.random() * size.x, Math.random() * size.y]);
      if (waveAt(pt.lat, pt.lng)) { p.ll = pt; p.age = Math.floor(Math.random() * MAX_AGE); return p; }
    }
    p.ll = null; return p;
  }
  function respawnAll() {
    const size = map.getSize();
    parts = Array.from({ length: Math.round(Math.min(3000, size.x * size.y / 420)) }, () => spawn({}));
    shifted = true;
  }

  function frame() {
    requestAnimationFrame(frame);
    if (zooming) return;
    const size = map.getSize();
    if (cv.width !== size.x || cv.height !== size.y) { cv.width = size.x; cv.height = size.y; shifted = true; }
    L.DomUtil.setPosition(cv, map.containerPointToLayerPoint([0, 0]));
    if (shifted) { ctx.clearRect(0, 0, size.x, size.y); shifted = false; }   // old trails no longer line up
    else {
      ctx.globalCompositeOperation = "destination-in";
      ctx.fillStyle = "rgba(0,0,0,0.92)"; ctx.fillRect(0, 0, size.x, size.y);   // fade the trails
      ctx.globalCompositeOperation = "source-over";
    }
    const slow = new Path2D(), mid = new Path2D(), fast = new Path2D();
    for (const p of parts) {
      if (!p.ll || p.age++ >= MAX_AGE) { spawn(p); continue; }
      const f = waveAt(p.ll.lat, p.ll.lng), a = map.latLngToContainerPoint(p.ll);
      if (!f || a.x < 0 || a.y < 0 || a.x > size.x || a.y > size.y) { spawn(p); continue; }
      const bx = a.x + f.u * PX_PER_MS, by = a.y - f.v * PX_PER_MS, s = Math.hypot(f.u, f.v);
      const path = s < 6 ? slow : s < 10 ? mid : fast;
      path.moveTo(a.x, a.y); path.lineTo(bx, by);
      p.ll = map.containerPointToLatLng([bx, by]);
    }
    ctx.lineWidth = 1.5; ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(13,43,69,0.35)"; ctx.stroke(slow);
    ctx.strokeStyle = "rgba(13,43,69,0.6)"; ctx.stroke(mid);
    ctx.strokeStyle = "rgba(13,43,69,0.9)"; ctx.stroke(fast);
  }

  map.on("move resize", () => { shifted = true; });
  map.on("zoomstart", () => { zooming = true; ctx.clearRect(0, 0, cv.width, cv.height); });
  map.on("zoomend", () => { zooming = false; respawnAll(); });
  respawnAll();
  requestAnimationFrame(frame);
  return waveAt;
}

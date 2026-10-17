"""
Sail-planning route finder. No Streamlit in here.

How it works
1. Cut the depth grid to a corridor box around source and destination (nothing outside is used).
2. A cell is navigable when
     - UKC at the effective draft is at least the minimum UKC %, and
     - it is at least "required clearance" metres from the nearest too-shallow cell
       (clearance = beam x width factor / 2 + side margin), so the ship has room either side.
3. Each cell gets a cost: 1 + preference x (depth risk + closeness to shallows).
   Preference 0 gives the shortest route; higher values pay extra distance for safer water.
4. Dijkstra's algorithm on the 8-connected grid finds the cheapest path (no corner cutting).
5. The grid path is "string pulled" into straight legs that stay in navigable water.
6. Every ~half cell along the legs gets a confidence score from UKC margin and clearance.
"""
import math

import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.csgraph import dijkstra

from ukc import classify, ukc_percent

M_PER_DEG = 111_320.0
M_PER_NM = 1852.0
MAX_CELLS = 400_000  # corridor grid size; bigger corridors are thinned to this

BANDS = [  # by confidence, best first
    {"name": "Confident", "min": 70, "color": "#2E7D32"},
    {"name": "Take care", "min": 40, "color": "#F9A825"},
    {"name": "Caution", "min": 0, "color": "#C62828"},
]
AREAS = [  # navigability overlay classes 1..4
    {"name": "Sailable, reachable from start", "color": "#4DB6AC"},
    {"name": "Sailable, but cut off from start", "color": "#B0BEC5"},
    {"name": "Deep enough, too close to shallows", "color": "#FFB74D"},
    {"name": "Too shallow for this draft", "color": "#EF5350"},
]
RISK_BY_CATEGORY = np.array([3.0, 1.5, 0.6, 0.15, 0.0])  # critical .. deep


def band_of(conf):
    for i, b in enumerate(BANDS):
        if conf >= b["min"]:
            return i
    return len(BANDS) - 1


def haversine_m(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def bearing_deg(lon1, lat1, lon2, lat2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def corridor_box(src, dst, pad_km):
    (slon, slat), (dlon, dlat) = src, dst
    lat_mid = (slat + dlat) / 2
    plat = pad_km / 111.32
    plon = pad_km / (111.32 * max(0.01, math.cos(math.radians(lat_mid))))
    return (min(slon, dlon) - plon, min(slat, dlat) - plat,
            max(slon, dlon) + plon, max(slat, dlat) + plat)


def _build_graph(nav, cost, cy, cx):
    ny, nx = nav.shape
    idx = np.arange(ny * nx).reshape(ny, nx)
    rows, cols, wts = [], [], []
    for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:  # the other 4 directions are the same edges
        ys, xs = slice(0, ny - dy), slice(max(0, -dx), nx - max(0, dx))
        yt, xt = slice(dy, ny), slice(max(0, dx), nx + min(0, dx))
        ok = nav[ys, xs] & nav[yt, xt]
        if dy and dx:  # diagonal: both side cells must be open too
            ok &= nav[ys, xt] & nav[yt, xs]
        step = math.hypot(dy * cy, dx * cx)
        w = step * (cost[ys, xs] + cost[yt, xt]) / 2
        rows.append(idx[ys, xs][ok])
        cols.append(idx[yt, xt][ok])
        wts.append(w[ok])
    n = ny * nx
    return sparse.coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))),
                             shape=(n, n)).tocsr()


def _line_clear(nav, p, q):
    n = int(3 * max(abs(q[0] - p[0]), abs(q[1] - p[1]))) + 2
    ys = np.rint(np.linspace(p[0], q[0], n)).astype(int)
    xs = np.rint(np.linspace(p[1], q[1], n)).astype(int)
    return bool(nav[ys, xs].all())


def _string_pull(nav, pts):
    """Keep only the turning points: jump as far ahead as a straight, clear line allows."""
    out, i, last = [pts[0]], 0, len(pts) - 1
    while i < last:
        j, step = i + 1, 1
        while j + step <= last and _line_clear(nav, pts[i], pts[j + step]):
            j += step
            step *= 2
        hi = min(j + step, last + 1)
        while hi - j > 1:
            mid = (j + hi) // 2
            if _line_clear(nav, pts[i], pts[mid]):
                j = mid
            else:
                hi = mid
        out.append(pts[j])
        i = j
    return out


def plan_route(grid, src, dst, *, water_level, teff, beam, width_factor, side_margin,
               min_ukc, preference, pad_km, max_cells=MAX_CELLS):
    box = corridor_box(src, dst, pad_km)
    win = grid.window(*box, max_cells=max_cells)
    if win is None:
        return {"error": "Start and destination are outside the bathymetry area.", "box": box}

    z, cell = win["z"], win["cell"]
    lat_mid = (win["s"] + win["n"]) / 2
    cy, cx = cell * M_PER_DEG, cell * M_PER_DEG * math.cos(math.radians(lat_mid))
    half_cell = min(cy, cx) / 2

    # 1-2. Navigability
    h = water_level - z
    with np.errstate(invalid="ignore", divide="ignore"):
        ukc = np.where(h > 0, ukc_percent(h, teff), np.nan)
    depth_ok = np.nan_to_num(ukc, nan=-1e9) >= min_ukc
    clear_need = beam * width_factor / 2 + side_margin
    clearance = np.maximum(ndimage.distance_transform_edt(depth_ok, sampling=(cy, cx)) - half_cell, 0)
    nav = depth_ok & (clearance >= clear_need)

    out = {"box": box, "win": {k: win[k] for k in ("w", "s", "e", "n", "cell", "stride")},
           "clear_need": clear_need, "cell_m": min(cy, cx)}

    if not nav.any():
        out["classes"] = _classes(h, depth_ok, nav, np.zeros_like(nav))
        out["error"] = "No water in the corridor is deep and wide enough for this ship."
        return out

    # 3. Cost
    cat = classify(ukc)
    risk = np.where(cat >= 0, RISK_BY_CATEGORY[np.clip(cat, 0, 4)], 3.0)
    near = np.clip(1 - clearance / (4 * clear_need), 0, 1)
    cost = 1 + preference * (risk + near)

    # Snap start/destination to the nearest navigable cell
    _, (sy_i, sx_i) = ndimage.distance_transform_edt(~nav, sampling=(cy, cx), return_indices=True)
    ny, nx = nav.shape

    def to_cell(lon, lat):
        ix = int(np.clip((lon - win["w"]) / cell, 0, nx - 1))
        iy = int(np.clip((lat - win["s"]) / cell, 0, ny - 1))
        return iy, ix

    def center(iy, ix):
        return win["w"] + (ix + 0.5) * cell, win["s"] + (iy + 0.5) * cell

    snaps = []
    for name, (lon, lat) in (("start", src), ("destination", dst)):
        iy, ix = to_cell(lon, lat)
        jy, jx = int(sy_i[iy, ix]), int(sx_i[iy, ix])
        clon, clat = center(jy, jx)
        snaps.append({"name": name, "cell": (jy, jx), "moved_m": haversine_m(lon, lat, clon, clat),
                      "lonlat": (clon, clat)})
    out["snaps"] = [{k: v for k, v in s.items() if k != "cell"} for s in snaps]

    # 4. Dijkstra
    graph = _build_graph(nav, cost, cy, cx)
    s_idx = snaps[0]["cell"][0] * nx + snaps[0]["cell"][1]
    d_idx = snaps[1]["cell"][0] * nx + snaps[1]["cell"][1]
    dist, pred = dijkstra(graph, directed=False, indices=s_idx, return_predecessors=True)
    reachable = np.isfinite(dist).reshape(ny, nx)
    out["classes"] = _classes(h, depth_ok, nav, reachable)

    if not np.isfinite(dist[d_idx]):
        out["error"] = ("No safe connection: the destination can't be reached through water that is "
                        "deep and wide enough. The teal area shows where the ship can go from the start.")
        return out

    path, k = [], d_idx
    while k != s_idx and k >= 0:
        path.append(divmod(int(k), nx))
        k = pred[k]
    path.append(divmod(int(s_idx), nx))
    path.reverse()

    # 5. Smooth into straight legs
    turns = _string_pull(nav, path)
    waypoints = [center(iy, ix) for iy, ix in turns]
    # Close to the real positions, start/end exactly there; if snapped far, the map shows a connector
    for end, i in ((0, 0), (1, -1)):
        if snaps[end]["moved_m"] <= min(cy, cx):
            waypoints[i] = (src, dst)[end]

    # 6. Sample along the legs and score confidence
    samples = []
    for (p, q), (a, b) in zip(zip(turns[:-1], turns[1:]), zip(waypoints[:-1], waypoints[1:])):
        n = int(2 * max(abs(q[0] - p[0]), abs(q[1] - p[1]))) + 2
        for t in np.linspace(0, 1, n)[:-1]:
            iy = int(round(p[0] + (q[0] - p[0]) * t))
            ix = int(round(p[1] + (q[1] - p[1]) * t))
            samples.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                            float(h[iy, ix]), float(ukc[iy, ix]), float(clearance[iy, ix])))
    iy, ix = turns[-1]
    samples.append((*waypoints[-1], float(h[iy, ix]), float(ukc[iy, ix]), float(clearance[iy, ix])))

    s_ukc = np.array([s[3] for s in samples])
    s_clr = np.array([s[4] for s in samples])
    ukc_score = np.clip((s_ukc - min_ukc) / max(100 - min_ukc, 1), 0, 1)
    clr_score = np.clip(s_clr / (3 * clear_need), 0, 1)
    conf = np.nan_to_num(100 * (0.6 * ukc_score + 0.4 * clr_score), nan=0.0)
    if win["stride"] > 1:
        conf *= 0.9  # coarser data in a very large corridor
    # Smooth along the track (about 3 cells) so bands don't flicker sample to sample
    k = min(7, len(conf))
    if k > 1:
        pad = np.pad(conf, (k // 2, k - 1 - k // 2), mode="edge")
        conf = np.convolve(pad, np.ones(k) / k, mode="valid")
    bands = [band_of(c) for c in conf]
    bands = _merge_short_runs(bands, min_len=6)

    # Coloured stretches and caution zones
    segments, zones = [], []
    start = 0
    for i in range(1, len(samples) + 1):
        if i == len(samples) or bands[i] != bands[start]:
            j = min(i, len(samples) - 1)
            coords = [[s[0], s[1]] for s in samples[start:j + 1]]
            seg_len = sum(haversine_m(*coords[k], *coords[k + 1]) for k in range(len(coords) - 1))
            seg = {"band": bands[start], "coords": coords, "length_m": seg_len,
                   "conf": float(conf[start:j + 1].mean()),
                   "min_ukc": float(np.nanmin(s_ukc[start:j + 1])),
                   "min_clear": float(s_clr[start:j + 1].min())}
            segments.append(seg)
            if bands[start] > 0 and seg_len > 0:
                zones.append(seg)
            start = i

    legs, total = [], 0.0
    for k, (a, b) in enumerate(zip(waypoints[:-1], waypoints[1:]), start=1):
        d = haversine_m(*a, *b)
        total += d
        legs.append({"leg": k, "from_lon": a[0], "from_lat": a[1], "to_lon": b[0], "to_lat": b[1],
                     "course_deg": bearing_deg(*a, *b), "dist_nm": d / M_PER_NM, "cum_nm": total / M_PER_NM})

    out.update({
        "waypoints": waypoints, "legs": legs, "segments": segments, "zones": zones,
        "length_nm": total / M_PER_NM,
        "straight_nm": haversine_m(*src, *dst) / M_PER_NM,
        "conf_mean": float(conf.mean()), "conf_min": float(conf.min()),
        "min_ukc": float(np.nanmin(s_ukc)), "min_clear": float(s_clr.min()),
        "min_h": float(np.nanmin([s[2] for s in samples])),
    })
    return out


def _classes(h, depth_ok, nav, reachable):
    c = np.zeros(h.shape, dtype=np.uint8)            # 0 = dry or no data
    c[(h > 0) & ~depth_ok] = 4
    c[depth_ok & ~nav] = 3
    c[nav & ~reachable] = 2
    c[nav & reachable] = 1
    return c


def _merge_short_runs(bands, min_len):
    """Absorb runs shorter than min_len samples into the worse of their neighbours."""
    bands = list(bands)
    changed = True
    while changed:
        changed = False
        runs, s = [], 0
        for i in range(1, len(bands) + 1):
            if i == len(bands) or bands[i] != bands[s]:
                runs.append((s, i))
                s = i
        if len(runs) < 2:
            break
        for r, (a, b) in enumerate(runs):
            if b - a >= min_len:
                continue
            left = bands[a - 1] if a > 0 else None
            right = bands[b] if b < len(bands) else None
            target = max(x for x in (left, right) if x is not None)  # higher index = worse band
            bands[a:b] = [target] * (b - a)
            changed = True
            break
    return bands

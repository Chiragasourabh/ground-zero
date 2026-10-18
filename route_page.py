"""Page 2: sail planning."""
import base64

import numpy as np
import pandas as pd
import streamlit as st

from common import (JS_CATEGORIES, box_feature, contour_layer, load_grid, map_limits, png_data_url,
                    render_map, require_ships, to_web_mercator)
from routing import AREAS, BANDS, M_PER_NM, MAX_CELLS, plan_route
from ukc import barrass_squat, hex_to_rgb

CB = 0.8  # block coefficient for squat on this page

# Harbour positions, each moved onto the nearest grid water at least 12 m deep (within about 1 km).
# The route still snaps to the nearest water deep and wide enough for the ship.
PORTS = {
    "Brest": (-4.4860, 48.3710),
    "Roscoff": (-3.9620, 48.7200),
    "Lorient": (-3.3580, 47.7300),
    "Saint-Nazaire": (-2.1980, 47.2660),
    "La Rochelle (La Pallice)": (-1.2280, 46.1580),
    "Bordeaux (Le Verdon)": (-1.0580, 45.5640),
    "Bayonne": (-1.5260, 43.5300),
    "Saint-Malo": (-2.0380, 48.6460),
    "Cherbourg": (-1.6180, 49.6500),
    "Le Havre": (0.0920, 49.4780),
    "Calais": (1.8620, 50.9700),
    "Dunkerque": (2.3520, 51.0600),
}


def picked(name):
    """A point chosen on the map ("Start here" / "Destination here"), passed in the URL as lon,lat."""
    try:
        lon, lat = (float(v) for v in st.query_params.get(name, "").split(","))
        return lon, lat
    except ValueError:
        return None


picked_src, picked_dst, picked_ship = picked("start"), picked("dest"), st.query_params.get("ship")

ships = require_ships()
grid = load_grid()
sb = st.sidebar

sb.title("Sail planning")
sb.caption("Finds a short, safe route for the ship's draft and width, and marks where to take care.")

if grid is None:
    sb.warning("No bathymetry grid found. Run `docker compose run --rm --build preprocess`, then rebuild.")
    st.stop()

# ---------- Ship ----------
status_by_id = dict(zip(ships["id_ano"], ships["Statut_de_navigation"].astype(str)))
ids = ships["id_ano"].tolist()
ship_id = sb.selectbox("Ship", ids, index=ids.index(picked_ship) if picked_ship in ids else 0,
                       format_func=lambda i: f"{i} ({status_by_id.get(i, '')})", key="route_ship")
ship = ships.loc[ships["id_ano"] == ship_id].iloc[0]
recorded = float(ship["Tirant_d_eau"]) if pd.notna(ship["Tirant_d_eau"]) else 0.0

c1, c2 = sb.columns(2)
draft = c1.number_input("Draft T (m)", 0.1, 30.0, max(0.1, recorded), 0.1, key=f"rdraft_{ship_id}",
                        help=f"Pre-filled from the ship data ({recorded:.2f} m).")
beam = c2.number_input("Beam (m)", 2.0, 80.0, 25.0, 1.0, key=f"beam_{ship_id}",
                       help="Ship width. Not in the ship file, so enter it here.")
c3, c4 = sb.columns(2)
speed = c3.number_input("Speed (knots)", 1.0, 30.0, 10.0, 0.5, key="rspeed")
water_level = c4.number_input("Water level (m)", -3.0, 8.0, 0.0, 0.1, key="rwl",
                              help="Height above chart datum, such as tide.")
use_squat = sb.checkbox("Add squat at this speed", value=True, key="rsquat")
squat = barrass_squat(CB, speed, False) if use_squat else 0.0
teff = draft + squat

# ---------- Start and destination ----------
sb.subheader("Voyage")
src_mode = sb.radio("Start", ["Ship position", "Port", "Coordinates"], index=2 if picked_src else 0,
                    horizontal=True, key="src_mode")
if src_mode == "Ship position":
    src = (float(ship["Longitude"]), float(ship["Latitude"]))
elif src_mode == "Port":
    src = PORTS[sb.selectbox("Start port", list(PORTS), key="sport")]
else:
    slon, slat = picked_src or (float(ship["Longitude"]), float(ship["Latitude"]))
    a, b = sb.columns(2)
    src = (b.number_input("Start lon", value=slon, format="%.4f", key="slon"),
           a.number_input("Start lat", value=slat, format="%.4f", key="slat"))

dst_mode = sb.radio("Destination", ["Another ship", "Port", "Coordinates"], index=2 if picked_dst else 0,
                    horizontal=True, key="dst_mode")
others = [i for i in ids if i != ship_id]
if dst_mode == "Another ship" and others:
    far = ships.loc[ships["id_ano"] != ship_id].assign(
        d=lambda d: (d["Longitude"] - src[0]) ** 2 + (d["Latitude"] - src[1]) ** 2)["d"].idxmax()
    default = others.index(ships.loc[far, "id_ano"])
    dst_id = sb.selectbox("Destination ship", others, index=default, key=f"dst_{ship_id}")
    d = ships.loc[ships["id_ano"] == dst_id].iloc[0]
    dst = (float(d["Longitude"]), float(d["Latitude"]))
    dst_label = f"Ship {dst_id}"
elif dst_mode == "Port":
    dst_label = sb.selectbox("Destination port", list(PORTS), key="dport")
    dst = PORTS[dst_label]
else:
    w, s, e, n = grid.bounds()
    dlon, dlat = picked_dst or (round((w + e) / 2, 4), round((s + n) / 2, 4))
    a, b = sb.columns(2)
    dst = (b.number_input("Dest lon", value=dlon, format="%.4f", key="dlon"),
           a.number_input("Dest lat", value=dlat, format="%.4f", key="dlat"))
    dst_label = "Destination"
sb.caption("Tip: click the map and choose Start here or Destination here.")

with sb.expander("Safety rules"):
    min_ukc = st.select_slider("Minimum UKC", options=[10, 20, 50], value=20,
                               format_func=lambda v: f"{v}%", key="rminukc",
                               help="Water with less clearance than this is avoided.")
    width_factor = st.slider("Clear width needed (x beam)", 1.0, 8.0, 3.0, 0.5, key="rwf")
    side_margin = st.number_input("Extra side margin (m)", 0, 1000, 50, 10, key="rmargin")
    preference = st.slider("Route preference", 0.0, 5.0, 1.5, 0.5, key="rpref",
                           help="0 = shortest possible. Higher = goes further to stay in deeper water.")
    st.caption("0 is shortest, 5 is safest.")
    pad_km = st.slider("Search corridor around the voyage (km)", 2, 60, 15, key="rpad")

results = sb.container()

sb.subheader("Layers")
show_areas = sb.checkbox("Sailable area", value=True, key="rareas")
opacity = sb.slider("Area opacity", 0.1, 1.0, 0.45, 0.05, disabled=not show_areas, key="ropac")
show_contours = sb.checkbox("Bathymetry contours", value=False, key="rcont")
show_ships = sb.checkbox("Other ships", value=True, key="rships")


# ---------- Plan (cached: same inputs give an instant answer) ----------
@st.cache_data(show_spinner="Planning route...", max_entries=32)
def cached_plan(_grid, grid_key, src, dst, water_level, teff, beam, width_factor, side_margin,
                min_ukc, preference, pad_km):
    return plan_route(_grid, src, dst, water_level=water_level, teff=teff, beam=beam,
                      width_factor=width_factor, side_margin=side_margin, min_ukc=min_ukc,
                      preference=preference, pad_km=pad_km)


# r = cached_plan(grid, grid.bounds(), src, dst, water_level, round(teff, 3), beam, width_factor,
#                 side_margin, min_ukc, preference, pad_km)

# Widen the corridor automatically when land or shallows block every route inside it
gw, gs, ge, gn = grid.bounds()
used_pad = pad_km
while True:
    r = cached_plan(grid, grid.bounds(), src, dst, water_level, round(teff, 3), beam, width_factor,
                    side_margin, min_ukc, preference, used_pad)
    w, s, e, n = r["box"]
    covers_all = w <= gw and s <= gs and e >= ge and n >= gn
    if "error" not in r or "outside the bathymetry" in r["error"] or covers_all:
        break
    used_pad *= 2  # 15 -> 30 -> 60 -> 120 km ... until a route is found or the whole map is used

if used_pad != pad_km:
    sb.info(f"The direct corridor was blocked, so the search was widened to {used_pad} km.")

# ---------- Sidebar results ----------
with results:
    st.subheader("Route")
    if "error" in r:
        st.error(r["error"])
        st.caption("Try a higher water level, a lower minimum UKC, a smaller width factor, "
                   "or a wider search corridor.")
    else:
        m1, m2 = st.columns(2)
        m1.metric("Distance", f"{r['length_nm']:.1f} nm",
                  delta=f"+{(r['length_nm'] / max(r['straight_nm'], 1e-6) - 1) * 100:.0f}% vs straight",
                  delta_color="off")
        hours = r["length_nm"] / speed
        m2.metric("Time at speed", f"{int(hours)} h {int(round((hours % 1) * 60)):02d} min")
        m3, m4 = st.columns(2)
        m3.metric("Confidence", f"{r['conf_mean']:.0f}%", delta=f"lowest {r['conf_min']:.0f}%",
                  delta_color="off")
        m4.metric("Lowest UKC", f"{r['min_ukc']:.0f}%", delta=f"min depth {r['min_h']:.1f} m",
                  delta_color="off")
        if use_squat:
            st.caption(f"Draft {draft:.2f} m + squat {squat:.2f} m at {speed:.1f} kn = {teff:.2f} m.")
        st.caption(f"Needs {r['clear_need']:.0f} m clear water each side of the track. "
                   f"Grid cells are about {r['cell_m']:.0f} m, so narrower channels can't be resolved.")
        for snap in r["snaps"]:
            if snap["moved_m"] > r["cell_m"]:
                where = "starts" if snap["name"] == "start" else "ends"
                msg = (f"The {snap['name']} is in water too shallow or narrow for this ship. The safe route "
                       f"{where} at the nearest sailable point, {snap['moved_m'] / M_PER_NM:.2f} nm away "
                       f"(red dashed line on the map). That stretch is not covered by the route.")
                (st.error if snap["moved_m"] > 1000 else st.warning)(msg)

        zones = sorted(r["zones"], key=lambda z: (-z["band"], z["conf"]))
        if zones:
            with st.expander(f"Take care: {len(zones)} stretch{'es' if len(zones) > 1 else ''}", expanded=True):
                for z in zones:
                    b = BANDS[z["band"]]
                    lon, lat = z["coords"][0]
                    st.markdown(
                        f"<div style='border-left:5px solid {b['color']};padding:2px 8px;margin:6px 0'>"
                        f"<b>{b['name']}</b>, {z['length_m'] / M_PER_NM:.2f} nm from {lat:.4f}, {lon:.4f}<br>"
                        f"<span style='font-size:0.85em'>Lowest UKC {z['min_ukc']:.0f}%, "
                        f"nearest shallows {z['min_clear']:.0f} m, confidence {z['conf']:.0f}%</span></div>",
                        unsafe_allow_html=True)
        else:
            st.success("No caution stretches on this route.")

        legs = pd.DataFrame(r["legs"])
        with st.expander(f"Waypoints ({len(legs) + 1})"):
            wp = pd.DataFrame([{"WP": i, "Lat": lat, "Lon": lon} for i, (lon, lat) in enumerate(r["waypoints"])])
            wp["Course to next (°)"] = list(legs["course_deg"].round(0)) + [None]
            wp["Leg (nm)"] = list(legs["dist_nm"].round(2)) + [None]
            st.dataframe(wp.round({"Lat": 5, "Lon": 5}), hide_index=True, use_container_width=True)
            st.download_button("Download waypoints (CSV)", wp.to_csv(index=False),
                               file_name=f"route_{ship_id}.csv", mime="text/csv")
    st.divider()

# ---------- Map payload ----------
payload = {"maxBounds": map_limits(grid, ships), "fitBounds": [[r["box"][0], r["box"][1]], [r["box"][2], r["box"][3]]],
           "viewKey": f"route|{ship_id}|{src}|{dst}|{pad_km}", "shipId": ship_id, "corridor": box_feature(r["box"]),
           "src": src, "dst": dst, "dstLabel": dst_label, "bands": BANDS, "areas": AREAS,
           "showAreas": show_areas, "opacity": opacity, "error": r.get("error"),
           "contours": contour_layer(show_contours), "image": None, "route": None, "ships": None,
           "categories": JS_CATEGORIES, "game": None}

if show_areas and "classes" in r:
    cls = r["classes"]
    rgba = np.zeros(cls.shape + (4,), dtype=np.uint8)
    for k, a in enumerate(AREAS, start=1):
        rgba[cls == k] = (*hex_to_rgb(a["color"]), 255)
    wn = r["win"]
    payload["image"] = {"url": png_data_url(to_web_mercator(np.flipud(rgba), wn["s"], wn["n"])),
                        "bounds": [[wn["s"], wn["w"]], [wn["n"], wn["e"]]]}

if "error" not in r:
    payload["route"] = {
        "segments": [{"coords": s["coords"], "band": s["band"], "conf": s["conf"],
                      "min_ukc": s["min_ukc"], "min_clear": s["min_clear"], "length_nm": s["length_m"] / M_PER_NM}
                     for s in r["segments"]],
        "waypoints": [{"lon": lon, "lat": lat,
                       "course": r["legs"][i]["course_deg"] if i < len(r["legs"]) else None,
                       "leg_nm": r["legs"][i]["dist_nm"] if i < len(r["legs"]) else None}
                      for i, (lon, lat) in enumerate(r["waypoints"])],
        "connectors": [{"from": list((src, dst)[i]), "to": list(sn["lonlat"]), "moved_m": sn["moved_m"], "name": sn["name"]}
                       for i, sn in enumerate(r["snaps"]) if sn["moved_m"] > r["cell_m"]],
        "length_nm": r["length_nm"], "conf": r["conf_mean"], "conf_min": r["conf_min"],
        "min_ukc": r["min_ukc"], "hours": r["length_nm"] / speed, "speed": speed, "ship": ship_id, "teff": teff,
    }
    # Water depth over the corridor, for the "water says" time and the plot-your-own-route game that
    # run in the browser. Same window as the route search; decimetres as int16, -32768 = no data.
    win = grid.window(*r["box"], max_cells=MAX_CELLS)
    h = water_level - win["z"]
    dm = np.where(np.isnan(h), -32768, np.round(np.clip(h, -3000, 3000) * 10)).astype("<i2")
    payload["game"] = {"w": win["w"], "s": win["s"], "cell": win["cell"], "nx": int(h.shape[1]),
                       "ny": int(h.shape[0]), "depth_dm": base64.b64encode(dm.tobytes()).decode(),
                       "draft": draft, "cb": CB, "squat": use_squat, "speed": speed}

if show_ships:
    payload["ships"] = [{"id": s.id_ano, "lon": float(s.Longitude), "lat": float(s.Latitude),
                         "statut": str(s.Statut_de_navigation), "draft": float(s.Tirant_d_eau)}
                        for s in ships.itertuples() if s.id_ano != ship_id]

render_map("route_template.html", payload)

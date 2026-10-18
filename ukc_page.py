"""Page 1: UKC what-if analysis."""
import numpy as np
import pandas as pd
import streamlit as st

from common import (JS_CATEGORIES, box_feature, cat_color, cat_name, contour_layer, load_grid, map_limits,
                    png_data_url, render_map, require_ships, to_web_mercator)
from ukc import CATEGORIES, CAT_RGB, barrass_squat, box_around, classify, ukc_percent

MAX_IMAGE_CELLS = 1_500_000   # UKC overlay is thinned above this
MAX_HOVER_CELLS = 250_000     # per-cell hover values are sent only below this

# ---------- Data ----------
ships = require_ships()
grid = load_grid()

# ---------- Sidebar: inputs ----------
sb = st.sidebar
sb.title("Under-keel clearance")
sb.caption("What-if analysis for the selected ship. Change any value and the map updates.")

status_by_id = dict(zip(ships["id_ano"], ships["Statut_de_navigation"].astype(str)))
ship_id = sb.selectbox("Ship", ships["id_ano"].tolist(),
                       format_func=lambda i: f"{i} ({status_by_id.get(i, '')})")
ship = ships.loc[ships["id_ano"] == ship_id].iloc[0]

c1, c2 = sb.columns(2)
c1.text_input("Navigation status", str(ship["Statut_de_navigation"]), disabled=True)
c2.text_input("Position", f"{ship['Latitude']:.4f}, {ship['Longitude']:.4f}", disabled=True)

recorded_draft = float(ship["Tirant_d_eau"]) if pd.notna(ship["Tirant_d_eau"]) else 0.0
draft = sb.number_input("Draft T (m)", min_value=0.1, max_value=30.0, step=0.1,
                        value=max(0.1, recorded_draft), key=f"draft_{ship_id}",
                        help=f"Pre-filled from the ship data ({recorded_draft:.2f} m). Edit it to test another loading.")
water_level = sb.slider("Water level above chart datum (m)", -3.0, 8.0, 0.0, 0.1,
                        help="Tide or surge. Positive adds to the water depth everywhere.")

with sb.expander("Squat (ship sinkage at speed)"):
    use_squat = st.checkbox("Add squat to the draft", value=False)
    speed = st.number_input("Speed (knots)", 0.0, 30.0, 10.0, 0.5, disabled=not use_squat)
    cb = st.number_input("Block coefficient Cb", 0.50, 0.90, 0.80, 0.01, disabled=not use_squat)
    confined = st.radio("Waterway", ["Open water", "Confined channel"], horizontal=True,
                        disabled=not use_squat) == "Confined channel"
squat = barrass_squat(cb, speed, confined) if use_squat else 0.0
teff = draft + squat

results = sb.container()  # filled after the calculation

sb.subheader("Analysis area")
whole = sb.radio("Calculate UKC for", ["Area around ship", "Whole map"], horizontal=True) == "Whole map"
radius_km = sb.slider("Radius around ship (km)", 1, 50, 10, disabled=whole)
target = sb.select_slider("Target UKC for planning", options=[10, 20, 50, 200], value=20,
                          format_func=lambda v: f"{v}%")

sb.subheader("Layers")
show_ukc = sb.checkbox("UKC colours", value=True)
opacity = sb.slider("UKC opacity", 0.1, 1.0, 0.65, 0.05, disabled=not show_ukc)
show_contours = sb.checkbox("Bathymetry contours", value=True)
show_ships = sb.checkbox("All ships (coloured by their own UKC)", value=True)

# ---------- Calculation (only inside the bounding box) ----------
payload = {"categories": JS_CATEGORIES, "teff": teff, "opacity": opacity, "showUkc": show_ukc,
           "stride": 1, "image": None, "hover": None, "stats": None, "aoi": None,
           "contours": None, "ships": None}

if grid is None:
    sb.warning("No bathymetry grid found. Run `docker compose run --rm --build preprocess`, then rebuild.")
    gw, gs, ge, gn = (ships["Longitude"].min(), ships["Latitude"].min(),
                      ships["Longitude"].max(), ships["Latitude"].max())
    z_ship = np.nan
else:
    gw, gs, ge, gn = grid.bounds()
    z_ship = float(grid.sample(ship["Longitude"], ship["Latitude"])[0])

h_ship = water_level - z_ship  # depth stored as elevation: negative below datum
ukc_ship = ukc_percent(h_ship, teff) if h_ship > 0 else np.nan
cat_ship = int(classify(ukc_ship)[()])

payload["maxBounds"] = map_limits(grid, ships)

if whole:
    box = (gw, gs, ge, gn)
else:
    box = box_around(ship["Latitude"], ship["Longitude"], radius_km)
payload["fitBounds"] = [[box[0], box[1]], [box[2], box[3]]]
payload["viewKey"] = f"{ship_id}|{'all' if whole else radius_km}"
payload["aoi"] = box_feature(box)

min_ukc = below_target = None
if grid is not None:
    win = grid.window(*box, max_cells=MAX_IMAGE_CELLS)
    if win is not None:
        h = water_level - win["z"]
        wet = h > 0
        ukc = np.where(wet, ukc_percent(h, teff), np.nan)
        cat = classify(ukc)

        n_wet = int(wet.sum())
        if n_wet:
            payload["stats"] = {c["name"]: float((cat == i).sum()) / n_wet * 100 for i, c in enumerate(CATEGORIES)}
            min_ukc = float(np.nanmin(ukc))
            below_target = float((ukc[wet] < target).sum()) / n_wet * 100

        if show_ukc:
            rgba = np.zeros(cat.shape + (4,), dtype=np.uint8)
            valid = cat >= 0
            rgba[valid, :3] = CAT_RGB[cat[valid]]
            rgba[valid, 3] = 255
            payload["image"] = {"url": png_data_url(to_web_mercator(np.flipud(rgba), win["s"], win["n"])),  # image row 0 = north
                                "coords": [[win["w"], win["n"]], [win["e"], win["n"]],
                                           [win["e"], win["s"]], [win["w"], win["s"]]]}
            payload["stride"] = win["stride"]

        if h.size <= MAX_HOVER_CELLS:
            hv = np.round(h.astype(float), 1).ravel()
            payload["hover"] = {"w": win["w"], "s": win["s"], "cell": win["cell"],
                                "nx": int(h.shape[1]), "ny": int(h.shape[0]),
                                "h": [None if np.isnan(v) else float(v) for v in hv]}

# Every ship, checked against its own recorded draft (one lookup per ship)
if grid is not None:
    hs = water_level - grid.sample(ships["Longitude"].to_numpy(), ships["Latitude"].to_numpy())
else:
    hs = np.full(len(ships), np.nan)
drafts = pd.to_numeric(ships["Tirant_d_eau"], errors="coerce").to_numpy()
with np.errstate(invalid="ignore", divide="ignore"):
    uk = np.where((hs > 0) & (drafts > 0), ukc_percent(hs, drafts), np.nan)
cats = classify(uk)
feats = []
for i, r in ships.iterrows():
    selected = r["id_ano"] == ship_id
    if not show_ships and not selected:
        continue
    u = ukc_ship if selected else uk[i]
    ci = cat_ship if selected else int(cats[i])
    feats.append({"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [float(r["Longitude"]), float(r["Latitude"])]},
                  "properties": {"id": r["id_ano"], "statut": str(r["Statut_de_navigation"]),
                                 "draft": float(teff if selected else drafts[i]),
                                 "h": None if np.isnan(hs[i]) else round(float(hs[i]), 2),
                                 "ukc": None if np.isnan(u) else round(float(u), 1),
                                 "category": cat_name(ci), "color": cat_color(ci),
                                 "selected": bool(selected)}})
feats.sort(key=lambda f: f["properties"]["selected"])  # selected ship drawn last, on top
payload["ships"] = {"type": "FeatureCollection", "features": feats}

payload["contours"] = contour_layer(show_contours)

payload["readout"] = {"ship": ship_id, "h": None if np.isnan(h_ship) else float(h_ship),
                      "ukc": None if np.isnan(ukc_ship) else float(ukc_ship),
                      "teff": teff, "category": cat_name(cat_ship), "color": cat_color(cat_ship)}

# ---------- Sidebar: results ----------
with results:
    st.subheader("Result at ship position")
    if np.isnan(z_ship):
        st.info("This ship is outside the bathymetry area, so its UKC can't be calculated.")
    elif h_ship <= 0:
        st.error(f"Dry at this water level (h = {h_ship:.1f} m).")
    else:
        if ukc_ship < 0:
            st.error(f"Draft {teff:.2f} m is more than the water depth {h_ship:.1f} m: the ship would ground here.")
        m1, m2 = st.columns(2)
        m1.metric("Water depth h", f"{h_ship:.1f} m")
        m2.metric("UKC", f"{ukc_ship:.0f}%")
        st.markdown(
            f"<div style='padding:6px 10px;border-radius:4px;background:{cat_color(cat_ship)};"
            f"color:{'#000' if cat_ship in (2, 3) else '#fff'};font-weight:600'>{cat_name(cat_ship)}</div>",
            unsafe_allow_html=True)
        if use_squat:
            st.caption(f"Effective draft {teff:.2f} m = {draft:.2f} m draft + {squat:.2f} m squat.")

        max_draft = h_ship / (1 + target / 100) - squat
        need_wl = teff * (1 + target / 100) + z_ship
        m3, m4 = st.columns(2)
        m3.metric(f"Max draft for {target}% UKC", f"{max_draft:.2f} m",
                  delta=f"{max_draft - draft:+.2f} m", delta_color="normal")
        m4.metric(f"Water level for {target}% UKC", f"{need_wl:+.1f} m",
                  delta=f"{need_wl - water_level:+.1f} m", delta_color="inverse")
    if min_ukc is not None:
        st.caption(f"In the analysis area: lowest UKC {min_ukc:.0f}%, "
                   f"{below_target:.0f}% of the water is below the {target}% target.")
    st.divider()

# ---------- Map ----------
render_map("map_template.html", payload)

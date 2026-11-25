"""Shared loading, styling and map rendering for both pages."""
import base64
import io
import json
import os
from pathlib import Path

import pandas as pd
import numpy as np
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from ukc import CATEGORIES, NO_DATA_COLOR, BathyGrid

HERE = Path(__file__).parent
DATA = os.getenv("DATA_DIR", str(HERE / "data"))
SHIPS_CSV = os.getenv("CSV_PATH", f"{DATA}/ship_to_daftvalues.csv")
CONTOURS_JSON = f"{DATA}/contours.json"
GRID_NPY, GRID_META = f"{DATA}/bathy_grid.npy", f"{DATA}/bathy_grid.json"
WAVE_CSV = os.getenv("WAVE_CSV", str(HERE / "WaveData" / "Wave Movement Data.csv"))

FULLSCREEN_CSS = """
<style>
  [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer { display:none !important; }
  .block-container, [data-testid="stMainBlockContainer"] { padding:0 !important; max-width:100% !important; }
  section.main [data-testid="stVerticalBlock"], [data-testid="stMain"] [data-testid="stVerticalBlock"] { gap:0 !important; }
  [data-testid="stMain"], section.main { overflow:hidden; }
  iframe { height:100vh !important; width:100% !important; display:block; border:0; }
  /* Logo bigger than st.logo's 32 px cap, and white on the dark theme (it follows the system setting) */
  [data-testid="stSidebarHeader"] [data-testid="stLogo"] { height:3rem; max-width:100%; }
  @media (prefers-color-scheme: dark) { [data-testid="stLogo"] { filter:invert(1); } }
</style>
"""


# ---------- Loading (cached) ----------
@st.cache_data
def load_ships(path):
    df = pd.read_csv(path)
    df = df.dropna(subset=["Latitude", "Longitude"]).copy()
    df["id_ano"] = df["id_ano"].astype(str)
    return df.sort_values("id_ano").reset_index(drop=True)


@st.cache_resource
def load_grid():  # memory-mapped, shared by all sessions
    if os.path.exists(GRID_NPY) and os.path.exists(GRID_META):
        return BathyGrid(GRID_NPY, GRID_META)
    return None


@st.cache_data
def load_contours(path):
    with open(path) as f:
        data = json.load(f)
    zmin, zmax = data["zmin"], data["zmax"]
    feats = []
    for line in data["lines"]:
        t = (line["depth"] - zmin) / ((zmax - zmin) or 1)  # 0 deepest, 1 shallowest
        r, g, b = int(10 + 140 * t), int(40 + 170 * t), int(120 + 135 * t)
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": line["path"]},
                      "properties": {"depth": line["depth"], "color": f"#{r:02x}{g:02x}{b:02x}"}})
    return {"type": "FeatureCollection", "features": feats}


@st.cache_data
def load_waves(path):
    """Copernicus Marine global wave model (GLOBAL_ANALYSISFORECAST_WAV_001_027, 1/12 degree grid) as one
    velocity field per time step. wave_u / wave_v are the deep-water group velocity in the direction the
    waves travel (m/s, east and north), as exported by WaveData/Extract Wave Notebook.ipynb."""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    if "time" in df.columns:
        steps = [(str(t), g) for t, g in df.groupby("time", sort=True)]
    else:
        # The current export has no time column. Each time step is a block sorted by latitude,
        # so a new block starts wherever the latitude drops back.
        block = (df["latitude"].diff() < 0).cumsum()
        steps = [(f"Step {i + 1}", g) for i, g in df.groupby(block)]
    lats, lons = np.sort(df["latitude"].unique()), np.sort(df["longitude"].unique())
    dlat, dlon = float(np.median(np.diff(lats))), float(np.median(np.diff(lons)))
    lat0, lon0 = float(lats[0]), float(lons[0])
    ny = int(round((lats[-1] - lat0) / dlat)) + 1
    nx = int(round((lons[-1] - lon0) / dlon)) + 1

    def flat(g, col):
        a = np.full(ny * nx, np.nan)
        iy = np.rint((g["latitude"].to_numpy() - lat0) / dlat).astype(int)
        ix = np.rint((g["longitude"].to_numpy() - lon0) / dlon).astype(int)
        a[iy * nx + ix] = g[col].to_numpy()
        return [None if np.isnan(x) else round(float(x), 2) for x in a]  # None = land or no data

    return {"lat0": lat0, "lon0": lon0, "dlat": dlat, "dlon": dlon, "nx": nx, "ny": ny,
            "steps": [{"label": label, "u": flat(g, "wave_u"), "v": flat(g, "wave_v")} for label, g in steps]}


# Categories for the browser; infinite ends become large numbers for JSON
JS_CATEGORIES = [{**c, "lo": max(c["lo"], -1e9), "hi": min(c["hi"], 1e9)} for c in CATEGORIES]


def png_data_url(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def cat_name(i):
    return CATEGORIES[i]["name"] if i >= 0 else "No bathymetry here"


def cat_color(i):
    return CATEGORIES[i]["color"] if i >= 0 else NO_DATA_COLOR




def require_ships():
    if not os.path.exists(SHIPS_CSV):
        st.error(f"Ship file not found at {SHIPS_CSV}. Put ship_to_daftvalues.csv in the data folder and rebuild.")
        st.stop()
    return load_ships(SHIPS_CSV)


def contour_layer(show):
    if show and os.path.exists(CONTOURS_JSON):
        return load_contours(CONTOURS_JSON)
    return None


def map_limits(grid, ships, pad=0.05):
    """Hard pan limit: bathymetry extent plus all ships."""
    if grid is not None:
        gw, gs, ge, gn = grid.bounds()
    else:
        gw, gs, ge, gn = (ships["Longitude"].min(), ships["Latitude"].min(),
                          ships["Longitude"].max(), ships["Latitude"].max())
    return [[min(gw, ships["Longitude"].min()) - pad, min(gs, ships["Latitude"].min()) - pad],
            [max(ge, ships["Longitude"].max()) + pad, max(gn, ships["Latitude"].max()) + pad]]


def box_feature(box):
    w, s, e, n = box
    return {"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}


def wave_layer_payload(sb, key):
    """Sidebar controls for the animated wave layer, and the field to send to the map (None when off)."""
    waves = load_waves(WAVE_CSV)
    show = sb.checkbox("Wave direction (animated)", value=waves is not None, disabled=waves is None, key=f"{key}_show",
                       help="Copernicus Marine global wave model, 1/12° grid. The lines move the way the waves "
                            "travel; faster lines are faster (deep-water group speed)."
                       if waves else f"No wave data found at {WAVE_CSV}.")
    if not (show and waves):
        return None
    i = 0
    if len(waves["steps"]) > 1:
        i = sb.select_slider("Wave time step", options=list(range(len(waves["steps"]))),
                             format_func=lambda k: waves["steps"][k]["label"], key=f"{key}_step")
    sel = waves["steps"][i]
    label = f"{sel['label']} of {len(waves['steps'])}" if sel["label"].startswith("Step") else sel["label"]
    return {k: waves[k] for k in ("lat0", "lon0", "dlat", "dlon", "nx", "ny")} | {
        "u": sel["u"], "v": sel["v"], "label": label}


def render_map(template_name, payload, height=900):
    template = (HERE / template_name).read_text().replace("__WAVE_LAYER__", (HERE / "wave_layer.js").read_text())
    html = template.replace("__DATA__", json.dumps(payload).replace("</", "<\\/"))
    components.html(html, height=height)


# Port view: the Cork Harbour sim in port_view/ (plain HTML/JS). A declared component makes Streamlit
# serve the folder with proper content types, so its ES modules and image load without a second server.
# Declared here because Streamlit only allows it in an importable module, not in a page script.
port_view = components.declare_component("port_view", path=str(HERE / "port_view"))


def to_web_mercator(rgba, south, north):
    """Leaflet stretches image overlays evenly in Web Mercator, but the grid rows are even in
    latitude. Resample the rows (image row 0 = north) so the overlay lines up with the map."""
    ny = rgba.shape[0]
    merc = lambda lat: np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
    y_top, y_bottom = merc(north), merc(south)
    y = y_top + (np.arange(ny) + 0.5) * (y_bottom - y_top) / ny        # centre of each output row
    lat = np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)
    src = np.floor((north - lat) / (north - south) * ny).astype(int).clip(0, ny - 1)
    return rgba[src]
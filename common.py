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

DATA = os.getenv("DATA_DIR", "/app/data")
SHIPS_CSV = os.getenv("CSV_PATH", f"{DATA}/ship_to_daftvalues.csv")
CONTOURS_JSON = f"{DATA}/contours.json"
GRID_NPY, GRID_META = f"{DATA}/bathy_grid.npy", f"{DATA}/bathy_grid.json"
HERE = Path(__file__).parent

FULLSCREEN_CSS = """
<style>
  [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer { display:none !important; }
  .block-container, [data-testid="stMainBlockContainer"] { padding:0 !important; max-width:100% !important; }
  section.main [data-testid="stVerticalBlock"], [data-testid="stMain"] [data-testid="stVerticalBlock"] { gap:0 !important; }
  [data-testid="stMain"], section.main { overflow:hidden; }
  iframe { height:100vh !important; width:100% !important; display:block; border:0; }
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


def render_map(template_name, payload, height=900):
    template = (HERE / template_name).read_text()
    html = template.replace("__DATA__", json.dumps(payload).replace("</", "<\\/"))
    components.html(html, height=height)

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
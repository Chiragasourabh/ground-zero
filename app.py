import json
import os

import pandas as pd
import pydeck as pdk
import streamlit as st

SHIPS_CSV = os.getenv("CSV_PATH", "/app/data/ship_to_daftvalues.csv")
CONTOURS_JSON = os.getenv("CONTOURS_PATH", "/app/data/contours.json")

st.set_page_config(layout="wide")
st.title("Vessel positions")


@st.cache_data
def load_ships(path):
    df = pd.read_csv(path)
    df["info"] = (
        "<b>id_ano:</b> " + df["id_ano"].astype(str)
        + "<br><b>Statut de navigation:</b> " + df["Statut_de_navigation"].astype(str)
        + "<br><b>Tirant d'eau:</b> " + df["Tirant_d_eau"].astype(str)
        + "<br><b>Lat / Lon:</b> " + df["Latitude"].astype(str) + ", " + df["Longitude"].astype(str)
    )
    return df


@st.cache_data(show_spinner="Loading bathymetry contours...")
def load_contours(path):
    with open(path) as f:
        data = json.load(f)
    df = pd.DataFrame(data["lines"])
    if df.empty:
        return df
    zmin, zmax = data["zmin"], data["zmax"]
    t = (df["depth"] - zmin) / ((zmax - zmin) or 1)   # 0 = deepest, 1 = shallowest
    df["color"] = [[int(10 + 140 * v), int(40 + 170 * v), int(120 + 135 * v), 220] for v in t]
    df["info"] = "<b>Depth:</b> " + df["depth"].round(1).astype(str) + " m"
    return df


# ---------- Sidebar ----------
st.sidebar.header("Layers")
show_bathy = st.sidebar.checkbox("Bathymetry contours", value=True)

# ---------- Data ----------
if not os.path.exists(SHIPS_CSV):
    st.error(f"Ship CSV not found at {SHIPS_CSV}")
    st.stop()

ships = load_ships(SHIPS_CSV)
layers = []

if show_bathy:
    if os.path.exists(CONTOURS_JSON):
        layers.append(
            pdk.Layer(
                "PathLayer",
                data=load_contours(CONTOURS_JSON),
                get_path="path",
                get_color="color",
                width_min_pixels=1,
                pickable=True,
            )
        )
    else:
        st.sidebar.warning(
            "contours.json not found. Run:\n\n"
            "`docker compose run --rm preprocess`"
        )

# Ships last so they draw on top
layers.append(
    pdk.Layer(
        "ScatterplotLayer",
        data=ships,
        get_position="[Longitude, Latitude]",
        get_radius=500,
        radius_min_pixels=4,
        get_fill_color=[255, 80, 0, 200],
        pickable=True,
    )
)

view = pdk.ViewState(
    latitude=ships["Latitude"].mean(),
    longitude=ships["Longitude"].mean(),
    zoom=6,
)

tooltip = {
    "html": "{info}",
    "style": {"backgroundColor": "white", "color": "black", "fontSize": "13px"},
}

st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        initial_view_state=view,
        tooltip=tooltip,
        map_provider="carto",
        map_style="light",
    ),
    height=750,
)
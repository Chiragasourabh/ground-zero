"""Under-keel clearance maths and the bathymetry grid. No Streamlit in here."""
import json
import math

import numpy as np

# Ordered from worst to best; lower bound inclusive, in UKC %
CATEGORIES = [
    {"name": "Critical", "range": "below 10%", "lo": -math.inf, "hi": 10, "color": "#6A1B9A"},
    {"name": "Very shallow water", "range": "10 to 20%", "lo": 10, "hi": 20, "color": "#E53935"},
    {"name": "Shallow water", "range": "20 to 50%", "lo": 20, "hi": 50, "color": "#FDD835"},
    {"name": "Medium deep water", "range": "50 to 200%", "lo": 50, "hi": 200, "color": "#9CCC65"},
    {"name": "Deep water", "range": "above 200%", "lo": 200, "hi": math.inf, "color": "#1B5E20"},
]
BINS = [10, 20, 50, 200]
NO_DATA_COLOR = "#9E9E9E"


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


CAT_RGB = np.array([hex_to_rgb(c["color"]) for c in CATEGORIES], dtype=np.uint8)


def ukc_percent(h, draft):
    """UKC % = (h - T) / T * 100"""
    return (h - draft) / draft * 100.0


def classify(ukc):
    """Category index 0..4 (see CATEGORIES); -1 where UKC is NaN."""
    ukc = np.asarray(ukc, dtype=float)
    idx = np.digitize(ukc, BINS)
    return np.where(np.isnan(ukc), -1, idx)


def barrass_squat(cb, speed_kn, confined=False):
    """Barrass max squat (m): Cb * V^2 / 100 in open water, / 50 in a confined channel."""
    return cb * speed_kn ** 2 / (50.0 if confined else 100.0)


class BathyGrid:
    """Depth grid on disk, memory-mapped: slicing a window reads only that window."""

    def __init__(self, npy_path, meta_path):
        self.z = np.load(npy_path, mmap_mode="r")  # elevation, negative below datum; row 0 = south
        with open(meta_path) as f:
            m = json.load(f)
        self.west, self.south, self.cell = m["west"], m["south"], m["cell"]
        self.nx, self.ny = m["nx"], m["ny"]
        self.east = self.west + self.nx * self.cell
        self.north = self.south + self.ny * self.cell

    def bounds(self):
        return self.west, self.south, self.east, self.north

    def sample(self, lon, lat):
        """Elevation at points (nearest cell); NaN outside the grid."""
        lon, lat = np.atleast_1d(lon).astype(float), np.atleast_1d(lat).astype(float)
        ix = np.floor((lon - self.west) / self.cell).astype(int)
        iy = np.floor((lat - self.south) / self.cell).astype(int)
        inside = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        out = np.full(lon.shape, np.nan)
        out[inside] = self.z[iy[inside], ix[inside]]
        return out

    def window(self, w, s, e, n, max_cells):
        """Grid cells inside a lon/lat box, thinned if larger than max_cells."""
        ix0 = max(0, int(np.floor((w - self.west) / self.cell)))
        ix1 = min(self.nx, int(np.ceil((e - self.west) / self.cell)))
        iy0 = max(0, int(np.floor((s - self.south) / self.cell)))
        iy1 = min(self.ny, int(np.ceil((n - self.south) / self.cell)))
        if ix1 <= ix0 or iy1 <= iy0:
            return None
        cells = (ix1 - ix0) * (iy1 - iy0)
        stride = max(1, math.ceil(math.sqrt(cells / max_cells)))
        sub = np.asarray(self.z[iy0:iy1:stride, ix0:ix1:stride], dtype=np.float32)
        cell = self.cell * stride
        ww, ss = self.west + ix0 * self.cell, self.south + iy0 * self.cell
        return {
            "z": sub, "cell": cell, "stride": stride,
            "w": ww, "s": ss, "e": ww + sub.shape[1] * cell, "n": ss + sub.shape[0] * cell,
        }


def box_around(lat, lon, radius_km):
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat

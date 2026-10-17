"""
One-off step. Turns the huge bathymetry CSV into two small, fast files:

  data/bathy_grid.npy + bathy_grid.json   depth grid used for the UKC what-if
  data/contours.json                      contour lines for the map

The CSV is streamed in batches, so 1.9 GB never sits in memory at once.
"""
import json
import os
import time
import warnings

import contourpy
import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import shapely

DATA = os.getenv("DATA_DIR", "/app/data")
SRC = os.getenv("BATHY_PATH", f"{DATA}/bathymetry.csv")
SEP = os.getenv("BATHY_SEP", ",")
GRID_DEG = float(os.getenv("GRID_DEG", "0.002"))      # UKC grid cell size (~200 m)
CELL_DEG = float(os.getenv("CELL_DEG", "0.005"))      # contour grid cell size (~500 m)
CONTOUR_STEP = float(os.getenv("CONTOUR_STEP", "5"))  # metres between contour lines
SIMPLIFY_DEG = float(os.getenv("SIMPLIFY_DEG", "0.001"))
COLS = ["longitude", "latitude", "depth"]


def batches():
    reader = pacsv.open_csv(
        SRC,
        read_options=pacsv.ReadOptions(block_size=64 << 20),
        parse_options=pacsv.ParseOptions(delimiter=SEP),
        convert_options=pacsv.ConvertOptions(
            include_columns=COLS, column_types={c: pa.float64() for c in COLS}
        ),
    )
    for batch in reader:
        yield [batch.column(c).to_numpy(zero_copy_only=False) for c in COLS]


def block_nanmean(z, f):
    """Average f x f blocks of the grid, ignoring NaN."""
    if f <= 1:
        return z
    ny, nx = z.shape
    py, px = (-ny) % f, (-nx) % f
    z = np.pad(z, ((0, py), (0, px)), constant_values=np.nan)
    z = z.reshape(z.shape[0] // f, f, z.shape[1] // f, f)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(z, axis=(1, 3))


t0 = time.time()

# Pass 1: extent
lon_min = lat_min = np.inf
lon_max = lat_max = -np.inf
n = 0
for lon, lat, _ in batches():
    lon_min, lon_max = min(lon_min, np.nanmin(lon)), max(lon_max, np.nanmax(lon))
    lat_min, lat_max = min(lat_min, np.nanmin(lat)), max(lat_max, np.nanmax(lat))
    n += len(lon)
print(f"{n:,} points, lon {lon_min}..{lon_max}, lat {lat_min}..{lat_max} ({time.time()-t0:.0f}s)")

# Grid edges, offset half a cell so source points never sit exactly on a cell edge
west, south = lon_min - GRID_DEG / 2, lat_min - GRID_DEG / 2
nx = int((lon_max - west) / GRID_DEG) + 1
ny = int((lat_max - south) / GRID_DEG) + 1
total = np.zeros(nx * ny)
count = np.zeros(nx * ny)

# Pass 2: average depth per cell
for lon, lat, depth in batches():
    ok = ~np.isnan(depth)
    ix = ((lon[ok] - west) / GRID_DEG).astype(np.int64)
    iy = ((lat[ok] - south) / GRID_DEG).astype(np.int64)
    idx = iy * nx + ix
    total += np.bincount(idx, weights=depth[ok], minlength=nx * ny)
    count += np.bincount(idx, minlength=nx * ny)

with np.errstate(invalid="ignore", divide="ignore"):
    z = (total / count).reshape(ny, nx).astype(np.float32)  # row 0 = south
del total, count
print(f"UKC grid {nx} x {ny} cells ({time.time()-t0:.0f}s)")

zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
np.save(f"{DATA}/bathy_grid.npy", z)
with open(f"{DATA}/bathy_grid.json", "w") as f:
    json.dump({"west": west, "south": south, "cell": GRID_DEG, "nx": nx, "ny": ny,
               "zmin": zmin, "zmax": zmax}, f)

# Contours from a coarser version of the same grid
fac = max(1, round(CELL_DEG / GRID_DEG))
zc = block_nanmean(z, fac)
xs = west + (np.arange(zc.shape[1]) + 0.5) * GRID_DEG * fac
ys = south + (np.arange(zc.shape[0]) + 0.5) * GRID_DEG * fac
gen = contourpy.contour_generator(xs, ys, zc)
levels = np.arange(np.floor(zmin / CONTOUR_STEP) * CONTOUR_STEP, zmax, CONTOUR_STEP)

lines, n_pts = [], 0
for level in levels:
    for line in gen.lines(level):
        if len(line) < 2:
            continue
        simple = shapely.simplify(shapely.LineString(line), SIMPLIFY_DEG)
        coords = np.round(np.asarray(simple.coords), 5).tolist()
        if len(coords) >= 2:
            lines.append({"path": coords, "depth": float(level)})
            n_pts += len(coords)

with open(f"{DATA}/contours.json", "w") as f:
    json.dump({"zmin": zmin, "zmax": zmax, "lines": lines}, f)

print(f"{len(lines):,} contour lines / {n_pts:,} points")
print(f"Done in {time.time()-t0:.0f}s. Files in {DATA}: bathy_grid.npy, bathy_grid.json, contours.json")

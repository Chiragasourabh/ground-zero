"""
One-off step: turn the huge bathymetry CSV into a small contours.json.

- streams the CSV in batches (never loads 1.9 GB into memory)
- averages points onto a coarser grid (CELL_DEG)
- computes contour lines every CONTOUR_STEP metres
- simplifies the lines so the browser can draw them quickly
"""
import json
import os
import time

import contourpy
import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import shapely

SRC = os.getenv("BATHY_PATH", "/app/data/bathymetry.csv")
OUT = os.getenv("CONTOURS_PATH", "/app/data/contours.json")
SEP = os.getenv("BATHY_SEP", ",")                      # use "\t" for tab-separated
CELL_DEG = float(os.getenv("CELL_DEG", "0.005"))       # grid size; bigger = faster, less detail
CONTOUR_STEP = float(os.getenv("CONTOUR_STEP", "5"))   # metres between lines
SIMPLIFY_DEG = float(os.getenv("SIMPLIFY_DEG", "0.001"))
COLS = ["longitude", "latitude", "depth"]


def batches():
    reader = pacsv.open_csv(
        SRC,
        read_options=pacsv.ReadOptions(block_size=64 << 20),
        parse_options=pacsv.ParseOptions(delimiter=SEP),
        convert_options=pacsv.ConvertOptions(
            include_columns=COLS,
            column_types={c: pa.float64() for c in COLS},
        ),
    )
    for batch in reader:
        yield [batch.column(c).to_numpy(zero_copy_only=False) for c in COLS]


t0 = time.time()

# Pass 1: find the extent
lon_min = lat_min = np.inf
lon_max = lat_max = -np.inf
n = 0
for lon, lat, _ in batches():
    lon_min, lon_max = min(lon_min, np.nanmin(lon)), max(lon_max, np.nanmax(lon))
    lat_min, lat_max = min(lat_min, np.nanmin(lat)), max(lat_max, np.nanmax(lat))
    n += len(lon)
print(f"{n:,} points, lon {lon_min}..{lon_max}, lat {lat_min}..{lat_max} ({time.time()-t0:.0f}s)")

nx = int((lon_max - lon_min) / CELL_DEG) + 1
ny = int((lat_max - lat_min) / CELL_DEG) + 1
total = np.zeros(nx * ny)
count = np.zeros(nx * ny)

# Pass 2: average depth per grid cell
for lon, lat, depth in batches():
    ok = ~np.isnan(depth)
    ix = ((lon[ok] - lon_min) / CELL_DEG).astype(np.int64)
    iy = ((lat[ok] - lat_min) / CELL_DEG).astype(np.int64)
    idx = iy * nx + ix
    total += np.bincount(idx, weights=depth[ok], minlength=nx * ny)
    count += np.bincount(idx, minlength=nx * ny)
print(f"Gridded to {nx} x {ny} cells ({time.time()-t0:.0f}s)")

with np.errstate(invalid="ignore", divide="ignore"):
    z = (total / count).reshape(ny, nx)   # empty cells become NaN and are skipped
del total, count

xs = lon_min + (np.arange(nx) + 0.5) * CELL_DEG
ys = lat_min + (np.arange(ny) + 0.5) * CELL_DEG
gen = contourpy.contour_generator(xs, ys, z)

zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
levels = np.arange(np.floor(zmin / CONTOUR_STEP) * CONTOUR_STEP, zmax, CONTOUR_STEP)

out, n_pts = [], 0
for level in levels:
    for line in gen.lines(level):
        if len(line) < 2:
            continue
        simple = shapely.simplify(shapely.LineString(line), SIMPLIFY_DEG)
        coords = np.round(np.asarray(simple.coords), 5).tolist()
        if len(coords) >= 2:
            out.append({"path": coords, "depth": float(level)})
            n_pts += len(coords)

with open(OUT, "w") as f:
    json.dump({"zmin": zmin, "zmax": zmax, "lines": out}, f)

size_mb = os.path.getsize(OUT) / 1e6
print(f"Wrote {len(out):,} lines / {n_pts:,} points to {OUT} ({size_mb:.1f} MB) in {time.time()-t0:.0f}s")
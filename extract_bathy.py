"""
Extract depth values from bathymetry grids (.asc, .bag, .grd).

Requires:  pip install rasterio numpy pandas pyproj

Usage:
  # Show what's in the file
  python extract_bathy.py info survey.bag

  # Export every valid cell to a CSV (longitude, latitude, depth[, uncertainty])
  python extract_bathy.py xyz survey.bag -o survey_xyz.csv
  # ...or only a sub-area:
  python extract_bathy.py xyz survey.bag --bbox -5 47.5 -4 48.5 -o brest.csv

  # Sample depths at your own points (CSV with columns x,y)
  python extract_bathy.py points survey.bag my_points.csv -o depths.csv
  # ...if your points are lon/lat (WGS84) instead of the grid's own CRS:
  python extract_bathy.py points survey.bag my_points.csv --lonlat -o depths.csv
"""
import argparse
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window, from_bounds
from pyproj import Transformer


def band_names(src):
    # BAG files: band 1 = elevation, band 2 = uncertainty
    if src.driver == "BAG":
        return ["depth", "uncertainty"][: src.count]
    return ["depth"] + [f"band{i}" for i in range(2, src.count + 1)]


def info(path):
    with rasterio.open(path) as src:
        print(f"Driver:     {src.driver}")
        print(f"Size:       {src.width} x {src.height} cells, {src.count} band(s)")
        print(f"CRS:        {src.crs}")
        print(f"Cell size:  {src.res}")
        print(f"Bounds:     {src.bounds}")
        print(f"NoData:     {src.nodata}")
        d = src.read(1, masked=True)
        print(f"Depth range: {d.min()} to {d.max()} (mean {d.mean():.2f})")


def to_xyz(path, out, block_rows=500, bbox=None):
    """Write every valid cell as longitude,latitude,depth[,uncertainty].
    Processes the grid in blocks of rows so huge files don't exhaust memory."""
    with rasterio.open(path) as src:
        names = band_names(src)
        geographic = src.crs is None or src.crs.is_geographic
        xname, yname = ("longitude", "latitude") if geographic else ("x", "y")
        full = Window(0, 0, src.width, src.height)
        area = from_bounds(*bbox, src.transform).round_offsets().round_lengths() if bbox else full
        area = area.intersection(full)
        T = src.transform
        total, first = 0, True
        for r0 in range(int(area.row_off), int(area.row_off + area.height), block_rows):
            nrows = min(block_rows, int(area.row_off + area.height) - r0)
            win = Window(int(area.col_off), r0, int(area.width), nrows)
            bands = [src.read(i + 1, window=win, masked=True) for i in range(src.count)]
            valid = ~np.ma.getmaskarray(bands[0])
            rows, cols = np.nonzero(valid)
            if rows.size == 0:
                continue
            cols_abs, rows_abs = cols + win.col_off, rows + r0
            df = pd.DataFrame({
                xname: T.c + (cols_abs + 0.5) * T.a,   # cell centres
                yname: T.f + (rows_abs + 0.5) * T.e,
            })
            for name, b in zip(names, bands):
                df[name] = b.data[rows, cols]
            df.to_csv(out, mode="w" if first else "a", header=first,
                      index=False, float_format="%.6f")
            first = False
            total += len(df)
            print(f"\r  rows {r0 + nrows - int(area.row_off)}/{int(area.height)}, "
                  f"{total:,} points written", end="", flush=True)
    print(f"\nWrote {total:,} points to {out}")


def sample_points(path, points_csv, out, lonlat=False):
    pts = pd.read_csv(points_csv)
    with rasterio.open(path) as src:
        x, y = pts["x"].to_numpy(), pts["y"].to_numpy()
        if lonlat:
            t = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            x, y = t.transform(x, y)
        names = band_names(src)
        vals = np.array(list(src.sample(zip(x, y), masked=True)))
        nodata = src.nodata
        for i, name in enumerate(names):
            col = vals[:, i].astype(float)
            if nodata is not None:
                col[col == nodata] = np.nan
            pts[name] = col
    pts.to_csv(out, index=False)
    print(f"Sampled {len(pts)} points -> {out} "
          f"({pts['depth'].isna().sum()} outside grid / no data)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["info", "xyz", "points"])
    p.add_argument("grid")
    p.add_argument("points_csv", nargs="?")
    p.add_argument("-o", "--out", default="output.csv")
    p.add_argument("--bbox", nargs=4, type=float,
                   metavar=("MINX", "MINY", "MAXX", "MAXY"),
                   help="xyz mode: only export this box (e.g. lon_min lat_min lon_max lat_max)")
    p.add_argument("--lonlat", action="store_true",
                   help="points are WGS84 lon/lat rather than grid coordinates")
    a = p.parse_args()

    if a.mode == "info":
        info(a.grid)
    elif a.mode == "xyz":
        to_xyz(a.grid, a.out, bbox=a.bbox)
    else:
        if not a.points_csv:
            p.error("points mode needs a CSV of x,y points")
        sample_points(a.grid, a.points_csv, a.out, a.lonlat)
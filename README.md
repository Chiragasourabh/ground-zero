<p align="center">
  <img src="assets/logo.png" alt="groundzero" width="360">
</p>

<p align="center"><b>Grounding-risk and under-keel clearance tools for ships, on real seabed data.</b><br>
Built for the Ocean Hackathon. Prototype, not for navigation.</p>

---

GroundZero answers one question for a ship: **how much water is under the keel, here, at this draft, speed and water level?** The answer, under-keel clearance (UKC), is computed from SHOM HOMONIM bathymetry of the French Atlantic and Channel coasts. It is shown three ways:

| Page | What it does |
| --- | --- |
| **UKC what-if** | Pick a ship, change its draft, water level and speed, and see the UKC of every cell around it on the map. |
| **Sail planning** | Finds a short, safe route for the ship's draft and beam, marks the stretches that need care, then lets you sail it yourself. |
| **Port view** | A Cork Harbour simulator: steer a vessel with the keyboard and watch the clearance under it change live. |

## UKC what-if

- Choose any of the ~1,200 ships in the dataset. Draft is pre-filled from the ship's record and can be edited.
- Set the water level (tide or surge) and optionally add squat at a chosen speed, block coefficient and waterway type.
- The map colours every bathymetry cell by UKC category, for a radius around the ship or the whole map. Hover any cell for its water depth.
- The sidebar shows UKC at the ship's position, the maximum draft for a target UKC, and the water level that target needs.
- Every other ship is coloured by its own UKC at its recorded draft.

## Sail planning

Choose a start and destination (ship position, one of 12 French ports, another ship, or a click on the map). The planner:

1. Cuts the depth grid to a corridor around the voyage.
2. Marks a cell sailable when UKC is at least the minimum (10, 20 or 50%) **and** there is enough clear water either side: `beam × width factor / 2 + side margin`.
3. Weights each cell by depth risk and closeness to shallows. A preference slider trades distance for safety.
4. Runs Dijkstra on the 8-connected grid, then straightens the path into legs that stay in sailable water.
5. Scores confidence along the track from UKC margin and clearance, and splits it into *Confident*, *Take care* and *Caution* stretches.

If land or shallows block the corridor, the search widens automatically (15, 30, 60 km and so on). Waypoints with courses and leg lengths can be downloaded as CSV.

### Sail it

The planned time assumes full speed all the way. In shallow water, squat grows with speed squared and eats into the clearance, so a careful ship slows down. The **Sail it** panel shows the gap:

- **Plan says / Water says.** *Plan says* is distance over speed. *Water says* sails the same route but slows wherever UKC would drop below 50%, never below 5 kn steerage.
- **Simulate the route.** Plays the planned route back on the map with both times.
- **Sail.** Take the helm with <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> or the arrow keys and sail the route yourself without going aground.
- **Plot my route.** Click your own waypoints, set a speed per leg, and race it against the water.

## Port view

A standalone HTML/JS simulator of Cork Harbour, served inside the app as a Streamlit component.

- Steer with <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> or the arrows, <kbd>Space</kbd> to stop, <kbd>V</kbd> to switch between port and bridge view, <kbd>R</kbd> to reset.
- Vessel presets, a draft slider and a 48-hour tide slider.
- A keel view of the next 2 km of track, and an advisory with UKC now, squat, the worst point ahead, the maximum safe speed and the next safe tide window.
- The seabed and tide here are **synthetic** stand-ins (see [Limitations](#limitations)).

## How UKC is calculated

```
h      = water level − seabed elevation          (water depth, m)
T_eff  = draft + squat                           (effective draft, m)
squat  = Cb × V² / 100   (open water)            (Barrass, m; V in knots)
       = Cb × V² / 50    (confined channel)
UKC %  = (h − T_eff) / T_eff × 100
```

| Category | UKC |
| --- | --- |
| Critical | below 10% |
| Very shallow water | 10 to 20% |
| Shallow water | 20 to 50% |
| Medium deep water | 50 to 200% |
| Deep water | above 200% |

The maths lives in [`ukc.py`](ukc.py) (Python pages) and [`port_view/ukc.js`](port_view/ukc.js) (Port view).

## Data

| File | Source | In git |
| --- | --- | --- |
| `data/ship_to_daftvalues.csv` | 1,219 anonymised ships: position, AIS navigation status, draft | yes |
| `data/contours.json` | Depth contours every 5 m, built by `preprocess_bathy.py` | yes |
| `data/bathy_grid.npy` + `.json` | 0.002° (~200 m) depth grid, built by `preprocess_bathy.py` | **no** (114 MB) |
| `data/bathymetry.csv` | Raw XYZ export of HOMONIM (~1.3 GB) | **no** |
| `MNT_FACADE_ATLANTIQUE_HOMONIM_NM/` | SHOM HOMONIM 100 m DTM, Atlantic façade, referenced to mean sea level | **no** |

The grid covers roughly 6° W to 5.8° E and 43.3° N to 52.9° N, from Bayonne to Dunkerque. The HOMONIM DTM is published by SHOM ([data.shom.fr](https://data.shom.fr)).

## Running it

A fresh clone has no depth grid, so build it first.

### 1. Build the depth grid (once)

Download the HOMONIM Atlantic DTM into `MNT_FACADE_ATLANTIQUE_HOMONIM_NM/`, then:

```bash
pip install rasterio pyproj numpy pandas   # extract_bathy.py only

# Raster to XYZ CSV (longitude, latitude, depth)
python extract_bathy.py xyz \
  MNT_FACADE_ATLANTIQUE_HOMONIM_NM/DONNEES/MNT_ATL100m_HOMONIM_WGS84_NM_ZNEG.asc \
  -o data/bathymetry.csv

# CSV to grid + contours (streams the CSV, so it never loads 1.3 GB at once)
docker compose run --rm --build preprocess
```

Without Docker, the last step is `DATA_DIR=./data python preprocess_bathy.py`. Grid cell size, contour step and smoothing are set by environment variables in [`docker-compose.yml`](docker-compose.yml).

`extract_bathy.py` also has `info` (describe a raster) and `points` (sample depths at your own coordinates) modes; run it with `-h` for details.

### 2. Start the app

With Docker:

```bash
docker compose up --build
```

Without Docker (Python 3.11):

```bash
pip install -r requirements.txt
DATA_DIR=./data streamlit run app.py
```

Open http://localhost:8501. `DATA_DIR` defaults to `/app/data` (the Docker path), so set it for local runs.

## Project layout

```
app.py                 Streamlit entry point and page navigation
common.py              Shared loading, caching, styling and map rendering
ukc.py                 UKC maths, categories, squat, memory-mapped depth grid
ukc_page.py            Page: UKC what-if
route_page.py          Page: sail planning
routing.py             Route finder (corridor, navigability, Dijkstra, confidence)
port_page.py           Page: port view (wraps port_view/)
map_template.html      Leaflet map for UKC what-if
route_template.html    Leaflet map and Sail it game for sail planning
port_view/             Cork Harbour simulator (plain HTML/JS)
extract_bathy.py       Raster (.asc/.bag/.grd) to XYZ CSV
preprocess_bathy.py    XYZ CSV to depth grid and contours
```

## Limitations

- **Water level is a manual input.** There is no live or predicted tide yet.
- **Datum.** HOMONIM depths are relative to mean sea level, so the water-level input is height above mean sea level, not chart datum.
- **Port view uses a synthetic seabed and tide** for Cork Harbour. The other pages use real French Atlantic data.
- **Port view divides UKC by the static draft**; the other pages divide by draft plus squat. Results differ slightly at speed.
- **Thresholds are placeholders** (50% slow-down, 5 kn steerage, category bands) pending review by a marine engineer.
- **Grid resolution** is about 200 m, so narrow channels and harbour approaches are not resolved.
- **Squat** is Barrass simplified. Heel, waves, trim and survey age are ignored.

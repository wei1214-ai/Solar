# SolarSync 鈥?Malaysia City-Scale Rooftop Solar Potential Mapper

SolarSync maps, analyses and **ranks** rooftop solar potential for Malaysian cities.
Give it a building or a district and it returns real geospatial output 鈥?roof planes,
obstacle polygons, a usable-roof polygon, an individual panel layout, expected annual
energy, a Malaysian CAPEX range and a cost-adjusted potential score.

> **Live data, no API key required.** Building footprints come from live
> OpenStreetMap Overpass. When `GOOGLE_MAPS_API_KEY` / `INKLING_API_KEY` are missing the
> system transparently switches to a physically-grounded Malaysia simulator
> (PVGIS-style 1450 kWh/kWp/year baseline) so the whole product runs end to end.

---

## Architecture

```
Next.js 15 (App Router, TS, Tailwind, react-leaflet)
        鈹? /api/v1/*
FastAPI (BackgroundTasks + asyncio.Semaphore(5))
        鈹?        鈹溾攢鈹€ providers/overpass.py    live OSM building footprints (+ disk snapshot cache)
        鈹溾攢鈹€ providers/google_solar.py  Building Insights findClosest  (404 鈫?UNRANKED_NO_DATA)
        鈹溾攢鈹€ providers/imagery.py     Keyless Esri mosaic (3x3 @ z19)
        鈹溾攢鈹€ providers/inkling.py     Inkling vision 鈫?[0,1000] polygons 鈫?metres (no GDAL)
        鈹溾攢鈹€ providers/simulator.py   keyless Malaysia rooftop / obstacle / flux model
        鈹溾攢鈹€ geo.py                   EPSG:4326 鈬?EPSG:32647, Web-Mercator resolution
        鈹溾攢鈹€ panels.py                projected-length grid + greedy flux placement
        鈹溾攢鈹€ cost.py                  MY tiered CAPEX, potential score, pitch metrics
        鈹斺攢鈹€ runs.py / pipeline.py    run orchestration, persistence, ranking
        鈹?     SQLite (GeoJSON text geometry)  鈫?solarsync.db
```

**Forbidden dependencies** (per the design spec's Appendix B): `gdal`, `rasterio`, `ortools`,
`celery`, `redis`, `psycopg2`/`postgis`, `opencv`. Only `fastapi`, `uvicorn`, `httpx`,
`pydantic` v2, `shapely` 鈮?2.0, `pyproj`, `sqlalchemy` (SQLite) and `python-dotenv` are used.

---

## Quick start (2 terminals)

### 1. Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
# python3 -m venv .venv && source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
copy .env.example .env                               # optional - all keys may stay empty
uvicorn app.main:app --reload --port 8000
```

API docs: <http://127.0.0.1:8000/docs>

### 2. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local      # points at http://127.0.0.1:8000/api/v1
npm run dev
```

Open <http://localhost:3000>. The dashboard auto-runs the Bangsar (KL) analysis on load.

> The map uses **Esri World Imagery** satellite tiles and **OpenStreetMap** street tiles, so a
> high-resolution aerial basemap is available immediately 鈥?no Google Maps key required.

---

## Configuration

All keys are optional (see `backend/.env.example`).

| Variable | Effect when set | Effect when empty |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Real Solar Building Insights roof planes (imagery is always keyless Esri) | Demo simulator for roof planes |
| `INKLING_API_KEY` + `INKLING_API_URL` | Real rooftop equipment detection from the live satellite tile | Deterministic simulated obstacles (hvac, water tanks, skylights, existing PV) |
| `DATABASE_URL` | SQLite path (default `sqlite:///./solarsync.db`) | 鈥?|
| `MAX_BUILDINGS_PER_RUN` | Batch size cap (default 150) | 鈥?|
| `EXCLUDE_RESIDENTIAL` | Drops roofs tagged `house/apartments/terrace/...` before analysis (default `true`) | `false` ranks every building |
| `MIN_FOOTPRINT_M2` | Geometric floor in m虏 (default 12; use ~150 to drop generic `building=yes` houses) | 鈥?|
| `MAX_CONCURRENT_BUILDINGS` | Concurrency limit (default 5) | 鈥?|
| `PVGIS_BASELINE_MODE` | `calibrate` rescales the yield model to the live PVGIS reference | `reference` only logs the comparison |

Without any key the whole pipeline still runs: real OSM footprints, real geometry, real
layout optimisation, real Malaysian cost model.

**Database location.** If the project sits inside a cloud-synced folder (OneDrive, Dropbox,
Google Drive) the backend automatically moves the SQLite file to
`%LOCALAPPDATA%\SolarSync\solarsync.db` and logs a warning, because sync clients lock and
occasionally mark database files read-only ("attempt to write a readonly database"). Set
`DATABASE_URL` to pin an explicit path.

---

## The model

### Geometry

* All area / distance / buffer maths happens in **EPSG:32647** (UTM 47N, Malaysia metric).
* `usable_roof = roof.buffer(-0.5 m setback) 鈭?obstacles.buffer(0.3 m clearance) 鈭?maintenance walkways`
* Obstacle polygons from Inkling are converted without GDAL:
  `meters_per_pixel = (156543.03392 路 cos(lat)) / 2^zoom / scale`
  鈫?normalised `[0,1000]` vertices become metre offsets from the tile centre 鈫?`pyproj` 鈫?UTM.

### Panel layout (no OR-Tools)

* Reference panel: **1.134 m 脳 1.762 m, 440 Wp**, 0.05 m installation gap.
* Sloped roofs use the 2D projection correction `L_proj = L 路 cos(pitch)`.
* Portrait **and** landscape grids are enumerated with Shapely; only rectangles fully
  contained in the usable polygon survive; the better orientation per roof plane wins.
* Candidates are then selected greedily in descending annual-flux order, never overlapping.

### Energy, cost and score (spec 搂12 / 搂13.7)

```
E_dc        = 危 (F_j 路 P_panel_kWp)                 # F_j = kWh/kWp/year per roof plane
E_ac        = E_dc 脳 0.82                            # inverter + line + thermal losses
C_installed = 3500 + kWp 脳 R_market                  # RM 4600 (<4 kWp) | 3900 (4鈥?0) | 3400 (>10)
Potential_Score  = E_ac / C_installed                # kWh_AC / year / RM
Annual_Savings   = E_ac 脳 RM 0.50
Payback_Years    = C_installed / Annual_Savings
CO2_Offset_Tons  = E_ac 脳 0.758 / 1000
```

Cost range: `base 脳 0.88` (low) 鈥?`base 脳 1.15` (high). Pre-subsidy, no battery.

### Status model (spec 搂2.3)

`RANKED` 路 `EXCLUDED_BY_RULE` (documented rule) 路 `UNRANKED_NO_DATA` (provider 404 / missing
core fields 鈥?**never** reported as zero potential) 路 `ANALYSIS_FAILED` (retryable).

A `404 NOT_FOUND` from Google Solar marks the building `UNRANKED_NO_DATA`, removes it from
the leaderboard and continues the batch 鈥?verified by `backend/tests/no_data_check.py`.

---

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Status, data mode, key presence |
| `GET` | `/api/v1/presets` | KL / Penang quick-test areas |
| `GET` | `/api/v1/cost-benchmarks` | Malaysian CAPEX tiers + provenance |
| `POST` | `/api/v1/analyses` | Create a `BUILDING` or `AREA` run 鈫?`run_id` |
| `GET` | `/api/v1/analyses/{run_id}` | Status, progress, ranked / excluded / no-data counts |
| `GET` | `/api/v1/areas/{run_id}/rankings?rank_mode=energy\|cost_adjusted` | Leaderboard |
| `GET` | `/api/v1/buildings/{id}/analysis` | Roof planes, obstacles, usable polygon, panel layout, metrics |
| `POST` | `/api/v1/buildings/{id}/recompute` | Re-run one building |

---

## Verification

```bash
cd backend
python -m tests.layout_check    # geometry: panels inside usable polygon, no overlaps,
                                 # no panel on an obstacle, all metric formulas re-checked
python -m tests.no_data_check    # 404 鈫?UNRANKED_NO_DATA 鈫?excluded from leaderboard
python -m tests.smoke            # full live run: Overpass 鈫?analysis 鈫?ranking
```

```bash
cd frontend
npm run typecheck && npm run build
```

---

## Notes, limits and compliance

* Roof planes come from Google Solar Building Insights when available; the roof **outline**
  is derived from the OpenStreetMap footprint (Google does not publish roof polygons), and
  the footprint is partitioned to match the number of roof planes.
* Google Solar data must not be permanently cached or used for bulk city-wide stored
  rankings without confirming the current Google Maps Platform terms (30-day Solar data
  retention). Use the open OSM inventory for long-lived datasets.
* Cost figures are a **market prior**, not quotes 鈥?calibrate against written installer
  quotes before production use.
* Layouts are indicative siting, not construction drawings: structure, waterproofing, fire
  compliance, switchboard capacity and site safety all need professional on-site review.

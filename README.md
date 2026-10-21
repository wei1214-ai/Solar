SolarSync — Malaysia City-Scale Rooftop Solar Potential Mapper
SolarSync maps, analyses and ranks rooftop solar potential for Malaysian cities. Give it a building or a district and it returns real geospatial output — roof planes, obstacle polygons, a usable-roof polygon, an individual panel layout, expected annual energy, a Malaysian CAPEX range and a cost-adjusted potential score.

Live data, minimal keys. Building footprints come from live OpenStreetMap Overpass. Roof equipment is read from satellite imagery by a vision model (Gemini by default). If no vision key is configured the system transparently switches to a physically-grounded Malaysia simulator (PVGIS-style 1450 kWh/kWp/year baseline) so the whole product still runs end to end.

Architecture
Next.js 15 (
        │  /api/v1/*
Fast
        │
        ├── providers/overpass.py     live OSM building footprints (+
        ├── providers/imagery.py      keyless Esri mosaic
        ├── providers/gemini.py       Gemini vision → [0
        ├── providers/inkling.py      optional alternative vision provider
        ├──
        ├── providers
        ├──
        ├── providers/presets.py      demo areas (Sel
        ├── providers/
        ├── geo
        ├── panels.py                 projected-length
        ├── cost.py                   MY tiered CAPEX, potential score, pitch metrics
        └── runs.py / pipeline.py     run orchestration, persistence, ranking
             │
          SQLite (GeoJSON text geometry) → solarsync.db
Forbidden dependencies (per the design spec's Appendix B): gdal, rasterio, ortools, celery, redis, psycopg2/postgis, opencv. Only fastapi, uvicorn, httpx, pydantic v2, shapely ≥ 2.0, pyproj, sqlalchemy (SQLite) and python-dotenv are used.

Quick start (2 terminals)
1. Backend
cd backend
python -m venv .venv
# python
pip install -r requirements.txt
copy .env.example .env                               # optional - all keys may stay empty
uvicorn app.main:
API docs: http://127.0.0.1:8000/docs

2. Frontend
cd frontend
npm install
cp .
npm run dev
Open http://localhost:3000. The dashboard opens on Kota Damansara (Selangor).

The map uses Esri World Imagery satellite tiles and OpenStreetMap street tiles, so a high-resolution aerial basemap is available immediately — no Google Maps key required.

Configuration
All keys are optional (see backend/.env.example).

Variable	Effect when set	Effect when empty
GEMINI_API_KEY	Vision model reads roof obstacles (HVAC, tanks, skylights, existing PV)	Simulator generates obstacles
GEMINI_API_KEYS	Comma-separated keys, sharded round-robin; each key keeps its own rate budget	Falls back to GEMINI_API_KEY
GEMINI_MODEL	Vision model id (default gemini-3.8-flash; gemini-3.5-flash-lite has the largest free tier)	—
GEMINI_MODEL_FALLBACKS	Comma-separated fallback model ids, tried in order when the primary is rate-limited	No fallback
VISION_PROVIDER	Force gemini / openrouter / none	Auto-detect from available keys
GOOGLE_MAPS_API_KEY	Real Solar Building Insights roof planes (imagery is always keyless Esri)	Simulated roof planes
DATABASE_URL	SQLite path (default sqlite:///./solarsync.db)	Default path
MAX_BUILDINGS_PER_RUN	Batch size cap (default 150)	—
EXCLUDE_RESIDENTIAL	Drops roofs tagged house/apartments/terrace/... before analysis (default true)	false ranks every building
MIN_FOOTPRINT_M2	Geometric floor in m² (default 12; use ~150 to drop generic building=yes houses)	—
MAX_CONCURRENT_BUILDINGS	Concurrency limit (default 5)	—
PVGIS_BASELINE_MODE	calibrate rescales the yield model to the live PVGIS reference	fixed (1450 kWh/kWp/yr)
Without any key the whole pipeline still runs: real OSM footprints, real geometry, real layout optimisation, real Malaysian cost model.

Database location. If the project sits inside a cloud-synced folder (OneDrive, Dropbox, Google Drive) the backend automatically moves the SQLite file to %LOCALAPPDATA%\SolarSync\solarsync.db and logs a warning, because sync clients lock and occasionally mark database files read-only ("attempt to write a readonly database"). Set DATABASE_URL to pin an explicit path.

The model
Geometry
All area / distance / buffer maths happens in EPSG:32647 (UTM 47N, Malaysia metric).
usable_roof = roof.buffer(-0.5 m setback) − obstacles.buffer(0.3 m clearance) − maintenance walkways
Vision polygons are converted without GDAL: meters_per_pixel = (156543.03392 · cos(lat)) / 2^zoom / scale → normalised [0,1000] vertices become metre offsets from the tile centre → pyproj → UTM.
Panel layout (no OR-Tools)
Reference panel: 1.134 m × 1.762 m, 440 Wp, 0.05 m installation gap.
Sloped roofs use the 2D projection correction L_proj = L · cos(pitch).
Portrait and landscape grids are enumerated with Shapely; only rectangles fully contained in the usable polygon survive; the better orientation per roof plane wins.
Candidates are then selected greedily in descending annual-flux order, never overlapping.
Energy, cost and score (spec §12 / §13.7)
E_dc             = Σ (F_j × P_panel_kWp)     # F
E_ac             = E_dc × 0.82               # inverter + line + thermal losses
C_installed
Potential_Score  = E_ac
Annual_Savings   = E_ac × RM 0.50
Payback_
CO2_Offset_Tons  = E_ac × 0.758 / 1000
Cost range: base × 0.88 (low) — base × 1.15 (high). Pre-subsidy, no battery.

Status model (spec §2.3)
RANKED · EXCLUDED_BY_RULE (documented rule) · UNRANKED_NO_DATA (provider 404 / missing core fields — never reported as zero potential) · ANALYSIS_FAILED (retryable).

A 404 NOT_FOUND from Google Solar marks the building UNRANKED_NO_DATA, removes it from the leaderboard and continues the batch — verified by backend/tests/no_data_check.py.

Caching and result reuse
Esri imagery and vision responses are cached on disk, so a repeated analysis of the same area skips the network entirely (and consumes no model quota).
POST /api/v1/analyses with a preset_id returns the previous completed run for that preset instead of recomputing. Send force_rerun: true (the dashboard's Re-run button) to analyse again from scratch.
API
Method	Endpoint	Purpose
GET	/api/v1/health	Status, data mode, key presence
GET	/api/v1/presets	Kota Damansara / Sungai Buloh / Subang Jaya
GET	/api/v1/cost-benchmarks	Malaysian CAPEX tiers + provenance
POST	/api/v1/analyses	Create a BUILDING or AREA run → run_id (force_rerun optional)
GET	/api/v1/analyses/{run_id}	Status, progress, ranked / excluded / no-data counts
GET	/api/v1/areas/{run_id}/rankings?rank_mode=energy|cost_adjusted	Leaderboard
GET	/api/v1/buildings/{id}/analysis	Roof planes, obstacles, usable polygon, panel layout, metrics
POST	/api/v1/buildings/{id}/recompute	Re-run one building
Deployment
A single image serves both apps: Next.js is built in standalone mode and FastAPI runs behind it, with /api/v1/* rewritten to 127.0.0.1:8000 (same origin, so no CORS).

gcloud run deploy sol
  --allow-unauthenticated
  --no-cpu-throttling --timeout 300 --max-instances 1 \
  --env-vars-file env.yaml
Keep --max-instances 1: the SQLite file lives on the instance filesystem, and background analysis tasks need CPU after the HTTP response returns, hence --no-cpu-throttling.

Verification
cd backend
python -m tests.layout_check     # panels inside usable polygon, no overlaps, no panel on
                                 # an obstacle, all metric formulas re-checked
python -m tests.no_data_check    # 404 → UNRANKED_NO_DATA → excluded from leaderboard
python -m tests.smoke            # full live run: Overpass → analysis → ranking

cd frontend
npm run type
Notes, limits and compliance
Roof planes come from Google Solar Building Insights when available; the roof outline is derived from the OpenStreetMap footprint (Google does not publish roof polygons), and the footprint is partitioned to match the number of roof planes.
Google Solar data must not be permanently cached or used for bulk city-wide stored rankings without confirming the current Google Maps Platform terms (30-day Solar data retention). Use the open OSM inventory for long-lived datasets.
Vision output is a detection aid, not a survey: overlaps are resolved by centroid/area rules and every polygon is clipped to the OSM footprint before layout.
Cost figures are a market prior, not quotes — calibrate against written installer quotes before production use.
Layouts are indicative siting, not construction drawings: structure, waterproofing, fire compliance, switchboard capacity and site safety all need professional on-site review.

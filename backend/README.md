---
title: SolarSync API
emoji: ☀️
colorFrom: yellow
colorTo: orange
sdk: docker
app_port: 7860
pinned: false
---

# SolarSync API

FastAPI backend for city-scale rooftop solar potential analysis: OSM footprints,
PVGIS yield, Esri imagery and Gemini roof-object detection, with panel layout,
CAPEX/energy/score computation and area ranking.

Health check: `GET /api/v1/health`

## Runtime secrets

Set these in **Settings → Variables and secrets** (never commit them):

| Name | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Roof-object detection (required for live vision) |
| `GOOGLE_MAPS_API_KEY` | Google Solar building insights (optional, no MY coverage) |
| `VISION_PROVIDER` | `gemini` |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `GEMINI_THINKING_LEVEL` | `low` |
| `MAX_BUILDINGS_PER_RUN` | `24` |
| `MAX_CONCURRENT_BUILDINGS` | `4` |
| `DATABASE_URL` | Optional Postgres URL; defaults to local SQLite |

Free CPU Spaces have ephemeral storage, so the SQLite file is reset whenever the
container restarts. Point `DATABASE_URL` at Postgres to keep run history.

"""SolarSync FastAPI application.

Start with:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import get_settings
from .db import init_db

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("solarsync")

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    log.info("SolarSync %s ready (data mode: %s)", settings.app_name, "live" if settings.has_google_key else "demo")
    yield


app = FastAPI(
    title="SolarSync API",
    description=(
        "Malaysia city-scale rooftop solar potential mapper. "
        "Overpass building footprints -> Solar roof planes -> rooftop obstacles -> "
        "usable roof polygons -> feasible panel layout -> annual energy -> Malaysian CAPEX -> ranking."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root() -> dict:
    return {
        "app": "SolarSync",
        "docs": "/docs",
        "health": "/api/v1/health",
        "presets": "/api/v1/presets",
        "analyses": "/api/v1/analyses",
    }

"""Aerial imagery adapter (spec 9.4 / hackathon 1B).

Rooftop overhead imagery is assembled from **Esri World Imagery** tiles, which
need no API key and no billing account: a 3x3 mosaic at zoom 19 covers ~229 m of
ground at ~0.30 m/px, which is the native resolution of Malaysian Esri imagery.

Each building's mosaic is cached under `.cache/imagery/` so recomputing a run
does not re-download tiles.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import BACKEND_DIR, Settings, get_settings

log = logging.getLogger("solarsync.imagery")

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

ESRI_ZOOM = 19
ESRI_GRID = 3
ESRI_TILE_PX = 256
ESRI_SIZE_PX = ESRI_GRID * ESRI_TILE_PX
ESRI_SCALE = 1

_CACHE_DIR = BACKEND_DIR / ".cache" / "imagery"


@dataclass
class SatelliteTile:
    """A satellite image plus the georeferencing of its centre pixel."""

    image: bytes
    content_type: str
    center_lat: float
    center_lon: float
    zoom: int
    scale: int
    size_px: int


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _tile_center_latlon(tx: int, ty: int, zoom: int) -> tuple[float, float]:
    n = 2**zoom
    lon = (tx + 0.5) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (ty + 0.5) / n))))
    return lat, lon


def _cache_path(lat: float, lon: float) -> Path:
    return _CACHE_DIR / f"esri_{lat:.6f}_{lon:.6f}_z{ESRI_ZOOM}_g{ESRI_GRID}.jpg"


def _cache_path_for_tile(cx: int, cy: int) -> Path:
    return _CACHE_DIR / f"esri_z{ESRI_ZOOM}_x{cx}_y{cy}_g{ESRI_GRID}.jpg"


async def _fetch_esri_mosaic(lat: float, lon: float) -> bytes | None:
    """Download a 3x3 tile mosaic centred on the coordinate (no API key)."""
    centre_x, centre_y = _tile_xy(lat, lon, ESRI_ZOOM)
    return await _fetch_mosaic_at_tile(centre_x, centre_y)


async def _fetch_mosaic_at_tile(centre_x: int, centre_y: int) -> bytes | None:
    from PIL import Image

    cached = _cache_path_for_tile(centre_x, centre_y)
    if cached.exists():
        return cached.read_bytes()

    half = ESRI_GRID // 2
    mosaic = Image.new("RGB", (ESRI_SIZE_PX, ESRI_SIZE_PX), (20, 22, 26))
    max_side = 2**ESRI_ZOOM - 1

    jobs: list[tuple[int, int, int, int]] = []
    for row in range(ESRI_GRID):
        for col in range(ESRI_GRID):
            tx = min(max(centre_x + col - half, 0), max_side)
            ty = min(max(centre_y + row - half, 0), max_side)
            jobs.append((row, col, tx, ty))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": "SolarSync/1.0 (rooftop solar potential research)"},
    ) as client:

        async def _one(row: int, col: int, tx: int, ty: int):
            url = ESRI_TILE_URL.format(z=ESRI_ZOOM, x=tx, y=ty)
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                log.warning("Esri tile %s/%s failed: %s", tx, ty, exc)
                return None
            if response.status_code != 200 or not response.content:
                log.warning("Esri tile %s/%s returned HTTP %s", tx, ty, response.status_code)
                return None
            return row, col, response.content

        results = await asyncio.gather(*(_one(*job) for job in jobs))

    for result in results:
        if result is None:
            continue
        row, col, body = result
        try:
            tile = Image.open(io.BytesIO(body)).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - corrupt tile
            log.warning("Esri tile row=%s col=%s was not an image: %s", row, col, exc)
            continue
        mosaic.paste(
            tile.resize((ESRI_TILE_PX, ESRI_TILE_PX)),
            (col * ESRI_TILE_PX, row * ESRI_TILE_PX),
        )

    if mosaic.getextrema() == ((20, 20), (22, 22), (26, 26)):
        return None

    buffer = io.BytesIO()
    mosaic.save(buffer, format="JPEG", quality=90)
    data = buffer.getvalue()
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError as exc:
        log.debug("Could not cache imagery tile: %s", exc)
    return data


async def fetch_satellite_tile(
    lat: float,
    lon: float,
    settings: Settings | None = None,
) -> SatelliteTile | None:
    """Download the rooftop imagery tile (Esri World Imagery, keyless).

    The returned tile is centred on the *tile* centre rather than on the
    requested point, so that centre is returned with the image and used to
    georeference the vision model's detections.
    """
    settings = settings or get_settings()
    mosaic = await _fetch_esri_mosaic(lat, lon)
    if not mosaic:
        return None
    centre_x, centre_y = _tile_xy(lat, lon, ESRI_ZOOM)
    centre_lat, centre_lon = _tile_center_latlon(centre_x, centre_y, ESRI_ZOOM)
    return SatelliteTile(
        image=mosaic,
        content_type="image/jpeg",
        center_lat=centre_lat,
        center_lon=centre_lon,
        zoom=ESRI_ZOOM,
        scale=ESRI_SCALE,
        size_px=ESRI_SIZE_PX,
    )


def encode_data_url(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


# ---------------------------------------------------------------------------
# Area mosaics: one image per ~230 m cell, aligned to the tile grid
# ---------------------------------------------------------------------------

# A mosaic spans ESRI_GRID tiles, so cells are spaced by ESRI_GRID to tile the
# plane without gaps or double-counting.
AREA_MAX_TILES = 36


def tile_ground_span_m(lat: float, zoom: int = ESRI_ZOOM, grid: int = ESRI_GRID) -> float:
    """Ground width of one mosaic in metres at a given latitude."""
    return 360.0 / (2**zoom) * grid * math.cos(math.radians(lat)) * 111_320.0


def area_tile_centers(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    max_tiles: int = AREA_MAX_TILES,
) -> list[tuple[int, int]]:
    """Tile indices whose 3x3 mosaics cover the bbox, snapped to a 3-tile grid."""
    left_x, bottom_y = _tile_xy(min_lat, min_lon, ESRI_ZOOM)
    right_x, top_y = _tile_xy(max_lat, max_lon, ESRI_ZOOM)
    x0, x1 = min(left_x, right_x), max(left_x, right_x)
    y0, y1 = min(top_y, bottom_y), max(top_y, bottom_y)

    start_x = (x0 // ESRI_GRID) * ESRI_GRID + ESRI_GRID // 2
    start_y = (y0 // ESRI_GRID) * ESRI_GRID + ESRI_GRID // 2

    centers: list[tuple[int, int]] = []
    cy = start_y
    while cy - ESRI_GRID // 2 <= y1:
        cx = start_x
        while cx - ESRI_GRID // 2 <= x1:
            centers.append((cx, cy))
            cx += ESRI_GRID
        cy += ESRI_GRID

    if len(centers) > max_tiles:
        mid_x = (x0 + x1) / 2.0
        mid_y = (y0 + y1) / 2.0
        centers.sort(key=lambda p: (p[0] - mid_x) ** 2 + (p[1] - mid_y) ** 2)
        centers = centers[:max_tiles]
    return centers


async def fetch_area_tiles(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    settings: Settings | None = None,
    max_tiles: int = AREA_MAX_TILES,
) -> list[SatelliteTile]:
    """Fetch every mosaic covering the bbox (parallel, disk-cached)."""
    settings = settings or get_settings()
    centers = area_tile_centers(min_lat, min_lon, max_lat, max_lon, max_tiles=max_tiles)
    if not centers:
        return []

    limit = max(1, min(settings.max_concurrent_buildings, 8))
    semaphore = asyncio.Semaphore(limit)
    tiles: list[SatelliteTile] = []

    async def _one(cx: int, cy: int) -> None:
        async with semaphore:
            image = await _fetch_mosaic_at_tile(cx, cy)
            if not image:
                return
            lat, lon = _tile_center_latlon(cx, cy, ESRI_ZOOM)
            tiles.append(
                SatelliteTile(
                    image=image,
                    content_type="image/jpeg",
                    center_lat=lat,
                    center_lon=lon,
                    zoom=ESRI_ZOOM,
                    scale=ESRI_SCALE,
                    size_px=ESRI_SIZE_PX,
                )
            )

    await asyncio.gather(*(_one(cx, cy) for cx, cy in centers))
    return tiles

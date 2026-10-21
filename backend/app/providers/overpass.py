"""OpenStreetMap Overpass adapter - live building footprints (spec 5.1).

No offline multi-GB building dump is needed: for a bounding box we query
`building=*` in real time and fall back between several public Overpass
endpoints.

Public Overpass mirrors are frequently rate limited (HTTP 429/502) or answer
with an empty element list plus an error `remark`. The adapter therefore
retries with backoff, treats `remark` errors as failures, and keeps a short
lived on-disk snapshot of every successful response so a network hiccup can
never break a live demo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..config import BACKEND_DIR, Settings, get_settings
from ..geo import centroid_lonlat, ensure_valid

log = logging.getLogger("solarsync.overpass")

CACHE_DIR = BACKEND_DIR / ".cache"
CACHE_TTL_SECONDS = 7 * 24 * 3600

_QUERY_TEMPLATE = """[out:json][timeout:{timeout}];
(
  way["building"]({south},{west},{north},{east});
  relation["building"]({south},{west},{north},{east});
);
out geom;"""


class OverpassError(RuntimeError):
    pass


@dataclass
class _Snapshot:
    path: Path
    payload: dict
    saved_at: float


def _cache_key(bbox: tuple[float, float, float, float]) -> str:
    rounded = ",".join(f"{v:.4f}" for v in bbox)
    return hashlib.sha1(rounded.encode("utf-8")).hexdigest()[:16]


def _cache_path(bbox: tuple[float, float, float, float]) -> Path:
    return CACHE_DIR / f"overpass_{_cache_key(bbox)}.json"


def _read_cache(bbox: tuple[float, float, float, float]) -> dict | None:
    path = _cache_path(bbox)
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > CACHE_TTL_SECONDS:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(bbox: tuple[float, float, float, float], payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(bbox)
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        log.warning("Could not write Overpass cache: %s", exc)


def _payload_has_error(payload: dict) -> str | None:
    remark = payload.get("remark")
    if isinstance(remark, str) and any(
        token in remark.lower() for token in ("error", "timeout", "out of memory", "too many")
    ):
        return remark
    return None


@dataclass
class OSMBuilding:
    source_feature_id: str
    source: str
    geom_wgs84: BaseGeometry
    centroid_lat: float
    centroid_lon: float
    height_m: float | None
    levels: int | None
    building_type: str
    name: str | None
    tags: dict

    @property
    def footprint_area_m2(self) -> float:
        from ..geo import polygon_area_m2

        return polygon_area_m2(self.geom_wgs84)


def _build_query(bbox: tuple[float, float, float, float], timeout: int = 90) -> str:
    south, west, north, east = bbox
    return _QUERY_TEMPLATE.format(
        south=south, west=west, north=north, east=east, timeout=timeout
    )


def _polygon_from_ring(coords: list[tuple[float, float]]) -> BaseGeometry | None:
    """Build a valid (Multi)Polygon from a coordinate ring."""
    if len(coords) < 4:
        return None
    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if isinstance(poly, (Polygon, MultiPolygon)) and not poly.is_empty and poly.area > 0:
        return poly
    return None


def _relation_geometry(element: dict) -> BaseGeometry | None:
    """Best-effort multipolygon assembly for OSM relations."""
    polygons: list[Polygon] = []
    for member in element.get("members") or []:
        if member.get("role") not in ("outer", ""):
            continue
        geometry = member.get("geometry") or []
        if len(geometry) < 4:
            continue
        coords = [(p.get("lon"), p.get("lat")) for p in geometry if p.get("lon") is not None]
        poly = _polygon_from_ring(coords)
        if poly is None:
            continue
        if isinstance(poly, Polygon):
            polygons.append(poly)
        else:
            polygons.extend(g for g in poly.geoms if isinstance(g, Polygon))
    if not polygons:
        return None
    merged = unary_union(polygons)
    if isinstance(merged, (Polygon, MultiPolygon)):
        return merged
    return None


def _way_geometry(element: dict) -> BaseGeometry | None:
    geometry = element.get("geometry") or []
    if len(geometry) < 4:
        return None
    coords = [(p.get("lon"), p.get("lat")) for p in geometry if p.get("lon") is not None]
    return _polygon_from_ring(coords)


def _extract_height(tags: dict) -> tuple[float | None, int | None]:
    height = None
    raw_height = tags.get("height") or tags.get("building:height")
    if raw_height:
        cleaned = raw_height.split(";")[0].strip().lower().replace("m", "")
        try:
            height = float(cleaned)
        except ValueError:
            height = None
    levels = None
    raw_levels = tags.get("building:levels")
    if raw_levels:
        try:
            levels = int(float(raw_levels.split(";")[0]))
        except ValueError:
            levels = None
    if height is None and levels:
        height = float(levels) * 3.0
    return height, levels


def _classify(tags: dict) -> str:
    building_type = (tags.get("building") or "").lower()
    if building_type in {"commercial", "retail", "office", "industrial", "warehouse", "hotel"}:
        return "commercial"
    if building_type in {"garage", "carport", "shed", "roof"}:
        return "outbuilding"
    if building_type in {"house", "residential", "apartments", "terrace", "detached", "semidetached_house"}:
        return "residential"
    if tags.get("amenity") in {"school", "university", "college"}:
        return "institutional"
    # `building=yes` and other generic values carry no use information, so they
    # stay "unknown" instead of being silently counted as residential.
    return "unknown"


def parse_elements(payload: dict) -> list[OSMBuilding]:
    buildings: list[OSMBuilding] = []
    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        if tags.get("building") in (None, "no"):
            continue
        etype = element.get("type")
        if etype == "way":
            geom = _way_geometry(element)
        elif etype == "relation":
            geom = _relation_geometry(element)
        else:
            geom = None
        if geom is None:
            continue
        geom = ensure_valid(geom)
        if geom is None or geom.is_empty:
            continue
        lon, lat = centroid_lonlat(geom)
        height, levels = _extract_height(tags)
        feature_id = f"{etype}/{element.get('id')}"
        buildings.append(
            OSMBuilding(
                source_feature_id=feature_id,
                source="openstreetmap",
                geom_wgs84=geom,
                centroid_lat=lat,
                centroid_lon=lon,
                height_m=height,
                levels=levels,
                building_type=_classify(tags),
                name=tags.get("name") or tags.get("addr:housename") or tags.get("addr:street"),
                tags=tags,
            )
        )
    return buildings


def stratified_sample(buildings: list[OSMBuilding], max_buildings: int) -> list[OSMBuilding]:
    """Pick a size-diverse subset when a bbox returns more buildings than needed.

    Buildings are ordered by footprint area and sampled with an even stride so
    the batch keeps the full size spectrum (terrace houses through commercial
    sheds) instead of only the largest roofs.
    """
    if max_buildings <= 0 or len(buildings) <= max_buildings:
        return buildings
    ordered = sorted(buildings, key=lambda b: -b.footprint_area_m2)
    picked: list[OSMBuilding] = []
    total = len(ordered)
    for i in range(max_buildings):
        index = min(total - 1, int(round(i * total / max_buildings)))
        candidate = ordered[index]
        if candidate not in picked:
            picked.append(candidate)
    return picked


async def _fetch_one(endpoint: str, query: str, timeout_s: float) -> dict:
    """Single Overpass call that raises on any non-usable answer."""
    timeout = httpx.Timeout(timeout_s, connect=15.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": "SolarSync/1.0 (hackathon)"}
    ) as client:
        response = await client.post(endpoint, data={"data": query})
    if response.status_code >= 400:
        raise OverpassError(f"{endpoint} -> HTTP {response.status_code}")
    payload = response.json()
    remark = _payload_has_error(payload)
    if remark:
        raise OverpassError(f"{endpoint} -> {remark[:120]}")
    if not payload.get("elements"):
        raise OverpassError(f"{endpoint} -> empty element list")
    return payload


async def _race_endpoints(endpoints: tuple[str, ...], query: str) -> dict:
    """Query mirrors concurrently and return the first usable payload.

    Public Overpass nodes go down without notice, and a sequentially tried dead
    mirror burns its full timeout before the next one is contacted. Racing the
    mirrors keeps a live demo fast even when half of them are offline.
    """
    tasks = {
        asyncio.create_task(_fetch_one(endpoint, query, 60.0)): endpoint
        for endpoint in endpoints
    }
    errors: list[str] = []
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                exc = task.exception()
                if exc is None:
                    log.info("Overpass answered via %s", tasks[task])
                    return task.result()
                errors.append(str(exc))
                log.warning("Overpass endpoint %s failed: %s", tasks[task], exc)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    raise OverpassError("All Overpass endpoints failed: " + "; ".join(errors))


async def fetch_buildings(
    bbox: tuple[float, float, float, float],
    settings: Settings | None = None,
    max_buildings: int | None = None,
    stats: dict | None = None,
    allow_cache: bool = True,
    exclude_residential: bool | None = None,
) -> list[OSMBuilding]:
    """Live Overpass fetch of `building=*` inside a bounding box.

    When `stats` is provided it receives `total_found` (every footprint in the
    AOI) next to the returned (possibly stratified) sample, plus
    `residential_skipped` when residential roofs are filtered out before the
    sample is taken.
    """
    settings = settings or get_settings()
    if exclude_residential is None:
        exclude_residential = settings.exclude_residential
    query = _build_query(bbox)

    payload: dict | None = None
    last_error: Exception | None = None
    try:
        payload = await _race_endpoints(settings.overpass_endpoints, query)
    except Exception as exc:  # noqa: BLE001 - fall through to the snapshot
        last_error = exc

    if payload is not None:
        _write_cache(bbox, payload)
        buildings = parse_elements(payload)
        buildings.sort(key=lambda b: -b.footprint_area_m2)
        if stats is not None:
            stats["total_found"] = len(buildings)
            stats["source"] = "overpass"
        if exclude_residential:
            kept = [b for b in buildings if b.building_type != "residential"]
            if stats is not None:
                stats["residential_skipped"] = len(buildings) - len(kept)
            buildings = kept
        if max_buildings:
            buildings = stratified_sample(buildings, max_buildings)
        log.info("Overpass returned %d buildings", len(buildings))
        return buildings

    # Every mirror failed - fall back to the last good snapshot for this bbox.
    if allow_cache:
        cached = _read_cache(bbox)
        if cached is not None:
            buildings = parse_elements(cached)
            buildings.sort(key=lambda b: -b.footprint_area_m2)
            if stats is not None:
                stats["total_found"] = len(buildings)
                stats["source"] = "cache"
            if exclude_residential:
                kept = [b for b in buildings if b.building_type != "residential"]
                if stats is not None:
                    stats["residential_skipped"] = len(buildings) - len(kept)
                buildings = kept
            if max_buildings:
                buildings = stratified_sample(buildings, max_buildings)
            log.warning("Using cached Overpass snapshot with %d buildings", len(buildings))
            return buildings

    raise OverpassError(f"All Overpass endpoints failed: {last_error}")


async def fetch_buildings_near(
    lat: float,
    lon: float,
    radius_deg: float = 0.0035,
    settings: Settings | None = None,
    max_buildings: int | None = None,
) -> list[OSMBuilding]:
    """Buildings inside a small box around a point (single-building scope)."""
    bbox = (lat - radius_deg, lon - radius_deg, lat + radius_deg, lon + radius_deg)
    return await fetch_buildings(bbox, settings=settings, max_buildings=max_buildings)


def as_feature_collection(buildings: list[OSMBuilding]) -> dict:
    from shapely.geometry import mapping

    features = []
    for b in buildings:
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(b.geom_wgs84),
                "properties": {
                    "id": b.source_feature_id,
                    "name": b.name,
                    "building_type": b.building_type,
                    "height_m": b.height_m,
                    "levels": b.levels,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def geometry_from_payload(payload: dict) -> BaseGeometry | None:
    try:
        return ensure_valid(shape(payload))
    except Exception:  # noqa: BLE001
        return None

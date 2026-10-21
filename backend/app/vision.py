"""Tile-level rooftop detection for area runs.

Instead of one vision call per building, one call is made per ~230 m mosaic and
the returned polygons are attributed to buildings geometrically. A 600 m area
with 150 buildings therefore needs ~9 calls instead of 150, while the imagery
resolution stays at the native ~0.30 m/px.

Every building still gets a deterministic answer from the same pass, so the
score shown in the ranking and the score shown in the detail card agree.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from shapely.prepared import prep

from .config import BACKEND_DIR, Settings, get_settings
from .geo import ensure_valid, to_metric, utm_epsg_for
from .providers import imagery, inkling
from .providers.overpass import OSMBuilding

log = logging.getLogger("solarsync.vision")

_CACHE_DIR = BACKEND_DIR / ".cache" / "vision"

# A detection must have at least this share of its area inside the building
# footprint it is attributed to, otherwise it belongs to a neighbouring roof.
MIN_ATTRIBUTION_OVERLAP = 0.25

# Buildings above this footprint get a second, single-building look when the
# tile pass found nothing on them (large roofs are where misses hurt most).
RECHECK_MIN_FOOTPRINT_M2 = 2000.0
RECHECK_MAX_BUILDINGS = 8


@dataclass
class AreaDetection:
    objects_by_building: dict[str, list] = field(default_factory=dict)
    tiles: int = 0
    calls: int = 0
    raw_objects: int = 0
    kept_objects: int = 0
    dropped_objects: int = 0
    failed_tiles: int = 0
    rechecks: int = 0

    def as_dict(self) -> dict:
        return {
            "tiles": self.tiles,
            "calls": self.calls,
            "raw_objects": self.raw_objects,
            "kept_objects": self.kept_objects,
            "dropped_objects": self.dropped_objects,
            "failed_tiles": self.failed_tiles,
            "rechecks": self.rechecks,
        }


def _cache_path(tile_lat: float, tile_lon: float, model: str) -> Path:
    slug = model.replace("/", "_").replace(":", "_")
    return _CACHE_DIR / f"vision_{tile_lat:.6f}_{tile_lon:.6f}_{slug}.json"


def _read_cache(tile_lat: float, tile_lon: float, model: str) -> dict | None:
    path = _cache_path(tile_lat, tile_lon, model)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(tile_lat: float, tile_lon: float, model: str, payload: dict) -> None:
    path = _cache_path(tile_lat, tile_lon, model)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        log.debug("Could not cache vision response: %s", exc)


def _bbox_of(buildings: list[OSMBuilding]) -> tuple[float, float, float, float] | None:
    bounds: tuple[float, float, float, float] | None = None
    for building in buildings:
        if building.geom_wgs84 is None or building.geom_wgs84.is_empty:
            continue
        b = building.geom_wgs84.bounds
        if bounds is None:
            bounds = (b[0], b[1], b[2], b[3])
        else:
            bounds = (
                min(bounds[0], b[0]),
                min(bounds[1], b[1]),
                max(bounds[2], b[2]),
                max(bounds[3], b[3]),
            )
    return bounds


def _attribute(
    detected: list[inkling.DetectedObject],
    prepared: list[tuple[OSMBuilding, object]],
    stats: AreaDetection,
) -> dict[str, list]:
    """Assign detections to the building footprint they actually sit on.

    Vision boxes are coarse, so for small roofs a box often overhangs the
    footprint. We therefore accept the building with the largest overlap as
    long as a meaningful share of the box lands on it.
    """
    out: dict[str, list] = {}
    for obj in detected:
        geom = obj.geom_4326
        if geom is None or geom.is_empty:
            continue

        target = None
        point = geom.representative_point()
        for building, prepared_geom in prepared:
            if prepared_geom.contains(point):
                target = building
                break

        if target is None:
            best_overlap = 0.0
            for building, _prepared in prepared:
                try:
                    inter = geom.intersection(building.geom_wgs84)
                except Exception:  # noqa: BLE001 - degenerate geometry
                    continue
                if inter.is_empty:
                    continue
                overlap = inter.area
                if overlap > best_overlap:
                    best_overlap = overlap
                    target = building
            if target is None or best_overlap / max(geom.area, 1e-12) < MIN_ATTRIBUTION_OVERLAP:
                stats.dropped_objects += 1
                continue

        clipped = ensure_valid(geom.intersection(target.geom_wgs84))
        if clipped is None or clipped.is_empty:
            stats.dropped_objects += 1
            continue
        metric = to_metric(clipped, epsg=utm_epsg_for(target.centroid_lon))
        out.setdefault(target.source_feature_id, []).append(
            inkling.DetectedObject(
                obj_class=obj.obj_class,
                geom_4326=clipped,
                confidence=obj.confidence,
                notes=obj.notes,
                area_m2=float(metric.area) if metric is not None else 0.0,
            )
        )
        stats.kept_objects += 1
    return out


async def detect_area(
    buildings: list[OSMBuilding],
    settings: Settings | None = None,
) -> AreaDetection:
    """Run one vision call per mosaic and attribute results to buildings."""
    settings = settings or get_settings()
    stats = AreaDetection()
    if not settings.has_inkling_key or not buildings:
        return stats

    bounds = _bbox_of(buildings)
    if bounds is None:
        return stats

    tiles = await imagery.fetch_area_tiles(
        bounds[1], bounds[0], bounds[3], bounds[2], settings=settings
    )
    stats.tiles = len(tiles)
    if not tiles:
        return stats

    prepared: list[tuple[OSMBuilding, object]] = []
    for building in buildings:
        if building.geom_wgs84 is None or building.geom_wgs84.is_empty:
            continue
        prepared.append((building, prep(building.geom_wgs84)))

    limit = max(1, min(settings.max_concurrent_buildings, 8))
    semaphore = asyncio.Semaphore(limit)
    lock = asyncio.Lock()

    async def _one(index: int, tile: imagery.SatelliteTile) -> None:
        cached = _read_cache(tile.center_lat, tile.center_lon, settings.vision_model)
        response = None
        if cached is not None:
            try:
                response = inkling.InklingResponse.model_validate(cached)
            except Exception:  # noqa: BLE001 - stale cache shape
                response = None

        async with semaphore:
            if response is None:
                try:
                    response = await inkling.call_inkling(
                        tile.image,
                        tile.content_type,
                        settings=settings,
                        tile_id=f"tile_{index:03d}",
                        prompt=inkling.AREA_SYSTEM_PROMPT,
                    )
                except inkling.InklingError as exc:
                    log.warning("Area vision call failed for tile %s: %s", index, exc)
                    async with lock:
                        stats.failed_tiles += 1
                    return
                _write_cache(
                    tile.center_lat,
                    tile.center_lon,
                    settings.vision_model,
                    response.model_dump(by_alias=True),
                )

        converted = inkling.convert_response(
            response,
            center_lat=tile.center_lat,
            center_lon=tile.center_lon,
            roof_geom_4326=None,
            zoom=tile.zoom,
            scale=tile.scale,
            tile_size_px=tile.size_px,
        )
        async with lock:
            stats.calls += 1
            stats.raw_objects += converted.raw_objects
            attributed = _attribute(converted.objects, prepared, stats)
            for key, objs in attributed.items():
                stats.objects_by_building.setdefault(key, []).extend(objs)

    await asyncio.gather(*(_one(i, tile) for i, tile in enumerate(tiles)))

    await _recheck_large_roofs(buildings, stats, settings)
    return stats


async def _recheck_large_roofs(
    buildings: list[OSMBuilding],
    stats: AreaDetection,
    settings: Settings,
) -> None:
    """Single-building pass for large roofs the mosaic pass left empty."""
    candidates = []
    for building in buildings:
        if stats.objects_by_building.get(building.source_feature_id):
            continue
        try:
            area = building.footprint_area_m2
        except Exception:  # noqa: BLE001
            continue
        if area >= RECHECK_MIN_FOOTPRINT_M2:
            candidates.append(building)
    candidates = candidates[:RECHECK_MAX_BUILDINGS]

    for building in candidates:
        tile = await imagery.fetch_satellite_tile(
            building.centroid_lat, building.centroid_lon, settings=settings
        )
        if tile is None:
            continue
        try:
            response = await inkling.call_inkling(
                tile.image, tile.content_type, settings=settings, tile_id="recheck"
            )
        except inkling.InklingError as exc:
            log.warning("Recheck failed for %s: %s", building.source_feature_id, exc)
            continue
        converted = inkling.convert_response(
            response,
            center_lat=tile.center_lat,
            center_lon=tile.center_lon,
            roof_geom_4326=building.geom_wgs84,
            zoom=tile.zoom,
            scale=tile.scale,
            epsg=utm_epsg_for(building.centroid_lon),
            tile_size_px=tile.size_px,
        )
        stats.rechecks += 1
        if converted.objects:
            stats.objects_by_building.setdefault(building.source_feature_id, []).extend(
                converted.objects
            )
            stats.kept_objects += len(converted.objects)
    log.info("Area vision rechecks: %s", stats.rechecks)



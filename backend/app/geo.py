"""Geospatial helpers: CRS transforms, Web-Mercator resolution, GeoJSON I/O.

All area / distance / buffer maths happens in EPSG:32647 (UTM zone 47N), the
metric CRS that covers Malaysia. EPSG:4326 is used only for interchange and
for addressing provider APIs.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any, Iterable, Sequence

from pyproj import CRS, Transformer
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform
from shapely.validation import make_valid

WGS84 = "EPSG:4326"
UTM47N = "EPSG:32647"  # Peninsular Malaysia metric projection (UTM zone 47N)
UTM47N_EPSG = 32647

_TZ = Transformer.from_crs(WGS84, UTM47N, always_xy=True)

EARTH_RADIUS_M = 6378137.0
TILE_SIZE_PX = 640  # Google Static API `size=640x640`
NORMALIZED_GRID = 1000  # Inkling normalised polygon coordinate space


# --------------------------------------------------------------------------
# CRS conversion
# --------------------------------------------------------------------------
def utm_epsg_for(lon: float) -> int:
    """EPSG code of the UTM zone that contains a longitude.

    Peninsular Malaysia sits in zone 47 (EPSG:32647), Sabah in zone 49
    (EPSG:32649) and most of Sarawak in zone 50. Using a fixed zone would
    distort distances by tens of percent east of 102 deg E, so the zone is
    always derived from the geometry itself.
    """
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(60, max(1, zone))
    return 32600 + zone  # northern hemisphere


@lru_cache(maxsize=16)
def _transformers(epsg: int) -> tuple[Transformer, Transformer]:
    forward = Transformer.from_crs(WGS84, f"EPSG:{epsg}", always_xy=True)
    inverse = Transformer.from_crs(f"EPSG:{epsg}", WGS84, always_xy=True)
    return forward, inverse


def to_metric(geom: BaseGeometry, epsg: int | None = None) -> BaseGeometry:
    """Project a 4326 geometry into the local UTM zone (metres)."""
    if epsg is None:
        epsg = utm_epsg_for(geom.centroid.x)
    forward, _ = _transformers(epsg)
    return shapely_transform(forward.transform, geom)


def to_wgs84(geom: BaseGeometry, epsg: int | None = None) -> BaseGeometry:
    """Project a UTM geometry back into 4326."""
    if epsg is None:
        epsg = UTM47N_EPSG
    _, inverse = _transformers(epsg)
    return shapely_transform(inverse.transform, geom)


def lonlat_to_metric(lon: float, lat: float, epsg: int | None = None) -> tuple[float, float]:
    if epsg is None:
        epsg = utm_epsg_for(lon)
    forward, _ = _transformers(epsg)
    return forward.transform(lon, lat)


def metric_to_lonlat(x: float, y: float, epsg: int | None = None) -> tuple[float, float]:
    if epsg is None:
        epsg = UTM47N_EPSG
    _, inverse = _transformers(epsg)
    return inverse.transform(x, y)


def ensure_valid(geom: BaseGeometry | None) -> BaseGeometry | None:
    """Repair self-intersecting geometry and drop NaN results."""
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "GeometryCollection":
        # keep only the polygonal parts (largest first)
        polygons = [g for g in geom.geoms if g.geom_type in {"Polygon", "MultiPolygon"} and not g.is_empty]
        if not polygons:
            return None
        polygons.sort(key=lambda g: -g.area)
        geom = polygons[0]
    return geom


# --------------------------------------------------------------------------
# Web Mercator resolution (no GDAL / rasterio required)
# --------------------------------------------------------------------------
def meters_per_pixel(lat: float, zoom: int = 20, scale: int = 2) -> float:
    """Ground resolution of a Web-Mercator tile.

    meters_per_pixel = (156543.03392 * cos(lat)) / (2 ** zoom) / scale

    At zoom=20, scale=2 and Malaysian latitudes this is ~0.0745 m/px, i.e. the
    640x640 satellite tile covers roughly 47.7 m of ground.
    """
    return (156543.03392 * math.cos(math.radians(lat))) / (2**zoom) / scale


def normalized_to_pixel_offset(
    x_norm: float, y_norm: float, tile_size_px: int = TILE_SIZE_PX
) -> tuple[float, float]:
    """Map a [0, 1000] normalised vertex to a pixel offset from tile centre.

    Returns (dx_px, dy_px) where dy is positive downwards (image convention).
    """
    cx = (x_norm / NORMALIZED_GRID) * tile_size_px
    cy = (y_norm / NORMALIZED_GRID) * tile_size_px
    return cx - tile_size_px / 2.0, cy - tile_size_px / 2.0


def normalized_polygon_to_metric(
    polygon_norm: Sequence[Sequence[float]],
    center_lat: float,
    center_lon: float,
    zoom: int = 20,
    scale: int = 2,
    tile_size_px: int = TILE_SIZE_PX,
    epsg: int | None = None,
) -> BaseGeometry:
    """Convert an Inkling [0,1000] normalised polygon into local UTM metres.

    The satellite tile is centred on (center_lat, center_lon) and each pixel
    covers `meters_per_pixel` metres. Normalised vertices are therefore
    converted to metre offsets from the centre and added to the projected
    centre point.
    """
    mpp = meters_per_pixel(center_lat, zoom=zoom, scale=scale)
    cx, cy = lonlat_to_metric(center_lon, center_lat, epsg=epsg)
    pts: list[tuple[float, float]] = []
    for x_norm, y_norm in polygon_norm:
        dx_px, dy_px = normalized_to_pixel_offset(x_norm, y_norm, tile_size_px)
        # image y grows downwards -> metric y grows northwards
        pts.append((cx + dx_px * mpp, cy - dy_px * mpp))
    if len(pts) < 3:
        raise ValueError("polygon needs at least three vertices")
    from shapely.geometry import Polygon

    return Polygon(pts)


# --------------------------------------------------------------------------
# GeoJSON helpers
# --------------------------------------------------------------------------
def to_geojson_dict(geom: BaseGeometry | None) -> dict[str, Any] | None:
    if geom is None or geom.is_empty:
        return None
    return mapping(geom)


def to_geojson_str(geom: BaseGeometry | None) -> str | None:
    """Serialise geometry as a GeoJSON string (how we persist to SQLite)."""
    if geom is None or geom.is_empty:
        return None
    return json.dumps(mapping(geom), separators=(",", ":"))


def from_geojson_str(payload: str | None) -> BaseGeometry | None:
    if not payload:
        return None
    try:
        return shape(json.loads(payload))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def geometry_collection_to_geojson(geoms: Iterable[BaseGeometry]) -> dict[str, Any]:
    """Combine many geometries into a single GeoJSON FeatureCollection."""
    features = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        features.append({"type": "Feature", "geometry": mapping(g), "properties": {}})
    return {"type": "FeatureCollection", "features": features}


def bbox_from_center(lat: float, lon: float, half_size_deg: float) -> tuple[float, float, float, float]:
    """Return (south, west, north, east) bbox around a centre point."""
    return (
        lat - half_size_deg,
        lon - half_size_deg,
        lat + half_size_deg,
        lon + half_size_deg,
    )


def bbox_polygon(bbox: Sequence[float]) -> BaseGeometry:
    south, west, north, east = bbox
    return box(west, south, east, north)


def centroid_lonlat(geom: BaseGeometry) -> tuple[float, float]:
    c = geom.representative_point() if geom.geom_type in {"Polygon", "MultiPolygon"} else geom.centroid
    return c.x, c.y


def polygon_area_m2(geom_wgs84: BaseGeometry | None) -> float:
    """Area in m2 of a 4326 geometry (reprojected on the fly)."""
    if geom_wgs84 is None or geom_wgs84.is_empty:
        return 0.0
    return float(to_metric(geom_wgs84).area)


def crs_utm47() -> CRS:
    return CRS.from_epsg(32647)

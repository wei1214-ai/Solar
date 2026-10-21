"""Deterministic Malaysia rooftop simulator - the "no API key" demo fallback.

The footprints themselves are REAL (live OpenStreetMap data via Overpass).
What this module synthesises is the *detail* that the paid providers would
otherwise supply:

*  roof planes with a physically plausible pitch / azimuth distribution for
   Malaysian terrace houses and flat commercial sheds,
*  rooftop equipment (air-con condensers, water tanks, skylights, vents and
   existing PV arrays) with realistic footprints and heights,
*  annual specific yield from the tilted-plane irradiance model
   (PVGIS-style 1450 kWh/kWp/year Malaysian baseline) modulated by orientation,
   roof micro-shading and neighbourhood density.

Everything is seeded from the building id, so a given building always yields
the same roof, the same obstacles and the same score.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import split, triangulate

from ..config import Settings, get_settings
from ..geo import ensure_valid, to_metric, to_wgs84
from ..solar import estimate_flux
from .overpass import OSMBuilding


@dataclass
class SimulatedSegment:
    segment_id: str
    geom_4326: BaseGeometry
    pitch_deg: float
    azimuth_deg: float
    area_m2: float
    flux_kwh_per_kwp: float
    orientation_factor: float
    shade_factor: float
    plane_height_m: float


@dataclass
class SimulatedObject:
    obj_class: str
    geom_4326: BaseGeometry
    height_m: float
    confidence: float = 0.72
    notes: str = "simulated rooftop equipment"


@dataclass
class SimulatedRoof:
    segments: list[SimulatedSegment] = field(default_factory=list)
    objects: list[SimulatedObject] = field(default_factory=list)
    mode: str = "demo"
    building_type: str = "residential"
    note: str = ""


def _seed_for(building_id: str) -> int:
    digest = hashlib.sha256(building_id.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _long_axis_angle(poly: BaseGeometry) -> float:
    try:
        rect = poly.minimum_rotated_rectangle
    except Exception:  # pragma: no cover
        return 0.0
    coords = list(rect.exterior.coords)
    best_angle, best_len = 0.0, -1.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        length = math.hypot(x2 - x1, y2 - y1)
        if length > best_len:
            best_len = length
            best_angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return best_angle


def _split_polygon(poly: BaseGeometry, angle_deg: float) -> list[BaseGeometry]:
    """Cut a polygon in two with an infinite line through its centre."""
    try:
        centre = poly.representative_point()
        radius = poly.length * 2.0 + 10.0
        radians = math.radians(angle_deg)
        dx, dy = math.cos(radians), math.sin(radians)
        line = LineString(
            [
                (centre.x - dx * radius, centre.y - dy * radius),
                (centre.x + dx * radius, centre.y + dy * radius),
            ]
        )
        result = split(poly, line)
        parts = [g for g in result.geoms if g.geom_type == "Polygon" and g.area > 1.0]
        if len(parts) >= 2:
            parts.sort(key=lambda g: -g.area)
            return parts[:2]
    except Exception:  # noqa: BLE001
        pass
    return [poly]


def _random_point_in_polygon(poly: BaseGeometry, rng: random.Random) -> Point:
    try:
        triangles = list(triangulate(poly).geoms)
    except Exception:  # noqa: BLE001
        triangles = []
    if triangles:
        triangle = triangles[rng.randrange(len(triangles))]
        minx, miny, maxx, maxy = triangle.bounds
        for _ in range(12):
            x = rng.uniform(minx, maxx)
            y = rng.uniform(miny, maxy)
            candidate = Point(x, y)
            if poly.contains(candidate):
                return candidate
        return triangle.centroid
    return poly.representative_point()


def _rectangle(point: Point, width: float, length: float, angle_deg: float) -> Polygon:
    from shapely import affinity

    radius = math.hypot(width, length) / 2.0 + 0.1
    minx, miny, maxx, maxy = point.buffer(radius).bounds
    poly = Polygon(
        [
            (minx, miny),
            (maxx, miny),
            (maxx, maxy),
            (minx, maxy),
        ]
    )
    # Scale the square into the requested rectangle, then rotate it.
    poly = affinity.scale(
        poly,
        xfact=width / max((maxx - minx), 1e-9),
        yfact=length / max((maxy - miny), 1e-9),
        origin="center",
    )
    return affinity.rotate(poly, angle_deg, origin="center")


def _circle_polygon(point: Point, diameter: float, sides: int = 12) -> Polygon:
    radius = diameter / 2.0
    coords = []
    for i in range(sides):
        angle = 2.0 * math.pi * i / sides
        coords.append((point.x + radius * math.cos(angle), point.y + radius * math.sin(angle)))
    return Polygon(coords)


def _place_object(
    poly: BaseGeometry,
    rng: random.Random,
    width: float,
    length: float,
    angle_deg: float,
    tries: int = 25,
) -> Polygon | None:
    for _ in range(tries):
        point = _random_point_in_polygon(poly, rng)
        candidate = _rectangle(point, width, length, angle_deg)
        if poly.contains(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Roof geometry
# ---------------------------------------------------------------------------
def _pitch_azimuth(rng: random.Random, building_type: str) -> tuple[float, float]:
    if building_type == "residential":
        pitch = rng.choice([3.0, 5.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0])
        azimuth = rng.choice([0.0, 15.0, 90.0, 100.0, 180.0, 190.0, 270.0, 280.0, 350.0])
        azimuth += rng.uniform(-8.0, 8.0)
    else:
        pitch = rng.choice([1.0, 2.0, 3.0, 4.0, 5.0])
        azimuth = rng.choice([180.0, 180.0, 190.0, 170.0, 200.0])
    return float(pitch), float(azimuth % 360.0)


def _neighbourhood_shade_factor(neighbours: list[OSMBuilding], self_building: OSMBuilding) -> float:
    """Very small urban-canyon penalty for dense surroundings."""
    if not neighbours:
        return 1.0
    dense = 0
    for other in neighbours:
        if other.source_feature_id == self_building.source_feature_id:
            continue
        dlat = other.centroid_lat - self_building.centroid_lat
        dlon = other.centroid_lon - self_building.centroid_lon
        distance_m = math.hypot(dlat * 111320.0, dlon * 111320.0 * math.cos(math.radians(self_building.centroid_lat)))
        if distance_m < 35.0:
            dense += 1
    if dense >= 12:
        return 0.90
    if dense >= 7:
        return 0.94
    if dense >= 3:
        return 0.97
    return 1.0


def simulate_roof(
    building: OSMBuilding,
    neighbours: list[OSMBuilding] | None = None,
    settings: Settings | None = None,
    epsg: int | None = None,
    baseline_kwh_per_kwp: float | None = None,
) -> SimulatedRoof:
    """Build a deterministic simulated roof model for one building."""
    settings = settings or get_settings()
    rng = random.Random(_seed_for(building.source_feature_id))

    metric = to_metric(building.geom_wgs84, epsg=epsg)
    metric = ensure_valid(metric)
    if metric is None or metric.is_empty:
        return SimulatedRoof(note="invalid footprint")

    footprint_area = float(metric.area)
    if footprint_area < 12.0:
        return SimulatedRoof(note="footprint below minimum area")

    building_type = building.building_type
    if building_type == "outbuilding":
        building_type = "residential"
    if footprint_area > 700.0 and building_type == "residential":
        building_type = "commercial"

    pitch, azimuth = _pitch_azimuth(rng, building_type)
    shade_factor = _neighbourhood_shade_factor(neighbours or [], building)

    # Split large roofs into two planes with slightly different orientation.
    parts: list[BaseGeometry] = [metric]
    if footprint_area > 140.0 and rng.random() < 0.65:
        axis = _long_axis_angle(metric)
        parts = _split_polygon(metric, axis + 90.0)
        if len(parts) == 2:
            pitch2 = max(0.0, pitch + rng.uniform(-6.0, 6.0))
            azimuth2 = (azimuth + rng.choice([-15.0, 0.0, 15.0])) % 360.0
        else:
            pitch2, azimuth2 = pitch, azimuth
    else:
        pitch2, azimuth2 = pitch, azimuth

    segments: list[SimulatedSegment] = []
    for index, part in enumerate(parts):
        part = ensure_valid(part)
        if part is None or part.is_empty:
            continue
        # The roof plate is slightly inside the wall footprint.
        roof = part.buffer(-0.15)
        roof = ensure_valid(roof)
        if roof is None or roof.is_empty or roof.area < 8.0:
            roof = part
        seg_pitch = pitch if index == 0 else pitch2
        seg_azimuth = azimuth if index == 0 else azimuth2
        flux = estimate_flux(
            lat_deg=building.centroid_lat,
            tilt_deg=seg_pitch,
            azimuth_deg=seg_azimuth,
            baseline_kwh_per_kwp=baseline_kwh_per_kwp or settings.solar_baseline_kwh_per_kwp_year,
            shade_factor=shade_factor,
        )
        height = building.height_m or (3.0 * (building.levels or 2) + 1.0)
        segments.append(
            SimulatedSegment(
                segment_id=f"sim_seg_{index}",
                geom_4326=to_wgs84(roof, epsg=epsg),
                pitch_deg=seg_pitch,
                azimuth_deg=seg_azimuth,
                area_m2=float(roof.area),
                flux_kwh_per_kwp=flux.flux_kwh_per_kwp,
                orientation_factor=flux.orientation_factor,
                shade_factor=flux.shade_factor,
                plane_height_m=float(height),
            )
        )

    objects = _simulate_objects(rng, segments, metric, building_type, epsg=epsg)
    return SimulatedRoof(
        segments=segments,
        objects=objects,
        mode="demo",
        building_type=building_type,
        note="Simulated roof model (PVGIS-style Malaysia baseline, no API key required)",
    )


def _simulate_objects(
    rng: random.Random,
    segments: list[SimulatedSegment],
    metric: BaseGeometry,
    building_type: str,
    epsg: int | None = None,
) -> list[SimulatedObject]:
    """Scatter realistic rooftop equipment over the roof planes."""
    objects: list[SimulatedObject] = []
    if not segments:
        return objects

    roof_metric = to_metric(segments[0].geom_4326, epsg=epsg)
    roof_metric = ensure_valid(roof_metric) or metric
    angle = _long_axis_angle(roof_metric)

    if building_type == "residential":
        plan = {
            "hvac_unit": rng.randint(1, 2),
            "water_tank": 1 if rng.random() < 0.55 else 0,
            "skylight": 1 if rng.random() < 0.32 else 0,
            "vent": 1 if rng.random() < 0.45 else 0,
            "existing_pv": 1 if rng.random() < 0.20 else 0,
        }
    else:
        plan = {
            "hvac_unit": rng.randint(3, 6),
            "water_tank": rng.randint(0, 2),
            "skylight": 1 if rng.random() < 0.25 else 0,
            "vent": rng.randint(1, 3),
            "existing_pv": 1 if rng.random() < 0.35 else 0,
        }

    # Existing PV arrays occupy a big chunk of the roof - place them first.
    if plan.get("existing_pv"):
        placed = _place_object(roof_metric, rng, 2.268, 5.286, angle)
        if placed is not None:
            objects.append(
                SimulatedObject(
                    obj_class="existing_pv",
                    geom_4326=to_wgs84(placed, epsg=epsg),
                    height_m=0.75,
                    confidence=0.68,
                    notes="existing 2x3 panel array detected on roof",
                )
            )

    for obj_class, count in plan.items():
        if obj_class == "existing_pv":
            continue
        for _ in range(count):
            if obj_class == "hvac_unit":
                geom = _place_object(roof_metric, rng, 0.95, 0.75, angle)
                height = 1.15
            elif obj_class == "water_tank":
                point = _random_point_in_polygon(roof_metric, rng)
                geom = _circle_polygon(point, 1.25)
                if not roof_metric.contains(geom):
                    geom = _place_object(roof_metric, rng, 1.25, 1.25, angle) or geom
                height = 2.30
            elif obj_class == "skylight":
                geom = _place_object(roof_metric, rng, 1.30, 1.05, angle)
                height = 0.55
            else:  # vent
                geom = _place_object(roof_metric, rng, 0.5, 0.5, angle)
                height = 0.85
            if geom is None:
                continue
            objects.append(
                SimulatedObject(
                    obj_class=obj_class,
                    geom_4326=to_wgs84(geom, epsg=epsg),
                    height_m=height,
                    confidence=round(rng.uniform(0.55, 0.9), 2),
                    notes=f"simulated {obj_class.replace('_', ' ')}",
                )
            )
    return objects


def build_flux_resolver(
    segment_flux: dict[str, float],
    objects: list[SimulatedObject],
    sun_altitude_deg: float = 60.0,
    epsg: int | None = None,
):
    """Build the per-candidate flux resolver used by the greedy layout.

    The shadow band of an obstacle of height h extends h / tan(altitude) from
    its footprint; a panel inside that band loses part of its yield.
    """
    from ..geo import lonlat_to_metric

    obstacles: list[tuple[float, float, float]] = []
    for obj in objects:
        centroid = obj.geom_4326.centroid
        x, y = lonlat_to_metric(centroid.x, centroid.y, epsg=epsg)
        obstacles.append((x, y, obj.height_m))

    tan_alt = max(0.2, math.tan(math.radians(sun_altitude_deg)))

    def resolve(segment_id: str, panel_geom) -> float:
        base = segment_flux.get(segment_id, 1450.0)
        centroid = panel_geom.centroid
        loss = 0.0
        for ox, oy, height in obstacles:
            distance = math.hypot(centroid.x - ox, centroid.y - oy)
            shadow_length = height / tan_alt
            if shadow_length <= 0.1:
                continue
            if distance < shadow_length:
                relative = 1.0 - (distance / shadow_length)
                loss = max(loss, 0.22 * relative)
        return base * (1.0 - loss)

    return resolve

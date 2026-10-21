"""End-to-end analysis pipeline for a single building.

    footprint -> Solar roof planes -> rooftop objects -> usable roof polygon
              -> feasible panel layout -> annual energy -> MY CAPEX -> score

Status handling follows spec 2.3:
    RANKED | EXCLUDED_BY_RULE | UNRANKED_NO_DATA | ANALYSIS_FAILED
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .config import Settings, get_settings
from .cost import compute_capex, compute_pitch_metrics
from .geo import ensure_valid, to_metric, to_wgs84, utm_epsg_for
from .panels import SegmentInput, apply_walkways, build_layout, panels_geojson_4326
from .providers import google_solar, imagery, inkling, simulator
from .providers.overpass import OSMBuilding
from .solar import estimate_flux

log = logging.getLogger("solarsync.pipeline")

RANKED = "RANKED"
EXCLUDED_BY_RULE = "EXCLUDED_BY_RULE"
UNRANKED_NO_DATA = "UNRANKED_NO_DATA"
ANALYSIS_FAILED = "ANALYSIS_FAILED"


@dataclass
class SegmentRecord:
    segment_id: str
    geom_4326: BaseGeometry
    area_m2: float
    pitch_deg: float
    azimuth_deg: float
    plane_height_m: float | None
    flux_kwh_per_kwp: float
    orientation_factor: float
    shade_factor: float
    solar_data_ref: str | None = None


@dataclass
class ObjectRecord:
    obj_class: str
    geom_4326: BaseGeometry
    height_m: float
    confidence: float
    source_model: str
    notes: str | None = None


@dataclass
class BuildingAnalysis:
    building: OSMBuilding
    status: str
    reason: str | None = None
    segments: list[SegmentRecord] = field(default_factory=list)
    objects: list[ObjectRecord] = field(default_factory=list)
    usable_geom_4326: BaseGeometry | None = None
    usable_area_m2: float = 0.0
    panels_geojson: list[dict] = field(default_factory=list)
    panel_count: int = 0
    installed_kwp: float = 0.0
    panel_area_m2: float = 0.0
    annual_energy_dc_kwh: float = 0.0
    annual_energy_ac_kwh: float = 0.0
    capex_base_rm: float = 0.0
    capex_low_rm: float = 0.0
    capex_high_rm: float = 0.0
    potential_score: float = 0.0
    annual_savings_rm: float = 0.0
    payback_years: float = 0.0
    co2_offset_tokens: float = 0.0
    metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Roof partitioning helpers
# ---------------------------------------------------------------------------
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


def partition_footprint(poly: BaseGeometry, count: int) -> list[BaseGeometry]:
    """Split a footprint into `count` roof planes (approximate)."""
    if count <= 1:
        return [poly]
    parts = [poly]
    while len(parts) < count:
        parts.sort(key=lambda g: -g.area)
        biggest = parts.pop(0)
        angle = _long_axis_angle(biggest) + 90.0
        pieces = simulator._split_polygon(biggest, angle)
        parts.extend(pieces)
    parts.sort(key=lambda g: -g.area)
    return parts[:count]


# ---------------------------------------------------------------------------
# Roof plane resolution (live Google data or simulated)
# ---------------------------------------------------------------------------
def _segments_from_solar(
    building: OSMBuilding,
    metric_footprint: BaseGeometry,
    solar: google_solar.SolarBuilding,
    settings: Settings,
    epsg: int,
) -> list[SegmentRecord]:
    planes = solar.roof_planes
    parts = partition_footprint(metric_footprint, len(planes))
    records: list[SegmentRecord] = []
    for index, part in enumerate(parts):
        plane = planes[index] if index < len(planes) else planes[0]
        pitch = float(plane.pitch_deg or 0.0)
        azimuth = float(plane.azimuth_deg if plane.azimuth_deg is not None else 180.0)
        flux_estimate = estimate_flux(
            lat_deg=building.centroid_lat,
            tilt_deg=pitch,
            azimuth_deg=azimuth,
            baseline_kwh_per_kwp=settings.solar_baseline_kwh_per_kwp_year,
        )
        # When the Solar API provides a building level flux, scale the plane
        # estimate so the building average matches the API baseline.
        flux = flux_estimate.flux_kwh_per_kwp
        if solar.flux_kwh_per_kwp:
            expected = solar.flux_kwh_per_kwp
            if flux > 0:
                flux = expected * (flux / flux_estimate.baseline_kwh_per_kwp)
        records.append(
            SegmentRecord(
                segment_id=plane.segment_id,
                geom_4326=to_wgs84(part, epsg=epsg),
                area_m2=float(part.area),
                pitch_deg=pitch,
                azimuth_deg=azimuth,
                plane_height_m=plane.plane_height_m,
                flux_kwh_per_kwp=float(flux),
                orientation_factor=flux_estimate.orientation_factor,
                shade_factor=1.0,
                solar_data_ref="google_solar:buildingInsights.findClosest",
            )
        )
    return records


def _segments_from_simulator(
    building: OSMBuilding,
    simulated: simulator.SimulatedRoof,
    source_ref: str = "demo_simulator:pv_gis_baseline",
) -> list[SegmentRecord]:
    return [
        SegmentRecord(
            segment_id=segment.segment_id,
            geom_4326=segment.geom_4326,
            area_m2=segment.area_m2,
            pitch_deg=segment.pitch_deg,
            azimuth_deg=segment.azimuth_deg,
            plane_height_m=segment.plane_height_m,
            flux_kwh_per_kwp=segment.flux_kwh_per_kwp,
            orientation_factor=segment.orientation_factor,
            shade_factor=segment.shade_factor,
            solar_data_ref=source_ref,
        )
        for segment in simulated.segments
    ]


# ---------------------------------------------------------------------------
# Rooftop object detection
# ---------------------------------------------------------------------------
async def _detect_objects(
    building: OSMBuilding,
    roof_geom_4326: BaseGeometry,
    settings: Settings,
    epsg: int | None = None,
) -> tuple[list[ObjectRecord], str]:
    """Try Inkling on live satellite imagery, else use the simulator."""
    if settings.has_inkling_key:
        tile = await imagery.fetch_satellite_tile(
            building.centroid_lat, building.centroid_lon, settings=settings
        )
        if tile:
            try:
                response = await inkling.call_inkling(
                    tile.image, tile.content_type, settings=settings
                )
                converted = inkling.convert_response(
                    response,
                    center_lat=tile.center_lat,
                    center_lon=tile.center_lon,
                    roof_geom_4326=roof_geom_4326,
                    zoom=tile.zoom,
                    scale=tile.scale,
                    epsg=epsg,
                    tile_size_px=tile.size_px,
                )
                records = [
                    ObjectRecord(
                        obj_class=obj.obj_class,
                        geom_4326=obj.geom_4326,
                        height_m=_default_height(obj.obj_class),
                        confidence=obj.confidence,
                        source_model="inkling",
                        notes=obj.notes,
                    )
                    for obj in converted.objects
                ]
                if records:
                    return records, "inkling"
            except inkling.InklingError as exc:
                log.warning("Inkling failed for %s: %s", building.source_feature_id, exc)

    # Demo fallback: deterministic rooftop equipment simulation.
    neighbours = getattr(building, "_neighbours", []) or []
    simulated = simulator.simulate_roof(
        building,
        neighbours,
        settings=settings,
        epsg=epsg,
        baseline_kwh_per_kwp=settings.solar_baseline_kwh_per_kwp_year,
    )
    records = [
        ObjectRecord(
            obj_class=obj.obj_class,
            geom_4326=obj.geom_4326,
            height_m=obj.height_m,
            confidence=obj.confidence,
            source_model="simulator",
            notes=obj.notes,
        )
        for obj in simulated.objects
    ]
    return records, "simulator"


def _default_height(obj_class: str) -> float:
    return {
        "hvac_unit": 1.15,
        "water_tank": 2.30,
        "skylight": 0.55,
        "vent": 0.85,
        "existing_pv": 0.75,
    }.get(obj_class, 0.8)


# ---------------------------------------------------------------------------
# Site recommendation
# ---------------------------------------------------------------------------
RECOMMENDATION_TIERS = (
    ("STRONG CANDIDATE", 0.30, 7.0),
    ("VIABLE CANDIDATE", 0.22, 9.0),
    ("MARGINAL", 0.15, 12.0),
)


def build_recommendation(
    potential_score: float,
    payback_years: float,
    installed_kwp: float,
    segments: list[SegmentRecord],
    objects: list[ObjectRecord],
) -> dict:
    """Plain-language site recommendation with the reasons behind it.

    Combines the cost-adjusted score, the payback horizon and the physical
    characteristics of the roof (size, pitch, orientation, obstacle load).
    """
    reasons: list[str] = []
    tier = "LOW PRIORITY"

    for name, min_score, max_payback in RECOMMENDATION_TIERS:
        if potential_score >= min_score and (payback_years <= max_payback or payback_years == 0):
            tier = name
            break

    if installed_kwp >= 50:
        reasons.append(f"Large usable roof ({installed_kwp:.0f} kWp installable)")
    elif installed_kwp >= 10:
        reasons.append(f"Medium roof ({installed_kwp:.0f} kWp installable)")
    elif installed_kwp > 0:
        reasons.append(f"Small roof ({installed_kwp:.0f} kWp installable)")

    best_flux = max((s.flux_kwh_per_kwp for s in segments), default=0.0)
    if best_flux >= 1400:
        reasons.append("Excellent solar resource / orientation")
    elif best_flux >= 1250:
        reasons.append("Good solar orientation")
    elif best_flux > 0:
        reasons.append("Sub-optimal roof orientation")

    flat = [s for s in segments if s.pitch_deg <= 6.0]
    if flat:
        reasons.append("Near-flat roof - low mounting cost and low shading risk")

    obstacle_classes = {o.obj_class for o in objects}
    if "existing_pv" in obstacle_classes:
        reasons.append("Roof already hosts a PV array (expansion potential)")
    if len(objects) >= 6:
        reasons.append(f"Dense rooftop equipment ({len(objects)} obstacles) - detailed survey required")

    if tier == "STRONG CANDIDATE":
        headline = "Recommended for immediate installer survey"
    elif tier == "VIABLE CANDIDATE":
        headline = "Worth a site visit - competitive cost-adjusted return"
    elif tier == "MARGINAL":
        headline = "Technically feasible but economically marginal"
    else:
        headline = "Low priority for rooftop PV"

    return {
        "tier": tier,
        "headline": headline,
        "reasons": reasons,
        "confidence": "indicative - requires on-site survey",
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
async def analyze_building(
    building: OSMBuilding,
    neighbours: list[OSMBuilding] | None = None,
    settings: Settings | None = None,
    allow_demo_fallback: bool = True,
    baseline_kwh_per_kwp: float | None = None,
    objects_override: list["inkling.DetectedObject"] | None = None,
    object_source_override: str | None = None,
) -> BuildingAnalysis:
    """Run the full pipeline for one building."""
    settings = settings or get_settings()
    neighbours = neighbours or []
    setattr(building, "_neighbours", neighbours)
    baseline = baseline_kwh_per_kwp or settings.solar_baseline_kwh_per_kwp_year

    epsg = utm_epsg_for(building.centroid_lon)
    metric_footprint = ensure_valid(to_metric(building.geom_wgs84, epsg=epsg))
    if metric_footprint is None or metric_footprint.is_empty:
        return BuildingAnalysis(
            building=building,
            status=EXCLUDED_BY_RULE,
            reason="Invalid or empty building footprint geometry",
        )
    if metric_footprint.area < settings.min_footprint_m2:
        return BuildingAnalysis(
            building=building,
            status=EXCLUDED_BY_RULE,
            reason=f"Footprint area {metric_footprint.area:.0f} m2 is below the minimum assessable roof size",
        )

    segments: list[SegmentRecord] = []
    solar_ref = None
    data_mode = "demo"
    solar_coverage_missing = False

    if settings.has_google_key:
        try:
            solar = await google_solar.find_closest(
                building.centroid_lat, building.centroid_lon, settings=settings
            )
            if not solar.has_core_fields:
                return BuildingAnalysis(
                    building=building,
                    status=UNRANKED_NO_DATA,
                    reason="Google Solar API response is missing core roof fields (roofSegments / panel config)",
                )
            segments = _segments_from_solar(building, metric_footprint, solar, settings, epsg)
            solar_ref = google_solar.building_insights_payload(solar)
            data_mode = "live"
        except google_solar.SolarNoDataError as exc:
            if not allow_demo_fallback:
                return BuildingAnalysis(
                    building=building,
                    status=UNRANKED_NO_DATA,
                    reason=f"Google Solar API 404 NOT_FOUND - no Solar data for this building ({exc})",
                    metrics={"data_mode": "live", "solar": None},
                )
            # Expected outside Google's coverage areas (e.g. Malaysia): fall back
            # to the geometric roof model with the PVGIS resource baseline.
            log.warning("Google Solar has no coverage here, using PVGIS-backed geometry: %s", exc)
            solar_coverage_missing = True
            data_mode = "hybrid"
        except google_solar.SolarQuotaError as exc:
            if not allow_demo_fallback:
                return BuildingAnalysis(
                    building=building,
                    status=ANALYSIS_FAILED,
                    reason=f"Google Solar quota error: {exc}",
                )
            log.warning("Google Solar quota hit, degrading to demo mode: %s", exc)
        except google_solar.SolarApiError as exc:
            if not allow_demo_fallback:
                return BuildingAnalysis(
                    building=building,
                    status=ANALYSIS_FAILED,
                    reason=f"Google Solar API error: {exc}",
                )
            log.warning("Google Solar error, degrading to demo mode: %s", exc)

    if not segments:
        simulated = simulator.simulate_roof(
            building, neighbours, settings=settings, epsg=epsg, baseline_kwh_per_kwp=baseline
        )
        if not simulated.segments:
            return BuildingAnalysis(
                building=building,
                status=EXCLUDED_BY_RULE,
                reason=simulated.note or "Roof geometry could not be derived",
            )
        segments = _segments_from_simulator(
            building,
            simulated,
            source_ref=(
                "pv_gis_geometry:google_solar_no_coverage"
                if solar_coverage_missing
                else "demo_simulator:pv_gis_baseline"
            ),
        )
        solar_ref = (
            {"mode": "pv_gis_geometry", "google_solar": "no_coverage"}
            if solar_coverage_missing
            else {"mode": "demo_simulator"}
        )

    roof_geom_4326 = unary_union([segment.geom_4326 for segment in segments])
    if objects_override is not None:
        objects = [
            ObjectRecord(
                obj_class=obj.obj_class,
                geom_4326=obj.geom_4326,
                height_m=_default_height(obj.obj_class),
                confidence=obj.confidence,
                source_model=object_source_override or "inkling",
                notes=obj.notes,
            )
            for obj in objects_override
        ]
        object_source = object_source_override or "inkling"
    else:
        objects, object_source = await _detect_objects(
            building, roof_geom_4326, settings, epsg=epsg
        )

    # --- usable roof geometry -------------------------------------------------
    obstacle_metrics = []
    for obj in objects:
        geom = ensure_valid(to_metric(obj.geom_4326, epsg=epsg))
        if geom is not None and not geom.is_empty:
            obstacle_metrics.append(geom)
    obstacle_union = unary_union(obstacle_metrics) if obstacle_metrics else None

    panel = settings.panel
    segment_inputs: list[SegmentInput] = []
    for segment in segments:
        roof_m = ensure_valid(to_metric(segment.geom_4326, epsg=epsg))
        if roof_m is None or roof_m.is_empty:
            continue
        usable = roof_m.buffer(-panel.edge_setback_m)
        if obstacle_union is not None and not obstacle_union.is_empty:
            usable = usable.difference(obstacle_union.buffer(panel.obstacle_clearance_m))
        usable = ensure_valid(usable)
        if usable is None or usable.is_empty:
            continue
        # Maintenance access corridors (spec 10.1 / 11.3).
        usable = ensure_valid(
            apply_walkways(
                usable,
                walkway_width_m=panel.walkway_width_m,
                spacing_m=panel.walkway_spacing_m,
            )
        )
        if usable is None or usable.is_empty:
            continue
        segment_inputs.append(
            SegmentInput(
                segment_id=segment.segment_id,
                usable_poly_m=usable,
                pitch_deg=segment.pitch_deg,
                flux_kwh_per_kwp=segment.flux_kwh_per_kwp,
            )
        )

    if not segment_inputs:
        return BuildingAnalysis(
            building=building,
            status=EXCLUDED_BY_RULE,
            reason="No usable roof area left after the 0.5 m edge setback and 0.3 m obstacle clearance",
            segments=segments,
            objects=objects,
            metrics={"data_mode": data_mode, "object_source": object_source},
        )

    total_usable_area = sum(s.usable_poly_m.area for s in segment_inputs)
    if total_usable_area < panel.area_m2:
        return BuildingAnalysis(
            building=building,
            status=EXCLUDED_BY_RULE,
            reason=f"Usable roof area {total_usable_area:.1f} m2 is smaller than one reference panel ({panel.area_m2:.2f} m2)",
            segments=segments,
            objects=objects,
            metrics={"data_mode": data_mode, "object_source": object_source},
        )

    # --- layout ---------------------------------------------------------------
    flux_map = {segment.segment_id: segment.flux_kwh_per_kwp for segment in segments}
    resolver = simulator.build_flux_resolver(
        flux_map,
        [
            simulator.SimulatedObject(
                obj_class=o.obj_class,
                geom_4326=o.geom_4326,
                height_m=o.height_m,
                confidence=o.confidence,
                notes=o.notes or "",
            )
            for o in objects
        ],
        epsg=epsg,
    )

    # Layout is pure geometry and can take seconds on large roofs; running it in
    # a worker thread keeps the event loop free so other buildings can keep
    # streaming Gemini calls (their quota slots) while this one is packed.
    layout = await asyncio.to_thread(
        build_layout, segment_inputs, panel, flux_resolver=resolver
    )
    if layout.panel_count == 0:
        return BuildingAnalysis(
            building=building,
            status=EXCLUDED_BY_RULE,
            reason="Layout optimisation produced zero feasible panels on the usable roof polygon",
            segments=segments,
            objects=objects,
            usable_area_m2=total_usable_area,
            metrics={"data_mode": data_mode, "object_source": object_source},
        )

    installed_kwp = layout.panel_count * panel.power_kwp
    annual_dc = sum(p.flux_kwh_per_kwp * panel.power_kwp for p in layout.panels)
    annual_ac = annual_dc * settings.system_efficiency_ac

    capex = compute_capex(installed_kwp, settings.cost_benchmark)
    metrics = compute_pitch_metrics(
        annual_dc,
        annual_ac,
        capex,
        tariff_rm_per_kwh=settings.electricity_tariff_rm_per_kwh,
        grid_emission_kg_per_kwh=settings.grid_emission_factor_kg_co2_per_kwh,
    )

    usable_union = unary_union([s.usable_poly_m for s in segment_inputs])
    panels_geo = panels_geojson_4326(layout.panels, lambda g: to_wgs84(g, epsg=epsg))
    recommendation = build_recommendation(
        metrics.potential_score,
        metrics.payback_years,
        installed_kwp,
        segments,
        objects,
    )

    return BuildingAnalysis(
        building=building,
        status=RANKED,
        reason=None,
        segments=segments,
        objects=objects,
        usable_geom_4326=to_wgs84(usable_union, epsg=epsg),
        usable_area_m2=float(usable_union.area),
        panels_geojson=panels_geo,
        panel_count=layout.panel_count,
        installed_kwp=installed_kwp,
        panel_area_m2=layout.panel_area_m2,
        annual_energy_dc_kwh=annual_dc,
        annual_energy_ac_kwh=annual_ac,
        capex_base_rm=capex.base_rm,
        capex_low_rm=capex.low_rm,
        capex_high_rm=capex.high_rm,
        potential_score=metrics.potential_score,
        annual_savings_rm=metrics.annual_savings_rm,
        payback_years=metrics.payback_years,
        co2_offset_tokens=metrics.co2_offset_tons,
        metrics={
            "data_mode": data_mode,
            "object_source": object_source,
            "panel_profile": {
                "version": panel.version,
                "width_m": panel.width_m,
                "height_m": panel.height_m,
                "rated_power_wp": panel.rated_power_wp,
                "gap_m": panel.gap_m,
                "edge_setback_m": panel.edge_setback_m,
                "obstacle_clearance_m": panel.obstacle_clearance_m,
                "walkway_width_m": panel.walkway_width_m,
                "walkway_spacing_m": panel.walkway_spacing_m,
            },
            "orientation_used": layout.orientations_used,
            "cost": {
                "base_rm": capex.base_rm,
                "low_rm": capex.low_rm,
                "high_rm": capex.high_rm,
                "tier_label": capex.tier_label,
                "rate_rm_per_kwp": capex.rate_rm_per_kwp,
                "fixed_base_rm": capex.fixed_base_rm,
                "benchmark_version": capex.benchmark_version,
                "included_scope": capex.included_scope,
                "source_url": capex.source_url,
            },
            "pitch_metrics": {
                "annual_energy_dc_kwh": metrics.annual_energy_dc_kwh,
                "annual_energy_ac_kwh": metrics.annual_energy_ac_kwh,
                "annual_savings_rm": metrics.annual_savings_rm,
                "payback_years": metrics.payback_years,
                "co2_offset_tons": metrics.co2_offset_tons,
                "potential_score": metrics.potential_score,
                "potential_score_pessimistic": metrics.potential_score_pessimistic,
                "potential_score_optimistic": metrics.potential_score_optimistic,
            },
            "solar_reference": solar_ref,
            "usable_area_m2": round(float(usable_union.area), 2),
            "panel_area_m2": round(layout.panel_area_m2, 2),
            "recommendation": recommendation,
            "solar_baseline_kwh_per_kwp": round(baseline, 1),
            "crs": f"EPSG:{epsg}",
        },
    )

"""Analysis run orchestration: ingestion, concurrency control, ranking.

City batch jobs use FastAPI BackgroundTasks plus an asyncio.Semaphore(5) to
stay inside the Google Solar / Inkling quotas without any Celery / Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import SessionLocal
from .geo import from_geojson_str, to_geojson_str, to_metric
from .models import (
    AnalysisRun,
    Building,
    BuildingScore,
    PanelLayout,
    RoofObject,
    RoofSegment,
    UsableRegion,
    utcnow,
)
from .pipeline import (
    ANALYSIS_FAILED,
    EXCLUDED_BY_RULE,
    RANKED,
    UNRANKED_NO_DATA,
    BuildingAnalysis,
    analyze_building,
)
from .providers import overpass, presets, pvgis

log = logging.getLogger("solarsync.runs")

STATUS_QUEUED = "QUEUED"
STATUS_INGESTING = "INGESTING_BUILDINGS"
STATUS_SOLAR = "SOLAR_DATA"
STATUS_VISION = "ROOF_VISION"
STATUS_GEOMETRY = "GEOMETRY"
STATUS_LAYOUT = "LAYOUT_OPTIMIZATION"
STATUS_SCORING = "SCORING"
STATUS_COMPLETED = "COMPLETED"
STATUS_COMPLETED_WITH_EXCLUSIONS = "COMPLETED_WITH_EXCLUSIONS"
STATUS_FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def persist_analysis(session: Session, run: AnalysisRun, analysis: BuildingAnalysis) -> Building:
    source = analysis.building.source
    feature_id = analysis.building.source_feature_id

    building = (
        session.query(Building)
        .filter(Building.source == source, Building.source_feature_id == feature_id)
        .first()
    )
    if building is None:
        building = Building(
            source=source,
            source_feature_id=feature_id,
            run_id=run.run_id,
        )
        session.add(building)
    building.run_id = run.run_id
    building.centroid_lat = analysis.building.centroid_lat
    building.centroid_lon = analysis.building.centroid_lon
    building.footprint_geom = to_geojson_str(analysis.building.geom_wgs84)
    building.footprint_area_m2 = round(analysis.building.footprint_area_m2, 2)
    building.height_m = analysis.building.height_m
    building.num_levels = analysis.building.levels
    building.building_type = analysis.building.building_type
    building.name = analysis.building.name
    building.roof_area_m2 = round(sum(segment.area_m2 for segment in analysis.segments), 2)
    building.data_status = analysis.status
    building.exclusion_reason = analysis.reason
    building.data_source_ref = analysis.metrics.get("solar_reference", {}).get("mode") if isinstance(
        analysis.metrics.get("solar_reference"), dict
    ) else None
    session.flush()

    # Replace child rows for a clean re-analysis.
    session.query(RoofSegment).filter(RoofSegment.building_id == building.building_id).delete()
    session.query(RoofObject).filter(RoofObject.building_id == building.building_id).delete()
    session.query(UsableRegion).filter(UsableRegion.building_id == building.building_id).delete()
    session.query(PanelLayout).filter(PanelLayout.building_id == building.building_id).delete()
    session.query(BuildingScore).filter(BuildingScore.building_id == building.building_id).delete()
    session.flush()

    for segment in analysis.segments:
        session.add(
            RoofSegment(
                segment_id=f"{building.building_id}_{segment.segment_id}",
                building_id=building.building_id,
                geom=to_geojson_str(segment.geom_4326),
                area_m2=round(segment.area_m2, 2),
                pitch_deg=segment.pitch_deg,
                azimuth_deg=segment.azimuth_deg,
                plane_height_m=segment.plane_height_m,
                flux_kwh_per_kwp=round(segment.flux_kwh_per_kwp, 2),
                orientation_factor=round(segment.orientation_factor, 4),
                shade_factor=round(segment.shade_factor, 4),
                solar_data_ref=segment.solar_data_ref,
            )
        )

    for obj in analysis.objects:
        metric_obj = to_metric(obj.geom_4326)
        session.add(
            RoofObject(
                building_id=building.building_id,
                obj_class=obj.obj_class,
                polygon_geom=to_geojson_str(obj.geom_4326),
                area_m2=round(float(metric_obj.area), 2),
                height_m=obj.height_m,
                confidence=obj.confidence,
                source_model=obj.source_model,
                review_status="auto",
                notes=obj.notes,
            )
        )

    if analysis.usable_geom_4326 is not None:
        session.add(
            UsableRegion(
                building_id=building.building_id,
                polygon_geom=to_geojson_str(analysis.usable_geom_4326),
                area_m2=round(analysis.usable_area_m2, 2),
                exclusion_inputs_version="1.0.0",
            )
        )

    if analysis.panel_count:
        session.add(
            PanelLayout(
                building_id=building.building_id,
                panel_profile_version="ref-panel-2026.1",
                panel_count=analysis.panel_count,
                installed_kwp=round(analysis.installed_kwp, 3),
                total_panel_area_m2=round(analysis.panel_area_m2, 2),
                usable_area_m2=round(analysis.usable_area_m2, 2),
                layout_geom=json.dumps(
                    {"type": "FeatureCollection", "features": analysis.panels_geojson},
                    separators=(",", ":"),
                ),
                annual_energy_dc_kwh=round(analysis.annual_energy_dc_kwh, 1),
                orientations=",".join(analysis.metrics.get("orientation_used", [])),
            )
        )

    session.add(
        BuildingScore(
            run_id=run.run_id,
            building_id=building.building_id,
            status=analysis.status,
            annual_energy_dc_kwh=round(analysis.annual_energy_dc_kwh, 1),
            annual_energy_ac_kwh=round(analysis.annual_energy_ac_kwh, 1),
            installed_kwp=round(analysis.installed_kwp, 3),
            panel_count=analysis.panel_count,
            capex_base_rm=round(analysis.capex_base_rm, 2),
            capex_low_rm=round(analysis.capex_low_rm, 2),
            capex_high_rm=round(analysis.capex_high_rm, 2),
            score_kwh_per_year_per_rm=analysis.potential_score,
            annual_savings_rm=analysis.annual_savings_rm,
            payback_years=analysis.payback_years,
            co2_offset_tons=analysis.co2_offset_tokens,
            exclusion_reason=analysis.reason,
            metrics=analysis.metrics,
        )
    )
    session.flush()
    return building


def _assign_ranks(session: Session, run_id: str) -> None:
    """Rank only the RANKED buildings; excluded / unranked ones never get a rank."""
    scores = (
        session.query(BuildingScore)
        .filter(BuildingScore.run_id == run_id, BuildingScore.status == RANKED)
        .all()
    )
    by_energy = sorted(scores, key=lambda s: (-s.annual_energy_dc_kwh, s.building_id))
    for index, score in enumerate(by_energy, start=1):
        score.rank_energy = index
    by_score = sorted(scores, key=lambda s: (-s.score_kwh_per_year_per_rm, s.building_id))
    for index, score in enumerate(by_score, start=1):
        score.rank_cost_adjusted = index


def _update_run_aggregates(session: Session, run: AnalysisRun) -> None:
    scores = session.query(BuildingScore).filter(BuildingScore.run_id == run.run_id).all()
    run.ranked_count = sum(1 for s in scores if s.status == RANKED)
    run.excluded_count = sum(1 for s in scores if s.status == EXCLUDED_BY_RULE)
    run.unranked_no_data_count = sum(1 for s in scores if s.status == UNRANKED_NO_DATA)
    run.failed_count = sum(1 for s in scores if s.status == ANALYSIS_FAILED)
    run.processed_count = len(scores)
    run.total_annual_energy_dc_kwh = round(sum(s.annual_energy_dc_kwh for s in scores), 1)
    run.total_installed_kwp = round(sum(s.installed_kwp for s in scores), 3)
    run.total_capex_base_rm = round(sum(s.capex_base_rm for s in scores), 2)


# ---------------------------------------------------------------------------
# Run creation
# ---------------------------------------------------------------------------
def create_run(
    session: Session,
    scope_type: str,
    *,
    preset_id: str | None = None,
    label: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    settings: Settings | None = None,
) -> AnalysisRun:
    settings = settings or get_settings()
    preset = presets.get_preset(preset_id) if preset_id else None
    if preset is not None:
        lat, lon = preset.lat, preset.lon
        bbox = presets.bbox_for(preset)
        label = label or preset.label

    run = AnalysisRun(
        scope_type=scope_type.upper(),
        scope_label=label or (preset.label if preset else "Custom area"),
        preset_id=preset.preset_id if preset else None,
        center_lat=lat,
        center_lon=lon,
        bbox=",".join(f"{v:.6f}" for v in bbox) if bbox else None,
        status=STATUS_QUEUED,
        data_mode="live" if settings.has_google_key else "demo",
        algorithm_version=settings.algorithm_version,
        model_version=settings.model_version,
        scoring_version=settings.scoring_version,
        source_policy_version=settings.source_policy_version,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _parse_bbox(run: AnalysisRun) -> tuple[float, float, float, float]:
    if run.bbox:
        parts = [float(p) for p in run.bbox.split(",")]
        if len(parts) == 4:
            return (parts[0], parts[1], parts[2], parts[3])
    if run.center_lat is not None and run.center_lon is not None:
        half = 0.0045
        return (
            run.center_lat - half,
            run.center_lon - half,
            run.center_lat + half,
            run.center_lon + half,
        )
    raise ValueError("Run has neither bbox nor centre coordinates")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
async def execute_run(run_id: str, settings: Settings | None = None) -> None:
    """Background worker for one analysis run."""
    settings = settings or get_settings()
    semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_buildings))

    with SessionLocal() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            log.error("Run %s not found", run_id)
            return
        run.status = STATUS_INGESTING
        run.message = "Fetching building footprints from OpenStreetMap Overpass"
        run.started_at = utcnow()
        session.commit()
        scope_type = run.scope_type
        bbox = _parse_bbox(run)
        center_lat = run.center_lat
        center_lon = run.center_lon

    try:
        ingest_stats: dict = {}
        candidates = await overpass.fetch_buildings(
            bbox, settings=settings, max_buildings=settings.max_buildings_per_run, stats=ingest_stats
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Overpass ingestion failed for run %s", run_id)
        with SessionLocal() as session:
            run = session.get(AnalysisRun, run_id)
            if run:
                run.status = STATUS_FAILED
                run.error = f"Building ingestion failed: {exc}"
                run.completed_at = utcnow()
                session.commit()
        return

    total_found = int(ingest_stats.get("total_found", len(candidates)))
    if not candidates:
        with SessionLocal() as session:
            run = session.get(AnalysisRun, run_id)
            if run:
                run.status = STATUS_FAILED
                run.error = (
                    "No building footprints could be retrieved for this area "
                    "(Overpass mirrors unavailable and no cached snapshot)."
                    if scope_type != "BUILDING"
                    else "No OpenStreetMap building was found within ~1 km of the requested point. "
                    "Zoom out on the map or use the area mode instead."
                )
                run.completed_at = utcnow()
                session.commit()
        return

    with SessionLocal() as session:
        run = session.get(AnalysisRun, run_id)
        if run:
            run.candidate_count = len(candidates)
            run.processed_count = 0
            run.status = STATUS_SOLAR
            run.message = f"Analysing {len(candidates)} candidate buildings"
            session.commit()
    if ingest_stats.get("residential_skipped"):
        log.info(
            "Screening policy skipped %s residential roofs before analysis",
            ingest_stats["residential_skipped"],
        )

    if scope_type == "BUILDING" and center_lat is not None and center_lon is not None:
        candidates = _select_single_building(candidates, center_lat, center_lon)
        with SessionLocal() as session:
            run = session.get(AnalysisRun, run_id)
            if run:
                run.candidate_count = len(candidates)
                run.message = f"Analysing the matched building near {center_lat:.5f}, {center_lon:.5f}"
                session.commit()

    # Independent radiation validation for the analysed area (PVGIS, no key).
    # Optional: a failure never affects the analysis result.
    reference = None
    if center_lat is not None and center_lon is not None:
        reference = await pvgis.fetch_annual_yield(
            center_lat, center_lon, settings=settings, timeout=12.0
        )

    baseline = settings.solar_baseline_kwh_per_kwp_year
    if reference and settings.pvgis_baseline_mode.lower() == "calibrate":
        # Calibrate the DC baseline so the model's AC output matches the
        # independent PVGIS reference for this location.
        baseline = pvgis.dc_baseline_from_pvgis(reference, settings.system_efficiency_ac)
        log.info("Using PVGIS-calibrated baseline: %.0f kWh/kWp/yr DC", baseline)

    with SessionLocal() as session:
        run = session.get(AnalysisRun, run_id)
        if run:
            run.solar_baseline_kwh_per_kwp = round(baseline, 1)
            if reference:
                our_ac = baseline * settings.system_efficiency_ac
                delta = ((our_ac - reference["E_y"]) / reference["E_y"]) * 100.0
                run.pvgis_reference_kwh_per_kwp = round(reference["E_y"], 1)
                run.pvgis_delta_pct = round(delta, 1)
            session.commit()
    if reference:
        log.info(
            "PVGIS reference: %.0f kWh/kWp/yr AC (SolarSync model %.0f, delta %.1f%%)",
            reference["E_y"],
            baseline * settings.system_efficiency_ac,
            ((baseline * settings.system_efficiency_ac - reference["E_y"]) / reference["E_y"]) * 100.0,
        )

    results: list[tuple[BuildingAnalysis | None, str | None]] = []
    completed = 0

    area_objects: dict[str, list] | None = None
    if (
        scope_type == "AREA"
        and settings.vision_tile_mode
        and len(candidates) > 1
        and settings.has_inkling_key
    ):
        from . import vision

        with SessionLocal() as session:
            run = session.get(AnalysisRun, run_id)
            if run:
                run.message = "Reading rooftop equipment from satellite imagery"
                session.commit()
        area_detection = await vision.detect_area(candidates, settings=settings)
        log.info("Area vision pass: %s", area_detection.as_dict())
        if area_detection.calls > 0:
            # Every building is scored from this single pass, so the ranking and
            # the detail card always agree.
            area_objects = area_detection.objects_by_building
        else:
            log.warning("Area vision pass produced no usable tile; falling back per building")

    async def worker(building) -> None:
        nonlocal completed
        async with semaphore:
            try:
                analysis = await analyze_building(
                    building,
                    candidates,
                    settings=settings,
                    baseline_kwh_per_kwp=baseline,
                    objects_override=(
                        None
                        if area_objects is None
                        else area_objects.get(building.source_feature_id, [])
                    ),
                    object_source_override="inkling" if area_objects is not None else None,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Analysis failed for %s", building.source_feature_id)
                analysis = BuildingAnalysis(
                    building=building,
                    status=ANALYSIS_FAILED,
                    reason=f"Unexpected analysis error: {exc}",
                )
            with SessionLocal() as session:
                run = session.get(AnalysisRun, run_id)
                if run is not None:
                    persist_analysis(session, run, analysis)
                    completed += 1
                    run.processed_count = completed
                    run.progress = round(completed / max(1, len(candidates)) * 100.0, 1)
                    run.message = f"Analysed {completed}/{len(candidates)} buildings"
                    session.commit()
            results.append((analysis, None))

    await asyncio.gather(*(worker(b) for b in candidates))

    with SessionLocal() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.status = STATUS_SCORING
        run.message = "Computing Malaysian CAPEX and ranking"
        session.commit()

        _assign_ranks(session, run_id)
        _update_run_aggregates(session, run)
        run.progress = 100.0
        run.status = (
            STATUS_COMPLETED
            if run.excluded_count == 0 and run.unranked_no_data_count == 0 and run.failed_count == 0
            else STATUS_COMPLETED_WITH_EXCLUSIONS
        )
        run.message = (
            f"Ranked {run.ranked_count} of {run.candidate_count} buildings "
            f"({run.excluded_count} excluded by rule, {run.unranked_no_data_count} unranked - no data, "
            f"{run.failed_count} failed)"
        )
        run.completed_at = utcnow()
        session.commit()


def _select_single_building(
    candidates: list[overpass.OSMBuilding], lat: float, lon: float
) -> list[overpass.OSMBuilding]:
    """Pick the OSM building whose footprint contains / is closest to the point."""
    if not candidates:
        return candidates
    import math

    def distance(b: overpass.OSMBuilding) -> float:
        dlat = b.centroid_lat - lat
        dlon = b.centroid_lon - lon
        return math.hypot(dlat * 111320.0, dlon * 111320.0 * math.cos(math.radians(lat)))

    containing = [b for b in candidates if b.geom_wgs84.contains(_point(lat, lon))]
    if containing:
        containing.sort(key=lambda b: b.footprint_area_m2)
        return [containing[0]]
    candidates.sort(key=distance)
    return [candidates[0]]


def _point(lat: float, lon: float):
    from shapely.geometry import Point

    return Point(lon, lat)


async def recompute_building(building_id: str, settings: Settings | None = None) -> dict:
    """Re-run the pipeline for one building and refresh its score."""
    settings = settings or get_settings()
    with SessionLocal() as session:
        building = session.get(Building, building_id)
        if building is None:
            return {"status": "not_found", "building_id": building_id}
        run = session.get(AnalysisRun, building.run_id)
        source_feature_id = building.source_feature_id
        centroid = (building.centroid_lat, building.centroid_lon)

    bbox = (
        centroid[0] - 0.004,
        centroid[1] - 0.004,
        centroid[0] + 0.004,
        centroid[1] + 0.004,
    )
    candidates = await overpass.fetch_buildings(
        bbox, settings=settings, max_buildings=80, exclude_residential=False
    )
    target = next(
        (c for c in candidates if c.source_feature_id == source_feature_id),
        None,
    )
    if target is None:
        return {"status": "not_found", "building_id": building_id}

    analysis = await analyze_building(target, candidates, settings=settings)
    with SessionLocal() as session:
        run = session.get(AnalysisRun, building.run_id)
        if run is not None:
            persist_analysis(session, run, analysis)
            _assign_ranks(session, run.run_id)
            _update_run_aggregates(session, run)
            session.commit()
    return {
        "status": analysis.status,
        "building_id": building_id,
        "reason": analysis.reason,
        "panel_count": analysis.panel_count,
        "potential_score": analysis.potential_score,
    }

"""FastAPI routes for SolarSync (spec 14.1)."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .geo import from_geojson_str
from .models import AnalysisRun, Building, BuildingScore, CostBenchmarkRow, PanelLayout, RoofObject, RoofSegment, UsableRegion
from .providers import geocode as geocode_provider
from .providers import presets
from .runs import create_run, execute_run, recompute_building
from .schemas import (
    AnalysisCreateRequest,
    BuildingAnalysisResponse,
    HealthResponse,
    PresetOut,
    RankingsResponse,
    RankingEntry,
    RunCreateResponse,
    RunSummary,
)

log = logging.getLogger("solarsync.api")

router = APIRouter(prefix="/api/v1", tags=["solarsync"])


def _run_summary(run: AnalysisRun) -> RunSummary:
    return RunSummary(
        run_id=run.run_id,
        scope_type=run.scope_type,
        scope_label=run.scope_label,
        preset_id=run.preset_id,
        status=run.status,
        progress=run.progress,
        message=run.message,
        candidate_count=run.candidate_count,
        processed_count=run.processed_count,
        ranked_count=run.ranked_count,
        excluded_count=run.excluded_count,
        unranked_no_data_count=run.unranked_no_data_count,
        failed_count=run.failed_count,
        total_annual_energy_dc_kwh=run.total_annual_energy_dc_kwh,
        total_installed_kwp=run.total_installed_kwp,
        total_capex_base_rm=run.total_capex_base_rm,
        data_mode=run.data_mode,
        solar_baseline_kwh_per_kwp=run.solar_baseline_kwh_per_kwp,
        pvgis_reference_kwh_per_kwp=run.pvgis_reference_kwh_per_kwp,
        pvgis_delta_pct=run.pvgis_delta_pct,
        center_lat=run.center_lat,
        center_lon=run.center_lon,
        bbox=run.bbox,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error=run.error,
        algorithm_version=run.algorithm_version,
        model_version=run.model_version,
        scoring_version=run.scoring_version,
        source_policy_version=run.source_policy_version,
    )


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    from .db import DATABASE_URL

    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version="1.0.0",
        data_mode="live" if settings.has_google_key else "demo",
        google_key_configured=settings.has_google_key,
        inkling_configured=settings.has_inkling_key,
        database=DATABASE_URL,
        algorithm_version=settings.algorithm_version,
        scoring_version=settings.scoring_version,
    )


@router.get("/presets", response_model=list[PresetOut])
def list_presets() -> list[PresetOut]:
    return [
        PresetOut(
            preset_id=p.preset_id,
            label=p.label,
            region=p.region,
            state=p.state,
            lat=p.lat,
            lon=p.lon,
            half_size_deg=p.half_size_deg,
            description=p.description,
        )
        for p in presets.PRESETS
    ]


@router.get("/geocode")
async def geocode(
    q: str = Query(..., min_length=2, max_length=120),
    limit: int = Query(default=6, ge=1, le=10),
    country_codes: str = Query(default="my", description="ISO country code filter, 'my' = Malaysia"),
) -> dict[str, Any]:
    """Free-form place search (Nominatim) so any area can be analysed."""
    results = await geocode_provider.search(
        q, limit=limit, settings=get_settings(), country_codes=country_codes
    )
    return {
        "query": q,
        "results": [
            {
                "label": r.label,
                "short_label": (r.label.split(",")[0] if r.label else ""),
                "lat": r.lat,
                "lon": r.lon,
                "osm_type": r.osm_type,
                "osm_id": r.osm_id,
                "category": r.category,
                "bbox": list(r.bbox) if r.bbox else None,
            }
            for r in results
        ],
    }


@router.get("/reverse-geocode")
async def reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    result = await geocode_provider.reverse(lat, lon, settings=get_settings())
    if result is None:
        return {"label": None, "lat": lat, "lon": lon}
    return {
        "label": result.label,
        "short_label": (result.label.split(",")[0] if result.label else ""),
        "lat": result.lat,
        "lon": result.lon,
        "category": result.category,
    }


@router.get("/cost-benchmarks")
def list_cost_benchmarks(session: Session = Depends(get_session)) -> dict[str, Any]:
    settings = get_settings()
    rows = session.query(CostBenchmarkRow).order_by(CostBenchmarkRow.capacity_min_kwp).all()
    return {
        "version": settings.cost_benchmark.version,
        "effective_date": settings.cost_benchmark.effective_date,
        "fixed_base_rm": settings.cost_benchmark.fixed_base_rm,
        "formula": "C_installed = 3500 + P_installed_kwp * R_market",
        "included_scope": settings.cost_benchmark.included_scope,
        "source_url": settings.cost_benchmark.source_url,
        "tiers": [
            {
                "capacity_min_kwp": row.capacity_min_kwp,
                "capacity_max_kwp": row.capacity_max_kwp,
                "low_rm_per_kwp": row.low_rm_per_kwp,
                "base_rm_per_kwp": row.base_rm_per_kwp,
                "high_rm_per_kwp": row.high_rm_per_kwp,
                "confidence_level": row.confidence_level,
                "sample_count": row.sample_count,
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
@router.post("/analyses", response_model=RunCreateResponse, status_code=202)
async def create_analysis(
    payload: AnalysisCreateRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> RunCreateResponse:
    bbox = payload.resolved_bbox()
    if payload.preset_id is None and bbox is None:
        raise HTTPException(
            status_code=422,
            detail="Provide preset_id, lat/lon, or bbox so the analysis area can be resolved",
        )
    if payload.preset_id and presets.get_preset(payload.preset_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown preset '{payload.preset_id}'")

    if payload.preset_id is None and payload.lat is not None and payload.lon is not None:
        # Custom area: name it so the leaderboard and run history stay readable.
        try:
            reverse = await geocode_provider.reverse(payload.lat, payload.lon, settings=get_settings())
        except Exception:  # noqa: BLE001
            reverse = None
        if reverse and reverse.label:
            payload = payload.model_copy(update={"label": payload.label or reverse.short_label or reverse.label})

    run = create_run(
        session,
        payload.scope_type,
        preset_id=payload.preset_id,
        label=payload.label,
        lat=payload.lat,
        lon=payload.lon,
        bbox=bbox,
    )
    background_tasks.add_task(execute_run, run.run_id)
    return RunCreateResponse(
        run_id=run.run_id,
        status=run.status,
        scope_type=run.scope_type,
        scope_label=run.scope_label,
        message="Analysis queued - buildings are ingested from OpenStreetMap Overpass in the background",
    )


@router.get("/analyses", response_model=list[RunSummary])
def list_analyses(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[RunSummary]:
    runs = session.query(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(limit).all()
    return [_run_summary(run) for run in runs]


@router.get("/analyses/{run_id}", response_model=RunSummary)
def get_analysis(run_id: str, session: Session = Depends(get_session)) -> RunSummary:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return _run_summary(run)


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------
@router.get("/areas/{run_id}/rankings", response_model=RankingsResponse)
def get_rankings(
    run_id: str,
    rank_mode: str = Query(default="cost_adjusted", pattern="^(energy|cost_adjusted)$"),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> RankingsResponse:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")

    order_field = (
        BuildingScore.annual_energy_dc_kwh.desc()
        if rank_mode == "energy"
        else BuildingScore.score_kwh_per_year_per_rm.desc()
    )
    rows = (
        session.query(BuildingScore, Building)
        .join(Building, Building.building_id == BuildingScore.building_id)
        .filter(BuildingScore.run_id == run_id, BuildingScore.status == "RANKED")
        .order_by(order_field, BuildingScore.building_id)
        .limit(limit)
        .all()
    )

    entries = []
    for index, (score, building) in enumerate(rows, start=1):
        metrics = score.metrics if isinstance(score.metrics, dict) else {}
        entries.append(
            RankingEntry(
                rank=index,
                building_id=building.building_id,
                source_feature_id=building.source_feature_id,
                name=building.name,
                building_type=building.building_type,
                centroid_lat=building.centroid_lat or 0.0,
                centroid_lon=building.centroid_lon or 0.0,
                status=score.status,
                panel_count=score.panel_count,
                installed_kwp=score.installed_kwp,
                annual_energy_dc_kwh=score.annual_energy_dc_kwh,
                annual_energy_ac_kwh=score.annual_energy_ac_kwh,
                capex_base_rm=score.capex_base_rm,
                capex_low_rm=score.capex_low_rm,
                capex_high_rm=score.capex_high_rm,
                score_kwh_per_year_per_rm=score.score_kwh_per_year_per_rm,
                annual_savings_rm=score.annual_savings_rm,
                payback_years=score.payback_years,
                co2_offset_tons=score.co2_offset_tons,
                footprint_area_m2=building.footprint_area_m2,
                height_m=building.height_m,
                footprint=json.loads(building.footprint_geom) if building.footprint_geom else None,
                recommendation=metrics.get("recommendation"),
            )
        )

    return RankingsResponse(
        run_id=run_id,
        rank_mode=rank_mode,  # type: ignore[arg-type]
        scope_label=run.scope_label,
        generated_at=run.completed_at.isoformat() if run.completed_at else None,
        evaluated_count=run.ranked_count,
        candidate_count=run.candidate_count,
        excluded_count=run.excluded_count,
        unranked_no_data_count=run.unranked_no_data_count,
        failed_count=run.failed_count,
        data_mode=run.data_mode,
        algorithm_version=run.algorithm_version,
        scoring_version=run.scoring_version,
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Single building
# ---------------------------------------------------------------------------
def _feature_collection(features: list[dict]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


@router.get("/buildings/{building_id}/analysis", response_model=BuildingAnalysisResponse)
def get_building_analysis(
    building_id: str,
    run_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> BuildingAnalysisResponse:
    building = session.get(Building, building_id)
    if building is None:
        raise HTTPException(status_code=404, detail="Building not found")

    # A building is re-analysed in every run, so the score of the *requested*
    # run is looked up by (run_id, building_id) rather than by the building's
    # most recent run.
    score_query = session.query(BuildingScore).filter(BuildingScore.building_id == building_id)
    if run_id:
        score_query = score_query.filter(BuildingScore.run_id == run_id)
    score = score_query.order_by(BuildingScore.score_id.desc()).first()
    segments = session.query(RoofSegment).filter(RoofSegment.building_id == building_id).all()
    objects = session.query(RoofObject).filter(RoofObject.building_id == building_id).all()
    regions = session.query(UsableRegion).filter(UsableRegion.building_id == building_id).all()
    layout = (
        session.query(PanelLayout).filter(PanelLayout.building_id == building_id).first()
    )

    segment_features = []
    for segment in segments:
        geom = from_geojson_str(segment.geom)
        if geom is None:
            continue
        segment_features.append(
            {
                "type": "Feature",
                "geometry": json.loads(segment.geom),
                "properties": {
                    "segment_id": segment.segment_id,
                    "area_m2": segment.area_m2,
                    "pitch_deg": segment.pitch_deg,
                    "azimuth_deg": segment.azimuth_deg,
                    "plane_height_m": segment.plane_height_m,
                    "flux_kwh_per_kwp": segment.flux_kwh_per_kwp,
                    "orientation_factor": segment.orientation_factor,
                    "shade_factor": segment.shade_factor,
                    "source": segment.solar_data_ref,
                },
            }
        )

    object_features = []
    for obj in objects:
        if not obj.polygon_geom:
            continue
        object_features.append(
            {
                "type": "Feature",
                "geometry": json.loads(obj.polygon_geom),
                "properties": {
                    "class": obj.obj_class,
                    "height_m": obj.height_m,
                    "confidence": obj.confidence,
                    "area_m2": obj.area_m2,
                    "source_model": obj.source_model,
                    "notes": obj.notes,
                },
            }
        )

    region_features = []
    for region in regions:
        if not region.polygon_geom:
            continue
        region_features.append(
            {
                "type": "Feature",
                "geometry": json.loads(region.polygon_geom),
                "properties": {"region_id": region.region_id, "area_m2": region.area_m2},
            }
        )

    layout_geo = None
    if layout and layout.layout_geom:
        try:
            layout_geo = json.loads(layout.layout_geom)
        except json.JSONDecodeError:
            layout_geo = None

    metrics = score.metrics if score else None
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except json.JSONDecodeError:
            metrics = None

    return BuildingAnalysisResponse(
        building_id=building.building_id,
        run_id=building.run_id,
        status=building.data_status,
        exclusion_reason=building.exclusion_reason,
        source=building.source,
        source_feature_id=building.source_feature_id,
        name=building.name,
        building_type=building.building_type,
        centroid_lat=building.centroid_lat or 0.0,
        centroid_lon=building.centroid_lon or 0.0,
        footprint_area_m2=building.footprint_area_m2,
        roof_area_m2=building.roof_area_m2,
        height_m=building.height_m,
        num_levels=building.num_levels,
        panel_count=score.panel_count if score else 0,
        installed_kwp=score.installed_kwp if score else 0.0,
        panel_area_m2=float(metrics.get("panel_area_m2", 0.0)) if isinstance(metrics, dict) else 0.0,
        usable_area_m2=float(metrics.get("usable_area_m2", 0.0)) if isinstance(metrics, dict) else 0.0,
        annual_energy_dc_kwh=score.annual_energy_dc_kwh if score else 0.0,
        annual_energy_ac_kwh=score.annual_energy_ac_kwh if score else 0.0,
        capex_base_rm=score.capex_base_rm if score else 0.0,
        capex_low_rm=score.capex_low_rm if score else 0.0,
        capex_high_rm=score.capex_high_rm if score else 0.0,
        potential_score=score.score_kwh_per_year_per_rm if score else 0.0,
        annual_savings_rm=score.annual_savings_rm if score else 0.0,
        payback_years=score.payback_years if score else 0.0,
        co2_offset_tons=score.co2_offset_tons if score else 0.0,
        rank_energy=score.rank_energy if score else None,
        rank_cost_adjusted=score.rank_cost_adjusted if score else None,
        footprint=json.loads(building.footprint_geom) if building.footprint_geom else None,
        roof_segments=segment_features,
        roof_objects=object_features,
        usable_regions=region_features,
        panel_layout=layout_geo,
        metrics=metrics,
        recommendation=(metrics.get("recommendation") if isinstance(metrics, dict) else None),
    )


@router.post("/buildings/{building_id}/recompute")
async def post_recompute(building_id: str) -> dict[str, Any]:
    result = await recompute_building(building_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Building not found")
    return result

"""Pydantic request / response contracts for the SolarSync API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AnalysisCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scope_type: Literal["BUILDING", "AREA"] = "AREA"
    preset_id: str | None = None
    label: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_deg: float | None = Field(default=None, gt=0, le=0.05)
    bbox: list[float] | None = Field(default=None, description="[south, west, north, east]")

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, value):
        if value is None:
            return value
        if len(value) != 4:
            raise ValueError("bbox must be [south, west, north, east]")
        south, west, north, east = value
        if not (south < north and west < east):
            raise ValueError("bbox must be ordered [south, west, north, east]")
        return value

    def resolved_bbox(self) -> tuple[float, float, float, float] | None:
        if self.bbox:
            return tuple(self.bbox)  # type: ignore[return-value]
        if self.lat is not None and self.lon is not None:
            half = self.radius_deg or 0.0045
            return (self.lat - half, self.lon - half, self.lat + half, self.lon + half)
        return None


class RunSummary(BaseModel):
    run_id: str
    scope_type: str
    scope_label: str
    preset_id: str | None = None
    status: str
    progress: float
    message: str
    candidate_count: int
    processed_count: int
    ranked_count: int
    excluded_count: int
    unranked_no_data_count: int
    failed_count: int
    total_annual_energy_dc_kwh: float
    total_installed_kwp: float
    total_capex_base_rm: float
    data_mode: str
    solar_baseline_kwh_per_kwp: float = 1450.0
    pvgis_reference_kwh_per_kwp: float | None = None
    pvgis_delta_pct: float | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    bbox: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    algorithm_version: str
    model_version: str
    scoring_version: str
    source_policy_version: str


class RunCreateResponse(BaseModel):
    run_id: str
    status: str
    scope_type: str
    scope_label: str
    message: str


class RankingEntry(BaseModel):
    rank: int
    building_id: str
    source_feature_id: str
    name: str | None = None
    building_type: str
    centroid_lat: float
    centroid_lon: float
    status: str
    panel_count: int
    installed_kwp: float
    annual_energy_dc_kwh: float
    annual_energy_ac_kwh: float
    capex_base_rm: float
    capex_low_rm: float
    capex_high_rm: float
    score_kwh_per_year_per_rm: float
    annual_savings_rm: float
    payback_years: float
    co2_offset_tons: float
    footprint_area_m2: float
    height_m: float | None = None
    footprint: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None


class RankingsResponse(BaseModel):
    run_id: str
    rank_mode: Literal["energy", "cost_adjusted"]
    scope_label: str
    generated_at: str | None = None
    evaluated_count: int
    candidate_count: int
    excluded_count: int
    unranked_no_data_count: int
    failed_count: int
    data_mode: str
    algorithm_version: str
    scoring_version: str
    entries: list[RankingEntry]


class BuildingAnalysisResponse(BaseModel):
    building_id: str
    run_id: str
    status: str
    exclusion_reason: str | None = None
    source: str
    source_feature_id: str
    name: str | None = None
    building_type: str
    centroid_lat: float
    centroid_lon: float
    footprint_area_m2: float
    roof_area_m2: float
    height_m: float | None = None
    num_levels: int | None = None
    panel_count: int
    installed_kwp: float
    panel_area_m2: float
    usable_area_m2: float
    annual_energy_dc_kwh: float
    annual_energy_ac_kwh: float
    capex_base_rm: float
    capex_low_rm: float
    capex_high_rm: float
    potential_score: float
    annual_savings_rm: float
    payback_years: float
    co2_offset_tons: float
    rank_energy: int | None = None
    rank_cost_adjusted: int | None = None
    footprint: dict[str, Any] | None = None
    roof_segments: list[dict[str, Any]] = Field(default_factory=list)
    roof_objects: list[dict[str, Any]] = Field(default_factory=list)
    usable_regions: list[dict[str, Any]] = Field(default_factory=list)
    panel_layout: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None


class PresetOut(BaseModel):
    preset_id: str
    label: str
    region: str
    state: str
    lat: float
    lon: float
    half_size_deg: float
    description: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    data_mode: str
    google_key_configured: bool
    inkling_configured: bool
    database: str
    algorithm_version: str
    scoring_version: str

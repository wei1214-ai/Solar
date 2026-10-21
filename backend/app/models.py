"""SQLAlchemy ORM models (SQLite, GeoJSON text geometry).

Per Appendix B the geometry of every feature is stored as a GeoJSON string
inside a TEXT column - no PostGIS, no spatial index, no binary GIS stack.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    scope_type: Mapped[str] = mapped_column(String(16), index=True)  # BUILDING | AREA
    scope_label: Mapped[str] = mapped_column(String(255), default="")
    preset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    center_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    center_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox: Mapped[str | None] = mapped_column(String(255), nullable=True)  # "s,w,n,e"
    area_geom: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(String(512), default="")
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    ranked_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    unranked_no_data_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_annual_energy_dc_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    total_installed_kwp: Mapped[float] = mapped_column(Float, default=0.0)
    total_capex_base_rm: Mapped[float] = mapped_column(Float, default=0.0)
    data_mode: Mapped[str] = mapped_column(String(32), default="demo")
    solar_baseline_kwh_per_kwp: Mapped[float] = mapped_column(Float, default=1450.0)
    pvgis_reference_kwh_per_kwp: Mapped[float | None] = mapped_column(Float, nullable=True)
    pvgis_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_label_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    model_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    scoring_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    source_policy_version: Mapped[str] = mapped_column(String(32), default="1.0.0")

    buildings: Mapped[list["Building"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    scores: Mapped[list["BuildingScore"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (UniqueConstraint("source", "source_feature_id", name="uq_building_source_feature"),)

    building_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bld"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="openstreetmap")
    source_feature_id: Mapped[str] = mapped_column(String(64), index=True)
    footprint_geom: Mapped[str | None] = mapped_column(Text, nullable=True)  # GeoJSON 4326
    centroid_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    centroid_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    roof_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    footprint_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_levels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_type: Mapped[str] = mapped_column(String(32), default="residential")
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_status: Mapped[str] = mapped_column(String(32), default="ELIGIBLE_FOR_ANALYSIS", index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imagery_date: Mapped[str | None] = mapped_column(String(64), nullable=True)

    run: Mapped[AnalysisRun] = relationship(back_populates="buildings")
    segments: Mapped[list["RoofSegment"]] = relationship(back_populates="building", cascade="all, delete-orphan")
    objects: Mapped[list["RoofObject"]] = relationship(back_populates="building", cascade="all, delete-orphan")
    regions: Mapped[list["UsableRegion"]] = relationship(back_populates="building", cascade="all, delete-orphan")
    layout: Mapped["PanelLayout | None"] = relationship(back_populates="building", cascade="all, delete-orphan", uselist=False)
    score: Mapped["BuildingScore | None"] = relationship(back_populates="building", cascade="all, delete-orphan", uselist=False)


class RoofSegment(Base):
    __tablename__ = "roof_segments"

    segment_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("seg"))
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.building_id", ondelete="CASCADE"), index=True)
    geom: Mapped[str | None] = mapped_column(Text, nullable=True)  # GeoJSON 4326
    area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    pitch_deg: Mapped[float] = mapped_column(Float, default=0.0)
    azimuth_deg: Mapped[float] = mapped_column(Float, default=180.0)
    plane_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    flux_kwh_per_kwp: Mapped[float] = mapped_column(Float, default=1450.0)
    orientation_factor: Mapped[float] = mapped_column(Float, default=1.0)
    shade_factor: Mapped[float] = mapped_column(Float, default=1.0)
    solar_data_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    building: Mapped[Building] = relationship(back_populates="segments")


class RoofObject(Base):
    __tablename__ = "roof_objects"

    object_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("obj"))
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.building_id", ondelete="CASCADE"), index=True)
    obj_class: Mapped[str] = mapped_column(String(32), index=True)
    polygon_geom: Mapped[str | None] = mapped_column(Text, nullable=True)  # GeoJSON 4326
    area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    height_m: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_model: Mapped[str] = mapped_column(String(32), default="simulator")
    review_status: Mapped[str] = mapped_column(String(16), default="auto")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    building: Mapped[Building] = relationship(back_populates="objects")


class UsableRegion(Base):
    __tablename__ = "usable_regions"

    region_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("reg"))
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.building_id", ondelete="CASCADE"), index=True)
    polygon_geom: Mapped[str | None] = mapped_column(Text, nullable=True)  # GeoJSON 4326
    area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    exclusion_inputs_version: Mapped[str] = mapped_column(String(32), default="1.0.0")

    building: Mapped[Building] = relationship(back_populates="regions")


class PanelLayout(Base):
    __tablename__ = "panel_layouts"

    layout_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("lay"))
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.building_id", ondelete="CASCADE"), index=True)
    panel_profile_version: Mapped[str] = mapped_column(String(32), default="ref-panel-2026.1")
    panel_count: Mapped[int] = mapped_column(Integer, default=0)
    installed_kwp: Mapped[float] = mapped_column(Float, default=0.0)
    total_panel_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    usable_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    layout_geom: Mapped[str | None] = mapped_column(Text, nullable=True)  # GeoJSON FeatureCollection 4326
    annual_energy_dc_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    orientations: Mapped[str] = mapped_column(String(64), default="")

    building: Mapped[Building] = relationship(back_populates="layout")


class BuildingScore(Base):
    __tablename__ = "building_scores"

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("scr"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), index=True)
    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.building_id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="RANKED", index=True)
    annual_energy_dc_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    annual_energy_ac_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    installed_kwp: Mapped[float] = mapped_column(Float, default=0.0)
    panel_count: Mapped[int] = mapped_column(Integer, default=0)
    capex_base_rm: Mapped[float] = mapped_column(Float, default=0.0)
    capex_low_rm: Mapped[float] = mapped_column(Float, default=0.0)
    capex_high_rm: Mapped[float] = mapped_column(Float, default=0.0)
    score_kwh_per_year_per_rm: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    rank_energy: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    rank_cost_adjusted: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    annual_savings_rm: Mapped[float] = mapped_column(Float, default=0.0)
    payback_years: Mapped[float] = mapped_column(Float, default=0.0)
    co2_offset_tons: Mapped[float] = mapped_column(Float, default=0.0)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    run: Mapped[AnalysisRun] = relationship(back_populates="scores")
    building: Mapped[Building] = relationship(back_populates="score")


class CostBenchmarkRow(Base):
    __tablename__ = "cost_benchmarks"

    benchmark_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cb"))
    region: Mapped[str] = mapped_column(String(64), default="MY")
    building_type: Mapped[str] = mapped_column(String(32), default="residential")
    capacity_min_kwp: Mapped[float] = mapped_column(Float, default=0.0)
    capacity_max_kwp: Mapped[float | None] = mapped_column(Float, nullable=True)
    fixed_base_rm: Mapped[float] = mapped_column(Float, default=3500.0)
    low_rm_per_kwp: Mapped[float] = mapped_column(Float, default=4600.0)
    base_rm_per_kwp: Mapped[float] = mapped_column(Float, default=4600.0)
    high_rm_per_kwp: Mapped[float] = mapped_column(Float, default=4600.0)
    extra_cost_model: Mapped[str] = mapped_column(String(255), default="none")
    included_scope: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    effective_date: Mapped[str] = mapped_column(String(32), default="2026-01-01")
    confidence_level: Mapped[str] = mapped_column(String(16), default="low")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    rebate_policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

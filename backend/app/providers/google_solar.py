"""Google Solar Building Insights adapter (spec 4, 5.1, 11.1).

`buildingInsights.findClosest` is a nearest-known-building lookup. A 404 means
"no usable Solar data for this location" and the building must be marked
UNRANKED_NO_DATA (excluded from the ranking, never reported as zero potential).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Settings, get_settings
from ..solar import google_azimuth_to_compass

log = logging.getLogger("solarsync.google_solar")

ENDPOINT = "https://solar.googleapis.com/v1/buildingInsights:findClosest"

# Roof objects and fields we consider mandatory for a rankable result.
REQUIRED_FIELDS = ("roofSegments",)


class SolarApiError(RuntimeError):
    """Recoverable API problem (network, 5xx, malformed payload)."""


class SolarNoDataError(RuntimeError):
    """404 NOT_FOUND -> the building simply has no Solar data (expected)."""


class SolarQuotaError(SolarApiError):
    """403 / quota exhausted -> the caller should degrade to demo mode."""


@dataclass
class SolarRoofPlane:
    segment_id: str
    pitch_deg: float
    azimuth_deg: float
    plane_height_m: float | None
    area_m2: float
    center_lat: float
    center_lon: float
    bounding_box: dict | None = None


@dataclass
class SolarBuilding:
    reference: dict
    roof_planes: list[SolarRoofPlane] = field(default_factory=list)
    panel_width_m: float | None = None
    panel_height_m: float | None = None
    panel_capacity_w: float | None = None
    panel_count: int | None = None
    yearly_energy_dc_kwh: float | None = None
    total_annual_energy_dc_kwh: float | None = None
    building_height_m: float | None = None
    flux_kwh_per_kwp: float | None = None

    @property
    def has_core_fields(self) -> bool:
        if not self.roof_planes:
            return False
        if self.panel_count is None or self.panel_capacity_w is None:
            return False
        return self.yearly_energy_dc_kwh is not None or self.flux_kwh_per_kwp is not None


def _extract_insights(payload: dict) -> dict:
    if isinstance(payload, dict):
        if "buildingInsights" in payload and isinstance(payload["buildingInsights"], dict):
            return payload["buildingInsights"]
        if payload.get("error"):
            raise SolarNoDataError(str(payload["error"]))
        return payload
    raise SolarApiError("Unexpected Solar API payload type")


def _parse_planes(insights: dict) -> list[SolarRoofPlane]:
    planes: list[SolarRoofPlane] = []
    for index, segment in enumerate(insights.get("roofSegments") or []):
        plane = segment.get("roofPlane") or {}
        center = segment.get("center") or {}
        try:
            pitch = float(plane.get("pitchDegrees", 0.0))
        except (TypeError, ValueError):
            pitch = 0.0
        try:
            area = float(segment.get("areaMeters2", 0.0))
        except (TypeError, ValueError):
            area = 0.0
        planes.append(
            SolarRoofPlane(
                segment_id=str(segment.get("id") or f"seg_{index}"),
                pitch_deg=pitch,
                azimuth_deg=google_azimuth_to_compass(plane.get("azimuthDegrees")),
                plane_height_m=plane.get("planeHeightMeters"),
                area_m2=area,
                center_lat=float(center.get("latitude", 0.0) or 0.0),
                center_lon=float(center.get("longitude", 0.0) or 0.0),
                bounding_box=segment.get("boundingBox"),
            )
        )
    return planes


def _parse_config(insights: dict) -> dict:
    """Pick the largest sensible solarPanelConfig as the reference scenario."""
    configs = insights.get("solarPanelConfigs") or []
    best = None
    for cfg in configs:
        try:
            energy = float(cfg.get("yearlyEnergyDcKwh", 0.0) or 0.0)
        except (TypeError, ValueError):
            energy = 0.0
        if best is None or energy > best[1]:
            best = (cfg, energy)
    if best is None:
        return {}
    cfg, energy = best
    return {
        "panel_count": cfg.get("panelCount"),
        "panel_capacity_w": cfg.get("panelCapacityWatts"),
        "yearly_energy_dc_kwh": energy,
    }


def parse_response(payload: dict) -> SolarBuilding:
    insights = _extract_insights(payload)
    planes = _parse_planes(insights)
    config = _parse_config(insights)

    panel_w = panel_h = None
    panels = insights.get("solarPanels") or []
    if panels:
        first = panels[0]
        panel_w = first.get("widthMeters")
        panel_h = first.get("heightMeters")

    panel_count = config.get("panel_count")
    capacity_w = config.get("panel_capacity_w")
    energy = config.get("yearly_energy_dc_kwh")

    flux = None
    if energy and panel_count and capacity_w:
        kwp = (float(panel_count) * float(capacity_w)) / 1000.0
        if kwp > 0:
            flux = float(energy) / kwp

    return SolarBuilding(
        reference=insights,
        roof_planes=planes,
        panel_width_m=float(panel_w) if panel_w else None,
        panel_height_m=float(panel_h) if panel_h else None,
        panel_capacity_w=float(capacity_w) if capacity_w else None,
        panel_count=int(panel_count) if panel_count else None,
        yearly_energy_dc_kwh=float(energy) if energy else None,
        total_annual_energy_dc_kwh=(
            float(insights.get("totalAnnualEnergyDcKwh"))
            if insights.get("totalAnnualEnergyDcKwh") is not None
            else None
        ),
        building_height_m=(
            float(insights.get("buildingHeightMeters"))
            if insights.get("buildingHeightMeters") is not None
            else None
        ),
        flux_kwh_per_kwp=flux,
    )


async def find_closest(
    lat: float,
    lon: float,
    settings: Settings | None = None,
) -> SolarBuilding:
    """Call `buildingInsights.findClosest`; raises SolarNoDataError on 404."""
    settings = settings or get_settings()
    if not settings.has_google_key:
        raise SolarNoDataError("GOOGLE_MAPS_API_KEY is not configured")

    params = {"key": settings.google_maps_api_key}
    body = {"location": {"latitude": lat, "longitude": lon}}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.post(ENDPOINT, params=params, json=body)
    except httpx.HTTPError as exc:
        raise SolarApiError(f"Google Solar request failed: {exc}") from exc

    if response.status_code == 404:
        raise SolarNoDataError("Solar API returned 404 NOT_FOUND for this building")
    if response.status_code in (403, 429):
        raise SolarQuotaError(f"Google Solar quota/permission error {response.status_code}")
    if response.status_code >= 500:
        raise SolarApiError(f"Google Solar server error {response.status_code}")
    if response.status_code >= 400:
        raise SolarApiError(f"Google Solar error {response.status_code}: {response.text[:200]}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise SolarApiError("Solar API returned a non-JSON body") from exc

    building = parse_response(payload)
    if not building.roof_planes:
        raise SolarNoDataError("Solar API response has no roofSegments")
    return building


def building_insights_payload(building: SolarBuilding) -> dict[str, Any]:
    """Small, non-copyright-sensitive reference for traceability."""
    return {
        "roof_planes": [
            {
                "segment_id": p.segment_id,
                "pitch_deg": p.pitch_deg,
                "azimuth_deg": p.azimuth_deg,
                "plane_height_m": p.plane_height_m,
                "area_m2": p.area_m2,
            }
            for p in building.roof_planes
        ],
        "panel_count": building.panel_count,
        "panel_capacity_w": building.panel_capacity_w,
        "panel_width_m": building.panel_width_m,
        "panel_height_m": building.panel_height_m,
        "yearly_energy_dc_kwh": building.yearly_energy_dc_kwh,
        "flux_kwh_per_kwp": building.flux_kwh_per_kwp,
        "building_height_m": building.building_height_m,
    }

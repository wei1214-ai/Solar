"""Inkling vision adapter (spec 9, hackathon 1B).

Inkling is a text-output vision model. We therefore:

1.  crop a rooftop tile from real-time satellite imagery,
2.  ask Inkling for a strict JSON contract with `[0, 1000]` normalised
    polygons,
3.  validate the answer with Pydantic (class whitelist, coordinate ranges,
    polygon closure, area floor),
4.  convert the normalised vertices to real metres with the Web-Mercator
    ground resolution and project them into EPSG:32647 -> EPSG:4326.

No GDAL / rasterio is involved anywhere.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from ..config import Settings, get_settings
from ..geo import (
    NORMALIZED_GRID,
    ensure_valid,
    normalized_polygon_to_metric,
    to_wgs84,
)

log = logging.getLogger("solarsync.inkling")

OBJECT_CLASSES = (
    "existing_pv",
    "hvac_unit",
    "water_tank",
    "skylight",
    "vent",
    "other_obstacle",
    "unknown",
)

# Minimum share of a detected box that must fall inside the target footprint
# before we treat it as an on-roof obstacle (guards against model bleed-over).
MIN_ON_ROOF_OVERLAP = 0.6

SYSTEM_PROMPT = (
    "You are a rooftop equipment detection model for Malaysian rooftop solar "
    "siting. Analyse the supplied satellite tile of one building roof and "
    "return ONLY a JSON object (no prose, no markdown fences) with this exact "
    "shape:\n"
    '{"tile_id": "tile_001", "objects": ['
    '{"class": "hvac_unit", "bbox_norm_1000": [x1, y1, x2, y2], '
    '"polygon_norm_1000": [[x, y], ...], "confidence": 0.0, '
    '"notes": "approximate boundary"}], "needs_review": false}\n'
    "Rules: coordinate space is normalised to [0, 1000] where (0,0) is the "
    "top-left corner of the tile and (1000,1000) the bottom-right corner. "
    "`class` must be one of: existing_pv, hvac_unit, water_tank, skylight, "
    "vent, other_obstacle, unknown. `polygon_norm_1000` must be a simple "
    "closed polygon with at least 3 vertices and clockwise or counter-clockwise "
    "ordering. Report air-conditioner condensers, water tanks, skylights, "
    "vents and existing solar panel arrays. Ignore shadows, cars, roads and "
    "vegetation."
)


AREA_SYSTEM_PROMPT = (
    "You are a rooftop equipment detection model for Malaysian rooftop solar "
    "siting. The supplied satellite tile covers roughly 230 x 230 m and may "
    "contain SEVERAL buildings. Return ONLY a JSON object (no prose, no "
    "markdown fences) with this exact shape:\n"
    '{"tile_id": "tile_001", "objects": ['
    '{"class": "hvac_unit", "bbox_norm_1000": [x1, y1, x2, y2], '
    '"polygon_norm_1000": [[x, y], ...], "confidence": 0.0, '
    '"notes": "short description"}], "needs_review": false}\n'
    "Rules: coordinate space is normalised to [0, 1000] where (0,0) is the "
    "top-left corner of the tile and (1000,1000) the bottom-right corner. "
    "`class` must be one of: existing_pv, hvac_unit, water_tank, skylight, "
    "vent, other_obstacle, unknown. Draw each object tightly around the "
    "equipment itself - never around a whole roof, a roof section, or a "
    "building. Report every rooftop object you can see, on every building in "
    "the tile, up to 60 objects. Ignore shadows, cars, roads, trees and "
    "ground-level structures."
)


class InklingObject(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    obj_class: str = Field(alias="class")
    bbox_norm_1000: list[float] | None = None
    polygon_norm_1000: list[list[float]] | None = None
    confidence: float | None = None
    notes: str | None = None

    @field_validator("obj_class")
    @classmethod
    def _check_class(cls, value: str) -> str:
        normalized = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if normalized in {"pv", "solar_panel", "solar_panels", "existing_solar"}:
            normalized = "existing_pv"
        if normalized in {"ac", "air_conditioner", "aircon", "ac_unit", "hvac"}:
            normalized = "hvac_unit"
        if normalized in {"tank", "water_tanks"}:
            normalized = "water_tank"
        if normalized in {"pv_panel", "panel"}:
            normalized = "existing_pv"
        return normalized if normalized in OBJECT_CLASSES else "unknown"

    @field_validator("polygon_norm_1000")
    @classmethod
    def _check_polygon(cls, value):
        if value is None:
            return None
        cleaned: list[list[float]] = []
        for point in value:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                x = float(point[0])
                y = float(point[1])
            except (TypeError, ValueError):
                continue
            x = min(max(x, 0.0), float(NORMALIZED_GRID))
            y = min(max(y, 0.0), float(NORMALIZED_GRID))
            cleaned.append([x, y])
        if len(cleaned) < 3:
            return None
        return cleaned


class InklingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    tile_id: str | None = None
    objects: list[InklingObject] = Field(default_factory=list)
    needs_review: bool = False


@dataclass
class DetectedObject:
    obj_class: str
    geom_4326: BaseGeometry
    confidence: float
    notes: str | None = None
    area_m2: float = 0.0


@dataclass
class VisionResult:
    objects: list[DetectedObject] = field(default_factory=list)
    needs_review: bool = False
    source: str = "inkling"
    raw_objects: int = 0


class InklingError(RuntimeError):
    pass


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model answer."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _content_from_response(payload: dict) -> str | None:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") in (None, "text")
            ]
            return "".join(parts)
    content = payload.get("content")
    if isinstance(content, str):
        return content
    output = payload.get("output_text")
    if isinstance(output, str):
        return output
    return None


async def call_inkling(
    image_bytes: bytes,
    content_type: str = "image/png",
    settings: Settings | None = None,
    tile_id: str = "tile_001",
    prompt: str | None = None,
) -> InklingResponse:
    """Call the configured vision endpoint (Gemini preferred, OpenRouter fallback)."""
    settings = settings or get_settings()
    provider = settings.active_vision_provider
    if provider == "none":
        raise InklingError("No vision provider is configured")

    if provider == "gemini":
        from .gemini import call_gemini

        try:
            return await call_gemini(
                image_bytes, content_type, settings=settings, tile_id=tile_id, prompt=prompt
            )
        except InklingError as exc:
            if not (settings.inkling_api_key and settings.inkling_api_url):
                raise
            log.warning("Gemini failed (%s); falling back to OpenRouter", exc)

    system_prompt = prompt or SYSTEM_PROMPT
    from .imagery import encode_data_url

    data_url = encode_data_url(image_bytes, content_type)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {settings.inkling_api_key}"}

    openai_style = {
        "model": settings.inkling_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Tile {tile_id}: detect rooftop equipment."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    raw_style = {
        "model": settings.inkling_model,
        "tile_id": tile_id,
        "prompt": system_prompt,
        "image_b64": data_url.split(",", 1)[1],
    }

    last_error: Exception | None = None
    for body in (openai_style, raw_style):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
                response = await client.post(settings.inkling_api_url, headers=headers, json=body)
            if response.status_code >= 500:
                raise InklingError(f"Inkling server error {response.status_code}")
            if response.status_code >= 400:
                raise InklingError(f"Inkling error {response.status_code}: {response.text[:200]}")
            payload = response.json()
        except (httpx.HTTPError, ValueError, InklingError) as exc:
            last_error = exc
            continue

        parsed_dict: dict | None = None
        if isinstance(payload, dict) and isinstance(payload.get("objects"), list):
            parsed_dict = payload
        else:
            content = _content_from_response(payload)
            parsed_dict = extract_json(content) if content else None
        if parsed_dict is None:
            last_error = InklingError("Could not parse JSON from Inkling response")
            continue
        try:
            return InklingResponse.model_validate(parsed_dict)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise InklingError(f"Inkling request failed: {last_error}")


def _polygon_from_bbox(bbox: list[float]) -> list[list[float]] | None:
    if not bbox or len(bbox) < 4:
        return None
    x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def convert_response(
    response: InklingResponse,
    center_lat: float,
    center_lon: float,
    roof_geom_4326: BaseGeometry | None = None,
    zoom: int = 19,
    scale: int = 1,
    epsg: int | None = None,
    tile_size_px: int = 768,
) -> VisionResult:
    """Convert normalised Inkling polygons into geographic geometry."""
    from ..geo import to_metric

    result = VisionResult(needs_review=response.needs_review, source="inkling")
    roof_metric = (
        to_metric(roof_geom_4326, epsg=epsg)
        if roof_geom_4326 is not None and not roof_geom_4326.is_empty
        else None
    )

    for obj in response.objects:
        polygon_norm = obj.polygon_norm_1000
        if not polygon_norm:
            polygon_norm = _polygon_from_bbox(obj.bbox_norm_1000 or [])
        if not polygon_norm:
            continue
        try:
            metric = normalized_polygon_to_metric(
                polygon_norm,
                center_lat=center_lat,
                center_lon=center_lon,
                zoom=zoom,
                scale=scale,
                tile_size_px=tile_size_px,
                epsg=epsg,
            )
        except ValueError:
            continue
        metric = ensure_valid(metric)
        if metric is None or metric.is_empty or metric.area < 0.2:
            continue
        if roof_metric is not None:
            clipped = metric.intersection(roof_metric)
            if clipped.is_empty or clipped.area < 0.2:
                # Detection clearly outside the footprint -> reject (spec 9.5).
                continue
            # A box that mostly sits on a neighbouring roof is a bleed-over,
            # not an obstacle on this building.
            if metric.area > 0 and clipped.area / metric.area < MIN_ON_ROOF_OVERLAP:
                continue
            metric = clipped
        geom_4326 = to_wgs84(metric, epsg=epsg)
        result.objects.append(
            DetectedObject(
                obj_class=obj.obj_class,
                geom_4326=geom_4326,
                confidence=float(obj.confidence or 0.0),
                notes=obj.notes,
                area_m2=float(metric.area),
            )
        )
    result.raw_objects = len(response.objects)
    return result


def objects_to_geojson(objects: list[DetectedObject]) -> list[dict]:
    from shapely.geometry import mapping

    return [
        {
            "type": "Feature",
            "geometry": mapping(o.geom_4326),
            "properties": {
                "class": o.obj_class,
                "confidence": round(o.confidence, 3),
                "area_m2": round(o.area_m2, 2),
                "notes": o.notes,
            },
        }
        for o in objects
    ]


def polygon_from_norm(points: list[list[float]], lat: float, lon: float) -> Polygon:
    """Convenience helper used by tests and the demo simulator."""
    return Polygon(normalized_polygon_to_metric(points, lat, lon))



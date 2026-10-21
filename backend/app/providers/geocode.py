"""Nominatim / OpenStreetMap geocoding adapter - free-form place search.

Used by the "search any area" box in the dashboard so the product is not
limited to the built-in Kuala Lumpur / Penang presets.

Public service, no API key. The usage policy requires a descriptive
User-Agent and at most one request per second, so requests are throttled.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from ..config import Settings, get_settings

log = logging.getLogger("solarsync.geocode")

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
PHOTON_SEARCH = "https://photon.komoot.io/api/"

_last_call = 0.0
_lock = asyncio.Lock()


class GeocodeError(RuntimeError):
    pass


@dataclass
class GeocodeResult:
    label: str
    lat: float
    lon: float
    osm_type: str
    osm_id: str
    category: str
    bbox: tuple[float, float, float, float] | None = None


async def _throttle() -> None:
    global _last_call
    async with _lock:
        elapsed = asyncio.get_event_loop().time() - _last_call
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_call = asyncio.get_event_loop().time()


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "User-Agent": settings.nominatim_user_agent,
        "Accept-Language": "en,ms",
    }


def _to_result(item: dict) -> GeocodeResult | None:
    try:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
    except (TypeError, ValueError):
        return None
    name = item.get("name") or item.get("display_name", "")
    category = (item.get("category") or item.get("class") or item.get("type") or "").lower()
    label = item.get("display_name") or name
    bbox = None
    raw_bbox = item.get("boundingbox")
    if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
        try:
            south, north, west, east = (float(v) for v in raw_bbox)
            bbox = (south, west, north, east)
        except (TypeError, ValueError):
            bbox = None
    return GeocodeResult(
        label=label,
        lat=lat,
        lon=lon,
        osm_type=str(item.get("osm_type", "")),
        osm_id=str(item.get("osm_id", "")),
        category=category,
        bbox=bbox,
    )


async def search(
    query: str,
    limit: int = 6,
    settings: Settings | None = None,
    country_codes: str = "my",
) -> list[GeocodeResult]:
    """Search for a place. Restricted to Malaysia by default."""
    settings = settings or get_settings()
    text = (query or "").strip()
    if len(text) < 2:
        return []

    await _throttle()
    params = {
        "q": text,
        "format": "jsonv2",
        "limit": str(max(1, min(limit, 10))),
        "addressdetails": "1",
    }
    if country_codes:
        params["countrycodes"] = country_codes

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            response = await client.get(NOMINATIM_SEARCH, params=params, headers=_headers(settings))
        if response.status_code != 200:
            log.warning("Nominatim search failed: HTTP %s", response.status_code)
            return []
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Nominatim search error: %s", exc)
        return []

    results = []
    for item in payload if isinstance(payload, list) else []:
        result = _to_result(item)
        if result is not None:
            results.append(result)
    if results:
        return results

    # Nominatim blocked / empty -> fall back to the Photon mirror.
    return await _search_photon(text, limit=limit, settings=settings)


async def _search_photon(
    text: str,
    limit: int = 6,
    settings: Settings | None = None,
) -> list[GeocodeResult]:
    """Photon (Komoot) geocoder - a keyless fallback for Nominatim."""
    settings = settings or get_settings()
    await _throttle()
    params = {"q": text, "limit": str(max(1, min(limit, 10)))}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            response = await client.get(PHOTON_SEARCH, params=params, headers=_headers(settings))
        if response.status_code != 200:
            log.warning("Photon search failed: HTTP %s", response.status_code)
            return []
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Photon search error: %s", exc)
        return []

    results: list[GeocodeResult] = []
    for feature in (payload.get("features") or [])[:limit]:
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        lon, lat = float(coordinates[0]), float(coordinates[1])
        props = feature.get("properties") or {}
        parts = [
            str(props.get(key))
            for key in ("name", "city", "state", "country")
            if props.get(key)
        ]
        label = ", ".join(dict.fromkeys(parts))
        results.append(
            GeocodeResult(
                label=label or f"{lat:.4f}, {lon:.4f}",
                lat=lat,
                lon=lon,
                osm_type=str(props.get("osm_type", "")),
                osm_id=str(props.get("osm_id", "")),
                category=str(props.get("type", "") or props.get("osm_value", "")),
            )
        )
    return results


async def reverse(lat: float, lon: float, settings: Settings | None = None) -> GeocodeResult | None:
    """Reverse geocode a point (used to name a custom area)."""
    settings = settings or get_settings()
    await _throttle()
    params = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
            response = await client.get(NOMINATIM_REVERSE, params=params, headers=_headers(settings))
        if response.status_code != 200:
            return None
        return _to_result(response.json())
    except (httpx.HTTPError, ValueError):
        return None

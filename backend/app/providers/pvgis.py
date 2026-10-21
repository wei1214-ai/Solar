"""PVGIS adapter (EU Joint Research Centre) - independent radiation validation.

The design spec lists PVGIS as the optional, recommended *validation* source
for the annual yield model (not as the primary result). SolarSync therefore
fetches the PVGIS yield for the analysed area and publishes the comparison
against its own model, so a reviewer can see how far the estimate sits from
an independent European model.

Public endpoint, no API key. The JRC service is occasionally slow or drops
connections, so every call is time boxed, cached and treated as strictly
optional - a failed lookup never affects the analysis result.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from ..config import BACKEND_DIR, Settings, get_settings

log = logging.getLogger("solarsync.pvgis")

PVGIS_CALC = "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc"
CACHE_DIR = BACKEND_DIR / ".cache"
CACHE_TTL_SECONDS = 30 * 24 * 3600
USER_AGENT = "SolarSync/1.0 (rooftop solar potential research)"


class PvgisError(RuntimeError):
    pass


def _cache_file(lat: float, lon: float) -> Path:
    key = f"{lat:.3f}_{lon:.3f}".replace("-", "n")
    return CACHE_DIR / f"pvgis_{key}.json"


def _read_cache(lat: float, lon: float) -> dict | None:
    path = _cache_file(lat, lon)
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(lat: float, lon: float, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(lat, lon).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:  # pragma: no cover
        pass


async def fetch_annual_yield(
    lat: float,
    lon: float,
    settings: Settings | None = None,
    use_cache: bool = True,
    timeout: float = 20.0,
) -> dict | None:
    """Return the PVGIS annual yield for a 1 kWp system at the given point.

    Returns a dict with `E_y` (kWh/year, AC, 1 kWp), `pr` (performance ratio)
    and `api_version`, or None when the service is unavailable. Never raises.
    """
    settings = settings or get_settings()
    if use_cache:
        cached = _read_cache(lat, lon)
        if cached:
            return cached

    params = {
        "lat": f"{lat:.5f}",
        "lon": f"{lon:.5f}",
        "peakpower": "1",
        "loss": "14",
        "outputformat": "json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            response = await client.get(PVGIS_CALC, params=params)
        if response.status_code != 200:
            log.warning("PVGIS returned HTTP %s", response.status_code)
            return None
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - the service is optional
        log.warning("PVGIS request failed: %s", exc)
        return None

    outputs = payload.get("outputs") or {}
    totals = outputs.get("totals") or {}
    # PVGIS groups the results by tilt scenario: totals.fixed for the default
    # (optimal) tilt, or totals.<angle> when an inclination is requested.
    if "E_y" not in totals:
        for key, value in totals.items():
            if isinstance(value, dict) and "E_y" in value:
                totals = value
                break
    monthly = outputs.get("monthly") or {}
    for key, value in monthly.items():
        if isinstance(value, list):
            monthly = value
            break

    e_y = totals.get("E_y")
    if e_y is None:
        log.warning("PVGIS response did not contain E_y")
        return None

    result = {
        "E_y": float(e_y),
        "E_d": float(totals.get("E_d", 0.0) or 0.0),
        "pr": float(totals.get("pr", 0.0) or 0.0),
        "ac_y": float(totals.get("AC_y", 0.0) or 0.0),
        "api_version": (payload.get("inputs") or {}).get("api_version", "5.2"),
        "monthly": monthly,
        "url": f"{PVGIS_CALC}?lat={lat:.5f}&lon={lon:.5f}&peakpower=1&loss=14",
    }
    _write_cache(lat, lon, result)
    return result


def dc_baseline_from_pvgis(reference: dict, system_efficiency: float = 0.82) -> float:
    """Convert a PVGIS AC yield into the DC specific yield used by SolarSync.

    SolarSync computes E_dc from the specific yield and then applies the
    system efficiency, so the DC-equivalent baseline is E_y / efficiency.
    """
    efficiency = max(0.1, system_efficiency)
    return round(reference["E_y"] / efficiency, 1)

"""Solar physics for Malaysia: tilted-plane irradiance and annual flux.

The model is deliberately transparent and defensible:

*  Daily beam transposition on an inclined plane (Liu/ASHRAE style) integrated
   over a representative day of each month for the target latitude.
*  The resulting horizontal->tilted ratio is normalised against the
   "optimal" Malaysian plane (tilt 10 deg, facing south) so that the
   configured PVGIS-style baseline (default 1450 kWh/kWp/year) stays the
   single source of truth for absolute yield.
*  Azimuth convention used everywhere in SolarSync is the standard compass
   azimuth in degrees (0 = North, 90 = East, 180 = South, 270 = West).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEG = math.pi / 180.0

OPTIMAL_TILT_DEG = 10.0
OPTIMAL_AZIMUTH_DEG = 180.0  # South

MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
MONTH_MID_DAYS = (15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349)


def declination_deg(day_of_year: int) -> float:
    """Cooper's equation for solar declination (degrees)."""
    return 23.45 * math.sin(2.0 * math.pi * (284 + day_of_year) / 365.0)


def _daily_beam_sum(
    lat_deg: float,
    tilt_deg: float,
    azimuth_deg: float,
    delta_deg: float,
    step_deg: float = 5.0,
) -> tuple[float, float]:
    """Return (tilted beam, horizontal beam) for one clear-sky day.

    Uses the classic daily integration of the beam transposition factor Rb.
    """
    phi = lat_deg * DEG
    beta = tilt_deg * DEG
    gamma = (azimuth_deg - 180.0) * DEG  # surface azimuth measured from south
    delta = delta_deg * DEG

    cos_ws = -math.tan(phi) * math.tan(delta)
    if cos_ws <= -1.0 or cos_ws >= 1.0:
        # Polar day / polar night -> no sunrise, no beam contribution.
        return 0.0, 0.0
    ws = math.acos(cos_ws)

    tilted = 0.0
    horizontal = 0.0
    steps = max(int(round(2.0 * ws / DEG / step_deg)), 1)
    d_omega = (2.0 * ws) / steps

    for i in range(steps + 1):
        omega = -ws + i * d_omega
        cos_z = math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(omega)
        if cos_z <= 0.0:
            continue
        horizontal += cos_z
        sin_z = math.sqrt(max(0.0, 1.0 - cos_z * cos_z))
        # Solar azimuth measured from south, positive towards west.
        sin_gamma_s = math.cos(delta) * math.sin(omega) / sin_z
        cos_gamma_s = (
            math.sin(delta) * math.cos(phi) - math.cos(delta) * math.sin(phi) * math.cos(omega)
        ) / sin_z
        rb = cos_z * math.cos(beta) + sin_z * math.sin(beta) * (sin_gamma_s * math.sin(gamma) + cos_gamma_s * math.cos(gamma))
        tilted += max(rb, 0.0)

    return tilted * d_omega, horizontal * d_omega


def annual_irradiance_ratio(
    lat_deg: float,
    tilt_deg: float,
    azimuth_deg: float,
    baseline_tilt_deg: float = OPTIMAL_TILT_DEG,
    baseline_azimuth_deg: float = OPTIMAL_AZIMUTH_DEG,
) -> float:
    """Ratio of annual plane-of-array irradiation against the optimal plane.

    1.0 means "as good as the optimal Malaysian plane"; 0.9 means 10 % less
    energy than the optimal plane.
    """
    tilted_total = 0.0
    horizontal_total = 0.0
    for month_index, day in enumerate(MONTH_MID_DAYS):
        delta = declination_deg(day)
        tilted, horizontal = _daily_beam_sum(lat_deg, tilt_deg, azimuth_deg, delta)
        tilted_total += tilted * MONTH_DAYS[month_index]
        horizontal_total += horizontal * MONTH_DAYS[month_index]

    base_tilted_total = 0.0
    for month_index, day in enumerate(MONTH_MID_DAYS):
        delta = declination_deg(day)
        tilted, _horizontal = _daily_beam_sum(
            lat_deg, baseline_tilt_deg, baseline_azimuth_deg, delta
        )
        base_tilted_total += tilted * MONTH_DAYS[month_index]

    if base_tilted_total <= 0.0 or horizontal_total <= 0.0:
        return 1.0
    # Normalise both against the horizontal plane so the ratio expresses
    # "tilted plane vs horizontal plane", then divide by the optimal plane.
    tilted_over_horizontal = tilted_total / horizontal_total
    base_over_horizontal = base_tilted_total / horizontal_total
    if base_over_horizontal <= 0.0:
        return 1.0
    return max(0.0, tilted_over_horizontal / base_over_horizontal)


@dataclass(frozen=True)
class FluxEstimate:
    """Annual specific yield for a roof plane (kWh/kWp/year)."""

    flux_kwh_per_kwp: float
    orientation_factor: float
    tilt_deg: float
    azimuth_deg: float
    shade_factor: float
    baseline_kwh_per_kwp: float


def estimate_flux(
    lat_deg: float,
    tilt_deg: float,
    azimuth_deg: float,
    baseline_kwh_per_kwp: float = 1450.0,
    shade_factor: float = 1.0,
) -> FluxEstimate:
    """Compute the annual specific yield of a roof plane."""
    tilt = max(0.0, min(float(tilt_deg), 60.0))
    factor = annual_irradiance_ratio(lat_deg, tilt, azimuth_deg)
    shade = max(0.5, min(float(shade_factor), 1.0))
    flux = baseline_kwh_per_kwp * factor * shade
    return FluxEstimate(
        flux_kwh_per_kwp=flux,
        orientation_factor=factor,
        tilt_deg=tilt,
        azimuth_deg=azimuth_deg,
        shade_factor=shade,
        baseline_kwh_per_kwp=baseline_kwh_per_kwp,
    )


def azimuth_delta(a: float, b: float) -> float:
    """Smallest absolute difference between two compass azimuths (degrees)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def google_azimuth_to_compass(google_azimuth_deg: float | None) -> float:
    """Convert Google Solar API azimuth to the standard compass convention.

    The Building Insights API reports the roof-plane azimuth counter-clockwise
    from north; SolarSync works with clockwise-from-north compass azimuths.
    """
    if google_azimuth_deg is None:
        return 180.0
    return (360.0 - float(google_azimuth_deg)) % 360.0

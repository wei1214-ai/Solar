"""Malaysia installed CAPEX model, potential score and pitch metrics.

Spec 13.7 / Appendix B:

    C_installed_base = 3500 + P_installed_kwp * R_market
    R_market          = 4600 (<4 kWp) | 3900 (4-10 kWp) | 3400 (>10 kWp)
    Potential_Score   = E_annual_ac / C_installed_base   [kWh_AC/year/RM]
    Annual_Savings_RM = E_annual_ac * 0.50
    Payback_Years     = C_installed_base / Annual_Savings_RM
    CO2_Offset_Tons   = (E_annual_ac * 0.758) / 1000
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import CostBenchmark


@dataclass(frozen=True)
class CapexResult:
    base_rm: float
    low_rm: float
    high_rm: float
    tier_label: str
    rate_rm_per_kwp: float
    fixed_base_rm: float
    benchmark_version: str
    included_scope: str
    source_url: str


@dataclass(frozen=True)
class PitchMetrics:
    annual_energy_dc_kwh: float
    annual_energy_ac_kwh: float
    annual_savings_rm: float
    payback_years: float
    co2_offset_tons: float
    potential_score: float
    potential_score_pessimistic: float
    potential_score_optimistic: float


def compute_capex(kwp: float, benchmark: CostBenchmark) -> CapexResult:
    """Tiered Malaysian installed CAPEX (pre-subsidy, no battery)."""
    capacity = max(float(kwp), 0.0)
    rate = benchmark.tier_rate(capacity)
    base = benchmark.fixed_base_rm + capacity * rate
    return CapexResult(
        base_rm=round(base, 2),
        low_rm=round(base * benchmark.low_multiplier, 2),
        high_rm=round(base * benchmark.high_multiplier, 2),
        tier_label=benchmark.tier_label(capacity),
        rate_rm_per_kwp=rate,
        fixed_base_rm=benchmark.fixed_base_rm,
        benchmark_version=benchmark.version,
        included_scope=benchmark.included_scope,
        source_url=benchmark.source_url,
    )


def compute_pitch_metrics(
    annual_energy_dc_kwh: float,
    annual_energy_ac_kwh: float,
    capex: CapexResult,
    tariff_rm_per_kwh: float = 0.50,
    grid_emission_kg_per_kwh: float = 0.758,
) -> PitchMetrics:
    """Hackathon pitch-hook metrics."""
    savings = annual_energy_ac_kwh * tariff_rm_per_kwh
    payback = (capex.base_rm / savings) if savings > 0 else 0.0
    co2 = (annual_energy_ac_kwh * grid_emission_kg_per_kwh) / 1000.0
    score = (annual_energy_ac_kwh / capex.base_rm) if capex.base_rm > 0 else 0.0
    score_pess = (annual_energy_ac_kwh / capex.high_rm) if capex.high_rm > 0 else 0.0
    score_opt = (annual_energy_ac_kwh / capex.low_rm) if capex.low_rm > 0 else 0.0
    return PitchMetrics(
        annual_energy_dc_kwh=round(annual_energy_dc_kwh, 1),
        annual_energy_ac_kwh=round(annual_energy_ac_kwh, 1),
        annual_savings_rm=round(savings, 2),
        payback_years=round(payback, 2),
        co2_offset_tons=round(co2, 3),
        potential_score=round(score, 6),
        potential_score_pessimistic=round(score_pess, 6),
        potential_score_optimistic=round(score_opt, 6),
    )


def to_dicts(capex: CapexResult, metrics: PitchMetrics) -> dict:
    payload = asdict(capex)
    payload.update(asdict(metrics))
    return payload

"""Runtime configuration for SolarSync.

Every third-party credential is optional. When a credential is missing the
corresponding adapter transparently degrades to the deterministic demo
fallback so the product is fully usable without any API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default)) or default))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "").lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ReferencePanel:
    """Versioned reference panel profile (spec 11.2)."""

    version: str = "ref-panel-2026.1"
    width_m: float = 1.134
    height_m: float = 1.762
    rated_power_wp: float = 440.0
    gap_m: float = 0.05
    edge_setback_m: float = 0.5
    obstacle_clearance_m: float = 0.3
    walkway_width_m: float = 0.8
    walkway_spacing_m: float = 6.0

    @property
    def area_m2(self) -> float:
        return self.width_m * self.height_m

    @property
    def power_kwp(self) -> float:
        return self.rated_power_wp / 1000.0

    @property
    def power_density_wp_m2(self) -> float:
        return self.rated_power_wp / self.area_m2


@dataclass(frozen=True)
class CostBenchmark:
    """Malaysia installed CAPEX benchmark (spec 13.7).

    C_installed = C_fixed_base + P_installed_kwp * R_market
    """

    version: str = "my-capex-2026.1"
    fixed_base_rm: float = 3500.0
    tier_lt_4_kwp_rm_per_kwp: float = 4600.0
    tier_4_to_10_kwp_rm_per_kwp: float = 3900.0
    tier_gt_10_kwp_rm_per_kwp: float = 3400.0
    low_multiplier: float = 0.88
    high_multiplier: float = 1.15
    included_scope: str = (
        "Modules, inverter, mounting, standard cabling and DC/AC protection, "
        "labour, commissioning and standard grid-application paperwork. "
        "No battery, no subsidy offset."
    )
    source_url: str = "https://www.volts.my/blog/solar-panel-cost-malaysia-2026"
    effective_date: str = "2026-01-01"

    def tier_rate(self, kwp: float) -> float:
        if kwp < 4.0:
            return self.tier_lt_4_kwp_rm_per_kwp
        if kwp <= 10.0:
            return self.tier_4_to_10_kwp_rm_per_kwp
        return self.tier_gt_10_kwp_rm_per_kwp

    def tier_label(self, kwp: float) -> str:
        if kwp < 4.0:
            return "< 4 kWp"
        if kwp <= 10.0:
            return "4 - 10 kWp"
        return "> 10 kWp"


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: _env("APP_NAME", "SolarSync"))
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", "sqlite:///./solarsync.db")
    )
    google_maps_api_key: str = field(
        default_factory=lambda: _env("GOOGLE_MAPS_API_KEY")
    )
    inkling_api_key: str = field(default_factory=lambda: _env("INKLING_API_KEY"))
    inkling_api_url: str = field(default_factory=lambda: _env("INKLING_API_URL"))
    inkling_model: str = field(default_factory=lambda: _env("INKLING_MODEL", "inkling-vision"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_thinking_level: str = field(
        default_factory=lambda: _env("GEMINI_THINKING_LEVEL", "low")
    )
    vision_provider: str = field(
        default_factory=lambda: _env("VISION_PROVIDER", "").strip().lower()
    )
    nominatim_user_agent: str = field(
        default_factory=lambda: _env(
            "NOMINATIM_USER_AGENT",
            "SolarSync/1.0 (rooftop solar potential research)",
        )
    )
    overpass_endpoints: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            e.strip()
            for e in _env(
                "OVERPASS_ENDPOINTS",
                "https://overpass-api.de/api/interpreter,"
                "https://overpass.kumi.systems/api/interpreter,"
                "https://overpass.private.coffee/api/interpreter,"
                "https://overpass.osm.ch/api/interpreter,"
                "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            ).split(",")
            if e.strip()
        )
    )
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in _env(
                "CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,"
                "http://localhost:3001,http://127.0.0.1:3001",
            ).split(",")
            if o.strip()
        )
    )
    algorithm_version: str = field(
        default_factory=lambda: _env("ALGORITHM_VERSION", "1.0.0")
    )
    model_version: str = field(default_factory=lambda: _env("MODEL_VERSION", "1.0.0"))
    scoring_version: str = field(
        default_factory=lambda: _env("SCORING_VERSION", "1.0.0")
    )
    source_policy_version: str = field(
        default_factory=lambda: _env("SOURCE_POLICY_VERSION", "1.0.0")
    )
    max_concurrent_buildings: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_BUILDINGS", 5)
    )
    max_buildings_per_run: int = field(
        default_factory=lambda: _env_int("MAX_BUILDINGS_PER_RUN", 150)
    )
    exclude_residential: bool = field(
        default_factory=lambda: _env_bool("EXCLUDE_RESIDENTIAL", True)
    )
    vision_tile_mode: bool = field(
        default_factory=lambda: _env_bool("VISION_TILE_MODE", False)
    )
    min_footprint_m2: float = field(
        default_factory=lambda: _env_float("MIN_FOOTPRINT_M2", 12.0)
    )
    solar_baseline_kwh_per_kwp_year: float = field(
        default_factory=lambda: _env_float("SOLAR_BASELINE_KWH_PER_KWP_YEAR", 1450.0)
    )
    grid_emission_factor_kg_co2_per_kwh: float = field(
        default_factory=lambda: _env_float("GRID_EMISSION_FACTOR_KG_CO2_PER_KWH", 0.758)
    )
    electricity_tariff_rm_per_kwh: float = field(
        default_factory=lambda: _env_float("ELECTRICITY_TARIFF_RM_PER_KWH", 0.50)
    )
    system_efficiency_ac: float = field(
        default_factory=lambda: _env_float("SYSTEM_EFFICIENCY_AC", 0.82)
    )
    pvgis_baseline_mode: str = field(
        default_factory=lambda: _env("PVGIS_BASELINE_MODE", "fixed")
    )
    panel: ReferencePanel = field(default_factory=ReferencePanel)
    cost_benchmark: CostBenchmark = field(default_factory=CostBenchmark)

    @property
    def has_google_key(self) -> bool:
        return bool(self.google_maps_api_key)

    @property
    def gemini_api_keys(self) -> tuple[str, ...]:
        """All Gemini keys available for quota sharding.

        Free-tier quota is per Google project, so rotating several keys from
        different projects multiplies the effective requests-per-minute.
        """
        raw = _env("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys and self.gemini_api_key:
            keys = [self.gemini_api_key.strip()]
        return tuple(dict.fromkeys(keys))

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_keys)

    @property
    def active_vision_provider(self) -> str:
        """Resolve which vision endpoint to call, preferring Gemini."""
        if self.vision_provider in {"gemini", "openrouter"}:
            if self.vision_provider == "gemini" and self.has_gemini_key:
                return "gemini"
            if self.vision_provider == "openrouter" and self.inkling_api_key:
                return "openrouter"
        if self.has_gemini_key:
            return "gemini"
        if self.inkling_api_key and self.inkling_api_url:
            return "openrouter"
        return "none"

    @property
    def has_inkling_key(self) -> bool:
        """True when any vision backend is configured (spec calls it Inkling)."""
        return self.active_vision_provider != "none"

    @property
    def vision_model(self) -> str:
        return self.gemini_model if self.active_vision_provider == "gemini" else self.inkling_model

    @property
    def demo_mode(self) -> bool:
        """True when no live vision model is reachable."""
        return not self.has_inkling_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

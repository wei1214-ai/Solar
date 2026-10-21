"""Quick-test areas for the hackathon demo (Appendix B item 2).

The demo covers three Selangor localities on the Klang Valley grid - a dense
commercial/residential mix, an industrial corridor and a mature township - so
the same scoring rules can be shown across different roof profiles.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    preset_id: str
    label: str
    region: str
    state: str
    lat: float
    lon: float
    half_size_deg: float = 0.0045
    description: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset(
        "kota-damansara",
        "Kota Damansara, Selangor",
        "Kota Damansara",
        "Selangor",
        3.1510,
        101.5870,
        0.0045,
        "Mixed commercial shophouses and mid-rise residential blocks along the MRT corridor.",
    ),
    Preset(
        "sungai-buloh",
        "Sungai Buloh, Selangor",
        "Sungai Buloh",
        "Selangor",
        3.2060,
        101.5680,
        0.0045,
        "Low-rise industrial and shop lots - large flat factory roofs suited to commercial PV.",
    ),
    Preset(
        "subang-jaya",
        "Subang Jaya, Selangor",
        "Subang Jaya",
        "Selangor",
        3.0850,
        101.5860,
        0.0050,
        "Dense terrace housing with high plot coverage - good for batch ranking.",
    ),
)

PRESETS_BY_ID: dict[str, Preset] = {p.preset_id: p for p in PRESETS}


def get_preset(preset_id: str) -> Preset | None:
    return PRESETS_BY_ID.get(preset_id)


def bbox_for(preset: Preset) -> tuple[float, float, float, float]:
    return (
        preset.lat - preset.half_size_deg,
        preset.lon - preset.half_size_deg,
        preset.lat + preset.half_size_deg,
        preset.lon + preset.half_size_deg,
    )

"""Layout sanity check on real buildings (no API keys required).

Verifies for every analysed building that
  * every panel lies fully inside the usable roof polygon,
  * no two panels overlap,
  * roof coverage stays in a physically plausible band,
  * CAPEX / energy / payback / CO2 metrics match the spec formulas.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import shape

from app.config import get_settings
from app.geo import from_geojson_str, to_geojson_str, to_metric
from app.pipeline import analyze_building
from app.providers import overpass, presets


def _object_features(analysis) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": json.loads(to_geojson_str(obj.geom_4326)),
            "properties": {"class": obj.obj_class, "height_m": obj.height_m},
        }
        for obj in analysis.objects
    ]


async def main() -> int:
    settings = get_settings()
    preset = presets.PRESETS[0]
    bbox = presets.bbox_for(preset)
    buildings = await overpass.fetch_buildings(bbox, settings=settings, max_buildings=40)
    print(f"fetched {len(buildings)} buildings\n")

    header = (
        f"{'id':<22}{'foot m2':>9}{'usable':>9}{'panels':>7}{'cov%':>7}"
        f"{'kWp':>7}{'DC kWh':>10}{'CAPEX':>9}{'score':>8}{'pay':>6}{'CO2':>7}"
    )
    print(header)
    print("-" * len(header))

    failures = 0
    for building in buildings:
        analysis = await analyze_building(building, buildings, settings=settings)
        if analysis.status != "RANKED":
            print(f"{building.source_feature_id:<22} -> {analysis.status}: {analysis.reason}")
            continue

        # Geometric verification against the stored usable polygon.
        usable = from_geojson_str(None)
        usable_geom = analysis.usable_geom_4326
        usable_metric = to_metric(usable_geom) if usable_geom is not None else None
        panels = [shape(f["geometry"]) for f in analysis.panels_geojson]
        panel_metric = [to_metric(p) for p in panels]

        outside = 0
        overlapping = 0
        if usable_metric is not None:
            for pm in panel_metric:
                if not usable_metric.contains(pm):
                    outside += 1
        for i in range(len(panel_metric)):
            for j in range(i + 1, len(panel_metric)):
                if panel_metric[i].intersection(panel_metric[j]).area > 1e-9:
                    overlapping += 1
                    break
            else:
                continue
            break

        # Obstacles must not be covered by a panel.
        obstacle_metric = [
            to_metric(shape(feature["geometry"])) for feature in _object_features(analysis)
        ]
        obstacle_hits = 0
        for obstacle in obstacle_metric:
            for pm in panel_metric:
                if obstacle.intersection(pm).area > 1e-9:
                    obstacle_hits += 1
                    break
        for obstacle in obstacle_metric:
            if obstacle.area < 0.05:
                failures += 1
                print("    !! obstacle with degenerate area")

        coverage = (
            analysis.panel_area_m2 / analysis.usable_area_m2 * 100.0
            if analysis.usable_area_m2
            else 0.0
        )
        if outside or overlapping or obstacle_hits:
            failures += 1

        # Formula verification (spec 13.7 / appendix B).
        assert abs(analysis.annual_energy_ac_kwh - analysis.annual_energy_dc_kwh * 0.82) < 1.0
        assert abs(analysis.annual_savings_rm - analysis.annual_energy_ac_kwh * 0.50) < 0.5
        assert abs(analysis.potential_score - analysis.annual_energy_ac_kwh / analysis.capex_base_rm) < 1e-5
        assert abs(analysis.co2_offset_tokens - analysis.annual_energy_ac_kwh * 0.758 / 1000.0) < 0.01

        print(
            f"{building.source_feature_id:<22}"
            f"{analysis.building.footprint_area_m2:>9.0f}"
            f"{analysis.usable_area_m2:>9.0f}"
            f"{analysis.panel_count:>7}"
            f"{coverage:>7.1f}"
            f"{analysis.installed_kwp:>7.2f}"
            f"{analysis.annual_energy_dc_kwh:>10,.0f}"
            f"{analysis.capex_base_rm:>9,.0f}"
            f"{analysis.potential_score:>8.3f}"
            f"{analysis.payback_years:>6.1f}"
            f"{analysis.co2_offset_tokens:>7.2f}"
        )
        if outside or overlapping or obstacle_hits:
            print(
                f"    !! {outside} panels outside usable polygon, {overlapping} overlapping pairs, "
                f"{obstacle_hits} panels overlapping obstacles"
            )

    print(f"\ngeometric failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


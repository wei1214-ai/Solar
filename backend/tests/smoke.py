"""Local smoke test: live Overpass -> pipeline -> ranking (no API keys needed).

Run with:  .venv\\Scripts\\python.exe -m tests.smoke
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.providers import overpass, presets
from app.runs import create_run, execute_run


async def main() -> int:
    settings = get_settings()
    init_db()
    preset = presets.get_preset("kl-bangsar") or presets.PRESETS[0]
    bbox = presets.bbox_for(preset)
    print(f"[1] fetching Overpass footprints for {preset.label} {bbox}")
    buildings = await overpass.fetch_buildings(bbox, settings=settings, max_buildings=25)
    print(f"    -> {len(buildings)} buildings fetched")
    if not buildings:
        print("!! Overpass returned nothing - network issue?")
        return 1
    for b in buildings[:5]:
        print(f"    - {b.source_feature_id} area={b.footprint_area_m2:.0f} m2 type={b.building_type}")

    with SessionLocal() as session:
        run = create_run(session, "AREA", preset_id=preset.preset_id, bbox=bbox)
        run_id = run.run_id
    print(f"[2] run created: {run_id}")

    await execute_run(run_id, settings=settings)

    with SessionLocal() as session:
        from app.models import AnalysisRun, BuildingScore

        run = session.get(AnalysisRun, run_id)
        print(
            f"[3] status={run.status} candidates={run.candidate_count} ranked={run.ranked_count} "
            f"excluded={run.excluded_count} unranked_no_data={run.unranked_no_data_count} failed={run.failed_count}"
        )
        print(
            f"    total DC energy={run.total_annual_energy_dc_kwh:,.0f} kWh  "
            f"installed={run.total_installed_kwp:.2f} kWp  capex=RM {run.total_capex_base_rm:,.0f}"
        )
        scores = (
            session.query(BuildingScore)
            .filter(BuildingScore.run_id == run_id, BuildingScore.status == "RANKED")
            .order_by(BuildingScore.rank_cost_adjusted)
            .limit(5)
            .all()
        )
        print("[4] top 5 by potential score:")
        for s in scores:
            print(
                f"    #{s.rank_cost_adjusted} {s.building_id} panels={s.panel_count} "
                f"kWp={s.installed_kwp:.2f} DC={s.annual_energy_dc_kwh:,.0f} kWh "
                f"AC={s.annual_energy_ac_kwh:,.0f} CAPEX=RM{s.capex_base_rm:,.0f} "
                f"score={s.score_kwh_per_year_per_rm:.3f} payback={s.payback_years:.1f}y "
                f"CO2={s.co2_offset_tons:.2f}t"
            )
        if not scores:
            print("!! nothing ranked")
            return 1
        top = scores[0]
        detail = session.get(BuildingScore, top.score_id)
        print("[5] detail metrics:")
        print(json.dumps(detail.metrics.get("cost", {}), indent=2)[:800])
        from app.models import PanelLayout

        layout_row = (
            session.query(PanelLayout).filter(PanelLayout.building_id == top.building_id).first()
        )
        if layout_row is not None and layout_row.layout_geom:
            features = json.loads(layout_row.layout_geom)["features"]
            print(f"    panel layout features: {len(features)}")
            print(f"    sample panel props: {features[0]['properties'] if features else None}")
        print(
            f"    roof area used {detail.metrics.get('usable_area_m2')} m2 / "
            f"panel area {detail.metrics.get('panel_area_m2')} m2"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

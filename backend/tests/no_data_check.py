"""Validates the 404 -> UNRANKED_NO_DATA rule (spec 2.3 / 5.1, hackathon 1A).

Simulates a Google Solar API 404 for every building and asserts that:
  * the batch keeps running (no exception, no interrupt),
  * every building ends up UNRANKED_NO_DATA,
  * none of them appear in the leaderboard,
  * the run reports the no-data count separately from rule-based exclusions.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import AnalysisRun, BuildingScore
from app.pipeline import analyze_building
from app.providers import google_solar, overpass, presets


async def main() -> int:
    settings = get_settings()
    init_db()

    # Force the live code path even without a key, then make it 404.
    original = google_solar.find_closest

    async def always_not_found(lat, lon, settings=None):  # noqa: ANN001
        raise google_solar.SolarNoDataError("Solar API returned 404 NOT_FOUND for this building")

    google_solar.find_closest = always_not_found  # type: ignore[assignment]
    settings = settings.__class__(**{**settings.__dict__, "google_maps_api_key": "test-key"})

    preset = presets.PRESETS[0]
    bbox = presets.bbox_for(preset)
    buildings = await overpass.fetch_buildings(bbox, settings=settings, max_buildings=12)
    print(f"fetched {len(buildings)} buildings - all should be UNRANKED_NO_DATA\n")

    statuses: dict[str, int] = {}
    for building in buildings:
        analysis = await analyze_building(building, buildings, settings=settings)
        statuses[analysis.status] = statuses.get(analysis.status, 0) + 1
        if analysis.status == "UNRANKED_NO_DATA":
            print(f"  {building.source_feature_id:<22} UNRANKED_NO_DATA: {analysis.reason[:70]}")

    google_solar.find_closest = original  # type: ignore[assignment]

    print("\nstatus counts:", statuses)
    ok = True
    if statuses.get("UNRANKED_NO_DATA", 0) != len(buildings):
        print("!! expected every building to be UNRANKED_NO_DATA")
        ok = False
    if statuses.get("RANKED", 0):
        print("!! some buildings were ranked despite 404")
        ok = False

    # A run with zero rankable buildings must publish an empty leaderboard.
    from app.runs import create_run, execute_run

    with SessionLocal() as session:
        run = create_run(session, "AREA", preset_id=preset.preset_id, bbox=bbox)
        run_id = run.run_id

    original_find = google_solar.find_closest
    google_solar.find_closest = always_not_found  # type: ignore[assignment]
    try:
        await execute_run(run_id, settings=settings)
    finally:
        google_solar.find_closest = original_find  # type: ignore[assignment]

    with SessionLocal() as session:
        run = session.get(AnalysisRun, run_id)
        scores = session.query(BuildingScore).filter(BuildingScore.run_id == run_id).all()
        print(
            f"\nrun status={run.status} candidates={run.candidate_count} ranked={run.ranked_count} "
            f"excluded={run.excluded_count} no_data={run.unranked_no_data_count} failed={run.failed_count}"
        )
        if run.ranked_count != 0:
            print("!! leaderboard must be empty")
            ok = False
        if run.unranked_no_data_count == 0:
            print("!! no-data buildings were not counted")
            ok = False
        if run.status != "COMPLETED_WITH_EXCLUSIONS":
            print("!! expected COMPLETED_WITH_EXCLUSIONS")
            ok = False

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))


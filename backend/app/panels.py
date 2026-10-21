"""Panel candidate generation and greedy layout optimisation.

Pure Shapely implementation (no OR-Tools, no GDAL), per Appendix B item 4:

1.  usable_poly = roof_poly.buffer(-edge_setback).difference(obstacles.buffer(clearance))
2.  Projected panel length on a pitched roof:  L_proj = L * cos(pitch)
3.  Enumerate a candidate grid in both Portrait and Landscape orientation
    (0.05 m installation gap), keep only rectangles fully contained in the
    usable polygon.
4.  Greedily select the non-overlapping candidates ordered by annual flux.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from .config import ReferencePanel

PORTRAIT = "portrait"
LANDSCAPE = "landscape"
EPSILON = 0.001  # metres - keeps panels strictly inside the usable polygon


@dataclass
class PanelCandidate:
    geom_m: BaseGeometry
    orientation: str
    segment_id: str
    flux_kwh_per_kwp: float
    pitch_deg: float

    @property
    def area_m2(self) -> float:
        return float(self.geom_m.area)


@dataclass
class SegmentInput:
    """One roof plane ready for layout."""

    segment_id: str
    usable_poly_m: BaseGeometry
    pitch_deg: float = 0.0
    flux_kwh_per_kwp: float = 1450.0


@dataclass
class LayoutResult:
    panels: list[PanelCandidate] = field(default_factory=list)
    usable_geom_m: BaseGeometry | None = None
    usable_area_m2: float = 0.0
    panel_area_m2: float = 0.0
    orientations_used: list[str] = field(default_factory=list)

    @property
    def panel_count(self) -> int:
        return len(self.panels)


def projected_length(panel: ReferencePanel, pitch_deg: float) -> float:
    """Horizontal 2D projection of the panel length on a pitched roof (spec 10.2 / 2B)."""
    pitch = math.radians(max(0.0, min(float(pitch_deg), 75.0)))
    return panel.height_m * math.cos(pitch)


def _roof_axis_angle(poly: BaseGeometry) -> float:
    """Principal direction of the usable polygon, in degrees."""
    try:
        rect = poly.minimum_rotated_rectangle
    except Exception:  # pragma: no cover - shapely always supports this
        rect = poly.envelope
    coords = list(rect.exterior.coords)
    if len(coords) < 4:
        return 0.0
    best_angle = 0.0
    best_length = -1.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        length = math.hypot(x2 - x1, y2 - y1)
        if length > best_length:
            best_length = length
            best_angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return best_angle


def _grid_rects(
    poly: BaseGeometry,
    angle_deg: float,
    width_m: float,
    length_m: float,
    gap_m: float,
) -> list[BaseGeometry]:
    """Axis aligned grid inside `poly` (already rotated into the roof frame)."""
    min_x, min_y, max_x, max_y = poly.bounds
    step_x = width_m + gap_m
    step_y = length_m + gap_m
    if step_x <= 0 or step_y <= 0:
        return []
    out: list[BaseGeometry] = []
    y = min_y
    while y + length_m <= max_y + 1e-9:
        x = min_x
        while x + width_m <= max_x + 1e-9:
            # 1 mm inset so a panel never touches (and therefore never leaks
            # out of) the usable polygon after the projection round trip.
            rect = box(x + EPSILON, y + EPSILON, x + width_m - EPSILON, y + length_m - EPSILON)
            if poly.contains(rect):
                out.append(rect)
            x += step_x
        y += step_y
    return out


def _rotate(geom: BaseGeometry, angle_deg: float, origin: tuple[float, float]) -> BaseGeometry:
    return affinity.rotate(geom, angle_deg, origin=origin)


def segment_candidates(
    segment: SegmentInput,
    panel: ReferencePanel,
    flux_kwh_per_kwp: float | None = None,
) -> list[PanelCandidate]:
    """Enumerate candidate panel rectangles for one roof segment."""
    poly = segment.usable_poly_m
    if poly is None or poly.is_empty:
        return []
    flux = segment.flux_kwh_per_kwp if flux_kwh_per_kwp is None else flux_kwh_per_kwp

    length_proj = projected_length(panel, segment.pitch_deg)
    min_dimension = min(panel.width_m, length_proj)
    if min_dimension <= 0.05:
        return []
    # A panel can only fit if its shortest side fits inside the polygon.
    if poly.area < min_dimension * min_dimension:
        return []

    angle = _roof_axis_angle(poly)
    pivot = (poly.centroid.x, poly.centroid.y)
    rotated = _rotate(poly, -angle, pivot)

    per_orientation: dict[str, list[BaseGeometry]] = {}
    for orientation, (w, l) in (
        (PORTRAIT, (panel.width_m, length_proj)),
        (LANDSCAPE, (length_proj, panel.width_m)),
    ):
        rects = _grid_rects(rotated, angle, w, l, panel.gap_m)
        per_orientation[orientation] = [
            _rotate(r, angle, pivot) for r in rects
        ]

    # Pick the orientation that fits the most panels for this segment.
    best_orientation = PORTRAIT
    best_rects = per_orientation.get(PORTRAIT, [])
    landscape = per_orientation.get(LANDSCAPE, [])
    if len(landscape) > len(best_rects):
        best_orientation, best_rects = LANDSCAPE, landscape

    return [
        PanelCandidate(
            geom_m=rect,
            orientation=best_orientation,
            segment_id=segment.segment_id,
            flux_kwh_per_kwp=flux,
            pitch_deg=segment.pitch_deg,
        )
        for rect in best_rects
    ]


def greedy_select(candidates: list[PanelCandidate]) -> list[PanelCandidate]:
    """Greedy flux-ordered selection of non-overlapping candidates."""
    if not candidates:
        return []
    geoms = [c.geom_m for c in candidates]
    tree = STRtree(geoms)
    order = sorted(
        range(len(candidates)),
        key=lambda i: (-candidates[i].flux_kwh_per_kwp, -geoms[i].area, i),
    )
    blocked: set[int] = set()
    selected: list[PanelCandidate] = []
    for i in order:
        if i in blocked:
            continue
        selected.append(candidates[i])
        for j in tree.query(geoms[i]):
            j = int(j)
            if j == i or j in blocked:
                continue
            if geoms[i].intersection(geoms[j]).area > 1e-9:
                blocked.add(j)
    return selected


def build_layout(
    segments: list[SegmentInput],
    panel: ReferencePanel,
    flux_resolver=None,
) -> LayoutResult:
    """Full layout pipeline for one building.

    `flux_resolver(segment_id, candidate_polygon) -> float` may be supplied to
    refine the flux of every candidate panel (used by the demo simulator to
    model micro-shading from rooftop obstacles).
    """
    result = LayoutResult()
    all_candidates: list[PanelCandidate] = []
    usable_geoms: list[BaseGeometry] = []
    orientations: list[str] = []

    for segment in segments:
        if segment.usable_poly_m is None or segment.usable_poly_m.is_empty:
            continue
        usable_geoms.append(segment.usable_poly_m)
        candidates = segment_candidates(segment, panel)
        if flux_resolver is not None:
            for cand in candidates:
                refined = flux_resolver(segment.segment_id, cand.geom_m)
                if refined is not None:
                    cand.flux_kwh_per_kwp = refined
        if candidates:
            orientations.append(candidates[0].orientation)
        all_candidates.extend(candidates)

    if not usable_geoms:
        return result

    usable = usable_geoms[0]
    for extra in usable_geoms[1:]:
        usable = usable.union(extra)

    selected = greedy_select(all_candidates)
    result.panels = selected
    result.usable_geom_m = usable
    result.usable_area_m2 = float(usable.area)
    result.panel_area_m2 = float(sum(p.area_m2 for p in selected))
    result.orientations_used = sorted(set(orientations))
    return result


def panels_geojson_4326(panels: list[PanelCandidate], to_wgs84_fn) -> list[dict]:
    """GeoJSON features for every selected panel (already projected to 4326)."""
    from shapely.geometry import mapping

    features = []
    for index, panel in enumerate(panels):
        geom = to_wgs84_fn(panel.geom_m)
        if geom is None or geom.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {
                    "index": index,
                    "orientation": panel.orientation,
                    "segment_id": panel.segment_id,
                    "flux_kwh_per_kwp": round(panel.flux_kwh_per_kwp, 1),
                    "pitch_deg": round(panel.pitch_deg, 2),
                },
            }
        )
    return features


def panels_union(panels: list[PanelCandidate]) -> BaseGeometry | None:
    if not panels:
        return None
    geoms = [p.geom_m for p in panels]
    merged = geoms[0]
    for g in geoms[1:]:
        merged = merged.union(g)
    return merged


def apply_walkways(
    poly: BaseGeometry,
    walkway_width_m: float = 0.8,
    spacing_m: float = 6.0,
) -> BaseGeometry:
    """Cut maintenance access corridors out of a usable roof polygon.

    Spec 10.1 lists `R_walkways` as one of the four terms subtracted from the
    roof plane, and spec 11.3 requires the layout to avoid maintenance
    corridors. Corridors run across the panel rows (the short direction of the
    roof) and one corridor runs along the long axis.
    """
    if poly is None or poly.is_empty or walkway_width_m <= 0:
        return poly
    from shapely.ops import unary_union

    angle = _roof_axis_angle(poly)
    pivot = (poly.centroid.x, poly.centroid.y)
    rotated = _rotate(poly, -angle, pivot)
    minx, miny, maxx, maxy = rotated.bounds

    corridors: list[BaseGeometry] = []
    y = miny + spacing_m
    while y < maxy - 1.0:
        corridors.append(box(minx - 1.0, y - walkway_width_m / 2.0, maxx + 1.0, y + walkway_width_m / 2.0))
        y += spacing_m
    x_mid = (minx + maxx) / 2.0
    corridors.append(
        box(x_mid - walkway_width_m / 2.0, miny - 1.0, x_mid + walkway_width_m / 2.0, maxy + 1.0)
    )

    cut = unary_union(corridors)
    result = rotated.difference(cut)
    result = _rotate(result, angle, pivot)
    from shapely.validation import make_valid

    if not result.is_valid:
        result = make_valid(result)
    return result

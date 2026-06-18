"""Street segment slicing from parsed Between rules."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from shapely.geometry import LineString, MultiLineString
from shapely.ops import substring, transform

import geo_indices as gi
import tcl_highway_resolve as thr
import tcl_graph as tg
from tcl_graph import PathPick, StreetGraph

STREET_NOT_FOUND = 'STREET_NOT_FOUND'
INTERSECTION_NOT_FOUND = 'INTERSECTION_NOT_FOUND'
UNSUPPORTED_RULE_TYPE = 'UNSUPPORTED_RULE_TYPE'
GEOMETRY_ERROR = 'GEOMETRY_ERROR'
ZERO_SPAN = 'ZERO_SPAN'
DISCONNECTED_BLOCK = 'DISCONNECTED_BLOCK'

_ZERO_SPAN_DETAIL = 'anchor equals terminus; no mappable span'
_COLLAPSE_TOL_M = 1e-3
AMBIGUOUS_INTERSECTION = 'AMBIGUOUS_INTERSECTION'

BLOCK_FAMILY_RULES = frozenset({
    'block',
    'block_to_terminus',
    'parenthetical_block',
    'parenthetical_end_block',
    'parenthetical_dual_block',
    'parenthetical_to_terminus',
})

TANGENT_EPSILON_M = 1.0
COMPASS_UNIT = {
    'north': (0.0, 1.0),
    'south': (0.0, -1.0),
    'east': (1.0, 0.0),
    'west': (-1.0, 0.0),
}

SUPPORTED_RULE_TYPES = frozenset({
    'entire_length',
    'block',
    'block_to_terminus',
    'terminus_end_metric',
    'terminus_to_terminus',
    'parenthetical_block',
    'parenthetical_end_block',
    'parenthetical_dual_block',
    'parenthetical_to_terminus',
    'intersect_extension',
    'perfect_offset',
    'intersect_to_offset',
    'offset_to_intersect',
    'relative_extension',
    'offset_span',
    'dual_anchor',
})

_WEST_DIRS = frozenset({'west', 'northwest', 'southwest'})
_EAST_DIRS = frozenset({'east', 'northeast', 'southeast'})
_NORTH_DIRS = frozenset({'north', 'northeast', 'northwest'})
_SOUTH_DIRS = frozenset({'south', 'southeast', 'southwest'})


@dataclass
class SliceResult:
    geometry: LineString | MultiLineString | None
    reason_code: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason_code is None and self.geometry is not None


def _terminus_dist_on_line(line_m: LineString, direction: str) -> float:
    """Along-line distance to the point on *line_m* that lies farthest in *direction* (UTM)."""
    direction = str(direction).lower().strip()
    if line_m.length == 0:
        return 0.0

    n = 21
    samples = [
        line_m.interpolate(i / (n - 1) * line_m.length)
        for i in range(n)
    ]
    for coord in (line_m.coords[0], line_m.coords[-1]):
        from shapely.geometry import Point
        samples.append(Point(coord))

    if direction in _WEST_DIRS:
        pt = min(samples, key=lambda p: p.x)
        return line_m.project(pt)
    if direction in _EAST_DIRS:
        pt = max(samples, key=lambda p: p.x)
        return line_m.project(pt)
    if direction in _NORTH_DIRS:
        pt = max(samples, key=lambda p: p.y)
        return line_m.project(pt)
    if direction in _SOUTH_DIRS:
        pt = min(samples, key=lambda p: p.y)
        return line_m.project(pt)
    return line_m.project(max(samples, key=lambda p: p.x))


def _disambiguate_project_dist(
    projects: list[float], qualifier: str,
) -> float | None:
    ql = str(qualifier).lower()
    if 'wester' in ql or re.search(r'\bwest\b', ql):
        return min(projects)
    if 'easter' in ql or re.search(r'\beast\b', ql):
        return max(projects)
    if 'norther' in ql or re.search(r'\bnorth\b', ql):
        return max(projects)
    if 'souther' in ql or re.search(r'\bsouth\b', ql):
        return min(projects)
    return None


def intersection_dist_with_qualifier(
    highway: str,
    cross_street: str,
    line_m: LineString,
    qualifier: str | None,
) -> tuple[float, None] | tuple[None, str]:
    if not highway or not cross_street:
        return None, str(cross_street)
    ids = tg.resolve_intersection_ids(highway, cross_street)
    if not ids:
        return None, str(cross_street)
    match = gi.intersections_gdf[gi.intersections_gdf['INTERSECTION_ID'].isin(ids)]
    if match.empty:
        return None, str(cross_street)

    if len(match) == 1 or not qualifier:
        pt_m = transform(
            gi.project_to_meters, match.iloc[0].geometry.centroid,
        )
        return line_m.project(pt_m), None

    projects = [
        line_m.project(transform(gi.project_to_meters, g.centroid))
        for g in match.geometry
    ]
    chosen = _disambiguate_project_dist(projects, qualifier)
    if chosen is None:
        return None, 'parenthetical_ambiguous'
    return chosen, None


def _clamp_dist(line_m: LineString, dist: float) -> float:
    return max(0.0, min(line_m.length, dist))


def offset_sign(line_m: LineString, anchor_dist: float, direction: str) -> int:
    """+1 or -1: which way along the centreline matches the stated compass direction."""
    compass = COMPASS_UNIT.get(str(direction).lower())
    if compass is None:
        return 1

    length = line_m.length
    if length == 0:
        return 1

    eps = min(TANGENT_EPSILON_M, length / 2)
    d_lo = max(0.0, anchor_dist - eps)
    d_hi = min(length, anchor_dist + eps)
    if d_hi <= d_lo:
        return 1

    p_lo = line_m.interpolate(d_lo)
    p_hi = line_m.interpolate(d_hi)
    tangent = (p_hi.x - p_lo.x, p_hi.y - p_lo.y)
    dot = tangent[0] * compass[0] + tangent[1] * compass[1]
    if math.isclose(dot, 0.0, abs_tol=1e-6):
        return 1
    return 1 if dot >= 0 else -1


def signed_offset_dist(
    line_m: LineString, anchor_dist: float, distance_m: float, direction: str,
) -> float:
    sign = offset_sign(line_m, anchor_dist, direction)
    return _clamp_dist(line_m, anchor_dist + sign * distance_m)


def _line_m_and_dist_for_cross(
    highway: str, cross: str,
) -> tuple[LineString, float] | tuple[None, str]:
    """Centreline (local graph component when possible) and along-line distance."""
    graph = _street_graph(highway)
    pt_m = gi._intersection_point_meters(highway, cross)
    if pt_m is None:
        return None, str(cross)

    if graph is not None:
        ids = gi.find_intersection_ids(highway, cross)
        if ids:
            line_m = tg.component_linestring_m(graph, ids[0])
            if line_m is not None and not line_m.is_empty:
                return line_m, line_m.project(pt_m)

    line_m = gi.get_street_line_meters(highway)
    if line_m is None:
        return None, str(cross)
    return line_m, line_m.project(pt_m)


def _project_ids_on_line(
    line_m: LineString, highway: str, cross: str,
) -> list[float]:
    """Sorted along-line distances for every graph node matching *cross* on *highway*."""
    dists: list[float] = []
    seen: set[float] = set()
    for node_id in gi.find_intersection_ids(highway, cross):
        pt = tg.node_point_gps(node_id)
        if pt is None:
            continue
        pt_m = transform(gi.project_to_meters, pt)
        d = line_m.project(pt_m)
        key = round(d, 3)
        if key not in seen:
            seen.add(key)
            dists.append(d)
    if not dists:
        pt_m = gi._intersection_point_meters(highway, cross)
        if pt_m is not None:
            dists.append(line_m.project(pt_m))
    return sorted(dists)


def _span_from_multi_ids(
    line_m: LineString, highway: str, cross: str,
) -> tuple[float, float] | None:
    """When one cross name maps to multiple nodes, use min/max along-line span."""
    dists = _project_ids_on_line(line_m, highway, cross)
    if len(dists) >= 2 and dists[-1] - dists[0] > _COLLAPSE_TOL_M:
        return dists[0], dists[-1]
    return None


def _offset_point_dist(
    line_m: LineString, anchor_dist: float, distance_m: float, direction: str,
) -> float:
    """
    Along-line distance to 'a point {distance} {direction} of' anchor at *anchor_dist*.

    When the raw offset falls past the component end, use the inbound distance from
    the anchor (cul-de-sac / terminus clamp cases).
    """
    sign = offset_sign(line_m, anchor_dist, direction)
    raw = anchor_dist + sign * float(distance_m)
    length = line_m.length
    if -1e-6 <= raw <= length + 1e-6:
        return raw
    return max(0.0, anchor_dist - sign * float(distance_m))


def _slice_component_distances(
    highway: str, line_m: LineString, d0: float, d1: float,
) -> SliceResult:
    if math.isclose(d0, d1, abs_tol=_COLLAPSE_TOL_M):
        return SliceResult(None, ZERO_SPAN, _ZERO_SPAN_DETAIL)
    line_gps = transform(gi.project_to_gps, line_m)
    return slice_between_distances(line_gps, line_m, d0, d1)


def _graph_slice_between_crosses(
    highway: str, cross_a: str, cross_b: str,
) -> SliceResult | None:
    """Graph path between two cross streets when centreline projection collapses."""
    graph = _street_graph(highway)
    if graph is None:
        return None
    pick = tg.pick_intersection_pair(graph, highway, cross_a, cross_b)
    if pick is None or pick.length_m < _COLLAPSE_TOL_M:
        return None
    return _path_result_to_slice(pick)


def _recover_collapsed_offset_span(
    highway: str,
    line_m: LineString,
    d0: float,
    d1: float,
    *,
    cross_a: str | None = None,
    cross_b: str | None = None,
) -> SliceResult | None:
    """Try multi-node span or graph path when metric projection equals anchor."""
    if not math.isclose(d0, d1, abs_tol=_COLLAPSE_TOL_M):
        return None
    if cross_a and cross_b and cross_a.strip().lower() != cross_b.strip().lower():
        graph_hit = _graph_slice_between_crosses(highway, cross_a, cross_b)
        if graph_hit is not None and graph_hit.ok:
            return graph_hit
    for cross in (cross_a, cross_b):
        if not cross:
            continue
        span = _span_from_multi_ids(line_m, highway, cross)
        if span is not None:
            return _slice_component_distances(highway, line_m, span[0], span[1])
    return None


def _relative_extension_distances(
    line_m: LineString,
    base_dist: float,
    dist1: float,
    dist2: float,
    dir1: str,
) -> tuple[float, float]:
    sign = offset_sign(line_m, base_dist, dir1)
    raw_d0 = base_dist + sign * dist1
    raw_d1 = base_dist + sign * (dist1 + dist2)
    d0 = _clamp_dist(line_m, raw_d0)
    d1 = _clamp_dist(line_m, raw_d1)
    if math.isclose(d0, d1, abs_tol=1e-3):
        length = line_m.length
        if raw_d0 < -1e-6 and raw_d1 < -1e-6:
            d0 = 0.0
            d1 = min(length, dist1 + dist2)
        elif raw_d0 > length + 1e-6 and raw_d1 > length + 1e-6:
            span = dist1 + dist2
            d1 = length
            d0 = max(0.0, length - span)
    return d0, d1


@lru_cache(maxsize=32768)
def _intersection_dist_cached(
    highway: str, cross_street: str,
) -> tuple[float, None] | tuple[None, str]:
    line_m = gi.get_street_line_meters(highway)
    if line_m is None:
        return None, str(cross_street)
    pt_m = gi._intersection_point_meters(highway, cross_street)
    if pt_m is None:
        return None, str(cross_street)
    return line_m.project(pt_m), None


def intersection_dist_on_street(
    highway: str, cross_street: str, line_m: LineString,
) -> tuple[float, None] | tuple[None, str]:
    return _intersection_dist_cached(highway, cross_street)


def _street_graph(highway: str) -> StreetGraph | None:
    return gi.street_graphs.get(thr.tcl_lookup_key(highway))


def _path_result_to_slice(pick: PathPick) -> SliceResult:
    geom = tg.slice_path_between(pick.edges, pick.id_start, pick.id_end)
    if geom.is_empty or geom.length == 0:
        return SliceResult(None, GEOMETRY_ERROR, 'zero-length segment')
    return SliceResult(geom)


def _try_disjoint_block_slice(
    graph: StreetGraph,
    highway: str,
    cross_start: str,
    cross_end: str,
    *,
    start_qualifier: str | None = None,
    end_qualifier: str | None = None,
) -> SliceResult | None:
    geom = tg.slice_disjoint_block_paths(
        graph,
        highway,
        cross_start,
        cross_end,
        start_qualifier=start_qualifier,
        end_qualifier=end_qualifier,
    )
    if geom is None or geom.is_empty:
        return None
    return SliceResult(geom)


def _block_pair_failure(
    highway: str, cross_a: str, cross_b: str,
) -> SliceResult:
    ids_a = gi.find_intersection_ids(highway, cross_a)
    ids_b = gi.find_intersection_ids(highway, cross_b)
    if not ids_a:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'start_intersection={cross_a}',
        )
    if not ids_b:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'end_intersection={cross_b}',
        )
    return SliceResult(
        None, DISCONNECTED_BLOCK,
        f'{cross_a}({len(ids_a)} ids) to {cross_b}({len(ids_b)} ids)',
    )


def _pick_qualified_block(
    graph: StreetGraph,
    highway: str,
    cross_start: str,
    cross_end: str,
    *,
    start_qualifier: str | None = None,
    end_qualifier: str | None = None,
    bylaw_highway: str | None = None,
) -> PathPick | SliceResult:
    start_ids = gi.find_intersection_ids(highway, cross_start)
    end_ids = gi.find_intersection_ids(highway, cross_end)
    if not start_ids:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'start_intersection={cross_start}',
        )
    if not end_ids:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'end_intersection={cross_end}',
        )

    leg_compass = thr.highway_leg_compass(bylaw_highway or '')
    pick, tied = tg.pick_qualified_block_path(
        graph,
        highway,
        cross_start,
        cross_end,
        start_qualifier=start_qualifier,
        end_qualifier=end_qualifier,
        leg_compass=leg_compass,
    )
    if pick is None:
        if not tied:
            disjoint = _try_disjoint_block_slice(
                graph,
                highway,
                cross_start,
                cross_end,
                start_qualifier=start_qualifier,
                end_qualifier=end_qualifier,
            )
            if disjoint is not None:
                return disjoint
        return _block_pair_failure(highway, cross_start, cross_end)
    if tied:
        return SliceResult(
            None, AMBIGUOUS_INTERSECTION,
            f'{cross_start} to {cross_end}',
        )
    return pick


def slice_block_path(
    highway: str,
    cross_start: str,
    cross_end: str,
    *,
    start_qualifier: str | None = None,
    end_qualifier: str | None = None,
    bylaw_highway: str | None = None,
) -> SliceResult:
    graph = _street_graph(highway)
    if graph is None:
        return SliceResult(None, STREET_NOT_FOUND, str(highway))

    if start_qualifier or end_qualifier:
        picked = _pick_qualified_block(
            graph, highway, cross_start, cross_end,
            start_qualifier=start_qualifier,
            end_qualifier=end_qualifier,
            bylaw_highway=bylaw_highway,
        )
        if isinstance(picked, SliceResult):
            return picked
        return _path_result_to_slice(picked)

    pick = tg.pick_intersection_pair(graph, highway, cross_start, cross_end)
    if pick is None:
        ids_a = gi.find_intersection_ids(highway, cross_start)
        ids_b = gi.find_intersection_ids(highway, cross_end)
        if not ids_a:
            return SliceResult(
                None, INTERSECTION_NOT_FOUND, f'start_intersection={cross_start}',
            )
        if not ids_b:
            return SliceResult(
                None, INTERSECTION_NOT_FOUND, f'end_intersection={cross_end}',
            )
        kind = tg.classify_intersection_pair_failure(
            graph, highway, cross_start, cross_end,
        )
        if kind == 'tied':
            return SliceResult(
                None, AMBIGUOUS_INTERSECTION,
                f'{cross_start} to {cross_end}',
            )
        disjoint = _try_disjoint_block_slice(
            graph, highway, cross_start, cross_end,
            start_qualifier=start_qualifier,
            end_qualifier=end_qualifier,
        )
        if disjoint is not None:
            return disjoint
        return _block_pair_failure(highway, cross_start, cross_end)
    return _path_result_to_slice(pick)


def slice_block_to_terminus_path(
    highway: str,
    cross_start: str,
    terminus_dir: str,
    *,
    start_qualifier: str | None = None,
) -> SliceResult:
    graph = _street_graph(highway)
    if graph is None:
        return SliceResult(None, STREET_NOT_FOUND, str(highway))

    start_ids = gi.find_intersection_ids(highway, cross_start)
    if not start_ids:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'start_intersection={cross_start}',
        )

    best_path: list | None = None
    best_start: int | None = None
    best_end: int | None = None
    best_len = -1.0

    for id_s in start_ids:
        result = tg.path_from_start_to_terminus(
            graph, id_s, terminus_dir, _terminus_dist_on_line,
        )
        if result is None:
            continue
        path, end_id = result
        line_m = tg.path_to_linestring(path, id_s, end_id, use_meters=True)
        length = line_m.length

        if start_qualifier:
            picked_s = tg.pick_id_with_qualifier(
                highway, cross_start, start_qualifier, line_m,
            )
            if picked_s is None:
                return SliceResult(
                    None, AMBIGUOUS_INTERSECTION,
                    f'start_intersection={cross_start}',
                )
            if picked_s != id_s:
                continue

        if length > best_len:
            best_len = length
            best_path = path
            best_start = id_s
            best_end = end_id

    if best_len < 1e-3:
        for id_s in start_ids:
            far_id = tg.farthest_node_in_component(graph, id_s)
            if far_id is None or far_id == id_s:
                continue
            path = tg.shortest_path(graph, id_s, far_id)
            if path is None:
                continue
            line_m = tg.path_to_linestring(path, id_s, far_id, use_meters=True)
            if line_m.length > best_len:
                best_len = line_m.length
                best_path = path
                best_start = id_s
                best_end = far_id

    if best_path is None or best_start is None or best_end is None:
        return SliceResult(
            None, DISCONNECTED_BLOCK,
            f'{cross_start} to terminus {terminus_dir}',
        )

    geom = tg.slice_path_between(best_path, best_start, best_end)
    if geom.is_empty or geom.length == 0:
        return SliceResult(None, ZERO_SPAN, _ZERO_SPAN_DETAIL)
    return SliceResult(geom)


def slice_between_distances(
    line_gps: LineString, line_m: LineString, d0: float, d1: float,
) -> SliceResult:
    if math.isclose(d0, d1, abs_tol=_COLLAPSE_TOL_M):
        return SliceResult(None, ZERO_SPAN, _ZERO_SPAN_DETAIL)

    lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
    sliced_m = substring(line_m, lo, hi)
    if sliced_m.is_empty:
        return SliceResult(None, GEOMETRY_ERROR, 'empty geometry')

    return SliceResult(transform(gi.project_to_gps, sliced_m))


def slice_street(
    highway,
    parsed_data,
    *,
    bylaw_highway: str | None = None,
) -> SliceResult:
    rule_type = parsed_data.get('rule_type')
    display_highway = bylaw_highway or highway

    if rule_type not in SUPPORTED_RULE_TYPES:
        detail = rule_type if rule_type else f"rule_type={rule_type!r}"
        return SliceResult(None, UNSUPPORTED_RULE_TYPE, detail)

    if rule_type in BLOCK_FAMILY_RULES:
        if _street_graph(highway) is None:
            return SliceResult(None, STREET_NOT_FOUND, str(display_highway))
        try:
            if rule_type == 'block':
                return slice_block_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('end_intersection'),
                    bylaw_highway=display_highway,
                )
            if rule_type == 'block_to_terminus':
                return slice_block_to_terminus_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('terminus_direction', ''),
                )
            if rule_type == 'parenthetical_block':
                return slice_block_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('end_intersection'),
                    start_qualifier=parsed_data.get('start_intersection_qualifier'),
                    bylaw_highway=display_highway,
                )
            if rule_type == 'parenthetical_end_block':
                return slice_block_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('end_intersection'),
                    end_qualifier=parsed_data.get('end_intersection_qualifier'),
                    bylaw_highway=display_highway,
                )
            if rule_type == 'parenthetical_dual_block':
                return slice_block_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('end_intersection'),
                    start_qualifier=parsed_data.get('start_intersection_qualifier'),
                    end_qualifier=parsed_data.get('end_intersection_qualifier'),
                    bylaw_highway=display_highway,
                )
            if rule_type == 'parenthetical_to_terminus':
                return slice_block_to_terminus_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('terminus_direction', ''),
                    start_qualifier=parsed_data.get('start_intersection_qualifier'),
                )
        except Exception as e:
            return SliceResult(None, GEOMETRY_ERROR, str(e)[:500])

    street_line_gps = gi.get_local_street_geometry(highway)
    if not street_line_gps:
        return SliceResult(None, STREET_NOT_FOUND, str(display_highway))

    try:
        street_line_m = gi.get_street_line_meters(highway)

        if rule_type == 'entire_length':
            return SliceResult(street_line_gps)

        if rule_type == 'terminus_to_terminus':
            d0 = _terminus_dist_on_line(
                street_line_m, parsed_data.get('terminus_start_dir', ''),
            )
            d1 = _terminus_dist_on_line(
                street_line_m, parsed_data.get('terminus_end_dir', ''),
            )
            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

        if rule_type == 'terminus_end_metric':
            terminus_street = parsed_data.get('terminus_street')
            line_m, err = _line_m_and_dist_for_cross(highway, terminus_street)
            if line_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"terminus_street={err}",
                )
            d0 = _terminus_dist_on_line(line_m, parsed_data.get('terminus_direction', ''))
            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d1 = _offset_point_dist(line_m, d0, distance, direction)
            return _slice_component_distances(highway, line_m, d0, d1)

        if rule_type in ('perfect_offset', 'intersect_extension'):
            start_intersection = parsed_data.get('start_intersection')
            line_m, anchor = _line_m_and_dist_for_cross(highway, start_intersection)
            if line_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={anchor}",
                )
            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d0 = anchor
            d1 = _offset_point_dist(line_m, anchor, distance, direction)
            recovered = _recover_collapsed_offset_span(
                highway, line_m, d0, d1, cross_a=start_intersection,
            )
            if recovered is not None:
                return recovered
            return _slice_component_distances(highway, line_m, d0, d1)

        if rule_type == 'intersect_to_offset':
            start_intersection = parsed_data.get('start_intersection')
            offset_intersection = parsed_data.get('offset_intersection')
            line_m, d0 = _line_m_and_dist_for_cross(highway, start_intersection)
            if line_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={d0}",
                )
            pt_m = gi._intersection_point_meters(highway, offset_intersection)
            if pt_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND,
                    f"offset_intersection={offset_intersection}",
                )
            anchor_dist = line_m.project(pt_m)
            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d1 = _offset_point_dist(line_m, anchor_dist, distance, direction)
            recovered = _recover_collapsed_offset_span(
                highway, line_m, d0, d1,
                cross_a=start_intersection,
                cross_b=offset_intersection,
            )
            if recovered is not None:
                return recovered
            return _slice_component_distances(highway, line_m, d0, d1)

        if rule_type == 'offset_to_intersect':
            start_intersection = parsed_data.get('start_intersection')
            end_intersection = parsed_data.get('end_intersection')
            line_m, anchor = _line_m_and_dist_for_cross(highway, start_intersection)
            if line_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={anchor}",
                )
            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d0 = _offset_point_dist(line_m, anchor, distance, direction)
            pt_m = gi._intersection_point_meters(highway, end_intersection)
            if pt_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"end_intersection={end_intersection}",
                )
            d1 = line_m.project(pt_m)
            recovered = _recover_collapsed_offset_span(
                highway, line_m, d0, d1,
                cross_a=start_intersection,
                cross_b=end_intersection,
            )
            if recovered is not None:
                return recovered
            return _slice_component_distances(highway, line_m, d0, d1)

        if rule_type == 'relative_extension':
            start_intersection = parsed_data.get('start_intersection')
            line_m, base_dist = _line_m_and_dist_for_cross(highway, start_intersection)
            if line_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={base_dist}",
                )

            dist1 = float(parsed_data.get('dist1', 0))
            dist2 = float(parsed_data.get('dist2', 0))
            dir1 = parsed_data.get('dir1', '')
            d0, d1 = _relative_extension_distances(line_m, base_dist, dist1, dist2, dir1)
            recovered = _recover_collapsed_offset_span(
                highway, line_m, d0, d1, cross_a=start_intersection,
            )
            if recovered is not None:
                return recovered
            return _slice_component_distances(highway, line_m, d0, d1)

        if rule_type == 'offset_span':
            start_intersection = parsed_data.get('start_intersection')
            line_m, base_dist = _line_m_and_dist_for_cross(highway, start_intersection)
            if line_m is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={base_dist}",
                )
            dist1 = float(parsed_data.get('dist1', 0))
            dist2 = float(parsed_data.get('dist2', 0))
            dir1 = parsed_data.get('dir1', '')
            dir2 = parsed_data.get('dir2', dir1)
            d0 = _offset_point_dist(line_m, base_dist, dist1, dir1)
            d1 = _offset_point_dist(line_m, base_dist, dist2, dir2)
            lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
            recovered = _recover_collapsed_offset_span(
                highway, line_m, lo, hi, cross_a=start_intersection,
            )
            if recovered is not None:
                return recovered
            return _slice_component_distances(highway, line_m, lo, hi)

        if rule_type == 'dual_anchor':
            start_intersection = parsed_data.get('start_intersection')
            end_intersection = parsed_data.get('end_intersection')
            line0, anchor0 = _line_m_and_dist_for_cross(highway, start_intersection)
            if line0 is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={anchor0}",
                )
            d0 = _offset_point_dist(
                line0, anchor0,
                float(parsed_data.get('dist1', 0)),
                parsed_data.get('dir1', ''),
            )

            line1, anchor1 = _line_m_and_dist_for_cross(highway, end_intersection)
            if line1 is None:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"end_intersection={anchor1}",
                )
            d1 = _offset_point_dist(
                line1, anchor1,
                float(parsed_data.get('dist2', 0)),
                parsed_data.get('dir2', ''),
            )

            if line0.equals(line1):
                lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
                recovered = _recover_collapsed_offset_span(
                    highway, line0, lo, hi,
                    cross_a=start_intersection,
                    cross_b=end_intersection,
                )
                if recovered is not None:
                    return recovered
                return _slice_component_distances(highway, line0, lo, hi)

            pt0 = line0.interpolate(max(0.0, min(d0, line0.length)))
            pt1 = line1.interpolate(max(0.0, min(d1, line1.length)))
            d0m = street_line_m.project(pt0)
            d1m = street_line_m.project(pt1)
            recovered = _recover_collapsed_offset_span(
                highway, street_line_m, d0m, d1m,
                cross_a=start_intersection,
                cross_b=end_intersection,
            )
            if recovered is not None:
                return recovered
            return slice_between_distances(street_line_gps, street_line_m, d0m, d1m)

    except Exception as e:
        return SliceResult(None, GEOMETRY_ERROR, str(e)[:500])

    return SliceResult(None, UNSUPPORTED_RULE_TYPE, f"rule_type={rule_type!r}")

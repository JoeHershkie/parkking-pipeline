import math
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

import geopandas as gpd
import pandas as pd
import pyproj
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, substring, transform

from failure_ledger import clear_stage, record_failure
import geo_cache as gc
import intersection_index as ix_index
from parse_format import _parse_valid_flag, highway_from_row, row_to_parsed
from tcl_highway_key import tcl_highway_key
from paths import data_path
from schedule_format import schedule_from_json
import tcl_graph as tg
from tcl_graph import PathPick, StreetGraph

STREET_NOT_FOUND = 'STREET_NOT_FOUND'
INTERSECTION_NOT_FOUND = 'INTERSECTION_NOT_FOUND'
UNSUPPORTED_RULE_TYPE = 'UNSUPPORTED_RULE_TYPE'
GEOMETRY_ERROR = 'GEOMETRY_ERROR'
DISCONNECTED_BLOCK = 'DISCONNECTED_BLOCK'
AMBIGUOUS_INTERSECTION = 'AMBIGUOUS_INTERSECTION'

BLOCK_FAMILY_RULES = frozenset({
    'block',
    'block_to_terminus',
    'parenthetical_block',
    'parenthetical_end_block',
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
    'terminus_to_terminus',
    'parenthetical_block',
    'parenthetical_end_block',
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

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
project_to_gps = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform

street_index: dict[str, LineString] = {}
street_metre_index: dict[str, LineString] = {}
street_graphs: dict[str, StreetGraph] = {}
intersections_gdf: gpd.GeoDataFrame | None = None
_timing: dict[str, float] = {}


@dataclass
class SliceResult:
    geometry: LineString | MultiLineString | None
    reason_code: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason_code is None and self.geometry is not None


def _geo_read_kwargs() -> dict:
    try:
        import pyogrio  # noqa: F401
        return {'engine': 'pyogrio'}
    except ImportError:
        return {}


def _geo_read_engine_label() -> str:
    return 'pyogrio' if _geo_read_kwargs() else 'fiona'


def _merge_street_geoms(geoms: list) -> LineString | None:
    if not geoms:
        return None
    merged = linemerge(MultiLineString(geoms))
    if merged.geom_type == 'MultiLineString':
        return max(merged.geoms, key=lambda x: x.length)
    return merged


def _build_street_index(gdf: gpd.GeoDataFrame) -> dict[str, LineString]:
    """Pre-merge TCL chunks per legal street name (lowercased key)."""
    name_lower = gdf['LINEAR_NAME_FULL_LEGAL'].str.lower()
    index: dict[str, LineString] = {}
    for s_name, group in gdf.groupby(name_lower, sort=False):
        geoms = []
        for g in group.geometry:
            if g.geom_type == 'LineString':
                geoms.append(g)
            elif g.geom_type == 'MultiLineString':
                geoms.extend(g.geoms)
        line = _merge_street_geoms(geoms)
        if line is not None:
            index[s_name] = line
    return index


def _load_tcl() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    read_kwargs = _geo_read_kwargs()
    engine = _geo_read_engine_label()
    print(f"   GeoJSON reader: {engine}")
    print("1. Loading Local Intersection Database...")
    t0 = time.perf_counter()
    ix_gdf = gpd.read_file(data_path('tcl_intersections.geojson'), **read_kwargs)
    _timing['intersections_load'] = time.perf_counter() - t0

    print("2. Loading Local Street Database (This might take a moment)...")
    t0 = time.perf_counter()
    st_gdf = gpd.read_file(data_path('tcl_streets.geojson'), **read_kwargs)
    _timing['streets_load'] = time.perf_counter() - t0
    return ix_gdf, st_gdf


def _init_indexes(ix_gdf: gpd.GeoDataFrame, st_gdf: gpd.GeoDataFrame) -> None:
    global intersections_gdf, street_index, street_graphs
    intersections_gdf = ix_gdf
    tg.configure_intersections(ix_gdf)

    streets_path = data_path('tcl_streets.geojson')
    cached_graphs = gc.load_street_graphs(streets_path)
    if cached_graphs is not None:
        street_graphs = cached_graphs
        _timing['street_graphs'] = 0.0
        _timing['street_graphs_cache'] = 1.0
        print(f"   Loaded {len(street_graphs)} street graphs from cache.")
    else:
        print("   Building street graphs...")
        t0 = time.perf_counter()
        street_graphs = tg.build_street_graphs(st_gdf)
        _timing['street_graphs'] = time.perf_counter() - t0
        gc.save_street_graphs(streets_path, street_graphs)
        print(f"   Graphs for {len(street_graphs)} streets (saved to cache).")

    print("   Building street index (legacy merge-longest)...")
    t0 = time.perf_counter()
    street_index = _build_street_index(st_gdf)
    _timing['street_index'] = time.perf_counter() - t0
    print(f"   Indexed {len(street_index)} streets.")


_ix_gdf, _st_gdf = _load_tcl()
_init_indexes(_ix_gdf, _st_gdf)
del _ix_gdf, _st_gdf


# --- HELPERS ---


def _intersection_mask(street_1: str, street_2: str) -> pd.Series:
    """Boolean mask over intersections_gdf rows (legacy helper for analysis scripts)."""
    ids = tg.resolve_intersection_ids(street_1, street_2)
    return intersections_gdf['INTERSECTION_ID'].isin(ids)


@lru_cache(maxsize=32768)
def find_intersection(street_1: str, street_2: str) -> Point | None:
    if not street_1 or not street_2:
        return None
    ids = tg.resolve_intersection_ids(street_1, street_2)
    if not ids:
        return None
    pt = tg.node_point_gps(ids[0])
    return pt


def _terminus_dist_on_line(line_m: LineString, direction: str) -> float:
    """Centreline distance to the end of the line in the stated compass direction."""
    direction = str(direction).lower().strip()
    if line_m.length == 0:
        return 0.0

    samples = 21
    dists = [
        line_m.interpolate(i / (samples - 1) * line_m.length)
        for i in range(samples)
    ]
    projects = [line_m.project(p) for p in dists]

    if direction in _WEST_DIRS:
        return min(projects)
    if direction in _EAST_DIRS:
        return max(projects)
    if direction in _NORTH_DIRS:
        north_pt = max(dists, key=lambda p: p.y)
        return line_m.project(north_pt)
    if direction in _SOUTH_DIRS:
        south_pt = min(dists, key=lambda p: p.y)
        return line_m.project(south_pt)
    return max(projects)


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
    match = intersections_gdf[intersections_gdf['INTERSECTION_ID'].isin(ids)]
    if match.empty:
        return None, str(cross_street)

    if len(match) == 1 or not qualifier:
        pt_m = transform(
            project_to_meters, match.iloc[0].geometry.centroid,
        )
        return line_m.project(pt_m), None

    projects = [
        line_m.project(transform(project_to_meters, g.centroid))
        for g in match.geometry
    ]
    chosen = _disambiguate_project_dist(projects, qualifier)
    if chosen is None:
        return None, 'parenthetical_ambiguous'
    return chosen, None


def get_local_street_geometry(street_name: str) -> LineString | None:
    """O(1) lookup in pre-built street index."""
    return street_index.get(tcl_highway_key(street_name))


def get_street_line_meters(highway: str) -> LineString | None:
    """Cached EPSG:32617 centreline for a highway."""
    s_name = tcl_highway_key(highway)
    cached = street_metre_index.get(s_name)
    if cached is not None:
        return cached
    street_line_gps = get_local_street_geometry(highway)
    if street_line_gps is None:
        return None
    street_line_m = transform(project_to_meters, street_line_gps)
    street_metre_index[s_name] = street_line_m
    return street_line_m


@lru_cache(maxsize=32768)
def _intersection_point_meters(highway: str, cross_street: str) -> Point | None:
    pt_gps = find_intersection(highway, cross_street)
    if pt_gps is None:
        return None
    return transform(project_to_meters, pt_gps)


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


@lru_cache(maxsize=32768)
def _intersection_dist_cached(
    highway: str, cross_street: str,
) -> tuple[float, None] | tuple[None, str]:
    line_m = get_street_line_meters(highway)
    if line_m is None:
        return None, str(cross_street)
    pt_m = _intersection_point_meters(highway, cross_street)
    if pt_m is None:
        return None, str(cross_street)
    return line_m.project(pt_m), None


def intersection_dist_on_street(
    highway: str, cross_street: str, line_m: LineString,
) -> tuple[float, None] | tuple[None, str]:
    return _intersection_dist_cached(highway, cross_street)


def find_intersection_ids(highway: str, cross: str) -> list[int]:
    return tg.resolve_intersection_ids(highway, cross)


def _street_graph(highway: str) -> StreetGraph | None:
    return street_graphs.get(tcl_highway_key(highway))


def _path_result_to_slice(pick: PathPick) -> SliceResult:
    geom = tg.slice_path_between(pick.edges, pick.id_start, pick.id_end)
    if geom.is_empty or geom.length == 0:
        return SliceResult(None, GEOMETRY_ERROR, 'zero-length segment')
    return SliceResult(geom)


def _block_pair_failure(
    highway: str, cross_a: str, cross_b: str,
) -> SliceResult:
    ids_a = find_intersection_ids(highway, cross_a)
    ids_b = find_intersection_ids(highway, cross_b)
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
) -> PathPick | SliceResult:
    start_ids = find_intersection_ids(highway, cross_start)
    end_ids = find_intersection_ids(highway, cross_end)
    if not start_ids:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'start_intersection={cross_start}',
        )
    if not end_ids:
        return SliceResult(
            None, INTERSECTION_NOT_FOUND, f'end_intersection={cross_end}',
        )

    best: PathPick | None = None
    tied = False

    for id_s in start_ids:
        for id_e in end_ids:
            if id_s == id_e:
                continue
            path = tg.shortest_path(graph, id_s, id_e)
            if path is None:
                continue
            line_m = tg.path_to_linestring(path, id_s, id_e, use_meters=True)

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

            if end_qualifier:
                picked_e = tg.pick_id_with_qualifier(
                    highway, cross_end, end_qualifier, line_m,
                )
                if picked_e is None:
                    return SliceResult(
                        None, AMBIGUOUS_INTERSECTION,
                        f'end_intersection={cross_end}',
                    )
                if picked_e != id_e:
                    continue

            length = tg.path_length_m(path)
            candidate = PathPick(id_s, id_e, path, length)
            if best is None:
                best = candidate
                tied = False
                continue
            if len(path) < len(best.edges):
                best = candidate
                tied = False
            elif len(path) == len(best.edges):
                if length < best.length_m - 1e-3:
                    best = candidate
                    tied = False
                elif abs(length - best.length_m) < 1e-3:
                    tied = True

    if best is None:
        return _block_pair_failure(highway, cross_start, cross_end)
    if tied:
        return SliceResult(
            None, AMBIGUOUS_INTERSECTION,
            f'{cross_start} to {cross_end}',
        )
    return best


def slice_block_path(
    highway: str,
    cross_start: str,
    cross_end: str,
    *,
    start_qualifier: str | None = None,
    end_qualifier: str | None = None,
) -> SliceResult:
    graph = _street_graph(highway)
    if graph is None:
        return SliceResult(None, STREET_NOT_FOUND, str(highway))

    if start_qualifier or end_qualifier:
        picked = _pick_qualified_block(
            graph, highway, cross_start, cross_end,
            start_qualifier=start_qualifier,
            end_qualifier=end_qualifier,
        )
        if isinstance(picked, SliceResult):
            return picked
        return _path_result_to_slice(picked)

    pick = tg.pick_intersection_pair(graph, highway, cross_start, cross_end)
    if pick is None:
        ids_a = find_intersection_ids(highway, cross_start)
        ids_b = find_intersection_ids(highway, cross_end)
        if not ids_a:
            return SliceResult(
                None, INTERSECTION_NOT_FOUND, f'start_intersection={cross_start}',
            )
        if not ids_b:
            return SliceResult(
                None, INTERSECTION_NOT_FOUND, f'end_intersection={cross_end}',
            )
        if ids_a and ids_b:
            return SliceResult(
                None, AMBIGUOUS_INTERSECTION,
                f'{cross_start} to {cross_end}',
            )
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

    start_ids = find_intersection_ids(highway, cross_start)
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

    if best_path is None or best_start is None or best_end is None:
        return SliceResult(
            None, DISCONNECTED_BLOCK,
            f'{cross_start} to terminus {terminus_dir}',
        )

    geom = tg.slice_path_between(best_path, best_start, best_end)
    if geom.is_empty or geom.length == 0:
        return SliceResult(None, GEOMETRY_ERROR, 'zero-length segment')
    return SliceResult(geom)


def slice_between_distances(
    line_gps: LineString, line_m: LineString, d0: float, d1: float,
) -> SliceResult:
    if math.isclose(d0, d1, abs_tol=1e-3):
        return SliceResult(None, GEOMETRY_ERROR, 'zero-length segment')

    lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
    sliced_m = substring(line_m, lo, hi)
    if sliced_m.is_empty:
        return SliceResult(None, GEOMETRY_ERROR, 'empty geometry')

    return SliceResult(transform(project_to_gps, sliced_m))


# --- THE GEOMETRY ENGINE ---


def slice_street(highway, parsed_data) -> SliceResult:
    rule_type = parsed_data.get('rule_type')

    if rule_type not in SUPPORTED_RULE_TYPES:
        detail = rule_type if rule_type else f"rule_type={rule_type!r}"
        return SliceResult(None, UNSUPPORTED_RULE_TYPE, detail)

    if rule_type in BLOCK_FAMILY_RULES:
        if _street_graph(highway) is None:
            return SliceResult(None, STREET_NOT_FOUND, str(highway))
        try:
            if rule_type == 'block':
                return slice_block_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('end_intersection'),
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
                )
            if rule_type == 'parenthetical_end_block':
                return slice_block_path(
                    highway,
                    parsed_data.get('start_intersection'),
                    parsed_data.get('end_intersection'),
                    end_qualifier=parsed_data.get('end_intersection_qualifier'),
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

    street_line_gps = get_local_street_geometry(highway)
    if not street_line_gps:
        return SliceResult(None, STREET_NOT_FOUND, str(highway))

    try:
        street_line_m = get_street_line_meters(highway)

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

        if rule_type in ('perfect_offset', 'intersect_extension'):
            start_intersection = parsed_data.get('start_intersection')
            d0, err = intersection_dist_on_street(highway, start_intersection, street_line_m)
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={err}",
                )

            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d1 = signed_offset_dist(street_line_m, d0, distance, direction)
            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

        if rule_type == 'intersect_to_offset':
            start_intersection = parsed_data.get('start_intersection')
            d0, err = intersection_dist_on_street(highway, start_intersection, street_line_m)
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={err}",
                )

            offset_intersection = parsed_data.get('offset_intersection')
            anchor_dist, err = intersection_dist_on_street(
                highway, offset_intersection, street_line_m,
            )
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"offset_intersection={err}",
                )

            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d1 = signed_offset_dist(street_line_m, anchor_dist, distance, direction)
            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

        if rule_type == 'offset_to_intersect':
            start_intersection = parsed_data.get('start_intersection')
            anchor_dist, err = intersection_dist_on_street(
                highway, start_intersection, street_line_m,
            )
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={err}",
                )

            distance = float(parsed_data.get('distance', 0))
            direction = parsed_data.get('direction', '')
            d0 = signed_offset_dist(street_line_m, anchor_dist, distance, direction)

            end_intersection = parsed_data.get('end_intersection')
            d1, err = intersection_dist_on_street(highway, end_intersection, street_line_m)
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"end_intersection={err}",
                )

            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

        if rule_type == 'relative_extension':
            start_intersection = parsed_data.get('start_intersection')
            base_dist, err = intersection_dist_on_street(
                highway, start_intersection, street_line_m,
            )
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={err}",
                )

            dist1 = float(parsed_data.get('dist1', 0))
            dist2 = float(parsed_data.get('dist2', 0))
            dir1 = parsed_data.get('dir1', '')
            sign = offset_sign(street_line_m, base_dist, dir1)
            d0 = _clamp_dist(street_line_m, base_dist + sign * dist1)
            d1 = _clamp_dist(street_line_m, base_dist + sign * (dist1 + dist2))
            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

        if rule_type == 'offset_span':
            start_intersection = parsed_data.get('start_intersection')
            base_dist, err = intersection_dist_on_street(
                highway, start_intersection, street_line_m,
            )
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={err}",
                )

            dist1 = float(parsed_data.get('dist1', 0))
            dist2 = float(parsed_data.get('dist2', 0))
            dir1 = parsed_data.get('dir1', '')
            dir2 = parsed_data.get('dir2', dir1)
            sign1 = offset_sign(street_line_m, base_dist, dir1)
            sign2 = offset_sign(street_line_m, base_dist, dir2)
            d0 = _clamp_dist(street_line_m, base_dist + sign1 * dist1)
            d1 = _clamp_dist(street_line_m, base_dist + sign2 * dist2)
            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

        if rule_type == 'dual_anchor':
            start_intersection = parsed_data.get('start_intersection')
            anchor0, err = intersection_dist_on_street(
                highway, start_intersection, street_line_m,
            )
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"start_intersection={err}",
                )
            d0 = signed_offset_dist(
                street_line_m, anchor0,
                float(parsed_data.get('dist1', 0)),
                parsed_data.get('dir1', ''),
            )

            end_intersection = parsed_data.get('end_intersection')
            anchor1, err = intersection_dist_on_street(
                highway, end_intersection, street_line_m,
            )
            if err:
                return SliceResult(
                    None, INTERSECTION_NOT_FOUND, f"end_intersection={err}",
                )
            d1 = signed_offset_dist(
                street_line_m, anchor1,
                float(parsed_data.get('dist2', 0)),
                parsed_data.get('dir2', ''),
            )
            return slice_between_distances(street_line_gps, street_line_m, d0, d1)

    except Exception as e:
        return SliceResult(None, GEOMETRY_ERROR, str(e)[:500])

    return SliceResult(None, UNSUPPORTED_RULE_TYPE, f"rule_type={rule_type!r}")


def _row_series_from_values(columns: pd.Index, values: tuple) -> pd.Series:
    return pd.Series(dict(zip(columns, values, strict=True)))


def _process_geo_row(args: tuple[pd.Index, tuple]) -> tuple[dict | None, dict | None]:
    """Returns (success_payload, failure_record)."""
    columns, values = args
    row_s = _row_series_from_values(columns, values)
    row_id = row_s['_id']
    highway = highway_from_row(row_s)
    between = row_s['Between']
    display_highway = row_s.get('Highway', highway)

    def failure(reason_code: str, detail: str) -> tuple[None, dict]:
        return None, {
            'row_id': row_id,
            'reason_code': reason_code,
            'detail': detail,
            'highway': display_highway,
            'between': between,
        }

    if 'parse_valid' in row_s.index and not _parse_valid_flag(row_s.get('parse_valid')):
        detail = str(row_s.get('parse_error') or 'parse_valid is false').strip()
        return failure(GEOMETRY_ERROR, detail or 'parse_valid is false')

    parsed = row_to_parsed(row_s)
    if not parsed.get('rule_type'):
        return failure(GEOMETRY_ERROR, 'missing or empty rule_type')

    try:
        result = slice_street(highway, parsed)
    except Exception as e:
        return failure(GEOMETRY_ERROR, str(e)[:500])

    if result.ok and not result.geometry.is_empty:
        max_period = row_s.get('Maximum Period Permitted')
        if pd.isna(max_period):
            max_period = None
        max_minutes = row_s.get('max_minutes')
        if pd.isna(max_minutes):
            max_minutes = None
        schedule = schedule_from_json(row_s.get('schedule_json'))
        props = {
            'Highway': display_highway,
            'Rule': row_s['Prohibited Times and/or Days'],
            'schedule_category': row_s.get('schedule_category'),
            'Side': row_s.get('Side'),
            'max': max_period,
            'maxMinutes': max_minutes,
            'schedule': schedule,
            'geometry': result.geometry,
        }
        return props, None

    if result.reason_code:
        return failure(result.reason_code, result.detail)

    return failure(GEOMETRY_ERROR, 'empty geometry')


_CROSS_STREET_KEYS = (
    'start_intersection',
    'end_intersection',
    'offset_intersection',
)


def _intersection_lookup_pairs(df: pd.DataFrame) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for _, row in df.iterrows():
        highway = highway_from_row(row)
        if not highway:
            continue
        parsed = row_to_parsed(row)
        for key in _CROSS_STREET_KEYS:
            cross = parsed.get(key)
            if cross:
                pairs.append((highway, str(cross)))
    return pairs


def warm_intersection_index_from_dataframe(df: pd.DataFrame) -> int:
    """Pre-index intersection search tokens used by rows in *df*."""
    pairs = _intersection_lookup_pairs(df)
    tokens = ix_index.collect_tokens_from_pairs(pairs)
    csv_path = data_path('parsed_successes.csv')
    ix_path = data_path('tcl_intersections.geojson')

    cached = gc.load_intersection_postings(ix_path, csv_path)
    if cached is not None:
        ix_index.install_postings(cached)
        _timing['intersection_warm_cache'] = 1.0
        return sum(1 for t in tokens if t in cached)

    warmed = ix_index.warm_tokens(tokens)
    gc.save_intersection_postings(ix_path, csv_path, ix_index.postings_snapshot())
    return warmed


def _geo_batch_limit(df: pd.DataFrame) -> pd.DataFrame:
    limit = os.environ.get('GEO_LIMIT', '').strip()
    if limit:
        return df.head(int(limit))
    return df


def _geo_workers() -> int:
    raw = os.environ.get('GEO_WORKERS', '').strip()
    if not raw:
        return 0
    return max(0, int(raw))


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    remainder = seconds % 60
    return f"{minutes}m {remainder:.1f}s"


def _print_timing_summary(
    *,
    row_count: int,
    workers: int,
    csv_load: float,
    slice_sec: float,
    export_sec: float,
    main_total: float,
) -> None:
    startup = (
        _timing.get('intersections_load', 0.0)
        + _timing.get('streets_load', 0.0)
        + _timing.get('street_graphs', 0.0)
        + _timing.get('street_index', 0.0)
    )
    rows_per_sec = row_count / slice_sec if slice_sec > 0 else 0.0
    worker_label = 'sequential' if workers <= 1 else f'{workers} workers'

    print("   Timing:")
    print(f"     TCL intersections load: {_format_duration(_timing.get('intersections_load', 0.0))}")
    print(f"     TCL streets load:       {_format_duration(_timing.get('streets_load', 0.0))}")
    graphs_sec = _timing.get('street_graphs', 0.0)
    if _timing.get('street_graphs_cache'):
        print("     Street graphs:          (disk cache)")
    else:
        print(f"     Street graph build:     {_format_duration(graphs_sec)}")
    print(f"     Street index build:     {_format_duration(_timing.get('street_index', 0.0))}")
    warm = _timing.get('intersection_warm', 0.0)
    if warm > 0 or _timing.get('intersection_warm_cache'):
        warm_label = "(disk cache)" if _timing.get('intersection_warm_cache') else _format_duration(warm)
        print(f"     Intersection warm:      {warm_label}")
    print(f"     Startup (import):       {_format_duration(startup)}")
    print(f"     CSV load:               {_format_duration(csv_load)}")
    print(
        f"     Slice ({row_count} rows, {worker_label}): "
        f"{_format_duration(slice_sec)} ({rows_per_sec:.1f} rows/s)"
    )
    if export_sec > 0:
        print(f"     Export GeoJSON:         {_format_duration(export_sec)}")
    print(f"     Total (__main__):       {_format_duration(main_total)}")
    print(f"     Total (incl. import):   {_format_duration(startup + main_total)}")


# --- EXECUTION ---
if __name__ == "__main__":
    main_start = time.perf_counter()

    print("3. Loading Parsed Successes CSV...")
    t0 = time.perf_counter()
    df = pd.read_csv(data_path('parsed_successes.csv'))
    if 'parse_valid' in df.columns:
        valid_mask = df['parse_valid'].map(_parse_valid_flag)
        skipped = int((~valid_mask).sum())
        if skipped:
            print(f'   Skipping {skipped} rows with parse_valid=false')
        df = df.loc[valid_mask].copy()
    csv_load_sec = time.perf_counter() - t0
    batch_df = _geo_batch_limit(df)
    print(f"   Processing {len(batch_df)} of {len(df)} rows.")

    print("   Warming intersection index from CSV...")
    t0 = time.perf_counter()
    warmed = warm_intersection_index_from_dataframe(batch_df)
    _timing['intersection_warm'] = time.perf_counter() - t0
    if _timing.get('intersection_warm_cache'):
        print(f"   Loaded {warmed} intersection tokens from cache.")
    else:
        print(f"   Indexed {warmed} intersection search tokens (saved to cache).")

    clear_stage('geo')
    results: list[dict] = []
    failure_counts = Counter()
    print("4. Slicing Streets Locally...")

    workers = _geo_workers()
    columns = batch_df.columns
    row_args = [(columns, vals) for vals in batch_df.itertuples(index=False, name=None)]

    def _apply_row_outcome(payload, fail_rec):
        if payload is not None:
            results.append(payload)
            return
        if fail_rec is not None:
            record_failure(
                fail_rec['row_id'], 'geo', fail_rec['reason_code'], fail_rec['detail'],
                fail_rec['highway'], fail_rec['between'],
            )
            failure_counts[fail_rec['reason_code']] += 1

    t0 = time.perf_counter()
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for payload, fail_rec in pool.map(
                _process_geo_row, row_args, chunksize=64,
            ):
                _apply_row_outcome(payload, fail_rec)
    else:
        for args in row_args:
            _apply_row_outcome(*_process_geo_row(args))
    slice_sec = time.perf_counter() - t0

    print(f"\n5. Exporting {len(results)} zones to GeoJSON...")
    print(f"   Successes: {len(results)}")
    if failure_counts:
        print("   Geo failures by reason:")
        for code, count in failure_counts.most_common():
            print(f"     {code}: {count}")

    export_sec = 0.0
    if results:
        t0 = time.perf_counter()
        gdf = gpd.GeoDataFrame(results, geometry='geometry')
        gdf.set_crs(epsg=4326, inplace=True)
        out_path = data_path('final_parking_map.geojson')
        gdf.to_file(out_path, driver="GeoJSON")
        export_sec = time.perf_counter() - t0
        print(f"Done! Open '{out_path}' to see your local work.")

    main_total = time.perf_counter() - main_start
    _print_timing_summary(
        row_count=len(batch_df),
        workers=workers,
        csv_load=csv_load_sec,
        slice_sec=slice_sec,
        export_sec=export_sec,
        main_total=main_total,
    )

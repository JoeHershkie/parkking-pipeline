"""TCL street graph: walk centreline edges between intersection nodes."""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

import geopandas as gpd
import pandas as pd
import pyproj
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, transform

import intersection_index as ix_index

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
project_to_gps = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform

_COLLAPSE_TOL_M = 1e-3
_AXIS_OVERLAP_TOL_M = 1.0
PairFailureKind = Literal['missing', 'no_path', 'tied', 'ok']

_intersections_gdf: gpd.GeoDataFrame | None = None
_node_points_gps: dict[int, Point] = {}


@dataclass
class StreetEdge:
    centreline_id: int
    from_id: int
    to_id: int
    line_gps: LineString
    line_m: LineString


@dataclass
class StreetGraph:
    name: str
    edges: list[StreetEdge]
    adj: dict[int, list[tuple[int, StreetEdge]]] = field(default_factory=dict)
    _path_cache: dict[tuple[int, int], list[StreetEdge] | None] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        if not self.adj:
            self.adj = _build_adjacency(self.edges)


def _linestring_part(geom) -> LineString | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == 'LineString':
        return geom
    if geom.geom_type == 'MultiLineString':
        return max(geom.geoms, key=lambda g: g.length)
    return None


def _build_adjacency(edges: list[StreetEdge]) -> dict[int, list[tuple[int, StreetEdge]]]:
    adj: dict[int, list[tuple[int, StreetEdge]]] = {}
    for edge in edges:
        adj.setdefault(edge.from_id, []).append((edge.to_id, edge))
        adj.setdefault(edge.to_id, []).append((edge.from_id, edge))
    return adj


def configure_intersections(ix_gdf: gpd.GeoDataFrame) -> None:
    """Store intersection lookup tables for ID resolution."""
    global _intersections_gdf, _node_points_gps
    _intersections_gdf = ix_gdf
    ix_index.configure(ix_gdf)
    _node_points_gps = {
        int(row['INTERSECTION_ID']): row.geometry.centroid
        for _, row in ix_gdf.iterrows()
    }


def resolve_intersection_ids(highway: str, cross: str) -> list[int]:
    """All INTERSECTION_IDs matching highway × cross street."""
    if not highway or not cross or _intersections_gdf is None:
        return []
    return list(ix_index.resolve_pair_ids(highway, cross))


def node_point_gps(node_id: int) -> Point | None:
    return _node_points_gps.get(int(node_id))


def build_street_graphs(st_gdf: gpd.GeoDataFrame) -> dict[str, StreetGraph]:
    """One undirected graph per legal street name (lowercased)."""
    name_lower = st_gdf['LINEAR_NAME_FULL_LEGAL'].str.lower()
    graphs: dict[str, StreetGraph] = {}
    for s_name, group in st_gdf.groupby(name_lower, sort=False):
        edges: list[StreetEdge] = []
        for _, row in group.iterrows():
            from_id = row.get('FROM_INTERSECTION_ID')
            to_id = row.get('TO_INTERSECTION_ID')
            if pd.isna(from_id) or pd.isna(to_id):
                continue
            line_gps = _linestring_part(row.geometry)
            if line_gps is None:
                continue
            line_m = transform(project_to_meters, line_gps)
            edges.append(StreetEdge(
                centreline_id=int(row['CENTRELINE_ID']),
                from_id=int(from_id),
                to_id=int(to_id),
                line_gps=line_gps,
                line_m=line_m,
            ))
        if edges:
            graphs[s_name] = StreetGraph(name=s_name, edges=edges)
    return graphs


def shortest_path(graph: StreetGraph, id_a: int, id_b: int) -> list[StreetEdge] | None:
    """BFS shortest path (fewest edges) between intersection nodes."""
    id_a, id_b = int(id_a), int(id_b)
    if id_a == id_b:
        return []

    cache_key = (id_a, id_b)
    if cache_key in graph._path_cache:
        return graph._path_cache[cache_key]

    if id_a not in graph.adj or id_b not in graph.adj:
        graph._path_cache[cache_key] = None
        graph._path_cache[(id_b, id_a)] = None
        return None

    prev: dict[int, tuple[int, StreetEdge] | None] = {id_a: None}
    queue: deque[int] = deque([id_a])

    while queue:
        node = queue.popleft()
        if node == id_b:
            break
        for neighbor, edge in graph.adj.get(node, []):
            if neighbor in prev:
                continue
            prev[neighbor] = (node, edge)
            queue.append(neighbor)

    if id_b not in prev:
        graph._path_cache[cache_key] = None
        graph._path_cache[(id_b, id_a)] = None
        return None

    path_edges: list[StreetEdge] = []
    cur = id_b
    while cur != id_a:
        parent, edge = prev[cur]
        path_edges.append(edge)
        cur = parent
    path_edges.reverse()

    graph._path_cache[cache_key] = path_edges
    graph._path_cache[(id_b, id_a)] = list(reversed(path_edges))
    return path_edges


def path_length_m(edges: list[StreetEdge]) -> float:
    return sum(e.line_m.length for e in edges)


def path_centreline_ids(edges: list[StreetEdge]) -> list[int]:
    return [e.centreline_id for e in edges]


def _orient_edge_line(edge: StreetEdge, from_id: int, to_id: int, use_meters: bool) -> LineString:
    line = edge.line_m if use_meters else edge.line_gps
    if edge.from_id == from_id and edge.to_id == to_id:
        return line
    if edge.from_id == to_id and edge.to_id == from_id:
        return LineString(list(line.coords)[::-1])
    raise ValueError(f'edge {edge.centreline_id} does not connect {from_id} and {to_id}')


def _concat_lines(lines: list[LineString]) -> LineString:
    if not lines:
        return LineString()
    coords: list = []
    for line in lines:
        part = list(line.coords)
        if not coords:
            coords.extend(part)
        elif coords[-1] == part[0]:
            coords.extend(part[1:])
        else:
            coords.extend(part)
    return LineString(coords)


def path_to_linestring(
    edges: list[StreetEdge],
    orient_from: int,
    orient_to: int,
    *,
    use_meters: bool = False,
) -> LineString:
    """Concatenate edge geometries head-to-tail from orient_from to orient_to."""
    if not edges:
        pt = node_point_gps(orient_from)
        if pt is None:
            return LineString()
        return LineString([pt.coords[0], pt.coords[0]])

    lines: list[LineString] = []
    cur = int(orient_from)
    target = int(orient_to)
    for edge in edges:
        nbrs = {edge.from_id, edge.to_id} - {cur}
        if not nbrs:
            break
        nxt = nbrs.pop()
        lines.append(_orient_edge_line(edge, cur, nxt, use_meters))
        cur = nxt
        if cur == target:
            break
    return _concat_lines(lines)


def slice_path_between(
    edges: list[StreetEdge],
    id_start: int,
    id_end: int,
) -> LineString:
    """Return GPS path geometry between two intersection nodes."""
    if id_start == id_end:
        pt = node_point_gps(id_start)
        if pt is None:
            return LineString()
        c = pt.coords[0]
        return LineString([c, c])
    return path_to_linestring(edges, id_start, id_end, use_meters=False)


@dataclass
class PathPick:
    id_start: int
    id_end: int
    edges: list[StreetEdge]
    length_m: float


def pick_intersection_pair(
    graph: StreetGraph,
    highway: str,
    cross_a: str,
    cross_b: str,
) -> PathPick | None:
    """Choose ID pair with shortest valid graph path (fewest edges, then length)."""
    ids_a = resolve_intersection_ids(highway, cross_a)
    ids_b = resolve_intersection_ids(highway, cross_b)
    if not ids_a or not ids_b:
        return None

    best: PathPick | None = None
    tied = False

    for id_a in ids_a:
        for id_b in ids_b:
            if id_a == id_b:
                continue
            path = shortest_path(graph, id_a, id_b)
            if path is None:
                continue
            length = path_length_m(path)
            candidate = PathPick(id_a, id_b, path, length)
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

    if best is None or tied:
        return None
    return best


def classify_intersection_pair_failure(
    graph: StreetGraph,
    highway: str,
    cross_a: str,
    cross_b: str,
) -> PairFailureKind:
    """Classify why ``pick_intersection_pair`` would return None."""
    ids_a = resolve_intersection_ids(highway, cross_a)
    ids_b = resolve_intersection_ids(highway, cross_b)
    if not ids_a or not ids_b:
        return 'missing'

    best: PathPick | None = None
    tied = False

    for id_a in ids_a:
        for id_b in ids_b:
            if id_a == id_b:
                continue
            path = shortest_path(graph, id_a, id_b)
            if path is None:
                continue
            length = path_length_m(path)
            candidate = PathPick(id_a, id_b, path, length)
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

    if best is not None and not tied:
        return 'ok'
    if tied:
        return 'tied'
    return 'no_path'


def _node_point_m(node_id: int) -> Point | None:
    pt = node_point_gps(node_id)
    if pt is None:
        return None
    return transform(project_to_meters, pt)


def _axis_from_points(start_m: Point, end_m: Point) -> tuple[Point, float, float, float] | None:
    """Return (origin_point, axis_len, ux, uy) or None if degenerate."""
    dx = end_m.x - start_m.x
    dy = end_m.y - start_m.y
    axis_len = math.hypot(dx, dy)
    if axis_len < _COLLAPSE_TOL_M:
        return None
    return start_m, axis_len, dx / axis_len, dy / axis_len


def _projection_on_axis(
    pt_m: Point,
    origin: Point,
    ux: float,
    uy: float,
) -> float:
    return (pt_m.x - origin.x) * ux + (pt_m.y - origin.y) * uy


def _reachable_nodes_in_component(
    graph: StreetGraph,
    from_id: int,
    comp: frozenset[int],
) -> set[int]:
    from_id = int(from_id)
    if from_id not in comp:
        return set()
    seen: set[int] = {from_id}
    queue: deque[int] = deque([from_id])
    while queue:
        node = queue.popleft()
        for neighbor, _edge in graph.adj.get(node, []):
            if neighbor in comp and neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def extreme_node_toward(
    graph: StreetGraph,
    from_id: int,
    comp: frozenset[int],
    origin: Point,
    ux: float,
    uy: float,
    *,
    maximize: bool,
) -> int | None:
    """Farthest reachable node along +axis (maximize) or -axis (not maximize)."""
    reachable = _reachable_nodes_in_component(graph, from_id, comp)
    if len(reachable) <= 1:
        return int(from_id) if from_id in reachable else None

    best_id: int | None = None
    best_proj = -math.inf if maximize else math.inf
    for node_id in reachable:
        pt_m = _node_point_m(node_id)
        if pt_m is None:
            continue
        proj = _projection_on_axis(pt_m, origin, ux, uy)
        if maximize:
            if proj > best_proj + 1e-6:
                best_proj = proj
                best_id = node_id
        elif proj < best_proj - 1e-6:
            best_proj = proj
            best_id = node_id
    return best_id


def _component_projection_interval(
    comp: frozenset[int],
    origin: Point,
    ux: float,
    uy: float,
) -> tuple[float, float] | None:
    projs: list[float] = []
    for node_id in comp:
        pt_m = _node_point_m(node_id)
        if pt_m is None:
            continue
        projs.append(_projection_on_axis(pt_m, origin, ux, uy))
    if not projs:
        return None
    return min(projs), max(projs)


def _node_nearest_projection(
    comp: frozenset[int],
    target_proj: float,
    origin: Point,
    ux: float,
    uy: float,
) -> int | None:
    best_id: int | None = None
    best_dist = math.inf
    for node_id in comp:
        pt_m = _node_point_m(node_id)
        if pt_m is None:
            continue
        proj = _projection_on_axis(pt_m, origin, ux, uy)
        d = abs(proj - target_proj)
        if d < best_dist:
            best_dist = d
            best_id = node_id
    return best_id


def _resolve_axis_anchor_id(
    graph: StreetGraph,
    highway: str,
    cross: str,
    ids_all: list[int],
    qualifier: str | None,
) -> int | None:
    if not ids_all:
        return None
    if len(ids_all) == 1:
        return int(ids_all[0])
    if not qualifier:
        return int(ids_all[0])
    for comp in graph_components(graph):
        comp_ids = [i for i in ids_all if i in comp]
        if not comp_ids:
            continue
        anchor = comp_ids[0]
        line_m = component_linestring_m(graph, anchor)
        if line_m is None or line_m.is_empty:
            continue
        picked = pick_id_with_qualifier(
            highway, cross, qualifier, line_m, allowed_ids=comp,
        )
        if picked is not None:
            return int(picked)
    return int(ids_all[0])


def _span_component_path(
    graph: StreetGraph,
    comp: frozenset[int],
    *,
    start_id: int | None,
    end_id: int | None,
    origin: Point,
    ux: float,
    uy: float,
    axis_len: float,
) -> LineString | None:
    """Build one GPS line for a component's contribution to a disjoint block."""
    if start_id is not None and end_id is not None:
        if start_id == end_id:
            return slice_path_between([], start_id, end_id)
        path = shortest_path(graph, start_id, end_id)
        if path is None:
            return None
        return slice_path_between(path, start_id, end_id)

    if start_id is not None:
        target = extreme_node_toward(
            graph, start_id, comp, origin, ux, uy, maximize=True,
        )
        if target is None:
            return None
        path = shortest_path(graph, start_id, target)
        if path is None:
            return None
        return slice_path_between(path, start_id, target)

    if end_id is not None:
        target = extreme_node_toward(
            graph, end_id, comp, origin, ux, uy, maximize=False,
        )
        if target is None:
            return None
        path = shortest_path(graph, target, end_id)
        if path is None:
            return None
        return slice_path_between(path, target, end_id)

    interval = _component_projection_interval(comp, origin, ux, uy)
    if interval is None:
        return None
    comp_min, comp_max = interval
    clip_lo = max(0.0, comp_min)
    clip_hi = min(axis_len, comp_max)
    if clip_hi < clip_lo - _AXIS_OVERLAP_TOL_M:
        return None
    id_lo = _node_nearest_projection(comp, clip_lo, origin, ux, uy)
    id_hi = _node_nearest_projection(comp, clip_hi, origin, ux, uy)
    if id_lo is None or id_hi is None:
        return None
    if id_lo == id_hi:
        return slice_path_between([], id_lo, id_hi)
    path = shortest_path(graph, id_lo, id_hi)
    if path is None:
        return None
    return slice_path_between(path, id_lo, id_hi)


def slice_disjoint_block_paths(
    graph: StreetGraph,
    highway: str,
    cross_start: str,
    cross_end: str,
    *,
    start_qualifier: str | None = None,
    end_qualifier: str | None = None,
) -> MultiLineString | None:
    """
  Build a MultiLineString block across disconnected TCL components.

  Each fragment is walked from its anchor toward the other cross street; gaps
  at offset intersections are not bridged.
  """
    start_ids_all = resolve_intersection_ids(highway, cross_start)
    end_ids_all = resolve_intersection_ids(highway, cross_end)
    if not start_ids_all or not end_ids_all:
        return None

    start_anchor = _resolve_axis_anchor_id(
        graph, highway, cross_start, start_ids_all, start_qualifier,
    )
    end_anchor = _resolve_axis_anchor_id(
        graph, highway, cross_end, end_ids_all, end_qualifier,
    )
    if start_anchor is None or end_anchor is None:
        return None

    start_m = _node_point_m(start_anchor)
    end_m = _node_point_m(end_anchor)
    if start_m is None or end_m is None:
        return None

    axis = _axis_from_points(start_m, end_m)
    if axis is None:
        return None
    origin, axis_len, ux, uy = axis

    segments: list[LineString] = []
    for comp in graph_components(graph):
        interval = _component_projection_interval(comp, origin, ux, uy)
        if interval is None:
            continue
        comp_min, comp_max = interval
        if comp_max < -_AXIS_OVERLAP_TOL_M or comp_min > axis_len + _AXIS_OVERLAP_TOL_M:
            continue

        start_on = start_anchor if start_anchor in comp else None
        end_on = end_anchor if end_anchor in comp else None
        geom = _span_component_path(
            graph,
            comp,
            start_id=start_on,
            end_id=end_on,
            origin=origin,
            ux=ux,
            uy=uy,
            axis_len=axis_len,
        )
        if geom is None or geom.is_empty or geom.length < _COLLAPSE_TOL_M:
            continue
        segments.append(geom)

    if not segments:
        return None

    segments.sort(
        key=lambda g: _projection_on_axis(
            transform(project_to_meters, Point(g.coords[0])),
            origin,
            ux,
            uy,
        ),
    )

    total_len = sum(
        transform(project_to_meters, seg).length for seg in segments
    )
    if total_len < _COLLAPSE_TOL_M:
        return None

    if len(segments) == 1:
        return MultiLineString([segments[0]])
    return MultiLineString(segments)


def _disambiguate_project_dist(projects: list[float], qualifier: str) -> float | None:
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


def graph_components(graph: StreetGraph) -> list[frozenset[int]]:
    """Connected node sets in *graph*."""
    seen: set[int] = set()
    out: list[frozenset[int]] = []
    for start in graph.adj:
        if start in seen:
            continue
        component: set[int] = set()
        queue: deque[int] = deque([start])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            component.add(node)
            for neighbor, _edge in graph.adj.get(node, []):
                if neighbor not in seen:
                    queue.append(neighbor)
        out.append(frozenset(component))
    return out


def pick_id_with_qualifier(
    highway: str,
    cross: str,
    qualifier: str,
    path_line_m: LineString,
    *,
    allowed_ids: frozenset[int] | None = None,
) -> int | None:
    """Pick one intersection ID using compass qualifier on a reference centreline."""
    ids = resolve_intersection_ids(highway, cross)
    if allowed_ids is not None:
        ids = [node_id for node_id in ids if node_id in allowed_ids]
    if not ids:
        return None
    if len(ids) == 1 or not qualifier:
        return ids[0]

    projects: list[float] = []
    for node_id in ids:
        pt = node_point_gps(node_id)
        if pt is None:
            continue
        pt_m = transform(project_to_meters, pt)
        projects.append(path_line_m.project(pt_m))

    if len(projects) != len(ids):
        return None

    chosen = _disambiguate_project_dist(projects, qualifier)
    if chosen is None:
        return None
    for node_id, proj in zip(ids, projects, strict=True):
        if abs(proj - chosen) < 1e-3:
            return node_id
    return ids[projects.index(chosen)]


def _component_centroid_m(graph: StreetGraph, node_id: int) -> tuple[float, float] | None:
    line_m = component_linestring_m(graph, node_id)
    if line_m is None or line_m.is_empty:
        return None
    c = line_m.centroid
    return float(c.x), float(c.y)


def pick_qualified_block_path(
    graph: StreetGraph,
    highway: str,
    cross_start: str,
    cross_end: str,
    *,
    start_qualifier: str | None = None,
    end_qualifier: str | None = None,
    leg_compass: str | None = None,
) -> tuple[PathPick | None, bool]:
    """
    Resolve a block using intersection qualifiers on each graph component.

    Returns ``(pick, tied)`` where *tied* is true when multiple equally good picks remain.
    """
    start_ids_all = resolve_intersection_ids(highway, cross_start)
    end_ids_all = resolve_intersection_ids(highway, cross_end)
    if not start_ids_all:
        return None, False
    if not end_ids_all:
        return None, False

    candidates: list[PathPick] = []
    for comp in graph_components(graph):
        start_ids = [i for i in start_ids_all if i in comp]
        end_ids = [i for i in end_ids_all if i in comp]
        if not start_ids or not end_ids:
            continue

        anchor = start_ids[0]
        line_m = component_linestring_m(graph, anchor)
        if line_m is None or line_m.is_empty:
            continue

        if start_qualifier:
            id_s = pick_id_with_qualifier(
                highway, cross_start, start_qualifier, line_m,
                allowed_ids=comp,
            )
        elif len(start_ids) == 1:
            id_s = start_ids[0]
        else:
            id_s = None

        if end_qualifier:
            id_e = pick_id_with_qualifier(
                highway, cross_end, end_qualifier, line_m,
                allowed_ids=comp,
            )
        elif len(end_ids) == 1:
            id_e = end_ids[0]
        else:
            id_e = None

        if id_s is None or id_e is None or id_s == id_e:
            continue

        path = shortest_path(graph, id_s, id_e)
        if path is None:
            continue

        candidates.append(
            PathPick(id_s, id_e, path, path_length_m(path)),
        )

    if not candidates:
        return None, False

    def rank_key(pick: PathPick) -> tuple[float, int, float]:
        centroid = _component_centroid_m(graph, pick.id_start)
        if centroid is None:
            compass_rank = 0.0
        else:
            easting, northing = centroid
            if leg_compass in ('south', 'southern'):
                compass_rank = northing
            elif leg_compass in ('north', 'northern'):
                compass_rank = -northing
            elif leg_compass in ('east', 'eastern'):
                compass_rank = -easting
            elif leg_compass in ('west', 'western'):
                compass_rank = easting
            else:
                compass_rank = 0.0
        return (compass_rank, len(pick.edges), pick.length_m)

    candidates.sort(key=rank_key)
    best = candidates[0]
    tied = len(candidates) > 1 and rank_key(candidates[1]) == rank_key(best)
    return best, tied


def connected_component_edges(graph: StreetGraph, start_id: int) -> list[StreetEdge]:
    """All edges in the connected component containing start_id."""
    start_id = int(start_id)
    if start_id not in graph.adj:
        return []
    seen_nodes: set[int] = {start_id}
    seen_edges: set[int] = set()
    component: list[StreetEdge] = []
    queue: deque[int] = deque([start_id])

    while queue:
        node = queue.popleft()
        for neighbor, edge in graph.adj.get(node, []):
            if edge.centreline_id not in seen_edges:
                seen_edges.add(edge.centreline_id)
                component.append(edge)
            if neighbor not in seen_nodes:
                seen_nodes.add(neighbor)
                queue.append(neighbor)
    return component


def component_linestring_m(graph: StreetGraph, start_id: int) -> LineString | None:
    """Merged metre geometry for the component containing start_id."""
    edges = connected_component_edges(graph, start_id)
    if not edges:
        return None
    merged = linemerge(MultiLineString([e.line_m for e in edges]))
    if merged.geom_type == 'MultiLineString':
        return max(merged.geoms, key=lambda g: g.length)
    return merged


def farthest_node_in_component(graph: StreetGraph, start_id: int) -> int | None:
    """Intersection node in *start_id*'s component with the longest graph path from *start_id*."""
    start_id = int(start_id)
    edges = connected_component_edges(graph, start_id)
    nodes: set[int] = set()
    for edge in edges:
        nodes.add(edge.from_id)
        nodes.add(edge.to_id)
    if len(nodes) <= 1:
        return None

    best_id: int | None = None
    best_len = -1.0
    for node_id in nodes:
        if node_id == start_id:
            continue
        path = shortest_path(graph, start_id, node_id)
        if path is None:
            continue
        length = path_length_m(path)
        if length > best_len:
            best_len = length
            best_id = node_id
    if best_id is None or best_len < 1e-3:
        return None
    return best_id


def path_from_start_to_terminus(
    graph: StreetGraph,
    start_id: int,
    terminus_dir: str,
    terminus_dist_fn,
) -> tuple[list[StreetEdge], int] | None:
    """
    Walk from start_id to the compass terminus on the local component.
    Returns (edge path, end_node_id) where end_node is the terminus node.
    """
    comp_line = component_linestring_m(graph, start_id)
    if comp_line is None or comp_line.length == 0:
        return None

    terminus_dist = terminus_dist_fn(comp_line, terminus_dir)
    start_pt = transform(project_to_meters, node_point_gps(start_id))
    if start_pt is None:
        return None
    start_dist = comp_line.project(start_pt)

    target_dist = max(0.0, min(comp_line.length, terminus_dist))
    target_pt = comp_line.interpolate(target_dist)
    end_id = _nearest_node_on_component(graph, start_id, target_pt)
    if end_id is None:
        return None

    if end_id == start_id and abs(start_dist - target_dist) > 1e-3:
        end_id = farthest_node_in_component(graph, start_id) or end_id

    if end_id == start_id:
        return [], start_id

    path = shortest_path(graph, start_id, end_id)
    if path is None:
        return None
    return path, end_id


def _nearest_node_on_component(
    graph: StreetGraph,
    start_id: int,
    target_pt_m: Point,
) -> int | None:
    edges = connected_component_edges(graph, start_id)
    nodes: set[int] = set()
    for e in edges:
        nodes.add(e.from_id)
        nodes.add(e.to_id)
    if not nodes:
        return int(start_id)

    best_id: int | None = None
    best_dist = math.inf
    for node_id in nodes:
        pt = node_point_gps(node_id)
        if pt is None:
            continue
        pt_m = transform(project_to_meters, pt)
        d = pt_m.distance(target_pt_m)
        if d < best_dist:
            best_dist = d
            best_id = node_id
    return best_id

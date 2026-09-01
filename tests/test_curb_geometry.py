"""Synthetic tests for measured curb extraction, offset fallback, and provenance."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyproj
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    Point,
    Polygon,
    box,
)
from shapely.ops import linemerge, transform

from parking_pipeline import geo_cache as gc
from parking_pipeline import geo_indices as gi
from parking_pipeline import geo_slice as gs
from parking_pipeline import tcl_graph as tg
from parking_pipeline.curb_geometry import (
    CENTERLINE_FALLBACK,
    CONSERVATIVE_GLOBAL_OFFSET_M,
    CURB_INVALID,
    METHOD_CENTERLINE_UNRESOLVED,
    METHOD_OFFSET_FALLBACK,
    METHOD_ROAD_EDGE,
    MIN_ROAD_EDGE_COVERAGE,
    POINT_LIKE_M,
    ROAD_EDGE_LOW_COVERAGE,
    ROAD_EDGE_NO_MATCH,
    SIDE_AMBIGUOUS,
    OffsetCalibration,
    displacement_compass,
    flatten_line_geometry,
    resolve_curb_geometry,
)
from parking_pipeline.curb_side import parse_side
from parking_pipeline.geo_slice import (
    CENTRELINE_BLOCK_PATH,
    CENTRELINE_DISTANCE_MERGE,
    recover_centreline_ids,
    slice_between_distances,
    slice_block_path,
    street_merge_dropped_component,
)
from parking_pipeline.paths import data_path
from parking_pipeline.road_edges import METRE_CRS, build_road_edge_index, load_road_edge_index
from parking_pipeline.tcl_graph import StreetEdge, StreetGraph

OX, OY = 630000.0, 4835000.0
ARMADALE = 'armadale avenue'
COLBECK = 'Colbeck Street'
ANNETTE = 'Annette Street'
EXPECTED_EDGE_IDS = {7963621, 14014944, 14014945}


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('GEO_CACHE', '1')
    monkeypatch.setattr(gc, 'cache_dir', lambda: tmp_path / '.geo_cache')
    yield


def P(x: float, y: float) -> tuple[float, float]:
    return (OX + x, OY + y)


def _index(roads: list, ixs: list | None = None):
    rows: list[dict] = []
    for i, geom in enumerate(roads, start=1):
        rows.append({'SUBTYPE_DESC': 'Road Edge', 'OBJECTID': i, 'geometry': geom})
    start = len(rows) + 1
    for i, geom in enumerate(ixs or [], start=start):
        rows.append({'SUBTYPE_DESC': 'Intersection', 'OBJECTID': i, 'geometry': geom})
    gdf = gpd.GeoDataFrame(rows, crs=METRE_CRS)
    return build_road_edge_index(gdf, source_path=Path('memory.gpkg'))


def _resolve(line: LineString, side: str, index=None, **kwargs):
    return resolve_curb_geometry(
        line,
        parse_side(side),
        road_index=index,
        input_crs=METRE_CRS,
        **kwargs,
    )


def _assert_line(geom) -> None:
    assert geom is not None
    assert geom.geom_type in {'LineString', 'MultiLineString'}
    assert not geom.is_empty
    assert geom.length > 0.05


def _mean_y(geom) -> float:
    parts = geom.geoms if geom.geom_type == 'MultiLineString' else [geom]
    ys = [c[1] for part in parts for c in part.coords]
    return sum(ys) / len(ys)


def _hausdorff(a, b) -> float:
    return a.hausdorff_distance(b)


def _iter_line_parts(geom):
    if geom.geom_type == 'MultiLineString':
        return list(geom.geoms)
    return [geom]


def _mean_displacement(centreline: LineString, curb) -> tuple[float, float]:
    dxs: list[float] = []
    dys: list[float] = []
    for part in _iter_line_parts(curb):
        for dist in (0.25, 0.5, 0.75):
            pt = part.interpolate(dist, normalized=True)
            nearest = centreline.interpolate(centreline.project(pt))
            dxs.append(pt.x - nearest.x)
            dys.append(pt.y - nearest.y)
    return (sum(dxs) / len(dxs), sum(dys) / len(dys))


def _assert_endpoints_in_span(centreline: LineString, curb, *, gate_m: float = 8.0) -> None:
    length = centreline.length
    for part in _iter_line_parts(curb):
        for coord in (part.coords[0], part.coords[-1]):
            along = centreline.project(Point(coord))
            assert -gate_m <= along <= length + gate_m


def _assert_curb_invariants(result, centreline: LineString, side: str) -> None:
    spec = parse_side(side)
    geom = result.geometry
    _assert_line(geom)
    assert geom.geom_type in {'LineString', 'MultiLineString'}
    assert geom.geom_type != 'Point'
    assert not geom.crosses(centreline)
    _assert_endpoints_in_span(centreline, geom)
    if spec.mode == 'single' and spec.directions:
        dx, dy = _mean_displacement(centreline, geom)
        assert displacement_compass(dx, dy) in spec.directions
    if spec.selects_multiple_curbs:
        parts = _iter_line_parts(geom)
        assert len(parts) >= 2
        assert _hausdorff(parts[0], parts[1]) > 2.0


def _strip_box(width: float = 140.0, height: float = 14.0, x0: float = 0.0, y0: float = 0.0):
    return box(*P(x0, y0), *P(x0 + width, y0 + height))


def test_flatten_keeps_geographic_linestrings():
    # ~600 m in Toronto; WGS84 length is far below POINT_LIKE_M (0.05 m).
    geom = LineString([(-79.484909, 43.653201), (-79.487063, 43.6585)])
    assert 0 < geom.length < POINT_LIKE_M
    flat = flatten_line_geometry(geom)
    assert flat is not None
    assert flat.geom_type == 'LineString'
    assert not flat.is_empty
    assert len(flat.coords) >= 2


def test_flatten_drops_points_polygons_and_zero_length():
    keep = LineString([P(0, 0), P(10, 0)])
    geom = GeometryCollection([
        Point(*P(1, 1)),
        box(*P(0, 0), *P(2, 2)),
        LineString([P(0, 0), P(0, 0)]),
        keep,
        MultiLineString([
            LineString([P(20, 0), P(20, 0)]),
            LineString([P(30, 0), P(40, 0)]),
        ]),
    ])
    flat = flatten_line_geometry(geom)
    _assert_line(flat)
    assert flat.geom_type == 'MultiLineString'
    assert all(part.geom_type == 'LineString' for part in flat.geoms)


def test_flatten_nested_multilinestring():
    nested = MultiLineString([
        LineString([P(0, 0), P(5, 0)]),
        LineString([P(10, 0), P(15, 0)]),
    ])
    flat = flatten_line_geometry(GeometryCollection([nested]))
    assert flat.geom_type == 'MultiLineString'
    assert len(flat.geoms) == 2


def test_straight_north_and_reversed_select_same_curb():
    index = _index([_strip_box()])
    line = LineString([P(10, 7), P(130, 7)])
    north = _resolve(line, 'North', index)
    _assert_line(north.geometry)
    assert north.method == METHOD_ROAD_EDGE
    assert north.coverage >= MIN_ROAD_EDGE_COVERAGE
    assert _mean_y(north.geometry) > OY + 7
    assert not north.geometry.crosses(line)

    reversed_line = LineString(list(line.coords)[::-1])
    north_rev = _resolve(reversed_line, 'North', index)
    _assert_line(north_rev.geometry)
    assert north_rev.method == METHOD_ROAD_EDGE
    assert _hausdorff(north.geometry, north_rev.geometry) < 0.5

    south = _resolve(line, 'South', index)
    assert _mean_y(south.geometry) < OY + 7
    assert _hausdorff(north.geometry, south.geometry) > 5
    _assert_curb_invariants(north, line, 'North')
    _assert_curb_invariants(south, line, 'South')


def test_displacement_compass_cardinals_and_diagonals():
    assert displacement_compass(0.0, 1.0) == 'north'
    assert displacement_compass(1.0, 0.0) == 'east'
    assert displacement_compass(1.0, 1.0) == 'northeast'
    assert displacement_compass(-1.0, 1.0) == 'northwest'


def test_diagonal_northeast_picks_matching_normal():
    strip = Polygon([
        P(0, 8), P(100, 108), P(108, 100), P(8, 0), P(0, 8),
    ])
    index = _index([strip])
    line = LineString([P(20, 20), P(90, 90)])
    result = _resolve(line, 'Northwest', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_ROAD_EDGE
    mid_line = line.interpolate(0.5, normalized=True)
    mid_curb = result.geometry.interpolate(0.5, normalized=True)
    assert mid_curb.x < mid_line.x


def test_s_curve_stays_continuous_on_one_side():
    spine = LineString([P(0, 20), P(40, 26), P(80, 20), P(120, 26)])
    strip = spine.buffer(8, cap_style='flat', join_style='round')
    index = _index([strip])
    result = _resolve(spine, 'North', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_ROAD_EDGE
    assert result.geometry.geom_type == 'LineString'
    assert not result.geometry.crosses(spine)


def test_closed_curve_measured_track():
    ring = LineString([
        P(0, 0), P(80, 0), P(80, 80), P(0, 80), P(0, 0),
    ])
    strip = ring.buffer(6, cap_style='flat', join_style='round')
    index = _index([strip])
    result = _resolve(ring, 'Both', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_ROAD_EDGE
    assert result.geometry.geom_type == 'MultiLineString'


def test_wrapping_north_and_east_selects_one_curb():
    line = LineString([P(0, 80), P(0, 0), P(80, 0)])
    strip = line.buffer(7, cap_style='flat', join_style='round')
    index = _index([strip])
    result = _resolve(line, 'North and east', index)
    _assert_line(result.geometry)
    assert parse_side('North and east').wrapping is True
    assert result.method == METHOD_ROAD_EDGE
    parts = (
        list(result.geometry.geoms)
        if result.geometry.geom_type == 'MultiLineString'
        else [result.geometry]
    )
    assert all(not part.crosses(line) for part in parts)
    # One wrapping curb, not both sides of the street.
    assert result.geometry.geom_type == 'LineString' or len(parts) <= 2


def test_opposing_and_both_select_two_curbs():
    index = _index([_strip_box()])
    line = LineString([P(10, 7), P(130, 7)])
    opposing = _resolve(line, 'North and south', index)
    both = _resolve(line, 'Both', index)
    for result in (opposing, both):
        _assert_line(result.geometry)
        assert result.method == METHOD_ROAD_EDGE
        assert result.geometry.geom_type == 'MultiLineString'
        assert len(result.geometry.geoms) == 2
        ys = sorted(_mean_y(part) for part in result.geometry.geoms)
        assert ys[0] < OY + 7 < ys[1]
    _assert_curb_invariants(opposing, line, 'North and south')
    _assert_curb_invariants(both, line, 'Both')


def test_intersection_mask_splits_coverage_gap():
    west = box(*P(0, 0), *P(80, 14))
    east = box(*P(120, 0), *P(200, 14))
    ix = box(*P(80, -10), *P(120, 24))
    index = _index([west, east], [ix])
    line = LineString([P(10, 7), P(190, 7)])
    result = _resolve(line, 'North', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_ROAD_EDGE
    assert result.geometry.geom_type == 'MultiLineString'
    assert len(result.geometry.geoms) >= 2


def test_divided_road_uses_the_roadway_containing_the_centreline():
    north = box(*P(0, 28), *P(160, 38))
    south = box(*P(0, 0), *P(160, 10))
    index = _index([north, south])
    line = LineString([P(10, 33), P(150, 33)])
    result = _resolve(line, 'North', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_ROAD_EDGE
    assert _mean_y(result.geometry) > OY + 33
    assert _mean_y(result.geometry) < OY + 40
    south_pick = _resolve(line, 'South', index)
    assert OY + 28 <= _mean_y(south_pick.geometry) <= OY + 33.5


def test_parity_hook_uses_tcl_left_right_when_unambiguous():
    index = _index([_strip_box()])
    line = LineString([P(10, 7), P(130, 7)])
    odd = resolve_curb_geometry(
        line,
        parse_side('Odd'),
        road_index=index,
        input_crs=METRE_CRS,
        construction_method='block_path',
        parity_l='O',
        parity_r='E',
    )
    _assert_line(odd.geometry)
    assert odd.method == METHOD_ROAD_EDGE
    assert _mean_y(odd.geometry) > OY + 7

    ambiguous = resolve_curb_geometry(
        line,
        parse_side('Odd'),
        road_index=index,
        input_crs=METRE_CRS,
        construction_method='distance_merge',
        parity_l='O',
        parity_r='E',
    )
    assert ambiguous.method == METHOD_CENTERLINE_UNRESOLVED
    assert SIDE_AMBIGUOUS in ambiguous.warnings


def test_island_and_median_do_not_guess():
    index = _index([_strip_box()])
    line = LineString([P(10, 7), P(130, 7)])
    island = _resolve(line, 'West side of traffic island', index)
    median = _resolve(line, 'East side of the roadway west of the median', index)
    assert island.method == METHOD_CENTERLINE_UNRESOLVED
    assert median.method == METHOD_CENTERLINE_UNRESOLVED
    assert SIDE_AMBIGUOUS in island.warnings
    assert island.geometry.geom_type == 'LineString'


def test_inner_outer_unresolved_without_ring_topology():
    index = _index([_strip_box()])
    line = LineString([P(10, 7), P(130, 7)])
    result = _resolve(line, 'Inner Perimeter', index)
    assert result.method == METHOD_CENTERLINE_UNRESOLVED
    assert SIDE_AMBIGUOUS in result.warnings


def test_inner_outer_when_polygon_has_a_hole():
    outer = box(*P(0, 0), *P(120, 80))
    hole = box(*P(30, 20), *P(90, 60))
    donut = Polygon(outer.exterior.coords, [hole.exterior.coords])
    index = _index([donut])
    line = LineString([P(10, 10), P(110, 10)])
    inner = _resolve(line, 'Inner Perimeter', index)
    outer_side = _resolve(line, 'Outer Perimeter', index)
    _assert_line(inner.geometry)
    _assert_line(outer_side.geometry)
    assert inner.method == METHOD_ROAD_EDGE
    assert outer_side.method == METHOD_ROAD_EDGE


def test_no_road_edge_match_uses_calibrated_offset():
    index = _index([_strip_box(x0=400, y0=400)])
    line = LineString([P(10, 7), P(130, 7)])
    result = _resolve(line, 'North', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_OFFSET_FALLBACK
    assert ROAD_EDGE_NO_MATCH in result.warnings
    assert CENTERLINE_FALLBACK in result.warnings
    assert _mean_y(result.geometry) > OY + 7
    reversed_line = LineString(list(line.coords)[::-1])
    reversed_result = _resolve(reversed_line, 'North', index)
    assert _hausdorff(result.geometry, reversed_result.geometry) < 0.75


def test_low_coverage_falls_back_to_offset():
    tiny = box(*P(60, 0), *P(70, 14))
    index = _index([tiny])
    line = LineString([P(0, 7), P(140, 7)])
    result = _resolve(line, 'North', index)
    _assert_line(result.geometry)
    assert result.method in {METHOD_OFFSET_FALLBACK, METHOD_CENTERLINE_UNRESOLVED}
    assert ROAD_EDGE_LOW_COVERAGE in result.warnings or ROAD_EDGE_NO_MATCH in result.warnings


def test_offset_distance_order_centreline_then_class_then_global():
    calib = OffsetCalibration(
        by_centreline_id={11: 5.0},
        by_feature_class={'Local': 4.0},
        global_offset_m=CONSERVATIVE_GLOBAL_OFFSET_M,
    )
    dist, source = calib.distance_for((11,), 'Local', sample_median=6.5)
    assert (dist, source) == (6.5, 'centreline_samples')
    dist, source = calib.distance_for((11,), 'Local', sample_median=None)
    assert (dist, source) == (5.0, 'centreline')
    dist, source = calib.distance_for((99,), 'Local', sample_median=None)
    assert (dist, source) == (4.0, 'feature_class')
    dist, source = calib.distance_for((99,), 'Unknown', sample_median=None)
    assert (dist, source) == (CONSERVATIVE_GLOBAL_OFFSET_M, 'global')


def test_invalid_or_too_close_offset_keeps_centreline():
    line = LineString([P(0, 0), P(40, 0)])
    result = resolve_curb_geometry(
        line,
        parse_side('North'),
        road_index=None,
        input_crs=METRE_CRS,
        calibration=OffsetCalibration(global_offset_m=0.01),
    )
    assert result.method == METHOD_CENTERLINE_UNRESOLVED
    assert CURB_INVALID in result.warnings
    assert CENTERLINE_FALLBACK in result.warnings
    assert result.geometry.geom_type == 'LineString'
    assert result.geometry.hausdorff_distance(line) < 1e-6


def test_missing_index_does_not_emit_point():
    line = LineString([P(0, 0), P(30, 0)])
    result = _resolve(line, 'North', None)
    _assert_line(result.geometry)
    assert result.geometry.geom_type != 'Point'


def test_sample_fixture_straight_measured_curb():
    sample = Path(__file__).resolve().parents[1] / 'data' / 'samples' / 'topographic_road_edges.gpkg'
    if not sample.exists():
        pytest.skip('sample road-edge fixture missing')
    index = load_road_edge_index(sample)
    line = LineString([(630010, 4835007), (630140, 4835007)])
    result = _resolve(line, 'North', index)
    _assert_line(result.geometry)
    assert result.method == METHOD_ROAD_EDGE
    assert result.road_edge_object_ids
    assert _mean_y(result.geometry) > 4835007


def test_qa_csv_and_summary_roundtrip(tmp_path):
    from parking_pipeline.curb_geometry import write_curb_geometry_qa

    rows = [{
        'row_id': '1',
        'Side': 'Island',
        'side_mode': 'specialized',
        'curb_geometry_method': METHOD_CENTERLINE_UNRESOLVED,
        'curb_confidence': 0.1,
        'curb_warnings': [SIDE_AMBIGUOUS, CENTERLINE_FALLBACK],
        'median_offset_m': 3.5,
    }]
    csv_path, summary_path = write_curb_geometry_qa(
        rows,
        csv_path=tmp_path / 'curb_geometry_qa.csv',
        summary_path=tmp_path / 'curb_geometry_qa_summary.json',
    )
    assert csv_path.exists()
    payload = json.loads(summary_path.read_text(encoding='utf-8'))
    assert payload['feature_count'] == 1
    assert payload['methods'][METHOD_CENTERLINE_UNRESOLVED] == 1
    assert 'Island' in payload['unresolved_side_values']
    invalid = Polygon([
        P(0, 0), P(40, 20), P(0, 20), P(40, 0), P(0, 0),
    ])
    assert invalid.is_valid is False
    index = _index([invalid])
    line = LineString([P(8, 10), P(32, 10)])
    result = _resolve(line, 'North', index)
    _assert_line(result.geometry)
    assert result.method in {METHOD_ROAD_EDGE, METHOD_OFFSET_FALLBACK}
    assert result.geometry.geom_type != 'Point'


@pytest.fixture(scope='module')
def street_graphs():
    ix = gpd.read_file(data_path('tcl_intersections.geojson'))
    st = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix)
    graphs = tg.build_street_graphs(st)
    gi.street_graphs = graphs
    gi.street_index = gi._build_street_index(st)
    gi.centreline_meta = gi._build_centreline_meta(st)
    from parking_pipeline import tcl_highway_resolve as thr
    thr.build_index_from_csv(legal_keys=set(graphs.keys()))
    return graphs


def test_block_path_keeps_exact_centreline_ids(street_graphs):
    graph = street_graphs[ARMADALE]
    pick = tg.pick_intersection_pair(graph, ARMADALE, COLBECK, ANNETTE)
    assert pick is not None
    result = slice_block_path(ARMADALE, COLBECK, ANNETTE)
    assert result.ok
    assert result.construction_method == CENTRELINE_BLOCK_PATH
    assert result.centreline_ids == tuple(tg.path_centreline_ids(pick.edges))
    assert set(result.centreline_ids) == EXPECTED_EDGE_IDS
    assert result.merge_dropped_component is False


def test_distance_merge_recovers_ids_and_flags_dropped_component():
    to_ll = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform
    to_m = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform

    def gps_line(coords_m):
        return LineString([to_ll(x, y) for x, y in coords_m])

    long_m = [P(0, 0), P(200, 0)]
    short_m = [P(0, 80), P(30, 80)]
    long_gps = gps_line(long_m)
    short_gps = gps_line(short_m)
    long_edge = StreetEdge(101, 1, 2, long_gps, transform(to_m, long_gps))
    short_edge = StreetEdge(202, 3, 4, short_gps, transform(to_m, short_gps))
    graph = StreetGraph('synthetic street', [long_edge, short_edge])
    gi.street_graphs['synthetic street'] = graph
    gi.street_index['synthetic street'] = long_gps

    from parking_pipeline import tcl_highway_resolve as thr
    thr.build_index(legal_keys={'synthetic street'}, base_to_legals={})

    assert street_merge_dropped_component('synthetic street') is True
    ids = recover_centreline_ids('synthetic street', long_gps)
    assert ids == (101,)
    result = gs._distance_merge_slice(long_gps, 'synthetic street')
    assert result.construction_method == CENTRELINE_DISTANCE_MERGE
    assert result.merge_dropped_component is True
    assert result.centreline_ids == (101,)
    assert result.geometry.equals(long_gps)
    assert recover_centreline_ids('synthetic street', short_gps) == (202,)


def _install_synthetic_street(name: str, edges: list[StreetEdge], merged_gps: LineString) -> None:
    gi.street_graphs[name] = StreetGraph(name, edges)
    gi.street_index[name] = merged_gps
    from parking_pipeline import tcl_highway_resolve as thr
    thr.build_index(legal_keys={name}, base_to_legals={})


def test_distance_merge_arbitrary_orientation_recovers_identity():
    to_ll = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform
    to_m = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform

    def gps_line(coords_m):
        return LineString([to_ll(x, y) for x, y in coords_m])

    west_gps = gps_line([P(0, 0), P(100, 0)])
    # East component digitized opposite the merge direction.
    east_gps = gps_line([P(200, 0), P(100, 0)])
    west_edge = StreetEdge(101, 1, 2, west_gps, transform(to_m, west_gps))
    east_edge = StreetEdge(202, 3, 2, east_gps, transform(to_m, east_gps))
    highway = 'oriented merge street'

    for geoms in (
        [west_gps, east_gps],
        [east_gps, west_gps],
        [LineString(list(west_gps.coords)[::-1]), east_gps],
        [west_gps, LineString(list(east_gps.coords)[::-1])],
    ):
        merged = linemerge(MultiLineString(geoms))
        if merged.geom_type == 'MultiLineString':
            merged = max(merged.geoms, key=lambda part: part.length)
        _install_synthetic_street(highway, [west_edge, east_edge], merged)
        ids = recover_centreline_ids(highway, merged)
        assert set(ids) == {101, 202}
        reversed_ids = recover_centreline_ids(
            highway, LineString(list(merged.coords)[::-1]),
        )
        assert set(reversed_ids) == {101, 202}


def test_distance_substring_recovers_overlapping_identity_only():
    to_ll = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform
    to_m = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform

    def gps_line(coords_m):
        return LineString([to_ll(x, y) for x, y in coords_m])

    west_gps = gps_line([P(0, 0), P(100, 0)])
    east_gps = gps_line([P(200, 0), P(100, 0)])
    west_edge = StreetEdge(101, 1, 2, west_gps, transform(to_m, west_gps))
    east_edge = StreetEdge(202, 3, 2, east_gps, transform(to_m, east_gps))
    highway = 'substring merge street'
    merged = linemerge(MultiLineString([west_gps, east_gps]))
    if merged.geom_type == 'MultiLineString':
        merged = max(merged.geoms, key=lambda part: part.length)
    _install_synthetic_street(highway, [west_edge, east_edge], merged)

    line_m = transform(to_m, merged)
    result = slice_between_distances(merged, line_m, 0.0, 40.0, highway=highway)
    assert result.ok
    assert result.construction_method == CENTRELINE_DISTANCE_MERGE
    assert result.merge_dropped_component is False
    assert len(result.centreline_ids) == 1
    assert result.centreline_ids[0] in {101, 202}

    mid = slice_between_distances(merged, line_m, 80.0, 120.0, highway=highway)
    assert mid.ok
    assert set(mid.centreline_ids) <= {101, 202}
    assert mid.centreline_ids


def test_curb_invariants_hold_for_measured_and_fallback():
    index = _index([_strip_box()])
    line = LineString([P(10, 7), P(130, 7)])
    measured = _resolve(line, 'North', index)
    _assert_curb_invariants(measured, line, 'North')
    left, right = measured.geometry, _resolve(line, 'South', index).geometry
    assert _hausdorff(left, right) > 5

    miss = _index([_strip_box(x0=400, y0=400)])
    fallback = _resolve(line, 'North', miss)
    _assert_curb_invariants(fallback, line, 'North')


def test_process_geo_row_never_emits_point(monkeypatch):
    from parking_pipeline import geometry_engine as ge

    ge.configure_curb_runtime(road_index=False, overrides={})
    columns = [
        '_id', 'Highway', 'Between', 'Prohibited Times and/or Days',
        'Side', 'parse_valid', 'resolve_valid', 'rule_type',
        'schedule_json', 'schedule_category', 'Maximum Period Permitted',
        'max_minutes',
    ]

    class FakeSlice:
        ok = True
        geometry = Point(0.0, 0.0)
        centreline_ids = ()
        construction_method = CENTRELINE_DISTANCE_MERGE
        merge_dropped_component = False
        reason_code = None
        detail = None

    monkeypatch.setattr(ge, 'slice_street', lambda *_a, **_k: FakeSlice())
    values = (
        'row-1', 'Synthetic Street', 'A and B', 'Anytime',
        'North', True, True, 'entire_length',
        None, None, None, None,
    )
    success, failure = ge._process_geo_row((pd.Index(columns), values))
    assert success is None
    assert failure is not None
    assert failure['reason_code'] == gs.EMPTY_GEOMETRY


def test_require_road_edges_raises_when_source_missing(monkeypatch):
    from parking_pipeline import geometry_engine as ge
    from parking_pipeline.road_edges import RoadEdgesError

    ge._road_edge_index = None
    ge._require_road_edges = True

    def boom(*, require=False):
        raise RoadEdgesError('missing')

    monkeypatch.setattr(ge, 'load_road_edge_index', boom)
    with pytest.raises(RoadEdgesError):
        ge._ensure_road_edge_index()
    ge._require_road_edges = False
    ge._road_edge_index = None


def test_process_geo_row_spatial_enrichment(monkeypatch):
    from parking_pipeline import geometry_engine as ge
    from parking_pipeline.hydrants import FireHydrantIndex
    from parking_pipeline.municipal_rules import MunicipalBoundaryIndex
    from parking_pipeline.permit_zones import PermitZoneIndex

    # Setup fake indexes
    poly_mun = Polygon([(-80, 43), (-79, 43), (-79, 44), (-80, 44)])
    gdf_mun = gpd.GeoDataFrame({'AREA_NAME': ['NORTH YORK']}, geometry=[poly_mun], crs='EPSG:4326')
    mun_idx = MunicipalBoundaryIndex(gdf_mun)

    poly_permit = Polygon([(-79.5, 43.6), (-79.4, 43.6), (-79.4, 43.7), (-79.5, 43.7)])
    gdf_permit = gpd.GeoDataFrame({'AREA_LONG_CODE': ['1C'], 'AREA_NAME': ['1C']}, geometry=[poly_permit], crs='EPSG:4326')
    permit_idx = PermitZoneIndex(gdf_permit)

    hydrant_pt = Point(-79.45, 43.65)
    gdf_hydrant = gpd.GeoDataFrame({'_id': [1], 'FACILITYID': ['HY123']}, geometry=[hydrant_pt], crs='EPSG:4326')
    hydrant_idx = FireHydrantIndex(gdf_hydrant)

    ge.configure_curb_runtime(
        road_index=False,
        overrides={},
        municipal_index=mun_idx,
        permit_index=permit_idx,
        hydrant_index=hydrant_idx,
    )

    class FakeSlice:
        ok = True
        geometry = LineString([(-79.46, 43.65), (-79.44, 43.65)])
        centreline_ids = [100]
        construction_method = CENTRELINE_DISTANCE_MERGE
        merge_dropped_component = False
        reason_code = None
        detail = None

    monkeypatch.setattr(ge, 'slice_street', lambda *_a, **_k: FakeSlice())

    columns = [
        '_id', 'Highway', 'Between', 'Prohibited Times and/or Days',
        'Side', 'parse_valid', 'resolve_valid', 'rule_type',
        'schedule_json', 'schedule_category', 'Maximum Period Permitted',
        'max_minutes',
    ]
    values = (
        'row-10', 'Dundas Street West', 'A and B', 'Major Snow Storm Conditions',
        'North', True, True, 'entire_length',
        '{}', 'snow_streetcar', None, None,
    )

    success, failure = ge._process_geo_row((pd.Index(columns), values))
    assert failure is None
    assert success is not None
    assert success['schedule_category'] == 'snow_streetcar'
    assert success['is_snow_route'] is True
    assert success['streetcar_corridor'] is True
    assert success['former_municipality'] == 'NORTH YORK'
    assert success['permit_area_id'] == '1C'
    assert success['has_hydrant'] is True
    assert success['hydrant_count'] == 1


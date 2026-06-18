"""Tests for disjoint multi-fragment block slicing and pair failure classification."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import pytest
import pyproj
from shapely.geometry import LineString, Point
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import geo_indices as gi
import geometry_engine as ge
import tcl_graph as tg
from paths import data_path
from tcl_graph import StreetEdge, StreetGraph

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform

MANNING = 'manning avenue'
HARBORD = 'Harbord Street'
DUPONT = 'Dupont Street'
QUEEN_W = 'Queen Street West'
MANSFIELD = 'Mansfield Avenue'


def _gps_line(coords_m: list[tuple[float, float]]) -> LineString:
    coords_gps = [
        pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform(x, y)
        for x, y in coords_m
    ]
    return LineString(coords_gps)


def _edge(cid: int, a: int, b: int, coords_m: list[tuple[float, float]]) -> StreetEdge:
    line_gps = _gps_line(coords_m)
    line_m = transform(project_to_meters, line_gps)
    return StreetEdge(centreline_id=cid, from_id=a, to_id=b, line_gps=line_gps, line_m=line_m)


@pytest.fixture(scope='module')
def street_graphs():
    ix = gpd.read_file(data_path('tcl_intersections.geojson'))
    st = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix)
    return tg.build_street_graphs(st)


@pytest.fixture(scope='module')
def manning(street_graphs):
    graph = street_graphs.get(MANNING)
    assert graph is not None
    return graph


@pytest.fixture(scope='module')
def geometry_indexes(street_graphs):
    gi.street_graphs = street_graphs
    thr = __import__('tcl_highway_resolve', fromlist=['build_index_from_csv'])
    thr.build_index_from_csv(legal_keys=set(street_graphs.keys()))


def test_classify_manning_harbord_dupont_no_path(manning):
    with patch.object(tg, 'resolve_intersection_ids', side_effect=lambda h, c: {
        (MANNING, HARBORD): [13465112],
        (MANNING, DUPONT): [13463197],
    }.get((h.lower(), c), [])):
        kind = tg.classify_intersection_pair_failure(manning, MANNING, HARBORD, DUPONT)
    assert kind == 'no_path'


def test_manning_harbord_dupont_disjoint_multiline(manning):
    geom = tg.slice_disjoint_block_paths(manning, MANNING, HARBORD, DUPONT)
    assert geom is not None
    assert geom.geom_type == 'MultiLineString'
    assert len(geom.geoms) == 2
    total_m = sum(transform(project_to_meters, g).length for g in geom.geoms)
    assert total_m > 500


def test_manning_queen_mansfield_disjoint_multiline(manning):
    geom = tg.slice_disjoint_block_paths(manning, MANNING, QUEEN_W, MANSFIELD)
    assert geom is not None
    assert geom.geom_type == 'MultiLineString'
    assert len(geom.geoms) == 2
    total_m = sum(transform(project_to_meters, g).length for g in geom.geoms)
    assert total_m > 200


def test_slice_block_path_manning_integration(geometry_indexes, manning):
    del manning
    for cross_start, cross_end in (
        (HARBORD, DUPONT),
        (QUEEN_W, MANSFIELD),
    ):
        result = ge.slice_block_path('Manning Avenue', cross_start, cross_end)
        assert result.ok, result.reason_code
        assert result.geometry.geom_type == 'MultiLineString'


def test_synthetic_classify_no_path():
    """Two components, one ID per cross — no graph path."""
    e1 = _edge(1, 1, 2, [(0, 0), (0, 100)])
    e2 = _edge(2, 3, 4, [(500, 0), (500, 100)])
    graph = StreetGraph(name='test', edges=[e1, e2])
    tg._node_points_gps = {
        1: Point(0, 0),
        2: Point(0, 0.001),
        3: Point(0.005, 0),
        4: Point(0.005, 0.001),
    }
    with patch.object(tg, 'resolve_intersection_ids', side_effect=lambda _h, c: {
        'A': [1],
        'B': [4],
    }.get(c, [])):
        assert tg.classify_intersection_pair_failure(graph, 'test', 'A', 'B') == 'no_path'


def test_synthetic_classify_tied():
    """Two start IDs with equal 2-edge paths to the same end ID."""
    e1 = _edge(1, 1, 2, [(0, 0), (100, 0)])
    e2 = _edge(2, 2, 4, [(100, 0), (100, 100)])
    e3 = _edge(3, 3, 6, [(0, 0), (0, 100)])
    e4 = _edge(4, 6, 4, [(0, 100), (100, 100)])
    graph = StreetGraph(name='test', edges=[e1, e2, e3, e4])
    tg._node_points_gps = {
        1: Point(0, 0),
        2: Point(0.001, 0),
        3: Point(0, 0.0005),
        4: Point(0.001, 0.001),
        6: Point(0, 0.0015),
    }
    with patch.object(tg, 'resolve_intersection_ids', side_effect=lambda _h, c: {
        'A': [1, 3],
        'B': [4],
    }.get(c, [])):
        assert tg.classify_intersection_pair_failure(graph, 'test', 'A', 'B') == 'tied'


def test_armadale_still_single_linestring(street_graphs):
    armadale = street_graphs['armadale avenue']
    pick = tg.pick_intersection_pair(
        armadale, 'armadale avenue', 'Colbeck Street', 'Annette Street',
    )
    assert pick is not None
    geom = tg.slice_path_between(pick.edges, pick.id_start, pick.id_end)
    assert geom.geom_type == 'LineString'

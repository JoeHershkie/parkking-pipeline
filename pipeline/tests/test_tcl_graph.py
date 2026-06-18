"""Tests for TCL street graph path finding."""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pyproj
import pytest
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import tcl_graph as tg
from paths import data_path

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform

ARMADALE = 'armadale avenue'
COLBECK = 'Colbeck Street'
ANNETTE = 'Annette Street'
EXPECTED_EDGE_IDS = {7963621, 14014944, 14014945}
EXPECTED_LENGTH_M = 614.0
LENGTH_TOL_M = 15.0
NORTHERN_COLBECK_ID = 13466437
SOUTHERN_COLBECK_ID = 13466420


@pytest.fixture(scope='module')
def street_graphs():
    ix = gpd.read_file(data_path('tcl_intersections.geojson'))
    st = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix)
    return tg.build_street_graphs(st)


@pytest.fixture(scope='module')
def armadale(street_graphs):
    graph = street_graphs.get(ARMADALE)
    assert graph is not None, 'Armadale Avenue graph missing'
    return graph


def test_armadale_colbeck_annette_path_exists(armadale):
    pick = tg.pick_intersection_pair(armadale, ARMADALE, COLBECK, ANNETTE)
    assert pick is not None
    assert pick.id_start == NORTHERN_COLBECK_ID
    assert set(tg.path_centreline_ids(pick.edges)) == EXPECTED_EDGE_IDS
    assert abs(pick.length_m - EXPECTED_LENGTH_M) < LENGTH_TOL_M


def test_armadale_path_length_meters(armadale):
    pick = tg.pick_intersection_pair(armadale, ARMADALE, COLBECK, ANNETTE)
    assert pick is not None
    line_m = tg.path_to_linestring(
        pick.edges, pick.id_start, pick.id_end, use_meters=True,
    )
    assert abs(line_m.length - EXPECTED_LENGTH_M) < LENGTH_TOL_M


def test_pick_northern_colbeck_not_southern_dead_end(armadale):
    pick = tg.pick_intersection_pair(armadale, ARMADALE, COLBECK, ANNETTE)
    assert pick is not None
    assert pick.id_start == NORTHERN_COLBECK_ID
    assert pick.id_start != SOUTHERN_COLBECK_ID

    southern_path = tg.shortest_path(armadale, SOUTHERN_COLBECK_ID, pick.id_end)
    assert southern_path is None or len(southern_path) > len(pick.edges)


def test_disconnected_pair_returns_none(armadale):
    ids = tg.resolve_intersection_ids(ARMADALE, COLBECK)
    annette_ids = tg.resolve_intersection_ids(ARMADALE, ANNETTE)
    assert len(ids) >= 2
    assert len(annette_ids) >= 1
    # Same intersection node — no block span
    path = tg.shortest_path(armadale, ids[0], ids[0])
    assert path == []


def test_resolve_intersection_ids_colbeck(armadale):
    del armadale
    ids = tg.resolve_intersection_ids(ARMADALE, COLBECK)
    assert NORTHERN_COLBECK_ID in ids
    assert SOUTHERN_COLBECK_ID in ids


def test_slice_path_returns_gps_linestring(armadale):
    pick = tg.pick_intersection_pair(armadale, ARMADALE, COLBECK, ANNETTE)
    assert pick is not None
    geom = tg.slice_path_between(pick.edges, pick.id_start, pick.id_end)
    assert geom.geom_type == 'LineString'
    assert geom.length > 0
    line_m = transform(project_to_meters, geom)
    assert abs(line_m.length - EXPECTED_LENGTH_M) < LENGTH_TOL_M

"""Tests for block_to_terminus geographic terminus and cul-de-sac paths."""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pyproj
import pytest
from shapely.geometry import LineString
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import geometry_engine as ge
import tcl_graph as tg
from parse_format import row_to_parsed
from paths import data_path

# Synthetic line: high easting at param 0 (Leslie mouth), low easting at param 1 (west tip).
_EQUESTRIAN_LIKE_M = LineString([(630934.0, 4851738.0), (630802.0, 4851700.0)])


def test_terminus_dist_west_is_geographic_not_param_zero() -> None:
    west = ge._terminus_dist_on_line(_EQUESTRIAN_LIKE_M, 'west')
    east = ge._terminus_dist_on_line(_EQUESTRIAN_LIKE_M, 'east')
    assert west > east
    assert abs(west - _EQUESTRIAN_LIKE_M.length) < 1.0
    assert east < 1.0


def test_terminus_dist_north_south_use_northing() -> None:
    line = LineString([(0.0, 0.0), (0.0, 100.0)])
    assert ge._terminus_dist_on_line(line, 'north') > ge._terminus_dist_on_line(line, 'south')
    assert abs(ge._terminus_dist_on_line(line, 'north') - 100.0) < 1.0


@pytest.fixture(scope='module')
def street_graphs():
    ix = gpd.read_file(data_path('tcl_intersections.geojson'))
    st = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix)
    graphs = tg.build_street_graphs(st)
    ge.street_graphs = graphs
    ge.street_index = ge._build_street_index(st)
    ge.intersections_gdf = ix
    return graphs


def test_equestrian_court_leslie_to_west_end(street_graphs) -> None:
    """Leslie Street + west end of Equestrian Court → span along the court, not ZERO_SPAN."""
    import pandas as pd

    import tcl_highway_resolve as thr

    thr.build_index(legal_keys=set(ge.street_index.keys()), base_to_legals={})

    row = pd.read_csv(data_path('parsed_successes.csv'))
    match = row[row['_id'] == 10241]
    if match.empty:
        pytest.skip('row 10241 not in parsed_successes.csv')
    r = match.iloc[0]
    parsed = row_to_parsed(r)
    highway = ge.highway_from_row(r)

    result = ge.slice_block_to_terminus_path(
        highway,
        parsed['start_intersection'],
        parsed['terminus_direction'],
    )
    assert result.reason_code is None, result.detail
    assert result.geometry is not None
    assert not result.geometry.is_empty
    to_m = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
    length_m = transform(to_m, result.geometry).length
    assert length_m > 50

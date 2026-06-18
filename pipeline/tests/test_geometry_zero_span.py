"""Tests for ZERO_SPAN geometry outcome and offset recovery."""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import geo_indices as gi
import geometry_engine as ge  # noqa: E402
import tcl_graph as tg  # noqa: E402
from geo_slice import ZERO_SPAN, SliceResult  # noqa: E402
from parse_format import highway_from_row, row_to_parsed  # noqa: E402
from paths import data_path  # noqa: E402


def test_zero_span_result_not_ok() -> None:
    result = SliceResult(None, ZERO_SPAN, 'anchor equals terminus; no mappable span')
    assert not result.ok
    assert result.reason_code == ZERO_SPAN


def test_offset_point_dist_inbound_when_past_line_end() -> None:
    line = LineString([(0.0, 0.0), (200.0, 0.0)])
    anchor = 200.0
    metric = ge._offset_point_dist(line, anchor, 30.5, 'north')
    assert metric < anchor - 25.0
    assert metric > anchor - 35.0


@pytest.fixture(scope='module')
def geo_env():
    ix = gpd.read_file(data_path('tcl_intersections.geojson'))
    st = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix)
    gi.street_graphs = tg.build_street_graphs(st)
    gi.street_index = gi._build_street_index(st)
    gi.intersections_gdf = ix
    import tcl_highway_resolve as thr

    thr.build_index(legal_keys=set(gi.street_index.keys()), base_to_legals={})
    return gi


def test_offset_to_intersect_heath_glen_recovers(geo_env) -> None:
    import pandas as pd

    row = pd.read_csv(data_path('parsed_successes.csv'))
    fl = pd.read_csv(data_path('failure_ledger.csv'))
    zs = fl[fl['reason_code'] == 'ZERO_SPAN']
    match = zs[
        zs['between'].astype(str).str.contains('Heath Street East', case=False, na=False)
        & zs['between'].astype(str).str.contains('Glen Elm', case=False, na=False)
    ]
    if match.empty:
        pytest.skip('Heath/Glen ZERO_SPAN row not in ledger')
    r = row[row['_id'].astype(str) == str(match.iloc[0]['row_id'])].iloc[0]
    parsed = row_to_parsed(r)
    result = ge.slice_street(highway_from_row(r), parsed, bylaw_highway=r['Highway'])
    assert result.reason_code is None, result.detail
    assert result.geometry is not None
    assert not result.geometry.is_empty

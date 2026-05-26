"""Integration checks for highway leg strip + component-qualified blocks."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import tcl_highway_resolve as thr  # noqa: E402
from parse_format import row_to_parsed  # noqa: E402

pytest.importorskip('geopandas')

import geometry_engine as ge  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def _geo_indexes():
    assert ge.street_graphs, 'geometry_engine indexes must be loaded'
    yield


@pytest.mark.parametrize(
    ('row_id', 'bylaw_highway'),
    [
        (10294, 'Joyce Parkway (south leg)'),
        (10377, 'Joyce Parkway (north leg)'),
    ],
)
def test_joyce_parkway_legs_slice(row_id: int, bylaw_highway: str) -> None:
    parsed = pd.read_csv(ROOT / 'data/parsed_successes.csv')
    row = parsed.loc[parsed['_id'] == row_id].iloc[0]
    lookup = thr.tcl_lookup_key(bylaw_highway)
    assert lookup == 'joyce parkway'

    result = ge.slice_street(
        lookup,
        row_to_parsed(row),
        bylaw_highway=bylaw_highway,
    )
    assert result.geometry is not None, (
        f'row {row_id}: {result.reason_code} {result.detail}'
    )

"""Integration checks for highway leg strip + component-qualified blocks."""


import pandas as pd
import pytest

from parking_pipeline import tcl_highway_resolve as thr  # noqa: E402
from parking_pipeline.parse_format import row_to_parsed  # noqa: E402

pytest.importorskip('geopandas')

from parking_pipeline import geometry_engine as ge  # noqa: E402
from parking_pipeline.geo_indices import init_geo  # noqa: E402
from parking_pipeline.paths import data_path  # noqa: E402


@pytest.fixture(scope='module', autouse=True)
def _geo_indexes():
    init_geo()
    yield


@pytest.mark.parametrize(
    ('row_id', 'bylaw_highway'),
    [
        (10294, 'Joyce Parkway (south leg)'),
        (10377, 'Joyce Parkway (north leg)'),
    ],
)
def test_joyce_parkway_legs_slice(row_id: int, bylaw_highway: str) -> None:
    successes = data_path('parsed_successes.csv')
    if not successes.exists():
        pytest.skip('parsed_successes.csv not present locally')
    parsed = pd.read_csv(successes)
    matching = parsed.loc[parsed['_id'] == row_id]
    if matching.empty:
        pytest.skip(f'row {row_id} not present in parsed_successes.csv')
    row = matching.iloc[0]
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

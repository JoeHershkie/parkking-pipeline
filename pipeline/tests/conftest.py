"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import shutil

import geopandas as gpd
import pytest

from parking_pipeline.paths import DATA_DIR, data_path

_REQUIRED_TEST_STREETS = {
    'test_intersection_pair_resolve.py': (
        'cardiff',
        'fairfield',
        'bond',
        'duncairn',
    ),
    'test_disjoint_block.py': ('manning',),
}

_FULL_LANE_INDEX_TESTS = {
    'test_resolve_bloor_royal_york',
    'test_resolve_christie_with_bloor_between',
    'test_lookup_highway_key_lane',
    'test_resolve_generic_lane_block_sibley_victoria_stays_ambiguous_with_graph',
    'test_equestrian_court_leslie_to_west_end',
    'test_tcl_search_tokens_suffix_remap_from_street_index',
}


@pytest.fixture(scope='session', autouse=True)
def ensure_local_tcl_data() -> None:
    """Use committed samples when full TCL downloads are not present."""
    for name in ('tcl_streets.geojson', 'tcl_intersections.geojson', 'tcl_street_names.csv'):
        target = data_path(name)
        sample = DATA_DIR / 'samples' / name
        if not target.exists() and sample.exists():
            shutil.copy(sample, target)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip geo integration tests when sample TCL lacks required streets."""
    streets_path = data_path('tcl_streets.geojson')
    if not streets_path.exists():
        return
    legal = (
        gpd.read_file(streets_path)['LINEAR_NAME_FULL_LEGAL']
        .fillna('')
        .str.lower()
    )
    for item in items:
        if item.name in _FULL_LANE_INDEX_TESTS:
            item.add_marker(
                pytest.mark.skip(reason='full TCL data required (not in sample fixtures)'),
            )
            continue
        required = _REQUIRED_TEST_STREETS.get(item.path.name)
        if not required:
            continue
        if not all(legal.str.contains(kw, regex=False).any() for kw in required):
            item.add_marker(
                pytest.mark.skip(reason='full TCL data required (street not in sample fixtures)'),
            )

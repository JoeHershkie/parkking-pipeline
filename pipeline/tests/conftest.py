"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import geopandas as gpd
import pytest

from parking_pipeline.paths import data_path
from sample_data import ensure_sample_data_copies, using_sample_tcl

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

_PARSED_ARTIFACT_TESTS = {
    'test_offset_to_intersect_heath_glen_recovers',
}

_GEOMETRY_GOLDEN_TESTS = {
    'test_geometry_golden_matches_fixture',
    'test_geometry_golden_exercises_geo_slice',
}

ensure_sample_data_copies()


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

    parsed_successes = data_path('parsed_successes.csv')

    for item in items:
        if item.name in _PARSED_ARTIFACT_TESTS and not parsed_successes.exists():
            item.add_marker(
                pytest.mark.skip(reason='parsed_successes.csv required (generated locally)'),
            )
            continue

        if item.name in _GEOMETRY_GOLDEN_TESTS and not using_sample_tcl():
            item.add_marker(
                pytest.mark.skip(
                    reason='geometry golden regression uses committed sample TCL fixtures',
                ),
            )
            continue

        if item.name in _FULL_LANE_INDEX_TESTS and using_sample_tcl():
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


@pytest.fixture(scope='session', autouse=True)
def ensure_local_tcl_data() -> None:
    """Session hook retained for backwards compatibility (copies run at import)."""
    ensure_sample_data_copies()

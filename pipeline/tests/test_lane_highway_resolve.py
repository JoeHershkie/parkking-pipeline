"""Tests for lane / laneway highway phrase parsing and TCL ``ln`` resolution."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from parking_pipeline.lane_highway_resolve import (  # noqa: E402
    infer_lane_phrase_from_between,
    lane_phrases_from_between,
    parse_lane_highway_phrase,
    resolve_lane_highway,
    lookup_highway_key,
)
from parking_pipeline.paths import data_path  # noqa: E402
from parking_pipeline import tcl_highway_resolve as thr  # noqa: E402
from parking_pipeline.tcl_highway_key import tcl_highway_key  # noqa: E402


@pytest.fixture(scope='module')
def tcl_lane_index() -> None:
    tcl = pd.read_csv(data_path('tcl_street_names.csv'))
    thr.build_index_from_csv(legal_keys={
        tcl_highway_key(x) for x in tcl['linear_name_full_legal'].dropna()
    })


def test_parse_lane_position() -> None:
    phrase = parse_lane_highway_phrase('Lane first north of Bloor Street West')
    assert phrase is not None
    assert phrase.direction == 'north'
    assert phrase.anchor == 'Bloor Street West'
    assert phrase.ordinal == 'first'


def test_infer_from_between_generic_lane() -> None:
    phrase = infer_lane_phrase_from_between(
        'Lane',
        "Sandra Road and north end, first west of O'Connor Drive",
    )
    assert phrase is not None
    assert phrase.direction == 'west'
    assert "O'Connor Drive" in phrase.anchor


def test_lane_phrases_from_between_two_clauses() -> None:
    phrases = lane_phrases_from_between(
        'Lane first north of Bloor Street West and a point 25 metres north thereof',
    )
    assert len(phrases) == 1
    assert phrases[0].direction == 'north'
    assert phrases[0].anchor == 'Bloor Street West'


def test_resolve_bloor_royal_york(tcl_lane_index: None) -> None:
    key = resolve_lane_highway(
        'Lane first north of Bloor Street West',
        'Royal York Road and a point 50 metres west',
    )
    assert key == 'ln n bloor e royal york'


def test_resolve_christie_with_bloor_between(tcl_lane_index: None) -> None:
    key = resolve_lane_highway(
        'Lane first east of Christie Street',
        'Lane first north of Bloor Street West and a point 25 metres north thereof',
    )
    assert key == 'ln 1 n bloor e christie'


def test_lookup_highway_key_lane(tcl_lane_index: None) -> None:
    key = lookup_highway_key(
        'Lane first north of Bloor Street West',
        'Royal York Road and a point 50 metres west',
    )
    assert key == 'ln n bloor e royal york'


def test_resolve_christie_without_cross_is_ambiguous(tcl_lane_index: None) -> None:
    key = resolve_lane_highway('Lane first east of Christie Street', '')
    assert key is None


@pytest.fixture(scope='module')
def tcl_lane_graph_index() -> None:
    """Street index + graphs for lane block disambiguation (needs TCL geo files)."""
    import geopandas as gpd
    from parking_pipeline import tcl_graph as tg

    from parking_pipeline.lane_highway_resolve import reset_lane_resolve_caches

    streets = data_path('tcl_streets.geojson')
    ix_path = data_path('tcl_intersections.geojson')
    if not streets.exists() or not ix_path.exists():
        pytest.skip('TCL geo files not present')

    reset_lane_resolve_caches()
    tcl = pd.read_csv(data_path('tcl_street_names.csv'))
    thr.build_index_from_csv(legal_keys={
        tcl_highway_key(x) for x in tcl['linear_name_full_legal'].dropna()
    })
    tg.configure_intersections(gpd.read_file(ix_path))


def test_resolve_generic_lane_block_sibley_victoria_stays_ambiguous_with_graph(
    tcl_lane_graph_index: None,
) -> None:
    """
    Block crosses + lane tail: east/west ``ln n danforth`` families both match;
    no single legal key has a graph path Sibley ↔ Victoria Park — do not guess.
    """
    between = (
        'Sibley Avenue and Victoria Park Avenue, first north of Danforth Avenue'
    )
    parsed = {
        'rule_type': 'block',
        'start_intersection': 'Sibley Avenue',
        'end_intersection': 'Victoria Park Avenue',
    }
    assert resolve_lane_highway('Lane', between, parsed) is None


def test_resolve_generic_lane_block_stays_ambiguous_without_graph(
    tcl_lane_index: None,
) -> None:
    """CSV-only index: multiple ``ln`` keys, no graph → no guess."""
    between = (
        'Sibley Avenue and Victoria Park Avenue, first north of Danforth Avenue'
    )
    parsed = {
        'rule_type': 'block',
        'start_intersection': 'Sibley Avenue',
        'end_intersection': 'Victoria Park Avenue',
    }
    assert resolve_lane_highway('Lane', between, parsed) is None

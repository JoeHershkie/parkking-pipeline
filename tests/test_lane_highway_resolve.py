"""Tests for lane / laneway highway phrase parsing and TCL ``ln`` resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from lane_highway_resolve import (  # noqa: E402
    infer_lane_phrase_from_between,
    lane_phrases_from_between,
    parse_lane_highway_phrase,
    resolve_lane_highway,
    lookup_highway_key,
)
from paths import data_path  # noqa: E402
import tcl_highway_resolve as thr  # noqa: E402
from tcl_highway_key import tcl_highway_key  # noqa: E402


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

"""Tests for resolve_rows stage."""

from pathlib import Path

import pandas as pd
import pytest

from parking_pipeline.parse_format import highway_from_row, resolve_columns_for_row, row_to_parsed  # noqa: E402
from parking_pipeline.resolve_rows import RESOLVE_STREET_NOT_FOUND, _init_resolve_index, resolve_rows  # noqa: E402


@pytest.fixture(scope='module')
def resolve_index():
    _init_resolve_index()


def test_resolve_columns_spadina(resolve_index) -> None:
    row = pd.Series({
        'Highway': 'Spadina Avenue',
        'Between': 'Entire length',
        'rule_type': 'entire_length',
    })
    parsed = row_to_parsed(row)
    cols = resolve_columns_for_row(row, parsed)
    assert cols['resolve_valid'] is True
    assert cols['highway_resolved'] == 'spadina avenue'


def test_highway_from_row_prefers_resolved() -> None:
    row = pd.Series({
        'Highway': 'Spadina Avenue',
        'Between': 'Entire length',
        'highway_resolved': 'spadina avenue',
        'resolve_valid': True,
        'rule_type': 'entire_length',
    })
    assert highway_from_row(row) == 'spadina avenue'


def test_resolve_rows_unknown_highway(resolve_index) -> None:
    df = pd.DataFrame([{
        '_id': 'test-resolve-1',
        'Highway': 'Totally Fake Street XYZ',
        'Between': 'Entire length',
        'parse_valid': True,
        'rule_type': 'entire_length',
    }])
    out, failures = resolve_rows(df)
    assert failures[RESOLVE_STREET_NOT_FOUND] == 1
    assert out.iloc[0]['resolve_valid'] == False  # noqa: E712

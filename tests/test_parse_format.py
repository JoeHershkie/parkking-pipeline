"""Tests for parse validation and norm columns."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from parse_format import (  # noqa: E402
    highway_from_row,
    norm_anchor,
    norm_columns_for_row,
    row_to_parsed,
    validate_parsed,
)


def test_validate_rejects_point_metres_anchor() -> None:
    parsed = {
        'rule_type': 'perfect_offset',
        'start_intersection': 'A point 59.4 metres north of Kintyre Avenue',
        'distance': '62.5',
        'direction': 'north',
    }
    ok, err = validate_parsed(parsed)
    assert not ok
    assert 'invalid anchor' in err


def test_validate_accepts_block() -> None:
    parsed = {
        'rule_type': 'block',
        'start_intersection': 'Appleton Avenue',
        'end_intersection': 'Brock Street',
    }
    ok, err = validate_parsed(parsed)
    assert ok, err


def test_norm_anchor_uses_abbreviation() -> None:
    assert 'st' in norm_anchor('Bathurst Street')
    assert norm_anchor('') == ''


def test_norm_columns_and_row_to_parsed_prefers_norm() -> None:
    parsed = {
        'rule_type': 'block',
        'start_intersection': 'Bathurst Street',
        'end_intersection': 'Bloor Street',
    }
    norms = norm_columns_for_row(parsed, 'Spadina Avenue')
    row = {
        **parsed,
        **norms,
        'Highway': 'Spadina Avenue',
        'parse_valid': True,
        'parse_error': '',
    }
    assert norms['highway_norm'] == norm_anchor('Spadina Avenue')
    assert norms['start_intersection_norm'] == norm_anchor('Bathurst Street')
    rebuilt = row_to_parsed(row)
    assert rebuilt['start_intersection'] == 'Bathurst Street'
    assert highway_from_row(row) == 'spadina avenue'


@pytest.mark.parametrize(
    ('rule_type', 'parsed_extra', 'should_pass'),
    [
        ('entire_length', {}, True),
        ('offset_span', {
            'start_intersection': 'Kintyre Avenue',
            'dist1': '59.4',
            'dist2': '62.5',
            'dir1': 'north',
        }, True),
        ('perfect_offset', {'start_intersection': 'X', 'direction': 'north'}, False),
    ],
)
def test_validate_required_fields(
    rule_type: str, parsed_extra: dict, should_pass: bool,
) -> None:
    parsed = {'rule_type': rule_type, **parsed_extra}
    ok, _ = validate_parsed(parsed)
    assert ok is should_pass

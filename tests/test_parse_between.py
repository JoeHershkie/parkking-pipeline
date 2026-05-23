"""Tests for parse_between misparse fixes."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from parse_between import (  # noqa: E402
    normalize_anchor_phrase,
    parse_between,
)


@pytest.mark.parametrize(
    ('phrase', 'expected'),
    [
        (
            'a point opposite the east limit of Baxter Street',
            'Baxter Street',
        ),
        (
            'a point opposite the westerly limit of Long Branch Avenue',
            'Long Branch Avenue',
        ),
        (
            "a point opposite St. Edmund's Drive",
            "St. Edmund's Drive",
        ),
        (
            'the westerly limit of Columbia Gate',
            'Columbia Gate',
        ),
        ('Yonge Street', 'Yonge Street'),
    ],
)
def test_normalize_anchor_phrase(phrase: str, expected: str) -> None:
    assert normalize_anchor_phrase(phrase) == expected


@pytest.mark.parametrize(
    ('between', 'rule_type', 'checks'),
    [
        (
            'A point approximately 234 metres north of Cottingham Road and a point 75 metres further north',
            'relative_extension',
            {'start_intersection': 'Cottingham Road', 'dist1': '234', 'dist2': '75'},
        ),
        (
            'A point 59.4 metres north of Kintyre Avenue and a point 62.5 metres north',
            'offset_span',
            {'start_intersection': 'Kintyre Avenue', 'dist1': '59.4', 'dist2': '62.5', 'dir1': 'north'},
        ),
        (
            'Yonge Street and a point 5.4 metres east of a point opposite the east limit of Baxter Street',
            'intersect_to_offset',
            {
                'start_intersection': 'Yonge Street',
                'offset_intersection': 'Baxter Street',
                'distance': '5.4',
            },
        ),
        (
            "A point opposite St. Edmund's Drive and a point 38.1 metres north",
            'perfect_offset',
            {'start_intersection': "St. Edmund's Drive", 'distance': '38.1'},
        ),
        (
            'A point 142 metres north of Eastern Avenue and a point 36 metres north',
            'offset_span',
            {'start_intersection': 'Eastern Avenue', 'dist1': '142', 'dist2': '36'},
        ),
        (
            'A point 135.7 metres west of Willard Avenue and a point 61 metres east',
            'offset_span',
            {
                'start_intersection': 'Willard Avenue',
                'dist1': '135.7',
                'dist2': '61',
                'dir1': 'west',
                'dir2': 'east',
            },
        ),
    ],
)
def test_parse_misparse_fixes(between: str, rule_type: str, checks: dict) -> None:
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == rule_type
    for key, val in checks.items():
        assert parsed.get(key) == val, f'{key}: got {parsed.get(key)!r}'

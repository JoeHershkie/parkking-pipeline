"""Tests for parse_normalize."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from parse_normalize import normalize_anchor_phrase, normalize_parsed  # noqa: E402
from parse_format import validate_parsed  # noqa: E402


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


def test_normalize_parsed_splits_qualifiers() -> None:
    parsed = normalize_parsed({
        'rule_type': 'block',
        'start_intersection': 'Penn Drive (northwest intersection)',
        'end_intersection': 'Finch Avenue West',
    })
    assert parsed['start_intersection'] == 'Penn Drive'
    assert parsed['start_intersection_qualifier'] == 'northwest intersection'
    ok, err = validate_parsed(parsed)
    assert ok, err

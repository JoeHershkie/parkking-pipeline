"""Tests for parse validation and norm columns."""


import pytest

from parking_pipeline.parse_format import (  # noqa: E402
    apply_trailing_qualifiers,
    highway_from_row,
    norm_anchor,
    norm_columns_for_row,
    row_to_parsed,
    split_trailing_qualifier,
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


def test_validate_accepts_compass_offset_direction() -> None:
    parsed = {
        'rule_type': 'offset_to_intersect',
        'start_intersection': 'Penn Drive',
        'end_intersection': 'Finch Avenue West',
        'distance': '198',
        'direction': 'southeast',
    }
    ok, err = validate_parsed(parsed)
    assert ok, err


def test_split_trailing_qualifier() -> None:
    name, qual = split_trailing_qualifier('Penn Drive (northwest intersection)')
    assert name == 'Penn Drive'
    assert qual == 'northwest intersection'


def test_apply_trailing_qualifiers_on_block() -> None:
    parsed = apply_trailing_qualifiers({
        'rule_type': 'block',
        'start_intersection': 'Penn Drive (northwest intersection)',
        'end_intersection': 'Finch Avenue West',
    })
    assert parsed['start_intersection'] == 'Penn Drive'
    assert parsed['start_intersection_qualifier'] == 'northwest intersection'
    ok, err = validate_parsed(parsed)
    assert ok, err


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


def test_highway_from_row_uses_resolved_column() -> None:
    row = {
        'Highway': 'Wrong Name Street',
        'Between': 'Entire length',
        'highway_resolved': 'spadina avenue',
        'resolve_valid': True,
        'rule_type': 'entire_length',
    }
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

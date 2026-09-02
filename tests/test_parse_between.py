"""Tests for parse_between misparse fixes."""


import pytest

from parking_pipeline.parse_between import (  # noqa: E402
    normalize_anchor_phrase,
    normalize_parsed,
    parse_between,
    preprocess_between,
)
from parking_pipeline.parse_format import validate_parsed  # noqa: E402


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


def test_preprocess_between_fixes_metres_spacing() -> None:
    assert '98 metres north' in preprocess_between(
        'A point 98 metresnorth of Ridge Hill Drive and the south end of Elm Ridge Circle',
    )
    assert '3604 metres west' in preprocess_between(
        'A point 3604metres west of Neilson Road and a point 28 metres further west',
    )


@pytest.mark.parametrize(
    ('between', 'expected_fragment'),
    [
        ('Yonge Street to Victoria Street', 'Yonge Street and Victoria Street'),
        (
            'A point 127 metres west of Christie Street to a point 30 metres further west',
            'Christie Street and a point 30 metres further west',
        ),
        ('Adjacent to Bessborough School', 'Adjacent to Bessborough School'),
        (
            '8:00 a.m. to 9:00 a.m. and 3:00 p.m. to 4:00 p.m., Mon. to Fri.',
            '8:00 a.m. to 9:00 a.m.',
        ),
    ],
)
def test_preprocess_between_replaces_joiner_to(between: str, expected_fragment: str) -> None:
    assert expected_fragment in preprocess_between(between)


def test_parse_between_joiner_to() -> None:
    parsed = parse_between('Yonge Street to Victoria Street')
    assert parsed is not None
    assert parsed['rule_type'] == 'block'
    assert parsed['start_intersection'] == 'Yonge Street'
    assert parsed['end_intersection'] == 'Victoria Street'


def test_normalize_parsed_splits_qualifiers() -> None:
    parsed = normalize_parsed({
        'rule_type': 'block',
        'start_intersection': 'Penn Drive (northwest intersection)',
        'end_intersection': 'Finch Avenue West',
    })
    assert parsed['start_intersection'] == 'Penn Drive'
    assert parsed['start_intersection_qualifier'] == 'northwest intersection'


@pytest.mark.parametrize(
    ('between', 'checks'),
    [
        (
            'Milvan Drive (northwest intersection) and Milvan Drive (southeast intersection)',
            {
                'rule_type': 'parenthetical_dual_block',
                'start_intersection': 'Milvan Drive',
                'end_intersection': 'Milvan Drive',
                'start_intersection_qualifier': 'northwest intersection',
                'end_intersection_qualifier': 'southeast intersection',
            },
        ),
        (
            'Husband Drive (west intersection) and Husband Drive (east intersection)',
            {
                'rule_type': 'parenthetical_dual_block',
                'start_intersection_qualifier': 'west intersection',
                'end_intersection_qualifier': 'east intersection',
            },
        ),
        (
            'Eugene Street (south intersection) and Caledonia Road (south intersection)',
            {
                'rule_type': 'parenthetical_dual_block',
                'start_intersection': 'Eugene Street',
                'end_intersection': 'Caledonia Road',
            },
        ),
    ],
)
def test_parse_between_parenthetical_dual_block(
    between: str, checks: dict,
) -> None:
    parsed = parse_between(between)
    assert parsed is not None
    for key, expected in checks.items():
        assert parsed[key] == expected


@pytest.mark.parametrize(
    ('between', 'rule_type', 'checks'),
    [
        (
            'A point 198 metres southeast of Penn Drive (northwest intersection) and Finch Avenue West',
            'offset_to_intersect',
            {
                'start_intersection': 'Penn Drive',
                'end_intersection': 'Finch Avenue West',
                'distance': '198',
                'direction': 'southeast',
                'start_intersection_qualifier': 'northwest intersection',
            },
        ),
        (
            'A point 405 metres south and west of Sheppard Avenue East and the west end of Settlers Road',
            'block_to_terminus',
            {
                'start_intersection': 'Sheppard Avenue East',
                'distance': '405',
                'direction': 'south',
                'terminus_street': 'Settlers Road',
            },
        ),
        (
            'Dundas Street and a point 104 metres northwest of Central Park Roadway (west intersection)',
            'intersect_to_offset',
            {
                'start_intersection': 'Dundas Street',
                'offset_intersection': 'Central Park Roadway',
                'distance': '104',
                'direction': 'northwest',
            },
        ),
        (
            'Marlee Avenue and a point 43 metres west (Cul-de-sac)',
            'perfect_offset',
            {
                'start_intersection': 'Marlee Avenue',
                'distance': '43',
                'direction': 'west',
            },
        ),
        (
            'A point 40.5 metres east and the east end of Labatt Avenue',
            'block_to_terminus',
            {
                'start_intersection': 'Labatt Avenue',
                'terminus_street': 'Labatt Avenue',
                'distance': '40.5',
                'direction': 'east',
            },
        ),
    ],
)
def test_parse_metric_anchor_upgrades(between: str, rule_type: str, checks: dict) -> None:
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == rule_type
    ok, err = validate_parsed(parsed)
    assert ok, err
    for key, val in checks.items():
        assert parsed.get(key) == val, f'{key}: got {parsed.get(key)!r}'


@pytest.mark.parametrize(
    ('raw', 'expected_fragment'),
    [
        ('Bathurst Street and a point 91.5 metres west Bathurst Street', 'west of Bathurst'),
        (
            'A point 342 metres south and Sheppard Avenue East',
            'metres south and Sheppard Avenue East',
        ),
        ('Greenwin Village Road and a point 321 metres south/west of Bison Drive', 'south and west of'),
        ('A point ppposite the southerly limit', 'opposite the southerly limit'),
    ],
)
def test_preprocess_between_point_and_street_fixes(raw: str, expected_fragment: str) -> None:
    assert expected_fragment in preprocess_between(raw)


@pytest.mark.parametrize(
    ('between', 'rule_type'),
    [
        (
            'A point 70 metres north and west of Horsham Avenue and Tamworth Road',
            'offset_to_intersect',
        ),
        (
            'Seneca Hill Drive and a point 155 metres southeast of Seneca Hill Drive',
            'intersect_to_offset',
        ),
        (
            'Spring Garden Avenue and a point 88 metres south thereof',
            'intersect_extension',
        ),
        (
            'A point 79 metres east of Scott Road and a point 25 metres further east thereof',
            'relative_extension',
        ),
        (
            'A point 45.8 metres west of Jethro Road and a point 76.3 metres north and west of Jethro Road',
            'dual_anchor',
        ),
        (
            'A point opposite the southerly limit of Glenbrook Avenue and Glengrove Avenue',
            'block',
        ),
    ],
)
def test_parse_point_and_street_patterns(between: str, rule_type: str) -> None:
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == rule_type
    ok, err = validate_parsed(parsed)
    assert ok, err


@pytest.mark.parametrize(
    ('between', 'rule_type'),
    [
        (
            'Yonge Street and a point opposite the easterly limit of Botham Road',
            'block',
        ),
        (
            'The south end of Flint Road and a point 40 metres north',
            'terminus_end_metric',
        ),
        (
            'A point 342 metres south and Sheppard Avenue East',
            'perfect_offset',
        ),
        (
            'Emily Avenue and 100 metres west',
            'perfect_offset',
        ),
        (
            'Royal York Road and and a point 103.5 metres east of Royal York Road',
            'intersect_to_offset',
        ),
    ],
)
def test_parse_abc_wave_patterns(between: str, rule_type: str) -> None:
    parsed = parse_between(between)
    assert parsed is not None, between
    assert parsed['rule_type'] == rule_type
    ok, err = validate_parsed(parsed)
    assert ok, err


def test_parse_block_lane_tail_sibley_victoria_danforth() -> None:
    between = (
        'Sibley Avenue and Victoria Park Avenue, first north of Danforth Avenue'
    )
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == 'block'
    assert parsed['start_intersection'] == 'Sibley Avenue'
    assert parsed['end_intersection'] == 'Victoria Park Avenue'
    ok, err = validate_parsed(parsed)
    assert ok, err


@pytest.mark.parametrize(
    ('between', 'rule_type', 'checks'),
    [
        (
            "The west end of Dora Avenue and St. Helen's Avenue",
            'block_to_terminus',
            {
                'terminus_direction': 'west',
                'terminus_street': 'Dora Avenue',
                'start_intersection': "St. Helen's Avenue",
            },
        ),
        (
            'A point 37 metres east of Muirhead Road and a point opposite the '
            'southerly limit of Endsleigh Crescent',
            'offset_to_intersect',
            {
                'start_intersection': 'Muirhead Road',
                'end_intersection': 'Endsleigh Crescent',
                'distance': '37',
                'direction': 'east',
            },
        ),
        (
            'Coxwell Avenue and northeast end of Robbins Avenue',
            'block_to_terminus',
            {
                'start_intersection': 'Coxwell Avenue',
                'terminus_direction': 'northeast',
                'terminus_street': 'Robbins Avenue',
            },
        ),
        (
            'The south end of Flint Road and a point opposite the northerly '
            'limit of Supertest Road',
            'block_to_terminus',
            {
                'terminus_direction': 'south',
                'terminus_street': 'Flint Road',
                'start_intersection': 'Supertest Road',
            },
        ),
        (
            'A point 275 metres west northwest of Ambercroft Boulevard '
            '(south intersection) and a point 85 metres further northwest',
            'relative_extension',
            {
                'start_intersection': 'Ambercroft Boulevard',
                'start_intersection_qualifier': 'south intersection',
                'dist1': '275',
                'dist2': '85',
                'dir1': 'west',
            },
        ),
        (
            'Yorkminster Road (southwest intersection) and a point opposite the '
            'southerly limit of Montressor Drive',
            'parenthetical_block',
            {
                'start_intersection': 'Yorkminster Road',
                'end_intersection': 'Montressor Drive',
                'start_intersection_qualifier': 'southwest intersection',
            },
        ),
        (
            'The west end of Wallace Avenue and a point 48 metres west of '
            'Symington Avenue',
            'block_to_terminus',
            {
                'terminus_street': 'Wallace Avenue',
                'start_intersection': 'Symington Avenue',
            },
        ),
    ],
)
def test_parse_338_cohort_patterns(
    between: str, rule_type: str, checks: dict,
) -> None:
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == rule_type
    ok, err = validate_parsed(parsed)
    assert ok, err
    for key, val in checks.items():
        assert parsed.get(key) == val, f'{key}: got {parsed.get(key)!r}'


def test_parse_between_parenthetical_dual_block_not_dual_anchor() -> None:
    """Plain dual-qualified block must not match dual_anchor (metric offsets)."""
    between = (
        'Milvan Drive (northwest intersection) and Milvan Drive (southeast intersection)'
    )
    assert parse_between(between)['rule_type'] == 'parenthetical_dual_block'
    metric = (
        'A point 45.7 metres east of Ellis Avenue (north intersection) and '
        'a point 45.7 metres east of Ellis Avenue (south intersection)'
    )
    assert parse_between(metric)['rule_type'] == 'dual_anchor'


def test_at_a_point_rewritten_to_and_a_point() -> None:
    between = 'Eastbound lane at a point 54 metres east of Freeland Street'
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == 'intersect_to_offset'
    assert parsed['start_intersection'] == 'Eastbound lane'
    assert parsed['offset_intersection'] == 'Freeland Street'
    ok, err = validate_parsed(parsed)
    assert ok, err


def test_at_a_point_chain_parses_block_then_offset() -> None:
    between = (
        'Cummer Avenue at a point 131.5 metres east thereof and Dunfield Avenue'
    )
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == 'intersect_thereof_block'
    assert parsed['start_intersection'] == 'Cummer Avenue'
    assert parsed['end_intersection'] == 'Dunfield Avenue'
    ok, err = validate_parsed(parsed)
    assert ok, err


def test_lane_tail_at_a_point_of_anchor() -> None:
    between = (
        'Cummer Avenue at a point 131.5 metres east of Dunfield Avenue and '
        'Greenfield Avenue'
    )
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == 'intersect_to_offset'
    assert parsed['start_intersection'] == 'Cummer Avenue'
    assert parsed['offset_intersection'] == 'Dunfield Avenue and Greenfield Avenue'


def test_juxtaposed_block_without_and() -> None:
    parsed = parse_between('Alberta Avenue Oakwood Avenue')
    assert parsed is not None
    assert parsed['rule_type'] == 'block'
    assert parsed['start_intersection'] == 'Alberta Avenue'
    assert parsed['end_intersection'] == 'Oakwood Avenue'
    ok, err = validate_parsed(parsed)
    assert ok, err


def test_juxtaposed_block_with_trailing_cardinal() -> None:
    parsed = parse_between('Queen Street West King Street')
    assert parsed is not None
    assert parsed['rule_type'] == 'block'
    assert parsed['start_intersection'] == 'Queen Street West'
    assert parsed['end_intersection'] == 'King Street'


def test_juxtaposed_block_does_not_steal_metric_text() -> None:
    assert parse_between('A point 61 metres south and a point 61 metres north of Humberside Avenue') is not None


def test_dual_offset_with_shared_trailing_anchor() -> None:
    between = 'A point 61 metres south and a point 61 metres north of Humberside Avenue'
    parsed = parse_between(between)
    assert parsed is not None
    assert parsed['rule_type'] == 'offset_span'
    assert parsed['start_intersection'] == 'Humberside Avenue'
    assert parsed['dist1'] == '61'
    assert parsed['dir1'] == 'south'
    assert parsed['dist2'] == '61'
    assert parsed['dir2'] == 'north'
    ok, err = validate_parsed(parsed)
    assert ok, err

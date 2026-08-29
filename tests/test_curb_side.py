"""Tests for Side vocabulary normalization and curb-geometry override lookup."""

from __future__ import annotations

import math

import pytest

from parking_pipeline.curb_side import (
    OVERRIDE_COLUMNS,
    OVERRIDE_FILENAME,
    CurbGeometryOverride,
    load_curb_geometry_overrides,
    override_for_row,
    parse_side,
    resolve_parity_side,
)
from parking_pipeline.paths import data_path

# Exact distinct Side strings from the full clean-targets inventory (129 values).
REAL_SIDE_VOCABULARY = (
    '',
    'All',
    'All Sides',
    'All sides',
    'Both',
    'Centre',
    'Curb sides of cul-de-sac',
    'EASt, north and west',
    'East',
    'East & west',
    'East and North',
    'East and North and South',
    'East and South',
    'East and West',
    'East and north',
    'East and north and west',
    'East and north and west (inner side of the street)',
    'East and north and west and south',
    'East and south',
    'East and south (outer circle)',
    'East and south, West and north',
    'East and west',
    'East side of the roadway west of the median',
    'East, North and West',
    'East, South, West, North',
    'East, north and west',
    'East, north west',
    'East, north, west and south',
    'East, south and west',
    'East, west and north',
    'East, west and south',
    'East/North',
    'East/South',
    'Inner Circle',
    'Inner Perimeter',
    'Inner Perimiter',
    'Inner perimeter, curb of centre traffic island',
    'Inside Perimeter',
    'Lay-by in the south side of centre island',
    'North',
    'North & south',
    'North And East',
    'North and East',
    'North and East and South and West',
    'North and South',
    'North and West',
    'North and east',
    'North and east (south leg)',
    'North and east and south',
    'North and east sides of the outer perimeter',
    'North and south',
    'North and south and west and east',
    'North and west',
    'North and west and north',
    'North end',
    'North side of north leg',
    'North side of the north (westbound) roadway',
    'North, East and South',
    'North, East and West',
    'North, East, West and South',
    'North, South and West',
    'North, South and centre island',
    'North, South, East and West',
    'North, West and South',
    'North, West, and South',
    'North, east and south',
    'North, east and south (inner radius)',
    'North, east and west',
    'North, east, south and west',
    'North, west and south',
    'North, west and south (Inner Radius)',
    'North-East',
    'North/East',
    'North/east',
    'Northeast',
    'Northeast and southwest',
    'Northwest',
    'Odd',
    'Outside Perimeter',
    'South',
    'South (inner radius)',
    'South Adjacent',
    'South And east',
    'South and East',
    'South and West',
    'South and east',
    'South and east and north',
    'South and east and south',
    'South and east sides of the outer perimeter',
    'South and north',
    'South and west',
    'South and west and north',
    'South, East and North',
    'South, East and West',
    'South, east and north',
    'South, east, south and west',
    'South, west and north',
    'South, West and North',
    'South/ East',
    'South/West',
    'South/east',
    'South/west',
    'Southeast',
    'Southwest',
    'West',
    'West And south',
    'West and East',
    'West and North',
    'West and South',
    'West and South and North',
    'West and east',
    'West and north',
    'West and north (outer circle)',
    'West and northwesterly',
    'West and south',
    'West and south and east',
    'West and south and east (outer side of the street)',
    'West side of traffic island',
    'West, North and East',
    'West, North, South and East',
    'West, South and East',
    'West, north and east',
    'West, north and south side',
    'West, north, east, south',
    'West, south and east',
    'West, south, east and north',
    'West/North',
    'West/South',
    'west',
)


@pytest.mark.parametrize('raw', REAL_SIDE_VOCABULARY)
def test_parse_side_preserves_raw_and_never_raises(raw: str) -> None:
    spec = parse_side(raw)
    assert spec.raw == raw
    assert spec.mode in {
        'single', 'wrapping', 'multi', 'parity', 'perimeter', 'specialized', 'unresolved',
    }


@pytest.mark.parametrize(
    ('raw', 'direction'),
    [
        ('North', 'north'),
        ('South', 'south'),
        ('East', 'east'),
        ('West', 'west'),
        ('west', 'west'),
        ('Northeast', 'northeast'),
        ('Northwest', 'northwest'),
        ('Southeast', 'southeast'),
        ('Southwest', 'southwest'),
        ('North-East', 'northeast'),
        ('  West  ', 'west'),
    ],
)
def test_single_cardinal_and_diagonal(raw: str, direction: str) -> None:
    spec = parse_side(raw)
    assert spec.mode == 'single'
    assert spec.directions == (direction,)
    assert spec.wrapping is False
    assert spec.normalized == direction
    assert spec.needs_override is False
    assert spec.selects_multiple_curbs is False


@pytest.mark.parametrize(
    ('raw', 'directions'),
    [
        ('North and east', ('north', 'east')),
        ('North And East', ('north', 'east')),
        ('North and East', ('north', 'east')),
        ('South And east', ('south', 'east')),
        ('West And south', ('west', 'south')),
        ('West/South', ('west', 'south')),
        ('North/East', ('north', 'east')),
        ('North/east', ('north', 'east')),
        ('East/North', ('east', 'north')),
        ('East/South', ('east', 'south')),
        ('South/West', ('south', 'west')),
        ('South/east', ('south', 'east')),
        ('South/west', ('south', 'west')),
        ('South/ East', ('south', 'east')),
        ('West/North', ('west', 'north')),
        ('West and northwesterly', ('west', 'northwest')),
        ('North and west and north', ('north', 'west')),
        ('South and east and south', ('south', 'east')),
        ('EASt, north and west', ('east', 'north', 'west')),
        ('East, north and west', ('east', 'north', 'west')),
        ('North, east and south', ('north', 'east', 'south')),
        ('North, West, and South', ('north', 'west', 'south')),
        ('East, north west', ('east', 'northwest')),
    ],
)
def test_adjacent_compounds_are_wrapping(raw: str, directions: tuple[str, ...]) -> None:
    spec = parse_side(raw)
    assert spec.mode == 'wrapping'
    assert spec.directions == directions
    assert spec.wrapping is True
    assert spec.selects_multiple_curbs is False
    assert spec.normalized == ' and '.join(directions)


@pytest.mark.parametrize(
    'raw',
    [
        'Both',
        'North and south',
        'North and South',
        'South and north',
        'North & south',
        'West and east',
        'East and west',
        'East & west',
        'East and West',
        'West and East',
        'Northeast and southwest',
        'All',
        'All sides',
        'All Sides',
        'North, South, East and West',
        'North and south and west and east',
        'East and north and west and south',
        'East and south, West and north',
        'North and East and South and West',
        'West, north, east, south',
        'East, South, West, North',
    ],
)
def test_opposing_both_and_all_are_multi(raw: str) -> None:
    spec = parse_side(raw)
    assert spec.mode == 'multi'
    assert spec.wrapping is False
    assert spec.selects_multiple_curbs is True
    assert spec.needs_override is False


def test_both_and_all_normalized_labels() -> None:
    assert parse_side('Both').normalized == 'both'
    assert parse_side('Both').directions == ()
    assert parse_side('All sides').normalized == 'all'
    assert parse_side('All Sides').normalized == 'all'
    assert parse_side('North, South, East and West').normalized == 'all'


def test_slash_is_compound_not_diagonal() -> None:
    spec = parse_side('North/East')
    assert spec.mode == 'wrapping'
    assert spec.directions == ('north', 'east')
    assert parse_side('North-East').directions == ('northeast',)
    assert parse_side('Northeast').mode == 'single'


@pytest.mark.parametrize(
    ('raw', 'parity'),
    [
        ('Odd', 'odd'),
        ('Even', 'even'),
        ('odd numbered side', 'odd'),
        ('Even sides', 'even'),
    ],
)
def test_odd_even_are_parity(raw: str, parity: str) -> None:
    spec = parse_side(raw)
    assert spec.mode == 'parity'
    assert spec.parity == parity
    assert spec.normalized == parity
    assert spec.needs_override is False


def test_resolve_parity_uses_tcl_codes_without_road_edges() -> None:
    odd = parse_side('Odd')
    even = parse_side('Even')
    assert resolve_parity_side(odd, parity_l='O', parity_r='E') == 'left'
    assert resolve_parity_side(odd, parity_l='E', parity_r='O') == 'right'
    assert resolve_parity_side(even, parity_l='O', parity_r='E') == 'right'
    assert resolve_parity_side(odd, parity_l='OE', parity_r='N') == 'left'
    assert resolve_parity_side(even, parity_l='N', parity_r='OE') == 'right'
    assert resolve_parity_side(odd, parity_l='O', parity_r='O') is None
    assert resolve_parity_side(odd, parity_l='OE', parity_r='OE') is None
    assert resolve_parity_side(odd, parity_l='N', parity_r='N') is None
    assert resolve_parity_side(
        odd, parity_l='O', parity_r='E', orientation_unambiguous=False,
    ) is None
    assert resolve_parity_side(parse_side('North'), parity_l='O', parity_r='E') is None


@pytest.mark.parametrize(
    ('raw', 'ring', 'radius', 'normalized'),
    [
        ('Inner Perimeter', 'inner', False, 'inner perimeter'),
        ('Inner Perimiter', 'inner', False, 'inner perimeter'),
        ('Inside Perimeter', 'inner', False, 'inner perimeter'),
        ('Outside Perimeter', 'outer', False, 'outer perimeter'),
        ('Inner Circle', 'inner', False, 'inner circle'),
    ],
)
def test_perimeter_without_compass(raw: str, ring: str, radius: bool, normalized: str) -> None:
    spec = parse_side(raw)
    assert spec.mode == 'perimeter'
    assert spec.ring == ring
    assert spec.radius is radius
    assert spec.normalized == normalized
    assert spec.directions == ()
    assert spec.needs_override is False


def test_radius_and_perimeter_qualifiers_retained_on_compass() -> None:
    inner = parse_side('South (inner radius)')
    assert inner.mode == 'single'
    assert inner.directions == ('south',)
    assert inner.ring == 'inner'
    assert inner.radius is True
    assert 'radius' in inner.qualifiers

    wrap = parse_side('North, east and south (inner radius)')
    assert wrap.mode == 'wrapping'
    assert wrap.ring == 'inner'
    assert wrap.radius is True

    outer = parse_side('North and east sides of the outer perimeter')
    assert outer.mode == 'wrapping'
    assert outer.directions == ('north', 'east')
    assert outer.ring == 'outer'
    assert outer.radius is False
    assert 'perimeter' in outer.qualifiers

    circle = parse_side('East and south (outer circle)')
    assert circle.mode == 'wrapping'
    assert circle.ring == 'outer'
    assert 'circle' in circle.qualifiers

    inner_side = parse_side('East and north and west (inner side of the street)')
    assert inner_side.mode == 'wrapping'
    assert inner_side.directions == ('east', 'north', 'west')
    assert inner_side.ring == 'inner'


@pytest.mark.parametrize(
    ('raw', 'kind'),
    [
        ('Centre', 'centre'),
        ('West side of traffic island', 'island'),
        ('North, South and centre island', 'island'),
        ('Inner perimeter, curb of centre traffic island', 'island'),
        ('East side of the roadway west of the median', 'median'),
        ('Lay-by in the south side of centre island', 'island'),
        ('Curb sides of cul-de-sac', 'cul_de_sac'),
        ('North and east (south leg)', 'leg'),
        ('North side of north leg', 'leg'),
        ('North side of the north (westbound) roadway', 'roadway'),
        ('North end', 'end'),
    ],
)
def test_specialized_cases_are_not_guessed(raw: str, kind: str) -> None:
    spec = parse_side(raw)
    assert spec.mode == 'specialized'
    assert spec.specialized_kind == kind
    assert spec.wrapping is False
    assert spec.needs_override is True
    assert spec.selects_multiple_curbs is False


def test_specialized_retains_directions_and_qualifiers() -> None:
    island = parse_side('West side of traffic island')
    assert island.directions == ('west',)
    assert 'island' in island.qualifiers

    layby = parse_side('Lay-by in the south side of centre island')
    assert layby.specialized_kind == 'island'
    assert 'lay_by' in layby.qualifiers
    assert 'island' in layby.qualifiers
    assert layby.directions == ('south',)

    leg = parse_side('North and east (south leg)')
    assert leg.directions == ('north', 'east')
    assert 'south_leg' in leg.qualifiers
    assert 'leg' in leg.qualifiers

    median = parse_side('East side of the roadway west of the median')
    assert median.directions == ('east',)
    assert 'median' in median.qualifiers
    assert 'roadway' in median.qualifiers

    end = parse_side('North end')
    assert end.directions == ()


def test_blank_and_nullish_are_unresolved() -> None:
    for raw in ('', '   ', None, float('nan')):
        spec = parse_side(raw)
        assert spec.mode == 'unresolved'
        assert spec.unresolved_reason == 'blank'
        assert spec.needs_override is True
        if raw is None or (isinstance(raw, float) and math.isnan(raw)):
            assert spec.raw == ''
        else:
            assert spec.raw == raw


def test_unsupported_without_compass_is_unresolved() -> None:
    spec = parse_side('not a real side')
    assert spec.mode == 'unresolved'
    assert spec.unresolved_reason == 'unsupported'
    assert spec.specialized_kind == 'unsupported'
    assert spec.needs_override is True


def test_adjacent_qualifier_retained_on_south() -> None:
    spec = parse_side('South Adjacent')
    assert spec.mode == 'single'
    assert spec.directions == ('south',)
    assert 'adjacent' in spec.qualifiers


def test_repeated_words_and_punctuation_normalize() -> None:
    spec = parse_side('South and east and south')
    assert spec.directions == ('south', 'east')
    assert spec.mode == 'wrapping'
    ampersand = parse_side('East & west')
    assert ampersand.mode == 'multi'
    assert ampersand.directions == ('east', 'west')


def test_committed_overrides_csv_contract() -> None:
    path = data_path(OVERRIDE_FILENAME)
    assert path.exists()
    rows = load_curb_geometry_overrides(path)
    text = path.read_text(encoding='utf-8')
    header = next(
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    )
    assert tuple(header.split(',')) == OVERRIDE_COLUMNS
    assert rows == {}
    assert load_curb_geometry_overrides() == {}


def test_overrides_skip_comments_and_incomplete_rows(tmp_path) -> None:
    path = tmp_path / OVERRIDE_FILENAME
    path.write_text(
        '\n'.join([
            '# comment before header',
            'row_id,reason,method,notes',
            '# 0,example,centerline_unresolved,ignored comment row',
            '12,island_topology,centerline_unresolved,do not guess island',
            '12,duplicate,road_edge,first wins',
            ',missing_id,road_edge,skip',
            '13,,road_edge,skip missing reason',
            '14,no_method,,skip missing method',
            '15,median_topology,calibrated_offset,ok',
            '',
        ]),
        encoding='utf-8',
    )
    rows = load_curb_geometry_overrides(path)
    assert set(rows) == {'12', '15'}
    assert rows['12'] == CurbGeometryOverride(
        row_id='12',
        reason='island_topology',
        method='centerline_unresolved',
        notes='do not guess island',
    )
    assert rows['15'].method == 'calibrated_offset'


def test_override_applied_only_after_deterministic_failure() -> None:
    overrides = {
        '99': CurbGeometryOverride('99', 'island_topology', 'centerline_unresolved', 'x'),
        '100': CurbGeometryOverride('100', 'blank_side', 'centerline_unresolved', ''),
        '101': CurbGeometryOverride('101', 'should_not_apply', 'road_edge', ''),
    }
    island = parse_side('West side of traffic island')
    blank = parse_side('')
    north = parse_side('North')
    both = parse_side('Both')
    perimeter = parse_side('Inner Perimeter')

    assert override_for_row(99, island, overrides) is overrides['99']
    assert override_for_row('100', blank, overrides) is overrides['100']
    assert override_for_row(101, north, overrides) is None
    assert override_for_row('101', both, overrides) is None
    assert override_for_row('101', perimeter, overrides) is None
    assert override_for_row('missing', island, overrides) is None


def test_missing_overrides_file_is_empty(tmp_path) -> None:
    assert load_curb_geometry_overrides(tmp_path / 'missing.csv') == {}

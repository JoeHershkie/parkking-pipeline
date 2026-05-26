"""Flat CSV columns for Between parse output (parse_between → geometry)."""

from __future__ import annotations

import math
import re

import pandas as pd

from intersection_normalize import apply_street_alias
from tcl_highway_resolve import tcl_lookup_key

PARSE_COLUMNS = [
    'rule_type',
    'start_intersection',
    'end_intersection',
    'offset_intersection',
    'start_intersection_qualifier',
    'end_intersection_qualifier',
    'terminus_direction',
    'terminus_street',
    'terminus_start_dir',
    'terminus_end_dir',
    'distance',
    'direction',
    'dist1',
    'dist2',
    'dir1',
    'dir2',
]

NORM_COLUMNS = [
    'highway_norm',
    'start_intersection_norm',
    'end_intersection_norm',
    'offset_intersection_norm',
    'terminus_street_norm',
]

META_COLUMNS = ['parse_valid', 'parse_error']

EXPORT_PARSE_COLUMNS = PARSE_COLUMNS + NORM_COLUMNS + META_COLUMNS

_FLOAT_KEYS = frozenset({'distance', 'dist1', 'dist2'})
_COMPASS_DIRS = frozenset({
    'north', 'south', 'east', 'west',
    'northeast', 'northwest', 'southeast', 'southwest',
})
_CARDINAL_DIRS = frozenset({'north', 'south', 'east', 'west'})

_POINT_METRES_FRAGMENT_RE = re.compile(r'^a point\s+.*\bmetres\b', re.IGNORECASE)

# string fields required per rule_type (qualifiers optional unless listed)
_RULE_REQUIRED_STRINGS: dict[str, tuple[str, ...]] = {
    'entire_length': (),
    'block': ('start_intersection', 'end_intersection'),
    'block_to_terminus': ('start_intersection', 'terminus_direction'),
    'terminus_to_terminus': ('terminus_start_dir', 'terminus_end_dir', 'terminus_street'),
    'parenthetical_block': ('start_intersection', 'end_intersection'),
    'parenthetical_end_block': ('start_intersection', 'end_intersection'),
    'parenthetical_dual_block': ('start_intersection', 'end_intersection'),
    'parenthetical_to_terminus': ('start_intersection', 'terminus_direction'),
    'intersect_extension': ('start_intersection', 'direction'),
    'perfect_offset': ('start_intersection', 'direction'),
    'intersect_to_offset': ('start_intersection', 'offset_intersection', 'direction'),
    'offset_to_intersect': ('start_intersection', 'end_intersection', 'direction'),
    'relative_extension': ('start_intersection', 'dir1'),
    'offset_span': ('start_intersection', 'dir1'),
    'dual_anchor': ('start_intersection', 'end_intersection', 'dir1', 'dir2'),
}

_RULE_REQUIRED_FLOATS: dict[str, tuple[str, ...]] = {
    'intersect_extension': ('distance',),
    'perfect_offset': ('distance',),
    'intersect_to_offset': ('distance',),
    'offset_to_intersect': ('distance',),
    'relative_extension': ('dist1', 'dist2'),
    'offset_span': ('dist1', 'dist2'),
    'dual_anchor': ('dist1', 'dist2'),
}

# Fields that must look like cross-street names (not point-offset phrases)
_ANCHOR_STRING_KEYS = frozenset({
    'start_intersection',
    'end_intersection',
    'offset_intersection',
    'terminus_street',
})

_NORM_BY_PARSE_KEY = {
    'start_intersection': 'start_intersection_norm',
    'end_intersection': 'end_intersection_norm',
    'offset_intersection': 'offset_intersection_norm',
    'terminus_street': 'terminus_street_norm',
}


def _coerce_float(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return float('nan')
    return float(val)


def _is_empty(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    return not str(val).strip()


def _parse_valid_flag(val) -> bool:
    if _is_empty(val):
        return True
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', '1', 'yes')


def norm_anchor(value) -> str:
    if _is_empty(value):
        return ''
    return apply_street_alias(str(value).strip())


def norm_columns_for_row(parsed: dict, highway: str) -> dict:
    """TCL-oriented tokens for highway and parsed anchor fields."""
    out = {col: '' for col in NORM_COLUMNS}
    out['highway_norm'] = norm_anchor(highway)
    for parse_key, norm_key in _NORM_BY_PARSE_KEY.items():
        if parse_key in parsed and not _is_empty(parsed.get(parse_key)):
            out[norm_key] = norm_anchor(parsed[parse_key])
    return out


def _anchor_ok(value) -> bool:
    if _is_empty(value):
        return False
    text = str(value).strip()
    if _POINT_METRES_FRAGMENT_RE.match(text):
        return False
    if _starts_with_a_point(text) and 'metres' in text.lower():
        return False
    return True


def _starts_with_a_point(text: str) -> bool:
    return bool(re.match(r'^a point\b', text.strip(), re.IGNORECASE))


_TRAILING_PAREN_QUAL_RE = re.compile(
    r'^(?P<name>.+?)\s*\((?P<qualifier>[^)]+)\)\s*$',
)

_INTERSECTION_QUALIFIER_FIELDS = {
    'start_intersection': 'start_intersection_qualifier',
    'end_intersection': 'end_intersection_qualifier',
    'offset_intersection': 'offset_intersection_qualifier',
}


def split_trailing_qualifier(anchor: str) -> tuple[str, str | None]:
    """Split 'Street Name (north intersection)' into name and qualifier."""
    text = str(anchor).strip()
    if not text:
        return '', None
    m = _TRAILING_PAREN_QUAL_RE.match(text)
    if not m:
        return text, None
    return m.group('name').strip(), m.group('qualifier').strip()


def apply_trailing_qualifiers(parsed: dict) -> dict:
    """Move trailing parenthetical qualifiers off anchor fields."""
    out = dict(parsed)
    for anchor_key, qual_key in _INTERSECTION_QUALIFIER_FIELDS.items():
        if anchor_key not in out or _is_empty(out.get(anchor_key)):
            continue
        name, qual = split_trailing_qualifier(str(out[anchor_key]))
        out[anchor_key] = name
        if qual and _is_empty(out.get(qual_key)):
            out[qual_key] = qual
    return out


def _float_ok(val, *, field: str) -> tuple[bool, str]:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return False, f'missing or non-numeric {field}'
    if not math.isfinite(f):
        return False, f'non-finite {field}'
    if f < 0:
        return False, f'negative {field}'
    return True, ''


def _direction_ok(val, *, field: str, allow_compass: bool = False) -> tuple[bool, str]:
    if _is_empty(val):
        return False, f'missing {field}'
    d = str(val).strip().lower()
    allowed = _COMPASS_DIRS if allow_compass else _CARDINAL_DIRS
    if d not in allowed:
        return False, f'invalid {field}={val!r}'
    return True, ''


def validate_parsed(parsed: dict) -> tuple[bool, str]:
    """Return (ok, error_detail) for a pattern-matched parse dict."""
    rule_type = parsed.get('rule_type')
    if _is_empty(rule_type):
        return False, 'missing rule_type'

    rule_type = str(rule_type).strip()
    if rule_type not in _RULE_REQUIRED_STRINGS:
        return False, f'unknown rule_type={rule_type!r}'

    for key in _RULE_REQUIRED_STRINGS[rule_type]:
        val = parsed.get(key)
        if _is_empty(val):
            return False, f'missing {key}'
        if key in _ANCHOR_STRING_KEYS and not _anchor_ok(val):
            return False, f'invalid anchor {key}={str(val)[:80]!r}'

    for key in _RULE_REQUIRED_FLOATS.get(rule_type, ()):
        ok, err = _float_ok(parsed.get(key), field=key)
        if not ok:
            return False, err

    if rule_type in (
        'perfect_offset', 'intersect_extension', 'intersect_to_offset', 'offset_to_intersect',
    ):
        ok, err = _direction_ok(
            parsed.get('direction'), field='direction', allow_compass=True,
        )
        if not ok:
            return False, err

    if rule_type in ('relative_extension', 'offset_span'):
        ok, err = _direction_ok(parsed.get('dir1'), field='dir1', allow_compass=True)
        if not ok:
            return False, err
        if rule_type == 'offset_span' and not _is_empty(parsed.get('dir2')):
            ok, err = _direction_ok(parsed.get('dir2'), field='dir2', allow_compass=True)
            if not ok:
                return False, err

    if rule_type == 'dual_anchor':
        for key in ('dir1', 'dir2'):
            ok, err = _direction_ok(parsed.get(key), field=key, allow_compass=True)
            if not ok:
                return False, err

    if rule_type in ('block_to_terminus', 'parenthetical_to_terminus'):
        ok, err = _direction_ok(
            parsed.get('terminus_direction'),
            field='terminus_direction',
            allow_compass=True,
        )
        if not ok:
            return False, err

    if rule_type == 'parenthetical_dual_block':
        for key in ('start_intersection_qualifier', 'end_intersection_qualifier'):
            if _is_empty(parsed.get(key)):
                return False, f'missing {key}'

    if rule_type == 'terminus_to_terminus':
        for key in ('terminus_start_dir', 'terminus_end_dir'):
            ok, err = _direction_ok(parsed.get(key), field=key, allow_compass=True)
            if not ok:
                return False, err

    return True, ''


def parsed_dict_to_columns(parsed: dict) -> dict:
    """Map parse_between dict to flat column values for CSV export."""
    out = {col: float('nan') if col in _FLOAT_KEYS else '' for col in PARSE_COLUMNS}
    for key, val in parsed.items():
        if key not in PARSE_COLUMNS:
            continue
        if key in _FLOAT_KEYS:
            out[key] = _coerce_float(val)
        else:
            out[key] = '' if val is None else str(val)
    return out


def _row_has(row, key: str) -> bool:
    if hasattr(row, 'index'):
        return key in row.index
    return key in row


def _row_get(row, key: str):
    if hasattr(row, 'index'):
        return row[key] if key in row.index else None
    return row.get(key)


def row_to_parsed(row) -> dict:
    """Build the dict slice_street expects from raw parse columns.

    Intersection lookup applies ``apply_street_alias`` at resolve time; do not
    inject stale ``*_norm`` CSV values here (they go out of date when aliases change).
    """
    parsed: dict = {}
    for col in PARSE_COLUMNS:
        if not _row_has(row, col):
            continue
        val = _row_get(row, col)
        if _is_empty(val):
            continue
        if col in _FLOAT_KEYS:
            parsed[col] = float(val)
        else:
            parsed[col] = str(val).strip()
    return parsed


def highway_from_row(row) -> str:
    """Highway key for TCL centreline lookup (``LINEAR_NAME_FULL_LEGAL``, lowercased).

    Applies ``tcl_lookup_key`` (borough suffix, ``St.`` punctuation, suffix remap).
    Does not use ``highway_norm`` — that column abbreviates types for INTERSECTION_DESC search.
    """
    return tcl_lookup_key(str(_row_get(row, 'Highway') or ''))

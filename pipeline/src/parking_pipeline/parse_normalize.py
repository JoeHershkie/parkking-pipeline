"""Post-parse normalization of extracted Between fields."""

from __future__ import annotations

import re

from .between_patterns import (
    A_POINT_OPPOSITE_LIMIT_RE,
    A_POINT_OPPOSITE_RE,
    ANCHOR_FIELDS,
    METRIC_OF_STREET_RE,
    METRIC_ONLY_RE,
    TERMINUS_ANCHOR_RE,
    THE_LIMIT_RE,
)
from .parse_format import apply_trailing_qualifiers

_TERMINUS_DIR_MAP = {
    'northerly': 'north',
    'southerly': 'south',
    'easterly': 'east',
    'westerly': 'west',
    'northeasterly': 'northeast',
    'northwesterly': 'northwest',
    'southeasterly': 'southeast',
    'southwesterly': 'southwest',
}


def _parse_metric_of_street(text: str) -> tuple[str, str, str] | None:
    """Return (street, distance, direction) from 'a point N metres DIR of Street'."""
    m = METRIC_OF_STREET_RE.match(str(text).strip())
    if not m:
        return None
    direction = m.group('direction').lower().split(' and ', 1)[0]
    return m.group('street').strip(), m.group('distance'), direction


def _parse_metric_only(text: str) -> tuple[str, str] | None:
    """Return (distance, direction) from 'a point N metres DIR' with no cross-street."""
    m = METRIC_ONLY_RE.match(str(text).strip())
    if not m:
        return None
    direction = m.group('direction').lower().split(' and ', 1)[0]
    return m.group('distance'), direction


def primary_compass(direction: str) -> str:
    """First compass token when direction is compound (e.g. 'north and east' → 'north')."""
    d = str(direction).strip().lower().split(' and ', 1)[0].strip()
    token = d.split()[0] if d.split() else d
    return _TERMINUS_DIR_MAP.get(token, token)


def _normalize_compass_fields(parsed: dict) -> dict:
    out = dict(parsed)
    for key in ('direction', 'dir1', 'dir2'):
        if key in out and out[key]:
            out[key] = primary_compass(out[key])
    return out


def _upgrade_metric_parsed(parsed: dict) -> dict:
    """Remap block-family parses that captured metric point phrases as intersections."""
    out = dict(parsed)
    rule = out.get('rule_type')

    if rule == 'parenthetical_block':
        metric = _parse_metric_of_street(out.get('start_intersection', ''))
        if metric:
            street, dist, direction = metric
            out['rule_type'] = 'offset_to_intersect'
            out['start_intersection'] = street
            out['distance'] = dist
            out['direction'] = direction
            return out

    if rule == 'parenthetical_end_block':
        end = out.get('end_intersection', '')
        metric = _parse_metric_of_street(end)
        if metric:
            street, dist, direction = metric
            out['rule_type'] = 'intersect_to_offset'
            out['offset_intersection'] = street
            out['distance'] = dist
            out['direction'] = direction
            out.pop('end_intersection', None)
            qual = out.pop('end_intersection_qualifier', None)
            if qual:
                out['offset_intersection_qualifier'] = qual
            return out
        bare = _parse_metric_only(end)
        if bare:
            dist, direction = bare
            out['rule_type'] = 'perfect_offset'
            out['distance'] = dist
            out['direction'] = direction
            out.pop('end_intersection', None)
            out.pop('end_intersection_qualifier', None)
            return out

    if rule == 'block_to_terminus':
        start = out.get('start_intersection', '')
        metric = _parse_metric_of_street(start)
        if metric:
            street, dist, direction = metric
            out['start_intersection'] = street
            out['distance'] = dist
            out['direction'] = direction
            return out
        bare = _parse_metric_only(start)
        if bare:
            dist, direction = bare
            term = str(out.get('terminus_street') or '').strip()
            if term:
                out['start_intersection'] = term
            out['distance'] = dist
            out['direction'] = direction
            return out

    return out


def normalize_anchor_phrase(text: str) -> str:
    """Reduce 'a point opposite the east limit of X' / similar to a street name."""
    raw = str(text).strip()
    if not raw:
        return raw
    metric = _parse_metric_of_street(raw)
    if metric:
        return metric[0]
    m = TERMINUS_ANCHOR_RE.match(raw)
    if m:
        return m.group('street').strip()
    m = A_POINT_OPPOSITE_LIMIT_RE.match(raw)
    if m:
        return m.group('street').strip()
    m = A_POINT_OPPOSITE_RE.match(raw)
    if m:
        return m.group('street').strip()
    m = THE_LIMIT_RE.match(raw)
    if m:
        return m.group('street').strip()
    return raw


def _strip_trailing_and_from_anchors(parsed: dict) -> dict:
    out = dict(parsed)
    for key in ANCHOR_FIELDS:
        if key in out and out[key]:
            out[key] = re.sub(r'\s+and\s*$', '', str(out[key]), flags=re.IGNORECASE).strip()
    return out


def normalize_parsed(parsed: dict) -> dict:
    """Normalize anchor fields on a successful parse."""
    out = apply_trailing_qualifiers(dict(parsed))
    out = _strip_trailing_and_from_anchors(out)
    out = _upgrade_metric_parsed(out)
    out = _normalize_compass_fields(out)
    if out.get('terminus_direction'):
        out['terminus_direction'] = primary_compass(out['terminus_direction'])
    for key in ANCHOR_FIELDS:
        if key in out and out[key]:
            out[key] = normalize_anchor_phrase(out[key])
    return out

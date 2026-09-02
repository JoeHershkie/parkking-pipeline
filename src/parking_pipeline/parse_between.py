"""Stage 2: parse Between text → parsed_successes.csv (failures → failure_ledger)."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from .between_patterns import (
    A_POINT_RE,
    PAREN_QUALIFIER_RE,
    POINT_METRES_FRAGMENT_RE,
    STREET_END_RE,
)
from .between_patterns import (
    APPROX as _APPROX,
)
from .between_patterns import (
    COMPASS as _COMPASS,
)
from .between_patterns import (
    COMPASS_END as _COMPASS_END,
)
from .between_patterns import (
    COMPOUND_DIR as _COMPOUND_DIR,
)
from .between_patterns import (
    DIR as _DIR,
)
from .between_patterns import (
    FURTHER_TAIL as _FURTHER_TAIL,
)
from .between_patterns import (
    METRES as _METRES,
)
from .between_patterns import (
    STREET_HEAD as _STREET_HEAD,
)
from .between_patterns import (
    OPT_PAREN_IN_ANCHOR as _OPT_PAREN_IN_ANCHOR,
)
from .bylaw_text import preprocess_between
from .failure_ledger import clear_stage, record_failure
from .parse_format import (
    EXPORT_PARSE_COLUMNS,
    norm_columns_for_row,
    parsed_dict_to_columns,
    validate_parsed,
)
from .parse_normalize import (
    normalize_anchor_phrase,
    normalize_parsed,
)
from .parse_normalize import (
    primary_compass as _primary_compass,
)
from .paths import data_path
from .schedule_format import SCHEDULE_EXPORT_COLUMNS

log = logging.getLogger(__name__)

__all__ = (
    'PARSE_NO_MATCH',
    'PARSE_EMPTY_BETWEEN',
    'PARSE_INVALID',
    'preprocess_between',
    'normalize_anchor_phrase',
    'normalize_parsed',
    'parse_between',
    'parse_rows',
)

# --- Failure reason codes (parse stage) ---

PARSE_NO_MATCH = 'PARSE_NO_MATCH'
PARSE_EMPTY_BETWEEN = 'PARSE_EMPTY_BETWEEN'
PARSE_INVALID = 'PARSE_INVALID'

@dataclass(frozen=True)
class _Patterns:
    offset_span: re.Pattern
    dual_offset_of_street: re.Pattern
    opposite_and_metric: re.Pattern
    opposite_limit_block: re.Pattern
    opposite_and_block: re.Pattern
    street_and_opposite: re.Pattern
    terminus_end_metric: re.Pattern
    metric_and_street: re.Pattern
    street_and_bare_metric: re.Pattern
    perfect_offset: re.Pattern
    intersect_to_offset: re.Pattern
    offset_to_intersect: re.Pattern
    relative_extension: re.Pattern
    dual_anchor: re.Pattern
    parenthetical_to_terminus: re.Pattern
    parenthetical_end_block: re.Pattern
    parenthetical_dual_block: re.Pattern
    thereof_and_block: re.Pattern
    juxtaposed_block: re.Pattern
    block_to_terminus: re.Pattern
    terminus_end_block: re.Pattern
    terminus_end_opposite: re.Pattern
    street_and_terminus_end: re.Pattern
    metric_and_opposite_limit: re.Pattern
    metric_and_metric_of: re.Pattern
    terminus_end_and_offset: re.Pattern
    terminus_end_same_metric: re.Pattern
    street_and_further_metric: re.Pattern
    terminus_to_terminus: re.Pattern
    parenthetical_block: re.Pattern
    block_lane_tail: re.Pattern
    block: re.Pattern
    intersect_extension: re.Pattern
    intersect_thereof: re.Pattern
    entire_length: re.Pattern


def _compile_patterns() -> _Patterns:
    return _Patterns(
        offset_span=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR}) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres (?P<dir2>{_COMPOUND_DIR})$',
            re.IGNORECASE,
        ),
        dual_offset_of_street=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_COMPOUND_DIR}) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres (?P<dir2>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR})$',
            re.IGNORECASE,
        ),
        opposite_and_metric=re.compile(
            rf'^A point opposite (?P<start_intersection>.+?) and a point {_APPROX}'
            rf'(?P<distance>{_METRES}) metres (?P<direction>{_COMPOUND_DIR})$',
            re.IGNORECASE,
        ),
        opposite_limit_block=re.compile(
            r'^A point opposite the .+?\blimit of (?P<start_intersection>.+?) '
            r'and (?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        opposite_and_block=re.compile(
            r'^A point opposite (?P<start_intersection>.+?) '
            r'and (?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        street_and_opposite=re.compile(
            r'^(?P<start_intersection>.+?) and a point opposite (?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        terminus_end_metric=re.compile(
            rf'^the (?P<terminus_direction>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street>.+?) and a point {_APPROX}'
            rf'(?P<distance>{_METRES}) metres (?P<direction>{_COMPOUND_DIR})$',
            re.IGNORECASE,
        ),
        metric_and_street=re.compile(
            rf'^a point {_APPROX}(?P<distance>{_METRES}) metres '
            rf'(?P<direction>{_COMPOUND_DIR})\s+and\s+(?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        street_and_bare_metric=re.compile(
            rf'^(?P<start_intersection>.+?) and (?:a(?:n)?\s*o?point\s+)?{_APPROX}'
            rf'(?P<distance>{_METRES})\s+metres?\s+(?P<direction>{_COMPOUND_DIR})$',
            re.IGNORECASE,
        ),
        perfect_offset=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres '
            rf'(?P<direction>{_COMPOUND_DIR})$',
            re.IGNORECASE,
        ),
        intersect_to_offset=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres '
            rf'(?P<direction>{_COMPOUND_DIR}) of (?P<offset_intersection>.*?)$',
            re.IGNORECASE,
        ),
        offset_to_intersect=re.compile(
            rf'^A point {_APPROX}(?P<distance>{_METRES}) metres (?P<direction>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR}) and (?P<end_intersection>.*?)$',
            re.IGNORECASE,
        ),
        relative_extension=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR}) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres {_FURTHER_TAIL}$',
            re.IGNORECASE,
        ),
        dual_anchor=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR}) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres (?P<dir2>{_COMPOUND_DIR}) of '
            rf'(?P<end_intersection>.+?{_OPT_PAREN_IN_ANCHOR})$',
            re.IGNORECASE,
        ),
        parenthetical_to_terminus=re.compile(
            rf'^(?P<start_intersection>.*?)\s*\((?P<start_intersection_qualifier>[^)]+)\)\s*'
            rf'and the (?P<terminus_direction>{_COMPASS}) end of (?P<terminus_street>.*?)$',
            re.IGNORECASE,
        ),
        parenthetical_end_block=re.compile(
            r'^(?P<start_intersection>.*?) and (?P<end_intersection>.*?)\s*'
            r'\((?P<end_intersection_qualifier>[^)]+)\)\s*$',
            re.IGNORECASE,
        ),
        parenthetical_dual_block=re.compile(
            r'^(?P<start_intersection>.*?)\s*\((?P<start_intersection_qualifier>[^)]+)\)\s*'
            r'and\s*(?P<end_intersection>.*?)\s*\((?P<end_intersection_qualifier>[^)]+)\)\s*$',
            re.IGNORECASE,
        ),
        block_to_terminus=re.compile(
            rf'^(?P<start_intersection>.*?) and the (?P<terminus_direction>{_COMPASS_END}) end of (?P<terminus_street>.*?)$',
            re.IGNORECASE,
        ),
        terminus_end_block=re.compile(
            rf'^the (?P<terminus_direction>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street>.+?)\s+and\s+'
            rf'(?P<start_intersection>(?!the\s+(?:{_COMPASS_END})\s+end\s+of|a\s+point).+?)$',
            re.IGNORECASE,
        ),
        terminus_end_opposite=re.compile(
            rf'^the (?P<terminus_direction>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street>.+?)\s+and\s+a\s+point\s+opposite\s+'
            rf'(?:the\s+.+?\blimit\s+of\s+)?(?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        street_and_terminus_end=re.compile(
            rf'^(?P<start_intersection>.+?)\s+and\s+'
            rf'(?P<terminus_direction>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street>.+?)$',
            re.IGNORECASE,
        ),
        metric_and_opposite_limit=re.compile(
            rf'^A point {_APPROX}(?P<distance>{_METRES}) metres (?P<direction>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR}) '
            rf'and a point opposite (?:the\s+.+?\blimit\s+of\s+)?(?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        metric_and_metric_of=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_COMPOUND_DIR}) of '
            rf'(?P<start_intersection>.+?{_OPT_PAREN_IN_ANCHOR}) '
            rf'and {_APPROX}(?P<dist2>{_METRES}) metres (?P<dir2>{_COMPOUND_DIR}) of (?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        terminus_end_and_offset=re.compile(
            rf'^the (?P<terminus_direction>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street>.+?)\s+and\s+a\s+point\s+{_APPROX}'
            rf'(?P<distance>{_METRES})\s+metres\s+(?P<direction>{_COMPOUND_DIR})\s+of\s+'
            rf'(?P<offset_anchor>.+?)$',
            re.IGNORECASE,
        ),
        terminus_end_same_metric=re.compile(
            rf'^the (?P<terminus_direction>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street>.+?)\s+and\s+a\s+point\s+{_APPROX}'
            rf'(?P<distance>{_METRES})\s+metres\s+(?P<direction>{_COMPOUND_DIR})\s+of\s+'
            rf'the\s+(?P<terminus_direction2>{_COMPASS_END})\s+end\s+of\s+'
            rf'(?P<terminus_street2>.+?)$',
            re.IGNORECASE,
        ),
        street_and_further_metric=re.compile(
            rf'^(?P<start_intersection>.+?)\s+and\s+a\s+point\s+{_APPROX}'
            rf'(?P<distance>{_METRES})\s+metres\s+further\s+(?P<direction>{_COMPOUND_DIR})\s+of\s+'
            rf'(?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        terminus_to_terminus=re.compile(
            rf'^The (?P<terminus_start_dir>{_COMPASS_END}) end of '
            rf'(?P<terminus_street>.+?{_OPT_PAREN_IN_ANCHOR}) '
            rf'and (?:the\s+)?(?P<terminus_end_dir>{_COMPASS_END}) end of '
            rf'(?P<terminus_street2>.+?{_OPT_PAREN_IN_ANCHOR})$',
            re.IGNORECASE,
        ),
        parenthetical_block=re.compile(
            r'^(?P<start_intersection>.*?)\s*\((?P<start_intersection_qualifier>[^)]+)\)\s*'
            r'and\s*(?P<end_intersection>.*?)$',
            re.IGNORECASE,
        ),
        block_lane_tail=re.compile(
            r'^(?P<start_intersection>.+?) and (?P<end_intersection>.+?), '
            r'(?:(?:first|second|third)\s+)?'
            rf'(?P<lane_direction>{_DIR})\s+of\s+'
            r'(?P<lane_anchor>.+?)$',
            re.IGNORECASE,
        ),
        block=re.compile(
            r'^(?P<start_intersection>.*?) and (?P<end_intersection>.*?)$',
            re.IGNORECASE,
        ),
        intersect_extension=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres further '
            rf'(?P<direction>{_COMPOUND_DIR})$',
            re.IGNORECASE,
        ),
        intersect_thereof=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres '
            rf'(?P<direction>{_COMPOUND_DIR}) thereof$',
            re.IGNORECASE,
        ),
        thereof_and_block=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres '
            rf'(?P<direction>{_COMPOUND_DIR}) thereof and (?P<end_intersection>.+?)$',
            re.IGNORECASE,
        ),
        juxtaposed_block=re.compile(
            rf'^(?P<start_intersection>{_STREET_HEAD})\s+'
            rf'(?P<end_intersection>{_STREET_HEAD})$',
            re.IGNORECASE,
        ),
        entire_length=re.compile(r'^Entire length$', re.IGNORECASE),
    )


_P = _compile_patterns()

ParseFn = Callable[[str], dict | None]


def _parsed(match: re.Match, rule_type: str, **extra: str) -> dict:
    return {**match.groupdict(), **extra, 'rule_type': rule_type}


def _starts_with_a_point(text: str) -> bool:
    return bool(A_POINT_RE.match(text.strip()))


def _is_street_end_phrase(text: str) -> bool:
    return bool(STREET_END_RE.search(text))


def _block_side_ok(side: str) -> bool:
    side = str(side).strip()
    if not side:
        return False
    if _is_street_end_phrase(side):
        return False
    if PAREN_QUALIFIER_RE.search(side):
        return False
    if _starts_with_a_point(side):
        return False
    sl = side.lower()
    if 'lane' in sl and 'point' in sl:
        return False
    return True


def _try_offset_span(text: str) -> dict | None:
    """Two metric offsets from the same cross-street anchor (not dist1 + dist2 further)."""
    m = _P.offset_span.match(text)
    return _parsed(m, 'offset_span') if m else None


def _try_dual_offset_of_street(text: str) -> dict | None:
    """Offsets bracketing a trailing shared anchor: 'A point 61 m south and a point 61 m north of X'."""
    m = _P.dual_offset_of_street.match(text)
    return _parsed(m, 'offset_span') if m else None


def _try_opposite_and_metric(text: str) -> dict | None:
    m = _P.opposite_and_metric.match(text)
    return _parsed(m, 'perfect_offset') if m else None


def _try_opposite_limit_block(text: str) -> dict | None:
    m = _P.opposite_limit_block.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    end = (m.group('end_intersection') or '').strip()
    if not (_block_side_ok(start) and _block_side_ok(end)):
        return None
    return _parsed(m, 'block')


def _try_opposite_and_block(text: str) -> dict | None:
    m = _P.opposite_and_block.match(text)
    if not m:
        return None
    start = normalize_anchor_phrase(m.group('start_intersection') or '')
    end = normalize_anchor_phrase(m.group('end_intersection') or '')
    if not start or not end or _is_street_end_phrase(start) or _is_street_end_phrase(end):
        return None
    return {
        'start_intersection': start,
        'end_intersection': end,
        'rule_type': 'block',
    }


def _try_street_and_opposite(text: str) -> dict | None:
    m = _P.street_and_opposite.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    end = normalize_anchor_phrase(m.group('end_intersection') or '')
    if not _block_side_ok(start) or not end:
        return None
    return {
        'start_intersection': start,
        'end_intersection': end,
        'rule_type': 'block',
    }


def _try_terminus_end_metric(text: str) -> dict | None:
    m = _P.terminus_end_metric.match(text)
    if not m:
        return None
    d = m.groupdict()
    d['terminus_direction'] = _primary_compass(d.get('terminus_direction', ''))
    d['direction'] = _primary_compass(d.get('direction', ''))
    return {**d, 'rule_type': 'terminus_end_metric'}


def _try_terminus_end_block(text: str) -> dict | None:
    m = _P.terminus_end_block.match(text)
    if not m:
        return None
    d = m.groupdict()
    d['terminus_direction'] = _primary_compass(d.get('terminus_direction', ''))
    return {**d, 'rule_type': 'block_to_terminus'}


def _try_terminus_end_opposite(text: str) -> dict | None:
    m = _P.terminus_end_opposite.match(text)
    if not m:
        return None
    end = normalize_anchor_phrase(m.group('end_intersection') or '')
    if not end:
        return None
    return {
        'rule_type': 'block_to_terminus',
        'terminus_direction': _primary_compass(m.group('terminus_direction') or ''),
        'terminus_street': (m.group('terminus_street') or '').strip(),
        'start_intersection': end,
    }


def _try_street_and_terminus_end(text: str) -> dict | None:
    m = _P.street_and_terminus_end.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if not _block_side_ok(start):
        return None
    return {
        'rule_type': 'block_to_terminus',
        'start_intersection': start,
        'terminus_direction': _primary_compass(m.group('terminus_direction') or ''),
        'terminus_street': (m.group('terminus_street') or '').strip(),
    }


def _try_metric_and_opposite_limit(text: str) -> dict | None:
    m = _P.metric_and_opposite_limit.match(text)
    if not m:
        return None
    end = normalize_anchor_phrase(m.group('end_intersection') or '')
    if not end:
        return None
    return {
        'rule_type': 'offset_to_intersect',
        'start_intersection': (m.group('start_intersection') or '').strip(),
        'end_intersection': end,
        'distance': m.group('distance'),
        'direction': _primary_compass(m.group('direction') or ''),
    }


def _try_metric_and_metric_of(text: str) -> dict | None:
    m = _P.metric_and_metric_of.match(text)
    return _parsed(m, 'dual_anchor') if m else None


def _try_terminus_end_and_offset(text: str) -> dict | None:
    m = _P.terminus_end_and_offset.match(text)
    if not m:
        return None
    anchor = normalize_anchor_phrase(m.group('offset_anchor') or '')
    if not anchor:
        return None
    return {
        'rule_type': 'block_to_terminus',
        'terminus_direction': _primary_compass(m.group('terminus_direction') or ''),
        'terminus_street': (m.group('terminus_street') or '').strip(),
        'start_intersection': anchor,
    }


def _try_terminus_end_same_metric(text: str) -> dict | None:
    m = _P.terminus_end_same_metric.match(text)
    if not m:
        return None
    street = (m.group('terminus_street') or '').strip()
    street2 = (m.group('terminus_street2') or '').strip()
    if street.lower() != street2.lower():
        return None
    return {
        'rule_type': 'terminus_end_metric',
        'terminus_street': street,
        'terminus_direction': _primary_compass(m.group('terminus_direction') or ''),
        'distance': m.group('distance'),
        'direction': _primary_compass(m.group('direction') or ''),
    }


def _try_street_and_further_metric(text: str) -> dict | None:
    m = _P.street_and_further_metric.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if not _block_side_ok(start):
        return None
    return {
        'rule_type': 'intersect_to_offset',
        'start_intersection': start,
        'offset_intersection': (m.group('end_intersection') or '').strip(),
        'distance': m.group('distance'),
        'direction': _primary_compass(m.group('direction') or ''),
    }


def _try_metric_and_street(text: str) -> dict | None:
    m = _P.metric_and_street.match(text)
    if not m:
        return None
    end = (m.group('end_intersection') or '').strip()
    if (
        _starts_with_a_point(end)
        or _is_street_end_phrase(end)
        or ' of ' in end.lower()
    ):
        return None
    return {
        'rule_type': 'perfect_offset',
        'start_intersection': end,
        'distance': m.group('distance'),
        'direction': _primary_compass(m.group('direction')),
    }


def _try_street_and_bare_metric(text: str) -> dict | None:
    m = _P.street_and_bare_metric.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if POINT_METRES_FRAGMENT_RE.match(start):
        return None
    return _parsed(m, 'perfect_offset')


def _try_perfect_offset(text: str) -> dict | None:
    m = _P.perfect_offset.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if POINT_METRES_FRAGMENT_RE.match(start):
        return None
    return _parsed(m, 'perfect_offset')


def _try_intersect_to_offset(text: str) -> dict | None:
    m = _P.intersect_to_offset.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if _starts_with_a_point(start) or _is_street_end_phrase(start):
        return None
    return _parsed(m, 'intersect_to_offset')


def _try_intersect_thereof(text: str) -> dict | None:
    m = _P.intersect_thereof.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if _starts_with_a_point(start):
        return None
    return _parsed(m, 'intersect_extension')


def _try_thereof_and_block(text: str) -> dict | None:
    """``X and a point N metres DIR thereof and Y`` — offset from X, block to Y."""
    m = _P.thereof_and_block.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    end = (m.group('end_intersection') or '').strip()
    if not (_block_side_ok(start) and _block_side_ok(end)):
        return None
    out = _parsed(m, 'intersect_thereof_block')
    out['start_intersection'] = start
    return out


def _try_relative_extension(text: str) -> dict | None:
    m = _P.relative_extension.match(text)
    return _parsed(m, 'relative_extension') if m else None


def _try_dual_anchor(text: str) -> dict | None:
    m = _P.dual_anchor.match(text)
    return _parsed(m, 'dual_anchor') if m else None


def _try_offset_to_intersect(text: str) -> dict | None:
    m = _P.offset_to_intersect.match(text)
    if not m:
        return None
    end = (m.group('end_intersection') or '').strip()
    if (
        _starts_with_a_point(end)
        or 'metres' in end.lower()
        or _is_street_end_phrase(end)
    ):
        return None
    return _parsed(m, 'offset_to_intersect')


def _try_parenthetical_to_terminus(text: str) -> dict | None:
    m = _P.parenthetical_to_terminus.match(text)
    return _parsed(m, 'parenthetical_to_terminus') if m else None


def _try_parenthetical_end_block(text: str) -> dict | None:
    m = _P.parenthetical_end_block.match(text)
    if not m or not _block_side_ok(m.group('start_intersection')):
        return None
    return _parsed(m, 'parenthetical_end_block')


def _try_parenthetical_dual_block(text: str) -> dict | None:
    m = _P.parenthetical_dual_block.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    end = (m.group('end_intersection') or '').strip()
    if not (_block_side_ok(start) and _block_side_ok(end)):
        return None
    return _parsed(m, 'parenthetical_dual_block')


def _try_terminus_to_terminus(text: str) -> dict | None:
    m = _P.terminus_to_terminus.match(text)
    if not m:
        return None
    d = m.groupdict()
    return {
        **d,
        'rule_type': 'terminus_to_terminus',
        'terminus_street': d.get('terminus_street') or d.get('terminus_street2'),
    }


def _try_block_to_terminus(text: str) -> dict | None:
    m = _P.block_to_terminus.match(text)
    return _parsed(m, 'block_to_terminus') if m else None


def _try_parenthetical_block(text: str) -> dict | None:
    m = _P.parenthetical_block.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    end = (m.group('end_intersection') or '').strip()
    if _block_side_ok(start) and _block_side_ok(end):
        return _parsed(m, 'parenthetical_block')
    if _block_side_ok(start) and end.lower().startswith('a point opposite'):
        end_street = normalize_anchor_phrase(end)
        if not end_street:
            return None
        out = _parsed(m, 'parenthetical_block')
        out['end_intersection'] = end_street
        return out
    return None


def _try_block_lane_tail(text: str) -> dict | None:
    """``X and Y, first north of Z`` — block endpoints before lane-position tail."""
    if 'point' in text.lower() or 'metres' in text.lower():
        return None
    m = _P.block_lane_tail.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    end = (m.group('end_intersection') or '').strip()
    if not (_block_side_ok(start) and _block_side_ok(end)):
        return None
    return _parsed(m, 'block')


def _try_block(text: str) -> dict | None:
    if 'point' in text.lower() or 'metres' in text.lower():
        return None
    m = _P.block.match(text)
    if not m:
        return None
    start = m.group('start_intersection') or ''
    end = m.group('end_intersection') or ''
    if not (_block_side_ok(start) and _block_side_ok(end)):
        return None
    return _parsed(m, 'block')


def _try_juxtaposed_block(text: str) -> dict | None:
    """Two street names with the ``and`` omitted: 'Alberta Avenue Oakwood Avenue'."""
    if 'point' in text.lower() or 'metres' in text.lower() or ' and ' in text.lower():
        return None
    m = _P.juxtaposed_block.match(text.strip())
    if not m:
        return None
    return _parsed(m, 'block')


def _try_intersect_extension(text: str) -> dict | None:
    m = _P.intersect_extension.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if POINT_METRES_FRAGMENT_RE.match(start):
        return None
    return _parsed(m, 'intersect_extension')


def _try_entire_length(text: str) -> dict | None:
    m = _P.entire_length.match(text)
    return {'rule_type': 'entire_length'} if m else None


# Order matters — first match wins.
_RULE_HANDLERS: tuple[ParseFn, ...] = (
    _try_offset_span,
    _try_dual_offset_of_street,
    _try_metric_and_opposite_limit,
    _try_metric_and_metric_of,
    _try_opposite_and_metric,
    _try_opposite_limit_block,
    _try_opposite_and_block,
    _try_street_and_opposite,
    _try_terminus_to_terminus,
    _try_terminus_end_metric,
    _try_terminus_end_opposite,
    _try_terminus_end_same_metric,
    _try_terminus_end_and_offset,
    _try_terminus_end_block,
    _try_block_to_terminus,
    _try_street_and_terminus_end,
    _try_parenthetical_to_terminus,
    _try_parenthetical_end_block,
    _try_parenthetical_dual_block,
    _try_relative_extension,
    _try_thereof_and_block,
    _try_street_and_bare_metric,
    _try_perfect_offset,
    _try_intersect_to_offset,
    _try_street_and_further_metric,
    _try_metric_and_street,
    _try_intersect_thereof,
    _try_dual_anchor,
    _try_offset_to_intersect,
    _try_parenthetical_block,
    _try_block_lane_tail,
    _try_juxtaposed_block,
    _try_block,
    _try_intersect_extension,
    _try_entire_length,
)


def parse_between(text) -> dict | None:
    """Match Between text against ordered patterns; return parse dict or None."""
    if pd.isna(text):
        return None
    text = preprocess_between(str(text).strip())
    for handler in _RULE_HANDLERS:
        result = handler(text)
        if result is not None:
            return normalize_parsed(result)
    return None


def _build_success_row(
    raw_dict: dict,
    row_id: object,
    parsed: dict,
    highway: str,
    schedule_by_id: dict | None = None,
) -> dict:
    row = dict(raw_dict)
    row.update(parsed_dict_to_columns(parsed))
    row.update(norm_columns_for_row(parsed, highway))
    row['parse_valid'] = True
    row['parse_error'] = ''
    if schedule_by_id is not None:
        sched = schedule_by_id.get(row_id)
        if sched is not None:
            row.update(sched)
        else:
            for col in SCHEDULE_EXPORT_COLUMNS:
                row[col] = None
    return row


def _load_schedule_by_id() -> dict:
    path = data_path('parsed_schedules.csv')
    if not path.exists():
        return {}
    sched_df = pd.read_csv(path)
    by_id: dict = {}
    col_idx = {c: sched_df.columns.get_loc(c) for c in sched_df.columns}
    id_idx = col_idx.get('_id')
    json_idx = col_idx.get('schedule_json')
    stat_idx = col_idx.get('schedule_status')
    max_idx = col_idx.get('max_minutes')
    if id_idx is None or json_idx is None or stat_idx is None:
        return {}
    for tup in sched_df.itertuples(index=False, name=None):
        by_id[tup[id_idx]] = {
            'schedule_json': tup[json_idx],
            'schedule_status': tup[stat_idx],
            'max_minutes': tup[max_idx] if max_idx is not None else None,
        }
    return by_id


def parse_rows(
    df: pd.DataFrame,
    schedule_by_id: dict | None = None,
) -> tuple[pd.DataFrame, Counter]:
    """Parse Between for all clean rows; record failures to the ledger."""
    failure_counts: Counter = Counter()
    rows: list[dict] = []

    parsed_cache: dict[str, dict | None] = {}
    validation_cache: dict[str, tuple[bool, str]] = {}

    col_names = df.columns.tolist()
    col_idx = {c: i for i, c in enumerate(col_names)}
    id_idx = col_idx.get('_id')
    hi_idx = col_idx.get('Highway')
    bt_idx = col_idx.get('Between')

    for tup in df.itertuples(index=False, name=None):
        row_id = tup[id_idx] if id_idx is not None else None
        highway = str(tup[hi_idx]) if hi_idx is not None and not pd.isna(tup[hi_idx]) else ''
        raw_bt = tup[bt_idx] if bt_idx is not None else None
        between = str(raw_bt) if raw_bt is not None and not pd.isna(raw_bt) else ''

        if not between.strip():
            record_failure(
                row_id, 'parse', PARSE_EMPTY_BETWEEN, 'empty Between', highway, between,
            )
            failure_counts[PARSE_EMPTY_BETWEEN] += 1
            continue

        bt_str = between.strip()
        if bt_str in parsed_cache:
            parsed = parsed_cache[bt_str]
        else:
            parsed = parse_between(between)
            parsed_cache[bt_str] = parsed

        if parsed is None:
            between_input = preprocess_between(bt_str)
            record_failure(
                row_id, 'parse', PARSE_NO_MATCH, 'no pattern matched', highway, between,
                between_input,
            )
            failure_counts[PARSE_NO_MATCH] += 1
            continue

        if bt_str in validation_cache:
            ok, err = validation_cache[bt_str]
        else:
            ok, err = validate_parsed(parsed)
            validation_cache[bt_str] = (ok, err)

        if not ok:
            between_input = preprocess_between(bt_str)
            record_failure(
                row_id, 'parse', PARSE_INVALID, err, highway, between, between_input,
            )
            failure_counts[PARSE_INVALID] += 1
            continue

        raw_dict = dict(zip(col_names, tup, strict=True))
        rows.append(_build_success_row(raw_dict, row_id, parsed, highway, schedule_by_id))

    extra_cols = list(SCHEDULE_EXPORT_COLUMNS) if schedule_by_id is not None else []
    if not rows:
        out_cols = list(df.columns) + [c for c in EXPORT_PARSE_COLUMNS if c not in df.columns]
        out_cols += [c for c in extra_cols if c not in out_cols]
        return pd.DataFrame(columns=out_cols), failure_counts

    return pd.DataFrame(rows), failure_counts


def _print_summary(
    total: int,
    success_count: int,
    parse_excluded: int,
    failure_counts: Counter,
) -> None:
    pct = lambda n: round((n / total) * 100, 1) if total else 0.0
    log.info(f'Total Rows: {total}')
    log.info(f'Parsed: {success_count} ({pct(success_count)}%)')
    log.info(f'  Excluded by parse failures: {parse_excluded}')
    if failure_counts:
        log.info('  Parse-stage failures (see failure_ledger.csv):')
        for code, count in failure_counts.most_common():
            log.info(f'    {code}: {count}')


def main() -> None:
    import argparse

    from .log_config import add_verbose_arg, setup_logging

    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    log.info('Parsing Between column...')
    df = pd.read_csv(data_path('clean_parking_targets.csv'))

    clear_stage('parse')
    schedule_by_id = _load_schedule_by_id()
    if not schedule_by_id:
        log.info('Warning: parsed_schedules.csv not found; run parse_schedule.py first.')
    successes, failure_counts = parse_rows(df, schedule_by_id=schedule_by_id or None)
    parse_excluded = len(df) - len(successes)

    successes.to_csv(data_path('parsed_successes.csv'), index=False)
    _print_summary(len(df), len(successes), parse_excluded, failure_counts)


if __name__ == '__main__':
    main()

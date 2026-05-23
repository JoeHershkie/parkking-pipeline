"""Stage 2: parse Between text → parsed_successes.csv (failures → failure_ledger)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from failure_ledger import clear_stage, record_failure
from parse_format import (
    EXPORT_PARSE_COLUMNS,
    norm_columns_for_row,
    parsed_dict_to_columns,
    validate_parsed,
)
from paths import data_path
from schedule_format import SCHEDULE_EXPORT_COLUMNS

# --- Failure reason codes (parse stage) ---

PARSE_NO_MATCH = 'PARSE_NO_MATCH'
PARSE_EMPTY_BETWEEN = 'PARSE_EMPTY_BETWEEN'
PARSE_INVALID = 'PARSE_INVALID'

# --- Shared pattern fragments ---

_COMPASS = r'north|south|east|west|northeast|northwest|southeast|southwest'
_APPROX = r'(?:approximately )?'
_METRES = r'\d+(?:\.\d+)?'
_DIR = r'north|south|east|west'

_FURTHER_TAIL = (
    r'(?:further (?:north|south|east|west)(?:\s+and\s+(?:north|south|east|west))?'
    r'|(?:north|south|east|west)(?:\s+and\s+(?:north|south|east|west))? thereof)'
)

_STREET_END_RE = re.compile(rf'\b(?:{_COMPASS})\s+end\s+of\b', re.IGNORECASE)
_PAREN_QUALIFIER_RE = re.compile(r'\([^)]*intersection[^)]*\)', re.IGNORECASE)
_A_POINT_RE = re.compile(r'^a point\b', re.IGNORECASE)
_POINT_METRES_FRAGMENT_RE = re.compile(r'^a point\s+.*\bmetres\b', re.IGNORECASE)
_A_POINT_OPPOSITE_LIMIT_RE = re.compile(
    r'^a\s+point\s+opposite\s+the\s+.+?\blimit\s+of\s+(?P<street>.+)$',
    re.IGNORECASE,
)
_A_POINT_OPPOSITE_RE = re.compile(r'^a\s+point\s+opposite\s+(?P<street>.+)$', re.IGNORECASE)
_THE_LIMIT_RE = re.compile(r'^the\s+.+?\blimit\s+of\s+(?P<street>.+)$', re.IGNORECASE)

_ANCHOR_FIELDS = frozenset({
    'start_intersection',
    'end_intersection',
    'offset_intersection',
})


@dataclass(frozen=True)
class _Patterns:
    offset_span: re.Pattern
    opposite_and_metric: re.Pattern
    perfect_offset: re.Pattern
    intersect_to_offset: re.Pattern
    offset_to_intersect: re.Pattern
    relative_extension: re.Pattern
    dual_anchor: re.Pattern
    parenthetical_to_terminus: re.Pattern
    parenthetical_end_block: re.Pattern
    block_to_terminus: re.Pattern
    terminus_to_terminus: re.Pattern
    parenthetical_block: re.Pattern
    block: re.Pattern
    intersect_extension: re.Pattern
    entire_length: re.Pattern


def _compile_patterns() -> _Patterns:
    return _Patterns(
        offset_span=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_DIR}) of (?P<start_intersection>.*?) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres (?P<dir2>{_DIR})$',
            re.IGNORECASE,
        ),
        opposite_and_metric=re.compile(
            rf'^A point opposite (?P<start_intersection>.+?) and a point {_APPROX}'
            rf'(?P<distance>{_METRES}) metres (?P<direction>{_DIR})$',
            re.IGNORECASE,
        ),
        perfect_offset=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres (?P<direction>{_DIR})$',
            re.IGNORECASE,
        ),
        intersect_to_offset=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres '
            rf'(?P<direction>{_DIR}) of (?P<offset_intersection>.*?)$',
            re.IGNORECASE,
        ),
        offset_to_intersect=re.compile(
            rf'^A point {_APPROX}(?P<distance>{_METRES}) metres (?P<direction>{_DIR}) of '
            rf'(?P<start_intersection>.*?) and (?P<end_intersection>.*?)$',
            re.IGNORECASE,
        ),
        relative_extension=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_DIR}) of (?P<start_intersection>.*?) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres {_FURTHER_TAIL}$',
            re.IGNORECASE,
        ),
        dual_anchor=re.compile(
            rf'^A point {_APPROX}(?P<dist1>{_METRES}) metres (?P<dir1>{_DIR}) of (?P<start_intersection>.*?) '
            rf'and a point {_APPROX}(?P<dist2>{_METRES}) metres (?P<dir2>{_DIR}) of (?P<end_intersection>.*?)$',
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
        block_to_terminus=re.compile(
            rf'^(?P<start_intersection>.*?) and the (?P<terminus_direction>{_COMPASS}) end of (?P<terminus_street>.*?)$',
            re.IGNORECASE,
        ),
        terminus_to_terminus=re.compile(
            rf'^The (?P<terminus_start_dir>{_COMPASS}) end of (?P<terminus_street>.*?) '
            rf'and the (?P<terminus_end_dir>{_COMPASS}) end of (?P<terminus_street2>.*?)$',
            re.IGNORECASE,
        ),
        parenthetical_block=re.compile(
            r'^(?P<start_intersection>.*?)\s*\((?P<start_intersection_qualifier>[^)]+)\)\s*'
            r'and\s*(?P<end_intersection>.*?)$',
            re.IGNORECASE,
        ),
        block=re.compile(
            r'^(?P<start_intersection>.*?) and (?P<end_intersection>.*?)$',
            re.IGNORECASE,
        ),
        intersect_extension=re.compile(
            rf'^(?P<start_intersection>.*?) and a point {_APPROX}(?P<distance>{_METRES}) metres further (?P<direction>{_DIR})$',
            re.IGNORECASE,
        ),
        entire_length=re.compile(r'^Entire length$', re.IGNORECASE),
    )


_P = _compile_patterns()

ParseFn = Callable[[str], dict | None]


def _parsed(match: re.Match, rule_type: str, **extra: str) -> dict:
    return {**match.groupdict(), **extra, 'rule_type': rule_type}


def normalize_anchor_phrase(text: str) -> str:
    """Reduce 'a point opposite the east limit of X' / similar to a street name."""
    raw = str(text).strip()
    if not raw:
        return raw
    m = _A_POINT_OPPOSITE_LIMIT_RE.match(raw)
    if m:
        return m.group('street').strip()
    m = _A_POINT_OPPOSITE_RE.match(raw)
    if m:
        return m.group('street').strip()
    m = _THE_LIMIT_RE.match(raw)
    if m:
        return m.group('street').strip()
    return raw


def normalize_parsed(parsed: dict) -> dict:
    """Normalize anchor fields on a successful parse."""
    out = dict(parsed)
    for key in _ANCHOR_FIELDS:
        if key in out and out[key]:
            out[key] = normalize_anchor_phrase(out[key])
    return out


def _starts_with_a_point(text: str) -> bool:
    return bool(_A_POINT_RE.match(text.strip()))


def _is_street_end_phrase(text: str) -> bool:
    return bool(_STREET_END_RE.search(text))


def _block_side_ok(side: str) -> bool:
    side = str(side).strip()
    if not side:
        return False
    if _is_street_end_phrase(side):
        return False
    if _PAREN_QUALIFIER_RE.search(side):
        return False
    if _starts_with_a_point(side):
        return False
    return True


def _try_offset_span(text: str) -> dict | None:
    """Two metric offsets from the same cross-street anchor (not dist1 + dist2 further)."""
    m = _P.offset_span.match(text)
    return _parsed(m, 'offset_span') if m else None


def _try_opposite_and_metric(text: str) -> dict | None:
    m = _P.opposite_and_metric.match(text)
    return _parsed(m, 'perfect_offset') if m else None


def _try_perfect_offset(text: str) -> dict | None:
    m = _P.perfect_offset.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if _POINT_METRES_FRAGMENT_RE.match(start):
        return None
    return _parsed(m, 'perfect_offset')


def _try_intersect_to_offset(text: str) -> dict | None:
    m = _P.intersect_to_offset.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if _starts_with_a_point(start):
        return None
    return _parsed(m, 'intersect_to_offset')


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
    if _starts_with_a_point(end) or 'metres' in end.lower():
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
    if not m or not _block_side_ok(m.group('end_intersection')):
        return None
    return _parsed(m, 'parenthetical_block')


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


def _try_intersect_extension(text: str) -> dict | None:
    m = _P.intersect_extension.match(text)
    if not m:
        return None
    start = (m.group('start_intersection') or '').strip()
    if _POINT_METRES_FRAGMENT_RE.match(start):
        return None
    return _parsed(m, 'intersect_extension')


def _try_entire_length(text: str) -> dict | None:
    m = _P.entire_length.match(text)
    return {'rule_type': 'entire_length'} if m else None


# Order matters — first match wins.
_RULE_HANDLERS: tuple[ParseFn, ...] = (
    _try_offset_span,
    _try_opposite_and_metric,
    _try_relative_extension,
    _try_perfect_offset,
    _try_intersect_to_offset,
    _try_dual_anchor,
    _try_offset_to_intersect,
    _try_parenthetical_to_terminus,
    _try_parenthetical_end_block,
    _try_terminus_to_terminus,
    _try_block_to_terminus,
    _try_parenthetical_block,
    _try_block,
    _try_intersect_extension,
    _try_entire_length,
)


def parse_between(text) -> dict | None:
    """Match Between text against ordered patterns; return parse dict or None."""
    if pd.isna(text):
        return None
    text = str(text).strip()
    for handler in _RULE_HANDLERS:
        result = handler(text)
        if result is not None:
            return normalize_parsed(result)
    return None


def _build_success_row(
    raw: pd.Series,
    parsed: dict,
    schedule_by_id: dict | None = None,
) -> dict:
    row = raw.to_dict()
    row.update(parsed_dict_to_columns(parsed))
    row.update(norm_columns_for_row(parsed, raw.get('Highway', '')))
    row['parse_valid'] = True
    row['parse_error'] = ''
    if schedule_by_id is not None:
        sched = schedule_by_id.get(raw['_id'])
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
    for _, srow in sched_df.iterrows():
        by_id[srow['_id']] = {
            'schedule_json': srow['schedule_json'],
            'schedule_status': srow['schedule_status'],
            'max_minutes': srow.get('max_minutes'),
        }
    return by_id


def parse_rows(
    df: pd.DataFrame,
    schedule_by_id: dict | None = None,
) -> tuple[pd.DataFrame, Counter]:
    """Parse Between for all clean rows; record failures to the ledger."""
    failure_counts: Counter = Counter()
    rows: list[dict] = []

    for _, raw in df.iterrows():
        between = raw.get('Between', '')
        highway = raw.get('Highway', '')
        row_id = raw['_id']

        if pd.isna(between) or not str(between).strip():
            record_failure(row_id, 'parse', PARSE_EMPTY_BETWEEN, 'empty Between', highway, between)
            failure_counts[PARSE_EMPTY_BETWEEN] += 1
            continue

        parsed = parse_between(between)
        if parsed is None:
            record_failure(row_id, 'parse', PARSE_NO_MATCH, 'no pattern matched', highway, between)
            failure_counts[PARSE_NO_MATCH] += 1
            continue

        ok, err = validate_parsed(parsed)
        if not ok:
            record_failure(row_id, 'parse', PARSE_INVALID, err, highway, between)
            failure_counts[PARSE_INVALID] += 1
            continue

        rows.append(_build_success_row(raw, parsed, schedule_by_id))

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
    print(f'Total Rows: {total}')
    print(f'Parsed: {success_count} ({pct(success_count)}%)')
    print(f'  Excluded by parse failures: {parse_excluded}')
    if failure_counts:
        print('  Parse-stage failures (see failure_ledger.csv):')
        for code, count in failure_counts.most_common():
            print(f'    {code}: {count}')


def main() -> None:
    print('Parsing Between column...')
    df = pd.read_csv(data_path('clean_parking_targets.csv'))

    clear_stage('parse')
    schedule_by_id = _load_schedule_by_id()
    if not schedule_by_id:
        print('Warning: parsed_schedules.csv not found; run parse_schedule.py first.')
    successes, failure_counts = parse_rows(df, schedule_by_id=schedule_by_id or None)
    parse_excluded = len(df) - len(successes)

    successes.to_csv(data_path('parsed_successes.csv'), index=False)
    _print_summary(len(df), len(successes), parse_excluded, failure_counts)


if __name__ == '__main__':
    main()

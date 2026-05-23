"""Parse bylaw time strings into a versioned JSON schedule for membership filters.

Frontend contract (implemented here for tests and documentation):

    overlaps_membership(schedule, slot) -> bool

*slot* uses ``dayOfWeek`` 0=Sun … 6=Sat (``Date.getDay()``), ``minuteOfDay`` 0–1439,
``month`` 1–12, ``dayOfMonth`` 1–31.

* ``status == 'anytime'``: matches time/day filters; calendar predicates still apply.
* ``status == 'failed'``: returns ``False`` (webapp may choose to include unknown rows).
* ``status == 'partial'``: OR over parsed ``windows`` only.
* ``flags.exceptPublicHolidays``: metadata only in v1 (no holiday calendar).
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

SCHEDULE_VERSION = 1

# Detect calendar phrases we do not fully parse yet (phases D/E).
_CALENDAR_MARKER_RE = re.compile(
    r'\b(?:'
    r'Jan\.|Feb\.|Mar\.|Apr\.|May\.|Jun\.|Jul\.|Aug\.|Sep\.|Sept\.|Oct\.|Nov\.|Dec\.'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d'
    r'|day of each month'
    r'|one year to'
    r'|following year'
    r')\b',
    re.IGNORECASE,
)

_INVERTED_MARKER_RE = re.compile(
    r'\bAnytime\s*,\s*except\b',
    re.IGNORECASE,
)

_MERIDIEM = r'a\.m\.?|p\.m\.?'

_TIME_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})',
    re.IGNORECASE,
)

_SIMPLE_RANGE_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})\s+to\s+'
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})',
    re.IGNORECASE,
)

_OVERNIGHT_RANGE_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})\s+of one day\s+to\s+'
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})\s+of the next(?:\s+following)?\s+day',
    re.IGNORECASE,
)

_ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]

# (pattern, days, extra_flags) — matched from end of clause segment
_DAY_TAIL_RULES: tuple[tuple[re.Pattern[str], list[int], dict[str, bool]], ...] = (
    (
        re.compile(
            r',\s*Mon\.?\s+to\s+Fri\.?,?\s*except\s+public\s+holidays\.?\s*$',
            re.IGNORECASE,
        ),
        [1, 2, 3, 4, 5],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',\s*Mon\.?\s+to\s+Fri\.?,?\s*inclusive\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    (
        re.compile(r',\s*Mon\.?\s+to\s+Fri\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    (
        re.compile(r',\s*Mon\.?\s+to\s+Sat\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5, 6],
        {},
    ),
    (
        re.compile(
            r',\s*Sat\.?,?\s+Sun\.?(?:\s+and\s+public\s+holidays)?\.?\s*$',
            re.IGNORECASE,
        ),
        [0, 6],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',\s*Sat\.?\s+and\s+Sun\.?\s*$', re.IGNORECASE),
        [0, 6],
        {},
    ),
    (
        re.compile(r',\s*Sat\.?\s*$', re.IGNORECASE),
        [6],
        {},
    ),
    (
        re.compile(r',\s*Sun\.?\s*$', re.IGNORECASE),
        [0],
        {},
    ),
    (
        re.compile(r',\s*except\s+public\s+holidays\.?\s*$', re.IGNORECASE),
        _ALL_DAYS,
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r'\s+Mon\.?\s+to\s+Fri\.?,?\s*except\s+public\s+holidays\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r'\s+Mon\.?\s+to\s+Fri\.?,?\s*inclusive\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    (
        re.compile(r'\s+Mon\.?\s+to\s+Fri\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    (
        re.compile(r'\s+Mon\.?\s+to\s+Sat\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5, 6],
        {},
    ),
    (
        re.compile(r'^Mon\.?\s+to\s+Fri\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    (
        re.compile(r'^Mon\.?\s+to\s+Sat\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5, 6],
        {},
    ),
)

_MAX_MINUTES_RE = re.compile(
    r'^(\d+)\s*(hour|hours|hr|hrs|mins?\.?|minute|minutes)\b',
    re.IGNORECASE,
)


def _is_blank(val: Any) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    return not str(val).strip()


def time_to_minutes(hour: int, minute: int, meridiem: str) -> int:
    """Convert 12-hour clock to minutes since midnight (0–1439)."""
    mer = meridiem.lower().replace('.', '')
    h = int(hour) % 12
    if mer == 'pm':
        h += 12
    if mer == 'am' and int(hour) == 12:
        h = 0
    return h * 60 + int(minute)


def parse_max_minutes(text: Any) -> int | None:
    """Parse ``Maximum Period Permitted`` into minutes, or None."""
    if _is_blank(text):
        return None
    s = str(text).strip()
    m = _MAX_MINUTES_RE.match(s)
    if not m:
        return None
    qty = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith('hour') or unit.startswith('hr'):
        return qty * 60
    return qty


def _empty_schedule(source: str, status: str = 'failed', **extra: Any) -> dict:
    out: dict[str, Any] = {
        'v': SCHEDULE_VERSION,
        'status': status,
        'source': source,
        'windows': [],
    }
    out.update(extra)
    return out


def _merge_flags(target: dict[str, bool], incoming: dict[str, bool]) -> None:
    flags = target.setdefault('flags', {})
    for k, v in incoming.items():
        if v:
            flags[k] = True


def _extract_day_tail(text: str) -> tuple[str, list[int], dict[str, bool]]:
    """Strip trailing weekday phrase; return (remainder, days, flags)."""
    for pat, days, flag_updates in _DAY_TAIL_RULES:
        m = pat.search(text)
        if m:
            return text[: m.start()].strip(), days, dict(flag_updates)
    return text.strip(), list(_ALL_DAYS), {}


def _split_on_and_before_time(text: str) -> list[str]:
    """Split `` and `` only when the next segment begins with a time."""
    parts: list[str] = []
    rest = text.strip()
    while rest:
        idx = rest.lower().find(' and ')
        if idx < 0:
            parts.append(rest.strip())
            break
        after = rest[idx + 5 :].lstrip()
        if _TIME_RE.match(after) or after.lower().startswith('anytime'):
            parts.append(rest[:idx].strip())
            rest = after
        else:
            parts.append(rest.strip())
            break
    return [p for p in parts if p]


def _parse_time_ranges(segment: str) -> tuple[list[dict], str | None]:
    """Extract windows from a segment that shares one day-tail (already stripped)."""
    windows: list[dict] = []
    remaining = segment.strip().rstrip(',')
    err: str | None = None

    while remaining:
        overnight = _OVERNIGHT_RANGE_RE.search(remaining)
        if overnight and (not windows or overnight.start() == 0):
            start_m = time_to_minutes(
                overnight.group(1),
                overnight.group(2),
                overnight.group(3),
            )
            end_m = time_to_minutes(
                overnight.group(4),
                overnight.group(5),
                overnight.group(6),
            )
            windows.append({
                'startMinute': start_m,
                'endMinute': end_m,
                'crossesMidnight': True,
            })
            remaining = remaining[overnight.end() :].strip().lstrip(',').strip()
            if remaining.lower().startswith('and '):
                remaining = remaining[4:].strip()
            continue

        simple = _SIMPLE_RANGE_RE.search(remaining)
        if simple and (not windows or simple.start() == 0):
            start_m = time_to_minutes(simple.group(1), simple.group(2), simple.group(3))
            end_m = time_to_minutes(simple.group(4), simple.group(5), simple.group(6))
            crosses = end_m <= start_m
            windows.append({
                'startMinute': start_m,
                'endMinute': end_m,
                'crossesMidnight': crosses,
            })
            remaining = remaining[simple.end() :].strip().lstrip(',').strip()
            if remaining.lower().startswith('and '):
                remaining = remaining[4:].strip()
            continue

        err = f'unparsed time fragment: {remaining[:80]}'
        break

    return windows, err


def _parse_segment(segment: str) -> tuple[list[dict], dict[str, bool], str | None]:
    """Parse one day-group segment (may contain multiple `` and ``-joined ranges)."""
    flags: dict[str, bool] = {}
    all_windows: list[dict] = []
    parts = _split_on_and_before_time(segment)
    for part in parts:
        part = part.strip().rstrip(',')
        if not part:
            continue
        is_anytime = bool(re.match(r'^anytime\b', part, re.IGNORECASE))
        body, days, seg_flags = _extract_day_tail(part)
        _merge_flags({'flags': flags}, seg_flags)
        if is_anytime:
            body = re.sub(r'^anytime,?\s*', '', body, flags=re.IGNORECASE).strip()
            if body:
                tw, err = _parse_time_ranges(body)
                if err:
                    return [], flags, err
                for w in tw:
                    w['days'] = days
                all_windows.extend(tw)
            else:
                all_windows.append({
                    'days': days,
                    'startMinute': 0,
                    'endMinute': 1439,
                    'crossesMidnight': False,
                })
            continue
        tw, err = _parse_time_ranges(body)
        if err:
            if not body.strip():
                all_windows.append({
                    'days': days,
                    'startMinute': 0,
                    'endMinute': 1439,
                    'crossesMidnight': False,
                })
                continue
            return [], flags, err
        if not tw and not body.strip():
            all_windows.append({
                'days': days,
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
            })
            continue
        for w in tw:
            w['days'] = days
        all_windows.extend(tw)
    if not all_windows:
        return [], flags, 'no time windows in segment'
    return all_windows, flags, None


def _parse_clause(clause: str) -> tuple[list[dict], dict[str, bool], str | None]:
    clause = clause.strip().rstrip(',')
    if not clause:
        return [], {}, 'empty clause'
    return _parse_segment(clause)


def _has_unsupported_calendar(text: str) -> bool:
    return bool(_CALENDAR_MARKER_RE.search(text))


def _has_unsupported_inverted(text: str) -> bool:
    return bool(_INVERTED_MARKER_RE.search(text))


def parse_schedule(source: Any) -> dict:
    """Parse ``Prohibited Times and/or Days`` into a schedule dict."""
    if _is_blank(source):
        return _empty_schedule('', status='failed')

    text = str(source).strip()
    normalized = text.casefold()

    if normalized == 'anytime':
        return {
            'v': SCHEDULE_VERSION,
            'status': 'anytime',
            'source': text,
            'windows': [],
        }

    if _has_unsupported_inverted(text):
        return _empty_schedule(text, status='failed')

    if _has_unsupported_calendar(text):
        return _empty_schedule(text, status='failed')

    clauses = [c.strip() for c in text.split(';') if c.strip()]
    if not clauses:
        return _empty_schedule(text, status='failed')

    combined_windows: list[dict] = []
    combined_flags: dict[str, bool] = {}
    unparsed: list[str] = []

    for clause in clauses:
        windows, flags, err = _parse_clause(clause)
        _merge_flags({'flags': combined_flags}, flags)
        if err:
            unparsed.append(clause)
        else:
            combined_windows.extend(windows)

    if not combined_windows:
        return _empty_schedule(text, status='failed', unparsedClauses=unparsed)

    out: dict[str, Any] = {
        'v': SCHEDULE_VERSION,
        'status': 'ok',
        'source': text,
        'windows': combined_windows,
    }
    if combined_flags:
        out['flags'] = combined_flags
    if unparsed:
        out['status'] = 'partial'
        out['unparsedClauses'] = unparsed
    return out


def schedule_to_json(schedule: dict) -> str:
    return json.dumps(schedule, separators=(',', ':'), ensure_ascii=False)


def schedule_from_json(raw: Any) -> dict | None:
    if _is_blank(raw):
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return None


def _calendar_ok(schedule: dict, slot: dict[str, int]) -> bool:
    cal = schedule.get('calendar')
    if not cal:
        return True
    # Phase D not implemented — should not appear without calendar keys
    return False


def _minute_in_window(minute: int, start: int, end: int, crosses_midnight: bool) -> bool:
    if crosses_midnight:
        return minute >= start or minute < end
    if end > start:
        return start <= minute < end
    return minute >= start or minute < end


def _window_overlaps_slot(window: dict, slot: dict[str, int]) -> bool:
    days = window.get('days', _ALL_DAYS)
    if slot['dayOfWeek'] not in days:
        return False
    return _minute_in_window(
        slot['minuteOfDay'],
        window['startMinute'],
        window['endMinute'],
        bool(window.get('crossesMidnight')),
    )


def overlaps_membership(schedule: dict | None, slot: dict[str, int]) -> bool:
    """Return whether *schedule* overlaps the membership slot."""
    if not schedule:
        return False
    status = schedule.get('status')
    if status == 'failed':
        return False
    if not _calendar_ok(schedule, slot):
        return False
    if status == 'anytime':
        return True
    for window in schedule.get('windows', []):
        if _window_overlaps_slot(window, slot):
            return True
    return False


SCHEDULE_EXPORT_COLUMNS = ['schedule_json', 'schedule_status', 'max_minutes']

"""Parse bylaw time strings into a versioned JSON schedule for membership filters.

Frontend contract (implemented here for tests and documentation):

    overlaps_membership(schedule, slot) -> bool

*slot* uses ``dayOfWeek`` 0=Sun … 6=Sat (``Date.getDay()``), ``minuteOfDay`` 0–1439,
``month`` 1–12, ``dayOfMonth`` 1–31, and optionally ``year`` (needed for ``end: "last"``
day-of-month ranges).

* ``status == 'anytime'``: matches time/day filters; calendar predicates still apply.
* ``status == 'failed'``: returns ``False`` (webapp may choose to include unknown rows).
* ``status == 'partial'``: OR over parsed ``windows`` only.
* ``inverted``: ``windows`` are EXCEPT periods; prohibition active when calendar ok and
  no except-window matches.
* ``flags.exceptPublicHolidays``: on a matching window, Ontario public holidays do not
  count as in-window (prohibition not active). Inverted schedules: holidays count as
  except periods when the flag is set. Requires ``year`` in *slot* (see ``public_holidays``).
"""

from __future__ import annotations

import calendar as cal_mod
import json
import re
from typing import Any

import pandas as pd

from .public_holidays import is_public_holiday

SCHEDULE_VERSION = 1

_ALL_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

_MONTH_ABBR: dict[str, int] = {
    'january': 1, 'jan': 1, 'jan.': 1,
    'february': 2, 'feb': 2, 'feb.': 2,
    'march': 3, 'mar': 3, 'mar.': 3,
    'april': 4, 'apr': 4, 'apr.': 4,
    'may': 5, 'may.': 5,
    'june': 6, 'jun': 6, 'jun.': 6,
    'july': 7, 'jul': 7, 'jul.': 7,
    'august': 8, 'aug': 8, 'aug.': 8,
    'september': 9, 'sept': 9, 'sep': 9, 'sept.': 9, 'sep.': 9,
    'october': 10, 'oct': 10, 'oct.': 10,
    'november': 11, 'nov': 11, 'nov.': 11,
    'december': 12, 'dec': 12, 'dec.': 12,
}

_WEEKDAY_ABBR: dict[str, int] = {
    'sunday': 0, 'sun': 0, 'sun.': 0,
    'monday': 1, 'mon': 1, 'mon.': 1,
    'tuesday': 2, 'tues': 2, 'tue': 2, 'tues.': 2, 'tue.': 2,
    'wednesday': 3, 'wed': 3, 'wed.': 3,
    'thursday': 4, 'thurs': 4, 'thur': 4, 'thu': 4, 'thurs.': 4, 'thur.': 4, 'thu.': 4,
    'friday': 5, 'fri': 5, 'fri.': 5,
    'saturday': 6, 'sat': 6, 'sat.': 6,
}

_INVERTED_MARKER_RE = re.compile(
    r'\bAnytime\s*,\s*except\b|\bAnytime\s+except\b',
    re.IGNORECASE,
)

_MERIDIEM = r'a\.m\.?|p\.m\.?|noon|midnight'

_TIME_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})',
    re.IGNORECASE,
)

_SIMPLE_RANGE_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})\s+(?:to|-)\s+'
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})',
    re.IGNORECASE,
)

_OVERNIGHT_RANGE_RE = re.compile(
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})\s+(?:of\s+one\s+day|on\s+one\s+day|one\s+day|of\s+day)\s+to\s+'
    rf'(\d{{1,2}}):(\d{{2}})\s*({_MERIDIEM})\s+(?:of\s+the\s+next|on\s+the\s+next|the\s+next|of\s+the\s+following|the\s+following)?(?:\s+following)?\s+day',
    re.IGNORECASE,
)

_MONTH_NAME_RE = r'(?:January|Jan|February|Feb|March|Mar|April|Apr|May|June|Jun|July|Jul|August|Aug|September|Sept|Sep|October|Oct|November|Nov|December|Dec)\.?'
_DAY_NAME_RE = r'(?:Monday|Mon|Tuesday|Tues|Tue|Wednesday|Wed|Thursday|Thurs|Thur|Thu|Friday|Fri|Saturday|Sat|Sunday|Sun)\.?'

# Date range with explicit year (e.g. from June 6, 2022 to June 12, 2022)
_DATE_RANGE_YEAR_RE = re.compile(
    rf',?\s*(?:from\s+(?:and\s+including\s+)?)?(?:{_DAY_NAME_RE}\s*,?\s+)?({_MONTH_NAME_RE}),?\s+(\d{{1,2}}),?\s+(\d{{4}}),?\s+'
    rf'to\s+(?:and\s+including\s+)?(?:{_DAY_NAME_RE}\s*,?\s+)?({_MONTH_NAME_RE}),?\s+(\d{{1,2}}),?\s+(\d{{4}}),?\s*(?:inclusive)?\.?\s*$',
    re.IGNORECASE,
)

_SINGLE_DATE_YEAR_RE = re.compile(
    rf',?\s*(?:on\s+)?(?:{_DAY_NAME_RE}\s*,?\s+)?({_MONTH_NAME_RE}),?\s+(\d{{1,2}}),?\s+(\d{{4}})\.?\s*$',
    re.IGNORECASE,
)

# Calendar tails (stripped from end of clause, may repeat)
_SEASONAL_YEAR_SPAN_RE = re.compile(
    r',?\s*(?:from\s+)?([A-Za-z]+)\.?\s+(\d{1,2})\s+of\s+one\s+year\s+to\s+'
    r'([A-Za-z]+)\.?\s+(\d{1,2})\s+of\s+the\s+(?:next\s+)?(?:following\s+)?year,?\s*(?:inclusive)?\.?\s*$',
    re.IGNORECASE,
)
_SEASONAL_NO_INCLUSIVE_RE = re.compile(
    r',?\s*([A-Za-z]+)\.?\s+(\d{1,2})\s+to\s+([A-Za-z]+)\.?\s+(\d{1,2})\.?\s*$',
    re.IGNORECASE,
)
_SEASONAL_FROM_RE = re.compile(
    r',?\s*from\s+([A-Za-z]+)\.?\s+(\d{1,2})\s+to\s+'
    r'([A-Za-z]+)\.?\s+(\d{1,2}),?\s*inclusive\.?\s*$',
    re.IGNORECASE,
)
_SEASONAL_PLAIN_RE = re.compile(
    r',?\s*(?:from\s+)?([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+to\s+'
    r'([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(?:inclusive)?\.?\s*$',
    re.IGNORECASE,
)
_SEASONAL_MONTH_TO_MONTH_RE = re.compile(
    rf',?\s*(?:from\s+)?({_MONTH_NAME_RE})\s+(?:1st|1)\s+to\s+({_MONTH_NAME_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*inclusive|\s+inclusive)?\.?\s*$',
    re.IGNORECASE,
)
_DOM_FROM_THE_RE = re.compile(
    r'(?:,\s*|\s+)from the (\d{1,2})(?:st|nd|rd|th)? day (?:of each month )?to the '
    r'(\d{1,2})(?:st|nd|rd|th)? day of each month\.?\s*$',
    re.IGNORECASE,
)
_DOM_FROM_THE_SHORT_ORD_RE = re.compile(
    r'(?:,\s*|\s+)from the (\d{1,2})(?:st|nd|rd|th)? day to the '
    r'(\d{1,2})(?:st|nd|rd|th)? day of each month\.?\s*$',
    re.IGNORECASE,
)
_DOM_FROM_THE_LAST_RE = re.compile(
    r'(?:,\s*|\s+)from the (\d{1,2})(?:st|nd|rd|th)? day (?:of each month )?to the '
    r'last day of each month\.?\s*$',
    re.IGNORECASE,
)
_DOM_FROM_THE_SHORT_LAST_RE = re.compile(
    r'(?:,\s*|\s+)from the (\d{1,2})(?:st|nd|rd|th)? day to the '
    r'last day of each month\.?\s*$',
    re.IGNORECASE,
)
_DOM_LEADING_ORD_LAST_RE = re.compile(
    r'^(\d{1,2})(?:st|nd|rd|th)? day to the last day of each month',
    re.IGNORECASE,
)
_DOM_LEADING_ORD_ORD_RE = re.compile(
    r'^(?:from the )?(\d{1,2})(?:st|nd|rd|th)? day to the '
    r'(\d{1,2})(?:st|nd|rd|th)? day of each month',
    re.IGNORECASE,
)
_DOM_LEADING_FIRST_RE = re.compile(
    r'^first day to the (\d{1,2})(?:st|nd|rd|th)? day of each month',
    re.IGNORECASE,
)
_DOM_FIRST_LAST_WORD_RE = re.compile(
    r',?\s*(?:from the )?first day to the (\d{1,2})(?:st|nd|rd|th)? day of each month\.?\s*$',
    re.IGNORECASE,
)
_DOM_ORD_TO_ORD_RE = re.compile(
    r',?\s*(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+)?to\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+)?of\s+(?:each\s+)?month\.?\s*$',
    re.IGNORECASE,
)
_DOM_ORD_TO_LAST_RE = re.compile(
    r',?\s*(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+)?to\s+(?:the\s+)?(?:last\s+day|end)\s+of\s+(?:each\s+)?month\.?\s*$',
    re.IGNORECASE,
)
_MONTH_LIST_RE = re.compile(
    rf'^(?:during the months of\s+)?((?:{_MONTH_NAME_RE}(?:\s*,\s*|\s+))+(?:and\s+)?{_MONTH_NAME_RE})\s*$',
    re.IGNORECASE,
)
_EACH_WEEKDAY_RE = re.compile(
    rf'^(?:on\s+)?(?:the\s+)?(?:1st|2nd|3rd|4th|first|second|third|fourth)?\s*(?:and\s+(?:1st|2nd|3rd|4th|first|second|third|fourth)\s+)?Each\s+({_DAY_NAME_RE})\s*,?\s*(.*)$',
    re.IGNORECASE,
)
_ORDINAL_WEEKDAY_RE = re.compile(
    rf'^(?:on\s+)?(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{_DAY_NAME_RE}\s+)?and\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+({_DAY_NAME_RE})\s+of\s+each\s+month\s*,?\s*(.*)$',
    re.IGNORECASE,
)

_ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]

# (pattern, days, extra_flags) — matched from end of clause segment
_DAY_TAIL_RULES: tuple[tuple[re.Pattern[str], list[int], dict[str, bool]], ...] = (
    # Explicit except Sunday & public holidays
    (
        re.compile(r',?\s*except\s+(?:Sun\.?|Sunday)\s+and\s+public\s+holidays\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5, 6],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*except\s+(?:Sun\.?|Sunday)\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5, 6],
        {},
    ),
    # Explicit except Saturday & Sunday (meaning Mon-Fri)
    (
        re.compile(r',?\s*except\s+(?:Sat\.?,?\s+(?:and\s+)?Sun\.?|Saturday\s*,?\s+and\s+Sunday)\.?,?\s+and\s+public\s+holidays\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*except\s+(?:Sat\.?,?\s+(?:and\s+)?Sun\.?|Saturday\s*,?\s+and\s+Sunday)\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    # Mon to Fri except each Thurs.
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Fri|Friday)\.?,?\s*except\s+each\s+(?:Thu|Thur|Thurs|Thursday)\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 5],
        {},
    ),
    # Mon to Fri with variations
    (
        re.compile(
            r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Fri|Friday)\.?,?\s*except\s+public\s+holidays\.?\s*$',
            re.IGNORECASE,
        ),
        [1, 2, 3, 4, 5],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Fri|Friday)\.?,?\s*inclusive\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Fri|Friday)\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5],
        {},
    ),
    # Mon to Sat
    (
        re.compile(
            r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Sat|Saturday)\.?,?\s*except\s+(?:Sun\.?\s+and\s+)?public\s+holidays\.?\s*$',
            re.IGNORECASE,
        ),
        [1, 2, 3, 4, 5, 6],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Sat|Saturday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4, 5, 6],
        {},
    ),
    # Mon to Thu
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Thu|Thur|Thurs|Thursday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 4],
        {},
    ),
    # Tue to Thu
    (
        re.compile(r',?\s*(?:Tue|Tues|Tuesday)\.?\s*(?:to|-)\s*(?:Thu|Thur|Thurs|Thursday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [2, 3, 4],
        {},
    ),
    # Fri through Mon
    (
        re.compile(r',?\s*(?:Fri|Friday)\.?\s*(?:through|to|-)\s*(?:Mon|Monday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [5, 6, 0, 1],
        {},
    ),
    # Fri and Sat / Fri to Sat
    (
        re.compile(r',?\s*(?:Fri|Friday)\.?\s*(?:and|to|-)\s*(?:Sat|Saturday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [5, 6],
        {},
    ),
    # Sat to Sun / Sat and Sun
    (
        re.compile(
            r',?\s*(?:on\s+)?(?:Sat|Saturday)\.?,?\s+(?:and|to|-)\s+(?:Sun|Sunday)\.?(?:\s+and\s+public\s+holidays)?\.?\s*$',
            re.IGNORECASE,
        ),
        [0, 6],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(
            r',?\s*(?:on\s+)?(?:Sat|Saturday)\.?,?\s+(?:Sun|Sunday)\.?(?:\s+and\s+public\s+holidays)?\.?\s*$',
            re.IGNORECASE,
        ),
        [0, 6],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*(?:on\s+)?(?:Sat|Saturday)\.?\s*(?:and|to|-)\s*(?:Sun|Sunday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [0, 6],
        {},
    ),
    # Sat to Thu
    (
        re.compile(r',?\s*(?:Sat|Saturday)\.?\s*(?:to|-)\s*(?:Thu|Thurs|Thursday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [6, 0, 1, 2, 3, 4],
        {},
    ),
    # Sun to Fri
    (
        re.compile(r',?\s*(?:Sun|Sunday)\.?\s*(?:to|-)\s*(?:Fri|Friday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        [0, 1, 2, 3, 4, 5],
        {},
    ),
    # Mon to Sun
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*(?:to|-)\s*(?:Sun|Sunday)\.?,?\s*(?:inclusive)?\.?\s*$', re.IGNORECASE),
        _ALL_DAYS,
        {},
    ),
    # Mon, Tues, Wed and Fri
    (
        re.compile(r',?\s*(?:Mon|Monday)\.?\s*,\s*(?:Tue|Tues|Tuesday)\.?\s*,\s*(?:Wed|Wednesday)\.?\s+and\s+(?:Fri|Friday)\.?\s*$', re.IGNORECASE),
        [1, 2, 3, 5],
        {},
    ),
    # Single days
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Sun|Sunday)\.?\s+and\s+public\s+holidays\.?\s*$', re.IGNORECASE),
        [0],
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Sun|Sunday)\.?\s*$', re.IGNORECASE),
        [0],
        {},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Mon|Monday)\.?\s*$', re.IGNORECASE),
        [1],
        {},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Tue|Tues|Tuesday)\.?\s*$', re.IGNORECASE),
        [2],
        {},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Wed|Wednesday)\.?\s*$', re.IGNORECASE),
        [3],
        {},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Thu|Thur|Thurs|Thursday)\.?\s*(?:Only)?\.?\s*$', re.IGNORECASE),
        [4],
        {},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Fri|Friday)\.?\s*$', re.IGNORECASE),
        [5],
        {},
    ),
    (
        re.compile(r',?\s*(?:on\s+|each\s+)?(?:Sat|Saturday)\.?\s*$', re.IGNORECASE),
        [6],
        {},
    ),
    (
        re.compile(r',?\s*(?:except|excluding)\s+public\s+holidays\.?\s*$', re.IGNORECASE),
        _ALL_DAYS,
        {'exceptPublicHolidays': True},
    ),
    (
        re.compile(r',?\s*inclusive\.?\s*$', re.IGNORECASE),
        _ALL_DAYS,
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


def _normalize_source(text: str) -> str:
    text = re.sub(r'\s*\([^)]*\)\s*', ' ', text)
    text = re.sub(r'\banytime\.', 'Anytime,', text, flags=re.IGNORECASE)
    text = re.sub(r'\banyitme\b', 'Anytime', text, flags=re.IGNORECASE)
    text = re.sub(r'\ball\s+times\b', 'Anytime', text, flags=re.IGNORECASE)
    text = re.sub(r'&', 'and', text)
    text = re.sub(r'\b(pubic|ppublic)\s+holidays\b', 'public holidays', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(pubic|ppublic)\b', 'public', text, flags=re.IGNORECASE)
    text = re.sub(r'\bholidayse\b', 'holidays', text, flags=re.IGNORECASE)
    text = re.sub(r'\bexcluding\s+public\s+holidays\b', 'except public holidays', text, flags=re.IGNORECASE)
    text = re.sub(r'\bMon\.?\s*to\s*Fri[iy]\b', 'Mon. to Fri.', text, flags=re.IGNORECASE)
    text = re.sub(r'\bFri[iy]\b', 'Fri.', text, flags=re.IGNORECASE)
    text = re.sub(r'\bMon\.to\b', 'Mon. to', text, flags=re.IGNORECASE)
    text = re.sub(r'\bMon\s*-\s*to\s*Fri\b', 'Mon. to Fri.', text, flags=re.IGNORECASE)
    text = re.sub(r'\bo\s+Nov\b', 'to Nov', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(inclsuive|inclusing|inlusive)\b', 'inclusive', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(nest|next)\s+following\s+day\b', 'next following day', text, flags=re.IGNORECASE)
    text = re.sub(r'\bfollowng\s+year\b', 'following year', text, flags=re.IGNORECASE)
    text = re.sub(r'\b6:00\s*pm\.', '6:00 p.m.', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<!:)\b(\d{1,2})\s*([ap]\.m\.)', r'\1:00 \2', text, flags=re.IGNORECASE)
    text = re.sub(r'\b10\.00\s*([ap]\.m\.)', r'10:00 \1', text, flags=re.IGNORECASE)
    text = re.sub(r'\.\.+', '.', text)
    text = re.sub(r',?\s*(?:school\s+)?buses?\s+excepted\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r',?\s*(?:removed|repealed)\b.*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r',?\s*except\s+by\s+permit.*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bof\s+the\s+same\s+year,?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.)),\s*(of\s+(?:the\s+)?(?:next\s+)?following\s+day)', r'\1 \2', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def _parse_month_abbr(token: str) -> int | None:
    key = token.strip().rstrip('.').lower()
    return _MONTH_ABBR.get(key)


def _month_day_key(month: int, day: int) -> int:
    return month * 32 + day


def _in_month_range(
    month: int,
    day: int,
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
) -> bool:
    pos = _month_day_key(month, day)
    start = _month_day_key(start_month, start_day)
    end = _month_day_key(end_month, end_day)
    if start <= end:
        return start <= pos <= end
    return pos >= start or pos <= end


def _last_day_for_slot(slot: dict[str, int]) -> int:
    year = slot.get('year')
    month = slot['month']
    if year is not None:
        return cal_mod.monthrange(int(year), month)[1]
    return 31


def _merge_calendar(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key in ('monthRanges', 'dayOfMonthRanges', 'months'):
        if key not in extra:
            continue
        if key not in out:
            out[key] = list(extra[key]) if key != 'months' else list(extra[key])
            continue
        if key == 'months':
            out['months'] = sorted(set(out['months']) | set(extra['months']))
        else:
            out[key] = list(out[key]) + list(extra[key])
    return out


def _append_month_range(
    cal: dict[str, Any],
    sm: int,
    sd: int,
    em: int,
    ed: int,
) -> None:
    cal.setdefault('monthRanges', []).append({
        'startMonth': sm,
        'startDay': sd,
        'endMonth': em,
        'endDay': ed,
    })


def _append_dom_range(cal: dict[str, Any], start: int, end: int | str) -> None:
    cal.setdefault('dayOfMonthRanges', []).append({'start': start, 'end': end})


def _parse_month_list(text: str) -> list[int] | None:
    parts = re.split(r'\s*,\s*|\s+and\s+', text.strip(), flags=re.IGNORECASE)
    months: list[int] = []
    for part in parts:
        part = part.strip().strip(',')
        if not part:
            continue
        mo = _parse_month_abbr(part)
        if mo is None:
            return None
        months.append(mo)
    return sorted(set(months)) if months else None


_SEASONAL_LEADING_FROM_RE = re.compile(
    r'^,?\s*(?:from\s+)?([A-Za-z]+)\.?\s+(\d{1,2})\s+to\s+'
    r'([A-Za-z]+)\.?\s+(\d{1,2}),?\s*(?:inclusive)?\.?\s*$',
    re.IGNORECASE,
)


def _extract_calendar_leading(text: str) -> tuple[str, dict[str, Any] | None]:
    """Strip day-of-month phrases at the start (before time or Anytime)."""
    remainder = text.strip()
    cal: dict[str, Any] = {}
    m = re.match(
        r'^(?:from the )?(\d{1,2})(?:st|nd|rd|th)? day (?:of each month )?to the '
        r'last day of each month',
        remainder,
        re.IGNORECASE,
    )
    if m:
        _append_dom_range(cal, int(m.group(1)), 'last')
        remainder = remainder[m.end() :].strip().lstrip(',').strip()
    m = re.match(
        r'^(?:from the )?(\d{1,2})(?:st|nd|rd|th)? day (?:of each month )?to the '
        r'(\d{1,2})(?:st|nd|rd|th)? day of each month',
        remainder,
        re.IGNORECASE,
    )
    if m:
        _append_dom_range(cal, int(m.group(1)), int(m.group(2)))
        remainder = remainder[m.end() :].strip().lstrip(',').strip()
    m = re.match(
        r'^(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+to\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+of\s+each\s+month',
        remainder,
        re.IGNORECASE,
    )
    if m:
        _append_dom_range(cal, int(m.group(1)), int(m.group(2)))
        remainder = remainder[m.end() :].strip().lstrip(',').strip()
    m = re.match(
        r'^(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+to\s+(?:the\s+)?(?:last\s+day|end)\s+of\s+each\s+month',
        remainder,
        re.IGNORECASE,
    )
    if m:
        _append_dom_range(cal, int(m.group(1)), 'last')
        remainder = remainder[m.end() :].strip().lstrip(',').strip()
    m = _DOM_LEADING_ORD_LAST_RE.match(remainder)
    if m:
        _append_dom_range(cal, int(m.group(1)), 'last')
        return remainder[m.end() :].strip().lstrip(',').strip(), cal
    m = _DOM_LEADING_FIRST_RE.match(remainder)
    if m:
        _append_dom_range(cal, 1, int(m.group(1)))
        return remainder[m.end() :].strip().lstrip(',').strip(), cal
    m = _DOM_LEADING_ORD_ORD_RE.match(remainder)
    if m:
        _append_dom_range(cal, int(m.group(1)), int(m.group(2)))
        remainder = remainder[m.end() :].strip().lstrip(',').strip()
    m = _SEASONAL_LEADING_FROM_RE.match(remainder)
    if m:
        sm = _parse_month_abbr(m.group(1))
        em = _parse_month_abbr(m.group(3))
        if sm and em:
            _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(4)))
            remainder = remainder[m.end() :].strip().lstrip(',').strip()
    m = re.match(
        r'^(?:from\s+)?([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+to\s+([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(?:inclusive)?',
        remainder,
        re.IGNORECASE,
    )
    if m:
        sm = _parse_month_abbr(m.group(1))
        em = _parse_month_abbr(m.group(3))
        if sm and em:
            _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(4)))
            remainder = remainder[m.end() :].strip().lstrip(',').strip()
    return remainder, cal if cal else None


def _extract_calendar_tail(text: str) -> tuple[str, dict[str, Any] | None, dict[str, bool]]:
    """Strip recognized calendar phrases from the end of a clause; merge into one calendar dict."""
    remainder, lead_cal = _extract_calendar_leading(text.strip())
    cal: dict[str, Any] = dict(lead_cal) if lead_cal else {}
    flags: dict[str, bool] = {}
    changed = True
    while changed and remainder:
        changed = False

        # Trailing except public holidays
        m = re.search(r',?\s*(?:except|excluding)\s+public\s+holidays\.?\s*$', remainder, re.IGNORECASE)
        if m:
            flags['exceptPublicHolidays'] = True
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        # Trailing except months (e.g. except July, August and public holidays)
        m = re.search(rf',?\s*except\s+((?:{_MONTH_NAME_RE}(?:\s*,\s*|\s+))+(?:and\s+)?{_MONTH_NAME_RE})(?:\s+and\s+public\s+holidays)?\.?\s*$', remainder, re.IGNORECASE)
        if m:
            except_months = _parse_month_list(m.group(1))
            if except_months:
                if 'and public holidays' in m.group(0).lower():
                    flags['exceptPublicHolidays'] = True
                active_months = [m_num for m_num in _ALL_MONTHS if m_num not in except_months]
                cal['months'] = sorted(set(active_months))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Date range with year (e.g. from June 6, 2022 to June 12, 2022)
        m = _DATE_RANGE_YEAR_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(4))
            if sm and em:
                _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(5)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Single date with year (e.g. on August 4, 2007)
        m = _SINGLE_DATE_YEAR_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            if sm:
                _append_month_range(cal, sm, int(m.group(2)), sm, int(m.group(2)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Seasonal year span
        m = _SEASONAL_YEAR_SPAN_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(3))
            if sm and em:
                _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(4)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Seasonal no inclusive
        m = _SEASONAL_NO_INCLUSIVE_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(3))
            if sm and em:
                _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(4)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Seasonal plain (with optional from and ordinals)
        m = _SEASONAL_PLAIN_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(3))
            if sm and em:
                _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(4)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Seasonal from
        m = _SEASONAL_FROM_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(3))
            if sm and em:
                _append_month_range(cal, sm, int(m.group(2)), em, int(m.group(4)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Month to month (e.g. from July 1 to Aug. 31)
        m = _SEASONAL_MONTH_TO_MONTH_RE.search(remainder)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(2))
            if sm and em:
                _append_month_range(cal, sm, 1, em, int(m.group(3)))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Month names only (e.g. Sept. to June, July and Aug.)
        m = re.search(rf',?\s*(?:during the months of\s+)?({_MONTH_NAME_RE})\s+to\s+({_MONTH_NAME_RE})\.?\s*$', remainder, re.IGNORECASE)
        if m:
            sm = _parse_month_abbr(m.group(1))
            em = _parse_month_abbr(m.group(2))
            if sm and em:
                _append_month_range(cal, sm, 1, em, cal_mod.monthrange(2025, em)[1])
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Day of month ranges
        m = _DOM_FROM_THE_LAST_RE.search(remainder)
        if m:
            _append_dom_range(cal, int(m.group(1)), 'last')
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        m = _DOM_FROM_THE_SHORT_LAST_RE.search(remainder)
        if m:
            _append_dom_range(cal, int(m.group(1)), 'last')
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        m = _DOM_FROM_THE_RE.search(remainder)
        if m:
            _append_dom_range(cal, int(m.group(1)), int(m.group(2)))
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        m = _DOM_FROM_THE_SHORT_ORD_RE.search(remainder)
        if m:
            _append_dom_range(cal, int(m.group(1)), int(m.group(2)))
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        m = _DOM_FIRST_LAST_WORD_RE.search(remainder)
        if m:
            _append_dom_range(cal, 1, int(m.group(1)))
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        m = _DOM_ORD_TO_ORD_RE.search(remainder)
        if m:
            _append_dom_range(cal, int(m.group(1)), int(m.group(2)))
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        m = _DOM_ORD_TO_LAST_RE.search(remainder)
        if m and not remainder[: m.start()].rstrip().lower().endswith('from the'):
            _append_dom_range(cal, int(m.group(1)), 'last')
            remainder = remainder[: m.start()].strip().rstrip(',').strip()
            changed = True
            continue

        # During months of...
        m = re.search(rf',?\s*(?:during the months of|during the months|during)\s+((?:{_MONTH_NAME_RE}(?:\s*,\s*|\s+))+(?:and\s+)?{_MONTH_NAME_RE})\.?\s*$', remainder, re.IGNORECASE)
        if m:
            months = _parse_month_list(m.group(1))
            if months:
                cal.setdefault('months', []).extend(months)
                cal['months'] = sorted(set(cal['months']))
                remainder = remainder[: m.start()].strip().rstrip(',').strip()
                changed = True
                continue

        # Standalone list of months (e.g. May, Jul., Sep. and Nov.)
        mlist = _parse_month_list(remainder)
        if mlist:
            cal.setdefault('months', []).extend(mlist)
            cal['months'] = sorted(set(cal['months']))
            remainder = ''
            changed = True
            continue

        # Check if leading part can be extracted now that tail is stripped
        rem_lead, lead_cal2 = _extract_calendar_leading(remainder)
        if lead_cal2:
            cal.update(lead_cal2)
            remainder = rem_lead
            changed = True
            continue

        # Cleanup trailing 'from', ',', 'inclusive'
        clean_rem = re.sub(r',?\s*(?:from|inclusive)\.?\s*$', '', remainder, flags=re.IGNORECASE).strip()
        if clean_rem != remainder:
            remainder = clean_rem
            changed = True
            continue

    return remainder, cal if cal else None, flags


def _slot_in_calendar(slot: dict[str, int], calendar: dict[str, Any] | None) -> bool:
    if not calendar:
        return True
    month = slot['month']
    day = slot['dayOfMonth']
    months_only = calendar.get('months')
    if months_only is not None and month not in months_only:
        return False
    for dom in calendar.get('dayOfMonthRanges', []):
        start = int(dom['start'])
        end = dom['end']
        if end == 'last':
            last = _last_day_for_slot(slot)
            if not (start <= day <= last):
                return False
        elif not (start <= day <= int(end)):
            return False
    for mr in calendar.get('monthRanges', []):
        if not _in_month_range(
            month,
            day,
            int(mr['startMonth']),
            int(mr['startDay']),
            int(mr['endMonth']),
            int(mr['endDay']),
        ):
            return False
    return True


def time_to_minutes(hour: int, minute: int, meridiem: str) -> int:
    """Convert 12-hour clock to minutes since midnight (0–1439)."""
    mer = meridiem.lower().replace('.', '')
    if mer == 'noon':
        return 12 * 60 + int(minute)
    if mer == 'midnight':
        return int(minute) if int(hour) == 12 else int(hour) * 60 + int(minute)
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


_WEEKDAY_SINGLE_TAIL_RE = re.compile(
    r',\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s*$',
    re.IGNORECASE,
)


def _extract_day_tail(text: str) -> tuple[str, list[int], dict[str, bool]]:
    """Strip trailing weekday phrase; return (remainder, days, flags)."""
    m = _WEEKDAY_SINGLE_TAIL_RE.search(text)
    if m:
        day_name = m.group(1).lower()
        day_num = _WEEKDAY_ABBR.get(day_name[:3]) or _WEEKDAY_ABBR.get(day_name)
        if day_num is not None:
            return text[: m.start()].strip(), [day_num], {}

    for pat, days, flag_updates in _DAY_TAIL_RULES:
        m = pat.search(text)
        if m:
            return text[: m.start()].strip(), days, dict(flag_updates)

    # Check each weekday tail (e.g. , each Thurs.)
    m_each = re.search(rf',?\s*(?:on\s+)?each\s+({_DAY_NAME_RE})\.?\s*$', text, re.IGNORECASE)
    if m_each:
        dname = m_each.group(1).lower().rstrip('.')
        dnum = _WEEKDAY_ABBR.get(dname)
        if dnum is not None:
            return text[: m_each.start()].strip().rstrip(','), [dnum], {}

    # Check ordinal weekday tail (e.g. on the 1st Thu. and 3rd Thu. of each month)
    m_ord = re.search(rf',?\s*(?:on\s+)?(?:the\s+)?(?:\d{{1,2}})(?:st|nd|rd|th)?\s+(?:{_DAY_NAME_RE}\s+)?and\s+(?:\d{{1,2}})(?:st|nd|rd|th)?\s+({_DAY_NAME_RE})\s+of\s+each\s+month\.?\s*$', text, re.IGNORECASE)
    if m_ord:
        dname = m_ord.group(1).lower().rstrip('.')
        dnum = _WEEKDAY_ABBR.get(dname)
        if dnum is not None:
            return text[: m_ord.start()].strip().rstrip(','), [dnum], {}

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


def _attach_calendar(windows: list[dict], calendar: dict[str, Any] | None) -> None:
    if not calendar:
        return
    for w in windows:
        w['calendar'] = dict(calendar)


def _parse_each_weekday_clause(clause: str) -> tuple[list[dict], dict[str, Any] | None, dict[str, bool], str | None]:
    m = _EACH_WEEKDAY_RE.match(clause.strip())
    if m:
        day_name = m.group(1).lower().rstrip('.')
        day_num = _WEEKDAY_ABBR.get(day_name)
        if day_num is not None:
            rest = m.group(2).strip()
            remainder, calendar, flags = _extract_calendar_tail(rest)
            time_body = remainder.strip()
            if not time_body:
                return [{
                    'days': [day_num],
                    'startMinute': 0,
                    'endMinute': 1439,
                    'crossesMidnight': False,
                    **({'calendar': calendar} if calendar else {}),
                }], calendar, flags, None
            tw, err = _parse_time_ranges(time_body)
            if not err:
                for w in tw:
                    w['days'] = [day_num]
                    if calendar:
                        w['calendar'] = dict(calendar)
                return tw, calendar, flags, None

    # Check ordinal weekday (e.g. 1st and 3rd Wed. of each month, Apr. 1 to Nov. 30)
    m_ord = _ORDINAL_WEEKDAY_RE.match(clause.strip())
    if m_ord:
        day_name = m_ord.group(2).lower().rstrip('.')
        day_num = _WEEKDAY_ABBR.get(day_name)
        if day_num is not None:
            rest = m_ord.group(5).strip()
            remainder, calendar, flags = _extract_calendar_tail(rest)
            if not remainder.strip():
                return [{
                    'days': [day_num],
                    'startMinute': 0,
                    'endMinute': 1439,
                    'crossesMidnight': False,
                    **({'calendar': calendar} if calendar else {}),
                }], calendar, flags, None

    return [], None, {}, 'not each-weekday'


def _parse_clause(clause: str) -> tuple[list[dict], dict[str, Any] | None, dict[str, bool], str | None]:
    clause = clause.strip().rstrip(',')
    flags: dict[str, bool] = {}
    if not clause:
        return [], None, flags, 'empty clause'

    each_windows, each_cal, each_flags, each_err = _parse_each_weekday_clause(clause)
    if each_err != 'not each-weekday':
        if each_err:
            return [], each_cal, each_flags, each_err
        _attach_calendar(each_windows, each_cal)
        return each_windows, each_cal, each_flags, None

    if re.match(r'^anytime\b', clause, re.IGNORECASE):
        rest = re.sub(r'^anytime[,\s]*', '', clause, flags=re.IGNORECASE).strip()
        if not rest:
            return [{
                'days': list(_ALL_DAYS),
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
            }], None, flags, None
        remainder, calendar, cal_flags = _extract_calendar_tail(rest)
        flags.update(cal_flags)
        if not remainder.strip():
            windows = [{
                'days': list(_ALL_DAYS),
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
            }]
            _attach_calendar(windows, calendar)
            return windows, calendar, flags, None
        body, days, seg_flags = _extract_day_tail(remainder)
        flags.update(seg_flags)
        if not body.strip():
            windows = [{
                'days': days,
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
            }]
            _attach_calendar(windows, calendar)
            return windows, calendar, flags, None

    remainder, calendar, cal_flags = _extract_calendar_tail(clause)
    flags.update(cal_flags)
    if not remainder.strip():
        if calendar:
            windows = [{
                'days': list(_ALL_DAYS),
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
            }]
            _attach_calendar(windows, calendar)
            return windows, calendar, flags, None
        return [], None, flags, 'empty clause after calendar'

    # Check standalone day phrases like "Sat. and Sun." or "Mon. to Fri."
    body, days, tail_flags = _extract_day_tail(remainder)
    flags.update(tail_flags)
    if not body.strip():
        windows = [{
            'days': days,
            'startMinute': 0,
            'endMinute': 1439,
            'crossesMidnight': False,
        }]
        _attach_calendar(windows, calendar)
        return windows, calendar, flags, None

    windows, seg_flags, err = _parse_segment(remainder)
    if err:
        if body != remainder.strip():
            tw, body_err = _parse_time_ranges(body)
            if not body_err and tw:
                for w in tw:
                    w['days'] = days
                windows = tw
                err = None
            elif not body.strip():
                windows = [{
                    'days': days,
                    'startMinute': 0,
                    'endMinute': 1439,
                    'crossesMidnight': False,
                }]
                err = None
            else:
                return [], calendar, flags, err
        else:
            return [], calendar, flags, err
    else:
        flags.update(seg_flags)
    _attach_calendar(windows, calendar)
    return windows, calendar, flags, None


def _parse_inverted(text: str, source: str) -> dict[str, Any]:
    m = _INVERTED_MARKER_RE.search(text)
    if not m:
        return _empty_schedule(source, status='failed')
    except_body = text[m.end() :].strip().rstrip(',')
    windows, calendar, flags, err = _parse_clause(except_body)
    if err or not windows:
        return _empty_schedule(source, status='failed', unparsedClauses=[except_body] if err else [])
    out: dict[str, Any] = {
        'v': SCHEDULE_VERSION,
        'status': 'ok',
        'source': source,
        'inverted': True,
        'windows': windows,
    }
    if calendar:
        out['calendar'] = calendar
    if flags:
        out['flags'] = flags
    return out


def _parse_month_list_schedule(text: str, source: str) -> dict[str, Any] | None:
    months = _parse_month_list(text)
    if months is None:
        return None
    return {
        'v': SCHEDULE_VERSION,
        'status': 'anytime',
        'source': source,
        'windows': [],
        'calendar': {'months': months},
    }


def _split_clauses(text: str) -> list[str]:
    # Split on missing punctuation between independent clauses like '...except public holidays 12:00 noon to...'
    text = re.sub(
        r'(\b(?:except|excluding)\s+public\s+holidays|\bpublic\s+holidays)\s+(\d{1,2}:\d{2})',
        r'\1; \2',
        text,
        flags=re.IGNORECASE,
    )
    semi_parts = [p.strip() for p in text.split(';') if p.strip()]
    clauses: list[str] = []
    for sp in semi_parts:
        sp = re.sub(r'^(?:and\s+)', '', sp, flags=re.IGNORECASE).strip()
        subs = re.split(
            r'(?:,\s*and\s+(?:from|anytime|all times)|;\s*and\s+(?:from|anytime|all times)|\s+and\s+(?:anytime|all times))\b',
            sp,
            flags=re.IGNORECASE,
        )
        for sub in subs:
            sub = sub.strip().rstrip(',')
            if sub:
                clauses.append(sub)
    return clauses


def parse_schedule(source: Any) -> dict:
    """Parse ``Prohibited Times and/or Days`` into a schedule dict."""
    if _is_blank(source):
        return _empty_schedule('', status='failed')

    raw_source = str(source).strip()
    text = _normalize_source(raw_source)
    normalized = text.casefold()

    if normalized in ('anytime', 'all times'):
        return {
            'v': SCHEDULE_VERSION,
            'status': 'anytime',
            'source': raw_source,
            'windows': [],
        }

    if 'snow storm condition' in normalized or 'snow emergency' in normalized:
        return {
            'v': SCHEDULE_VERSION,
            'status': 'conditional',
            'condition': 'major_snowstorm_declared',
            'is_snow_route': True,
            'source': raw_source,
            'windows': [],
        }

    month_list_sched = _parse_month_list_schedule(text, raw_source)
    if month_list_sched is not None:
        return month_list_sched

    if _INVERTED_MARKER_RE.search(text):
        return _parse_inverted(text, raw_source)

    clauses = _split_clauses(text)
    if not clauses:
        return _empty_schedule(raw_source, status='failed')

    combined_windows: list[dict] = []
    combined_flags: dict[str, bool] = {}
    schedule_calendar: dict[str, Any] | None = None
    unparsed: list[str] = []

    for clause in clauses:
        windows, cal, flags, err = _parse_clause(clause)
        _merge_flags({'flags': combined_flags}, flags)
        if err:
            unparsed.append(clause)
        else:
            combined_windows.extend(windows)
            if cal:
                schedule_calendar = (
                    _merge_calendar(schedule_calendar, cal)
                    if schedule_calendar
                    else dict(cal)
                )

    if not combined_windows:
        # Anytime + calendar-only clause(s)
        if schedule_calendar and not unparsed:
            return {
                'v': SCHEDULE_VERSION,
                'status': 'anytime',
                'source': raw_source,
                'windows': [],
                'calendar': schedule_calendar,
            }
        return _empty_schedule(
            raw_source,
            status='failed',
            unparsedClauses=unparsed,
        )

    out: dict[str, Any] = {
        'v': SCHEDULE_VERSION,
        'status': 'ok',
        'source': raw_source,
        'windows': combined_windows,
    }
    if schedule_calendar and all('calendar' not in w for w in combined_windows):
        out['calendar'] = schedule_calendar
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
    return _slot_in_calendar(slot, schedule.get('calendar'))


def _minute_in_window(minute: int, start: int, end: int, crosses_midnight: bool) -> bool:
    if crosses_midnight:
        return minute >= start or minute < end
    if end > start:
        return start <= minute < end
    return minute >= start or minute < end


def _excepts_public_holidays(schedule: dict, window: dict) -> bool:
    if window.get('flags', {}).get('exceptPublicHolidays'):
        return True
    return bool(schedule.get('flags', {}).get('exceptPublicHolidays'))


def _window_time_day_match(
    window: dict,
    slot: dict[str, int],
    schedule_calendar: dict[str, Any] | None = None,
) -> bool:
    cal = window.get('calendar') or schedule_calendar
    if not _slot_in_calendar(slot, cal):
        return False
    days = window.get('days', _ALL_DAYS)
    if slot['dayOfWeek'] not in days:
        return False
    return _minute_in_window(
        slot['minuteOfDay'],
        window['startMinute'],
        window['endMinute'],
        bool(window.get('crossesMidnight')),
    )


def _window_overlaps_slot(
    window: dict,
    slot: dict[str, int],
    schedule: dict,
    schedule_calendar: dict[str, Any] | None = None,
) -> bool:
    """Normal schedule: window match minus public-holiday exclusion when flagged."""
    if not _window_time_day_match(window, slot, schedule_calendar):
        return False
    if _excepts_public_holidays(schedule, window) and is_public_holiday(slot):
        return False
    return True


def _except_window_matches(
    window: dict,
    slot: dict[str, int],
    schedule: dict,
    schedule_calendar: dict[str, Any] | None = None,
) -> bool:
    """Inverted schedule: except-window match includes flagged public holidays."""
    if _excepts_public_holidays(schedule, window) and is_public_holiday(slot):
        return True
    return _window_time_day_match(window, slot, schedule_calendar)


def overlaps_membership(schedule: dict | None, slot: dict[str, int]) -> bool:
    """Return whether *schedule* overlaps the membership slot."""
    if not schedule:
        return False
    status = schedule.get('status')
    if status == 'failed':
        return False
    schedule_cal = schedule.get('calendar')
    if not _slot_in_calendar(slot, schedule_cal):
        return False
    windows = schedule.get('windows', [])
    if schedule.get('inverted'):
        if not windows:
            return status == 'anytime'
        return not any(
            _except_window_matches(w, slot, schedule, schedule_cal) for w in windows
        )
    if status == 'conditional':
        if schedule.get('condition') == 'major_snowstorm_declared':
            return bool(slot.get('majorSnowStorm'))
        return False
    if status == 'anytime':
        return True
    for window in windows:
        if _window_overlaps_slot(window, slot, schedule, schedule_cal):
            return True
    return False


SCHEDULE_EXPORT_COLUMNS = ['schedule_json', 'schedule_status', 'max_minutes']

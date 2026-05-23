"""Tests for schedule_format / parse_schedule."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from schedule_format import (  # noqa: E402
    overlaps_membership,
    parse_max_minutes,
    parse_schedule,
    schedule_from_json,
    schedule_to_json,
    time_to_minutes,
)


@pytest.mark.parametrize(
    ('hour', 'minute', 'mer', 'expected'),
    [
        (8, 0, 'a.m.', 480),
        (6, 0, 'p.m.', 1080),
        (12, 0, 'a.m.', 0),
        (12, 0, 'p.m.', 720),
        (12, 1, 'a.m.', 1),
    ],
)
def test_time_to_minutes(hour, minute, mer, expected) -> None:
    assert time_to_minutes(hour, minute, mer) == expected


@pytest.mark.parametrize(
    ('text', 'minutes'),
    [
        ('1 hour', 60),
        ('2 hours', 120),
        ('30 mins.', 30),
        ('15 mins.', 15),
        (None, None),
    ],
)
def test_parse_max_minutes(text, minutes) -> None:
    assert parse_max_minutes(text) == minutes


def test_anytime() -> None:
    s = parse_schedule('Anytime')
    assert s['status'] == 'anytime'
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 900, 'month': 4, 'dayOfMonth': 15})


def test_weekday_range() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m., Mon. to Fri.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 1
    w = s['windows'][0]
    assert w['days'] == [1, 2, 3, 4, 5]
    assert w['startMinute'] == 480
    assert w['endMinute'] == 1080
    assert overlaps_membership(
        s, {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 10},
    )
    assert not overlaps_membership(
        s, {'dayOfWeek': 0, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 10},
    )


def test_all_days_no_suffix() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m.')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == list(range(7))


def test_multiple_ranges_same_days() -> None:
    s = parse_schedule('7:00 a.m. to 9:00 a.m. and 4:00 p.m. to 6:00 p.m., Mon. to Fri.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2
    assert overlaps_membership(
        s, {'dayOfWeek': 3, 'minuteOfDay': 480, 'month': 1, 'dayOfMonth': 1},
    )
    assert not overlaps_membership(
        s, {'dayOfWeek': 3, 'minuteOfDay': 720, 'month': 1, 'dayOfMonth': 1},
    )


def test_semicolon_clauses() -> None:
    text = (
        '7:00 p.m. of one day to 7:00 a.m. of the next following day, Mon to Fri.; '
        'Anytime, Sat. and Sun.'
    )
    s = parse_schedule(text)
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2
    assert s['windows'][0]['crossesMidnight'] is True
    assert overlaps_membership(
        s, {'dayOfWeek': 6, 'minuteOfDay': 1200, 'month': 5, 'dayOfMonth': 1},
    )


def test_overnight() -> None:
    s = parse_schedule('6:00 a.m. of one day to 2:00 a.m. of the next day')
    assert s['status'] == 'ok'
    assert s['windows'][0]['crossesMidnight'] is True


def test_except_holidays_flag() -> None:
    s = parse_schedule('4:00 p.m. to 6:00 p.m., Mon. to Fri., except public holidays')
    assert s['status'] == 'ok'
    assert s['flags']['exceptPublicHolidays'] is True


def test_calendar_deferred_failed() -> None:
    s = parse_schedule('Anytime, from Dec. 1 of one year to Mar. 31 of the next following year, inclusive')
    assert s['status'] == 'failed'


def test_inverted_deferred_failed() -> None:
    s = parse_schedule('Anytime, except 7:00 a.m. to 9:00 a.m., Mon. to Fri.')
    assert s['status'] == 'failed'


def test_json_roundtrip() -> None:
    s = parse_schedule('2:00 a.m. to 6:00 a.m.')
    raw = schedule_to_json(s)
    back = schedule_from_json(raw)
    assert back == s
    assert json.loads(raw)['v'] == 1


def test_day_tail_without_comma() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m. Mon. to Fri.')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [1, 2, 3, 4, 5]


def test_split_day_groups() -> None:
    s = parse_schedule('4:00 p.m. to 6:00 p.m., Mon. to Fri. and 8:00 a.m. to 6:00 p.m., Sat. and Sun.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2
    assert overlaps_membership(
        s, {'dayOfWeek': 0, 'minuteOfDay': 540, 'month': 3, 'dayOfMonth': 1},
    )

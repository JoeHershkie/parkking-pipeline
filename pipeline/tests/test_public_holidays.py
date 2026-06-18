"""Tests for Ontario public holiday helper."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from public_holidays import is_public_holiday  # noqa: E402


def test_christmas_2025() -> None:
    slot = {'year': 2025, 'month': 12, 'dayOfMonth': 25, 'dayOfWeek': 4, 'minuteOfDay': 0}
    assert is_public_holiday(slot)


def test_christmas_observed_2022() -> None:
    # Christmas 2022 falls on Sunday; library marks observed day Dec 26
    assert is_public_holiday({
        'year': 2022, 'month': 12, 'dayOfMonth': 26, 'dayOfWeek': 0, 'minuteOfDay': 0,
    })
    assert not is_public_holiday({
        'year': 2022, 'month': 12, 'dayOfMonth': 27, 'dayOfWeek': 1, 'minuteOfDay': 0,
    })


def test_requires_year() -> None:
    assert not is_public_holiday({'month': 12, 'dayOfMonth': 25, 'dayOfWeek': 4, 'minuteOfDay': 0})

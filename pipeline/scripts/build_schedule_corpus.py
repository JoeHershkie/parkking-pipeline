#!/usr/bin/env python3
"""Build shared schedule parity corpus for Python and TypeScript tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from parking_pipeline.schedule_format import overlaps_membership

FIXTURES_DIR = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures'
OUTPUT = FIXTURES_DIR / 'schedule_corpus.json'

MON_FRI_8_6 = {
    'v': 1,
    'status': 'ok',
    'source': 'Mon–Fri 8am–6pm',
    'windows': [
        {
            'days': [1, 2, 3, 4, 5],
            'startMinute': 480,
            'endMinute': 1080,
            'crossesMidnight': False,
        },
    ],
}

TUE_3PM = {
    'dayOfWeek': 2,
    'minuteOfDay': 900,
    'month': 5,
    'dayOfMonth': 20,
    'year': 2025,
}

SAT_3PM = {
    'dayOfWeek': 6,
    'minuteOfDay': 900,
    'month': 5,
    'dayOfMonth': 24,
    'year': 2025,
}

SCHEDULES: dict[str, dict] = {
    'mon_fri_8_6': MON_FRI_8_6,
    'anytime_no_calendar': {
        'v': 1,
        'status': 'anytime',
        'source': 'anytime',
        'windows': [],
    },
    'anytime_winter_calendar': {
        'v': 1,
        'status': 'anytime',
        'source': 'winter',
        'windows': [],
        'calendar': {
            'monthRanges': [
                {'startMonth': 12, 'startDay': 1, 'endMonth': 3, 'endDay': 31},
            ],
        },
    },
    'failed_seasonal': {
        'v': 1,
        'status': 'failed',
        'source': 'Apr–Nov',
        'windows': [],
    },
    'overnight_crosses_midnight': {
        'v': 1,
        'status': 'ok',
        'source': 'overnight',
        'windows': [
            {
                'days': [1],
                'startMinute': 1320,
                'endMinute': 360,
                'crossesMidnight': True,
            },
        ],
    },
    'weekday_except_holidays': {
        'v': 1,
        'status': 'ok',
        'source': '4–6pm weekdays except holidays',
        'windows': [
            {
                'days': [1, 2, 3, 4, 5],
                'startMinute': 960,
                'endMinute': 1080,
                'crossesMidnight': False,
            },
        ],
        'flags': {'exceptPublicHolidays': True},
    },
    'inverted_sunday_holidays': {
        'v': 1,
        'status': 'ok',
        'source': 'anytime except Sun',
        'inverted': True,
        'windows': [
            {
                'days': [0],
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
            },
        ],
        'flags': {'exceptPublicHolidays': True},
    },
    'winter_window_calendar': {
        'v': 1,
        'status': 'ok',
        'source': 'Dec–Mar',
        'windows': [
            {
                'days': [0, 1, 2, 3, 4, 5, 6],
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
                'calendar': {
                    'monthRanges': [
                        {'startMonth': 12, 'startDay': 1, 'endMonth': 3, 'endDay': 31},
                    ],
                },
            },
        ],
    },
    'partial_mon_fri': {
        'v': 1,
        'status': 'partial',
        'source': 'partial',
        'windows': MON_FRI_8_6['windows'],
        'unparsedClauses': ['some other clause'],
    },
    'day_of_month_last': {
        'v': 1,
        'status': 'ok',
        'source': 'last days of month',
        'windows': [
            {
                'days': [0, 1, 2, 3, 4, 5, 6],
                'startMinute': 0,
                'endMinute': 1439,
                'crossesMidnight': False,
                'calendar': {
                    'dayOfMonthRanges': [{'start': 28, 'end': 'last'}],
                },
            },
        ],
    },
    'inverted_anytime_no_windows': {
        'v': 1,
        'status': 'anytime',
        'source': 'inverted anytime',
        'inverted': True,
        'windows': [],
    },
}

SLOTS: dict[str, dict[str, int]] = {
    'tue_3pm': TUE_3PM,
    'sat_3pm': SAT_3PM,
    'winter_jan': {
        'dayOfWeek': 2,
        'minuteOfDay': 600,
        'month': 1,
        'dayOfMonth': 15,
        'year': 2025,
    },
    'summer_jul': {
        'dayOfWeek': 2,
        'minuteOfDay': 600,
        'month': 7,
        'dayOfMonth': 15,
        'year': 2025,
    },
    'mon_11pm': {
        'dayOfWeek': 1,
        'minuteOfDay': 1380,
        'month': 1,
        'dayOfMonth': 1,
        'year': 2025,
    },
    'mon_5am': {
        'dayOfWeek': 1,
        'minuteOfDay': 300,
        'month': 1,
        'dayOfMonth': 1,
        'year': 2025,
    },
    'mon_noon': {
        'dayOfWeek': 1,
        'minuteOfDay': 720,
        'month': 1,
        'dayOfMonth': 1,
        'year': 2025,
    },
    'canada_day_2025': {
        'dayOfWeek': 2,
        'minuteOfDay': 1020,
        'month': 7,
        'dayOfMonth': 1,
        'year': 2025,
    },
    'july_2_2025': {
        'dayOfWeek': 3,
        'minuteOfDay': 1020,
        'month': 7,
        'dayOfMonth': 2,
        'year': 2025,
    },
    'sunday_jun_2025': {
        'dayOfWeek': 0,
        'minuteOfDay': 600,
        'month': 6,
        'dayOfMonth': 8,
        'year': 2025,
    },
    'monday_jun_2025': {
        'dayOfWeek': 1,
        'minuteOfDay': 600,
        'month': 6,
        'dayOfMonth': 9,
        'year': 2025,
    },
    'feb_10_winter': {
        'dayOfWeek': 3,
        'minuteOfDay': 720,
        'month': 2,
        'dayOfMonth': 10,
        'year': 2025,
    },
    'aug_10_summer': {
        'dayOfWeek': 3,
        'minuteOfDay': 720,
        'month': 8,
        'dayOfMonth': 10,
        'year': 2025,
    },
    'feb_29_leap': {
        'dayOfWeek': 0,
        'minuteOfDay': 0,
        'month': 2,
        'dayOfMonth': 29,
        'year': 2024,
    },
    'feb_28_non_leap': {
        'dayOfWeek': 0,
        'minuteOfDay': 0,
        'month': 2,
        'dayOfMonth': 28,
        'year': 2025,
    },
    'feb_27_non_leap': {
        'dayOfWeek': 0,
        'minuteOfDay': 0,
        'month': 2,
        'dayOfMonth': 27,
        'year': 2025,
    },
}

# Explicit (schedule_id, slot_id) pairs to include beyond the full cross product.
EXPLICIT_PAIRS: list[tuple[str, str]] = [
    ('mon_fri_8_6', 'tue_3pm'),
    ('mon_fri_8_6', 'sat_3pm'),
    ('anytime_no_calendar', 'sat_3pm'),
    ('anytime_winter_calendar', 'winter_jan'),
    ('anytime_winter_calendar', 'summer_jul'),
    ('failed_seasonal', 'tue_3pm'),
    ('overnight_crosses_midnight', 'mon_11pm'),
    ('overnight_crosses_midnight', 'mon_5am'),
    ('overnight_crosses_midnight', 'mon_noon'),
    ('weekday_except_holidays', 'canada_day_2025'),
    ('weekday_except_holidays', 'july_2_2025'),
    ('inverted_sunday_holidays', 'sunday_jun_2025'),
    ('inverted_sunday_holidays', 'monday_jun_2025'),
    ('inverted_sunday_holidays', 'canada_day_2025'),
    ('winter_window_calendar', 'feb_10_winter'),
    ('winter_window_calendar', 'aug_10_summer'),
    ('partial_mon_fri', 'tue_3pm'),
    ('partial_mon_fri', 'sat_3pm'),
    ('day_of_month_last', 'feb_29_leap'),
    ('day_of_month_last', 'feb_28_non_leap'),
    ('day_of_month_last', 'feb_27_non_leap'),
    ('inverted_anytime_no_windows', 'tue_3pm'),
    ('inverted_anytime_no_windows', 'sat_3pm'),
]


def build_cases() -> list[dict]:
    cases: list[dict] = []
    for schedule_id, slot_id in EXPLICIT_PAIRS:
        schedule = SCHEDULES[schedule_id]
        slot = SLOTS[slot_id]
        cases.append({
            'id': f'{schedule_id}__{slot_id}',
            'schedule': schedule,
            'slot': slot,
            'expected': overlaps_membership(schedule, slot),
        })
    return cases


def main() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    corpus = {
        'version': 1,
        'description': 'Shared schedule membership parity corpus (Python overlaps_membership)',
        'cases': build_cases(),
    }
    OUTPUT.write_text(json.dumps(corpus, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {len(corpus["cases"])} cases to {OUTPUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

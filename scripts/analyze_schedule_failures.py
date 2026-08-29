#!/usr/bin/env python3
"""Rank schedule parse failures from clean_parking_targets.csv."""

from __future__ import annotations

from collections import Counter

import pandas as pd

from parking_pipeline.paths import data_path  # noqa: E402
from parking_pipeline.schedule_format import parse_schedule  # noqa: E402


def _has_calendar(sched: dict) -> bool:
    if sched.get('calendar'):
        return True
    return any('calendar' in w for w in sched.get('windows', []))


def main() -> None:
    df = pd.read_csv(data_path('clean_parking_targets.csv'))
    col = 'Prohibited Times and/or Days'
    status_counts: Counter = Counter()
    failed_strings: Counter = Counter()
    partial_strings: Counter = Counter()
    with_calendar = 0
    inverted = 0

    for text in df[col].fillna('').astype(str):
        sched = parse_schedule(text)
        status_counts[sched['status']] += 1
        if _has_calendar(sched):
            with_calendar += 1
        if sched.get('inverted'):
            inverted += 1
        if sched['status'] == 'failed':
            failed_strings[text] += 1
        elif sched['status'] == 'partial':
            partial_strings[text] += 1

    total = len(df)
    print(f'Rows: {total}')
    print(f'  with calendar: {with_calendar}')
    print(f'  inverted: {inverted}')
    print('\nschedule_status:')
    for status, count in status_counts.most_common():
        pct = round(100.0 * count / total, 1) if total else 0
        print(f'  {status}: {count} ({pct}%)')

    print('\nTop failed strings (unique):')
    for text, count in failed_strings.most_common(25):
        print(f'  {count:5d}  {text[:120]}')

    if partial_strings:
        print('\nTop partial strings:')
        for text, count in partial_strings.most_common(10):
            print(f'  {count:5d}  {text[:120]}')


if __name__ == '__main__':
    main()

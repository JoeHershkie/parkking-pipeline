"""Stage 1b: parse Prohibited Times → parsed_schedules.csv."""

from __future__ import annotations

from collections import Counter

import pandas as pd

from failure_ledger import clear_stage, record_failure
from paths import data_path
from schedule_format import (
    SCHEDULE_EXPORT_COLUMNS,
    parse_max_minutes,
    parse_schedule,
    schedule_to_json,
)

SCHEDULE_EMPTY = 'SCHEDULE_EMPTY'


def parse_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Parse time strings for all clean rows."""
    failure_counts: Counter = Counter()
    rows: list[dict] = []

    for _, raw in df.iterrows():
        row_id = raw['_id']
        highway = raw.get('Highway', '')
        between = raw.get('Between', '')
        times = raw.get('Prohibited Times and/or Days')

        if pd.isna(times) or not str(times).strip():
            record_failure(row_id, 'schedule', SCHEDULE_EMPTY, 'empty times', highway, between)
            failure_counts[SCHEDULE_EMPTY] += 1
            continue

        schedule = parse_schedule(times)
        max_minutes = parse_max_minutes(raw.get('Maximum Period Permitted'))
        rows.append({
            '_id': row_id,
            'schedule_json': schedule_to_json(schedule),
            'schedule_status': schedule['status'],
            'max_minutes': max_minutes,
        })

    return pd.DataFrame(rows, columns=['_id'] + SCHEDULE_EXPORT_COLUMNS), failure_counts


def _print_summary(total: int, df: pd.DataFrame, failure_counts: Counter) -> None:
    print(f'Total rows: {total}')
    print(f'Schedule rows written: {len(df)}')
    if failure_counts:
        print('  Schedule-stage ledger failures (malformed input):')
        for code, count in failure_counts.most_common():
            print(f'    {code}: {count}')
    if len(df):
        print('  schedule_status:')
        print(df['schedule_status'].value_counts().to_string())


def main() -> None:
    print('Parsing Prohibited Times and/or Days...')
    df = pd.read_csv(data_path('clean_parking_targets.csv'))

    clear_stage('schedule')
    parsed, failure_counts = parse_rows(df)
    parsed.to_csv(data_path('parsed_schedules.csv'), index=False)
    _print_summary(len(df), parsed, failure_counts)


if __name__ == '__main__':
    main()

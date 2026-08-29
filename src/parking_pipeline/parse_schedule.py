"""Stage 1b: parse Prohibited Times → parsed_schedules.csv."""

from __future__ import annotations

import logging
from collections import Counter

import pandas as pd

from .failure_ledger import clear_stage, record_failure
from .paths import data_path
from .schedule_format import (
    SCHEDULE_EXPORT_COLUMNS,
    parse_max_minutes,
    parse_schedule,
    schedule_to_json,
)

log = logging.getLogger(__name__)

SCHEDULE_EMPTY = 'SCHEDULE_EMPTY'
EMPTY_TIMES_DEFAULT = 'Anytime'
_EMPTY_TIMES_DEFAULT_CATEGORIES = frozenset({
    'no_parking',
    'no_stopping',
    'no_standing',
})


def empty_times_default(row: pd.Series) -> str | None:
    """
    When prohibited times are blank, infer a parseable schedule for known bylaw types.
    No parking / no stopping / no standing with no times → anytime prohibition.
    """
    category = str(row.get('schedule_category', '')).strip()
    if category in _EMPTY_TIMES_DEFAULT_CATEGORIES:
        return EMPTY_TIMES_DEFAULT
    return None


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
            times = empty_times_default(raw)
        if times is None or not str(times).strip():
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
    log.info(f'Total rows: {total}')
    log.info(f'Schedule rows written: {len(df)}')
    if failure_counts:
        log.info('  Schedule-stage ledger failures (malformed input):')
        for code, count in failure_counts.most_common():
            log.info(f'    {code}: {count}')
    if len(df):
        log.info('  schedule_status:')
        log.info(df['schedule_status'].value_counts().to_string())


def main() -> None:
    import argparse

    from .log_config import add_verbose_arg, setup_logging

    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    log.info('Parsing Prohibited Times and/or Days...')
    df = pd.read_csv(data_path('clean_parking_targets.csv'))

    clear_stage('schedule')
    parsed, failure_counts = parse_rows(df)
    parsed.to_csv(data_path('parsed_schedules.csv'), index=False)
    _print_summary(len(df), parsed, failure_counts)


if __name__ == '__main__':
    main()

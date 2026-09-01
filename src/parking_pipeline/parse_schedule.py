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


def empty_times_default(row: pd.Series | dict | str) -> str | None:
    """
    When prohibited times are blank, infer a parseable schedule for known bylaw types.
    No parking / no stopping / no standing with no times → anytime prohibition.
    """
    if isinstance(row, str):
        category = row.strip()
    elif isinstance(row, dict):
        category = str(row.get('schedule_category', '')).strip()
    else:
        category = str(row.get('schedule_category', '')).strip()
    if category in _EMPTY_TIMES_DEFAULT_CATEGORIES:
        return EMPTY_TIMES_DEFAULT
    return None


def parse_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Parse time strings for all clean rows."""
    failure_counts: Counter = Counter()
    rows: list[dict] = []

    sched_cache: dict[str, tuple[str, str]] = {}
    max_cache: dict[str, int | None] = {}

    col_idx = {c: df.columns.get_loc(c) for c in df.columns}
    id_idx = col_idx.get('_id')
    hi_idx = col_idx.get('Highway')
    bt_idx = col_idx.get('Between')
    times_idx = col_idx.get('Prohibited Times and/or Days')
    cat_idx = col_idx.get('schedule_category')
    max_idx = col_idx.get('Maximum Period Permitted')

    for tup in df.itertuples(index=False, name=None):
        row_id = tup[id_idx] if id_idx is not None else None
        highway = tup[hi_idx] if hi_idx is not None and not pd.isna(tup[hi_idx]) else ''
        between = tup[bt_idx] if bt_idx is not None and not pd.isna(tup[bt_idx]) else ''
        raw_times = tup[times_idx] if times_idx is not None else None
        cat = tup[cat_idx] if cat_idx is not None and not pd.isna(tup[cat_idx]) else ''
        raw_max = tup[max_idx] if max_idx is not None else None

        times = str(raw_times).strip() if raw_times is not None and not pd.isna(raw_times) and str(raw_times).strip() else None
        if times is None:
            times = empty_times_default(cat)
        if times is None:
            record_failure(row_id, 'schedule', SCHEDULE_EMPTY, 'empty times', highway, between)
            failure_counts[SCHEDULE_EMPTY] += 1
            continue

        cached_sched = sched_cache.get(times)
        if cached_sched is None:
            schedule = parse_schedule(times)
            cached_sched = (schedule_to_json(schedule), schedule['status'])
            sched_cache[times] = cached_sched

        sched_json, sched_status = cached_sched

        max_key = '' if raw_max is None or pd.isna(raw_max) else str(raw_max).strip()
        if max_key in max_cache:
            max_minutes = max_cache[max_key]
        else:
            max_minutes = parse_max_minutes(max_key if max_key else None)
            max_cache[max_key] = max_minutes

        rows.append({
            '_id': row_id,
            'schedule_json': sched_json,
            'schedule_status': sched_status,
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

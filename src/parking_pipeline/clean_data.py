"""Stage 1: filter raw parking dump → clean_parking_targets.csv."""

from __future__ import annotations

import ast
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from .failure_ledger import clear_stage, record_failure
from .opendata import RawDumpError, ensure_raw_parking_dump
from .paths import data_path

log = logging.getLogger(__name__)

# --- Schedule allowlist (exact scheduleName strings; no substring matching) ---

_SCHEDULE_CATEGORIES: dict[str, tuple[str, ...]] = {
    'no_parking': (
        'Schedule 13: No Parking',
        'SCHEDULE XIII: No Parking',
    ),
    'no_stopping': (
        'Schedule 14: No Stopping',
        'SCHEDULE XIV: No Stopping',
    ),
    'restricted_periods': (
        'Schedule 15: Parking for Restricted Periods',
        'SCHEDULE XV: Parking for Restricted Periods',
    ),
    'no_standing': (
        'Schedule 16: No Standing',
        'SCHEDULE XVI: No Standing',
    ),
    'winter_maintenance': (
        'Schedule 04: Former City of North York Winter Maintenance Parking Prohibited',
        'SCHEDULE IV: Former City of North York Winter Maintenance Parking Prohibited',
    ),
    'snow_route': (
        'SCHEDULE XVIIA: Parking And Standing during Major Snow Storm Conditions',
        'Schedule 17A: Parking And Standing during Major Snow Storm Conditions',
    ),
    'snow_streetcar': (
        'SCHEDULE XVIIB: Parking/Standing on or Blocking Streetcar Tracks during Major Snow Storm Conditions',
        'Schedule 17B: Parking/Standing on or Blocking Streetcar Tracks during Major Snow Storm Conditions',
    ),
    'ev_charging': (
        'Schedule 44: Electrical Vehicle Charging Station Parking',
        'SCHEDULE XLIV: Electrical Vehicle Charging Station Parking',
    ),
    'car_share': (
        'Schedule 43: Car Share Vehicle Parking Areas',
        'SCHEDULE XLIII: Car Share Vehicle Parking Areas',
    ),
    'commercial_loading': (
        'Schedule 06: Commercial Loading Zones',
        'SCHEDULE VI: Commercial Loading Zones',
    ),
    'passenger_loading': (
        'Schedule 07: Passenger Loading Zones',
        'SCHEDULE VII: Passenger Loading Zones',
    ),
    'delivery_loading': (
        'Schedule 09: Delivery Vehicle Parking Zones',
        'SCHEDULE IX: Delivery Vehicle Parking Zones',
    ),
    'taxicab_stand': (
        'Schedule 05: Stands for Taxicabs',
        'SCHEDULE V: Stands for Taxicabs',
    ),
}

ALLOWED_SCHEDULE_NAMES = frozenset(
    name for names in _SCHEDULE_CATEGORIES.values() for name in names
)
SCHEDULE_CATEGORY_BY_NAME = {
    name: category
    for category, names in _SCHEDULE_CATEGORIES.items()
    for name in names
}

# --- Failure reason codes (clean stage) ---

UNPACK_PARSE_ERROR = 'UNPACK_PARSE_ERROR'
UNPACK_EMPTY_TABLE = 'UNPACK_EMPTY_TABLE'
UNPACK_MISSING_HIGHWAY = 'UNPACK_MISSING_HIGHWAY'
OUTPUT_COLUMNS = [
    '_id',
    'scheduleName',
    'schedule_category',
    'Highway',
    'Side',
    'Between',
    'Prohibited Times and/or Days',
    'Maximum Period Permitted',
]

# Same curb segment + schedule type + times → keep one row (lowest _id).
DEDUP_KEYS = [
    'Highway',
    'Side',
    'Between',
    'Prohibited Times and/or Days',
    'Maximum Period Permitted',
    'schedule_category',
]


@dataclass(frozen=True)
class UnpackResult:
    fields: dict
    reason: str | None = None
    detail: str = ''

    @property
    def ok(self) -> bool:
        return self.reason is None


def schedule_category(schedule_name: str) -> str:
    return SCHEDULE_CATEGORY_BY_NAME.get(str(schedule_name), 'other')


def _is_blank(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    return not str(val).strip()


def unpack_bylaw_table(cell_data) -> UnpackResult:
    """Parse a ByLaw_Table cell into flat bylaw fields."""
    if _is_blank(cell_data):
        return UnpackResult({}, UNPACK_EMPTY_TABLE, 'empty ByLaw_Table cell')

    # CKAN datastore CSV wraps the Python-literal list in an extra quoted string.
    data_list = cell_data
    try:
        for _ in range(3):
            if isinstance(data_list, list):
                break
            if not isinstance(data_list, str):
                break
            data_list = ast.literal_eval(data_list)
    except (ValueError, SyntaxError, TypeError) as e:
        return UnpackResult({}, UNPACK_PARSE_ERROR, str(e)[:500])

    if not isinstance(data_list, list) or not data_list:
        return UnpackResult({}, UNPACK_EMPTY_TABLE, 'ByLaw_Table is not a non-empty list')

    fields = {
        item['key']: item['value']
        for item in data_list
        if isinstance(item, dict) and 'key' in item and 'value' in item
    }
    if not fields:
        return UnpackResult({}, UNPACK_EMPTY_TABLE, 'no key/value pairs in ByLaw_Table')

    if _is_blank(fields.get('Highway')):
        return UnpackResult(fields, UNPACK_MISSING_HIGHWAY, 'Highway missing after unpack')

    return UnpackResult(fields)


def extract_side(fields: dict, category: str) -> str | None:
    side = fields.get('Side') or fields.get('Side Parking')
    if _is_blank(side) and category in ('winter_maintenance', 'snow_route', 'snow_streetcar'):
        return 'Both'
    return side


def extract_between(fields: dict) -> str | None:
    return fields.get('Between') or fields.get('Location')


def extract_max_period(fields: dict) -> str | None:
    return fields.get('Maximum Period Permitted') or fields.get('Maximum Period Parking')


def prohibited_times(fields: dict, category: str = ''):
    """Map API fields to the single prohibited-times column used downstream."""
    if category == 'winter_maintenance':
        return '2:00 a.m. to 6:00 a.m. from Dec. 1 to Mar. 31'
    if category in ('snow_route', 'snow_streetcar'):
        return 'Major Snow Storm Conditions'
    if category == 'car_share':
        return 'Anytime'

    prohibited = fields.get('Prohibited Times and/or Days')
    times = (
        fields.get('Times and/or Days')
        or fields.get('Hours (daily as indicated below)')
        or fields.get('Hours')
    )
    if _is_blank(prohibited) and _is_blank(times):
        between = extract_between(fields)
        if between and re.search(r'\banytime\b', str(between), re.IGNORECASE):
            return 'Anytime'
    if _is_blank(prohibited):
        return times
    if _is_blank(times):
        return prohibited
    return prohibited if str(prohibited).strip() else times


def _norm_cell(val) -> str:
    return '' if _is_blank(val) else str(val).strip().casefold()


def _clean_row(row_id: object, schedule_name: object, fields: dict) -> dict:
    cat = schedule_category(str(schedule_name))
    return {
        '_id': row_id,
        'scheduleName': schedule_name,
        'schedule_category': cat,
        'Highway': fields.get('Highway'),
        'Side': extract_side(fields, cat),
        'Between': extract_between(fields),
        'Prohibited Times and/or Days': prohibited_times(fields, cat),
        'Maximum Period Permitted': extract_max_period(fields),
    }


def unpack_rows(active: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Unpack ByLaw_Table for all active rows; record failures to the ledger."""
    rows: list[dict] = []
    failure_counts: Counter = Counter()

    col_idx = {c: active.columns.get_loc(c) for c in active.columns}
    id_idx = col_idx['_id']
    sched_idx = col_idx['scheduleName']
    tbl_idx = col_idx['ByLaw_Table']

    for tup in active.itertuples(index=False, name=None):
        row_id = tup[id_idx]
        sched_name = tup[sched_idx]
        raw_tbl = tup[tbl_idx]

        result = unpack_bylaw_table(raw_tbl)
        highway = result.fields.get('Highway', '')
        between = result.fields.get('Between', '')

        if not result.ok:
            record_failure(row_id, 'clean', result.reason, result.detail, highway, between)
            failure_counts[result.reason] += 1
            continue

        rows.append(_clean_row(row_id, sched_name, result.fields))

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS), failure_counts


def deduplicate_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep the lowest _id per DEDUP_KEYS group; drop later duplicates (not ledger failures)."""
    work = df.copy()
    norm_cols = [
        work[c].fillna('').astype(str).str.strip().str.casefold()
        for c in DEDUP_KEYS
    ]
    work['_dedup_key'] = list(zip(*norm_cols, strict=True))
    work['_id_num'] = pd.to_numeric(work['_id'], errors='coerce')

    keepers = (
        work.sort_values(['_id_num', '_id'], na_position='last')
        .drop_duplicates(subset=['_dedup_key'], keep='first')
    )
    kept_idx = keepers.index
    dropped_count = len(work) - len(keepers)

    return df.loc[kept_idx].copy(), dropped_count


def load_active_rules() -> pd.DataFrame:
    """Allowed schedules that are not repealed."""
    df = pd.read_csv(
        data_path('toronto_raw_parking_dump.csv'),
        usecols=['_id', 'scheduleName', 'ByLaw_Table', 'Latest_Action'],
    )
    return df[
        (df['Latest_Action'] != 'Repealed')
        & df['scheduleName'].isin(ALLOWED_SCHEDULE_NAMES)
    ].copy()


def _print_summary(
    *,
    clean_df: pd.DataFrame,
    unpack_excluded: int,
    dup_dropped: int,
    before_dedup: int,
    failure_counts: Counter,
) -> None:
    log.info(f'Wrote {len(clean_df)} rows to clean_parking_targets.csv')
    log.info(f'  Excluded by unpack failures: {unpack_excluded}')
    if dup_dropped:
        log.info(f'  Duplicates removed: {dup_dropped} ({before_dedup} → {len(clean_df)})')
    if failure_counts:
        log.info('  Clean-stage failures (see failure_ledger.csv):')
        for code, count in failure_counts.most_common():
            log.info(f'    {code}: {count}')
    log.info('Done! Clean CSV created.')


def main(argv: list[str] | None = None) -> None:
    import argparse

    from .log_config import add_verbose_arg, setup_logging

    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    parser.add_argument(
        '--skip-refresh',
        action='store_true',
        help='Use the existing local dump; do not contact Toronto Open Data',
    )
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Re-download the dump even if local CKAN metadata still matches',
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    try:
        ensure_raw_parking_dump(force=args.force_refresh, skip=args.skip_refresh)
    except RawDumpError as exc:
        log.error('%s', exc)
        sys.exit(1)

    active = load_active_rules()
    if active.empty:
        log.error('Wait! The filters removed all rows. Check the exact text in your CSV columns.')
        sys.exit(1)

    log.info(f'Success! Found {len(active)} active curb rules.')
    log.info(active['scheduleName'].map(schedule_category).value_counts().to_string())
    log.info('Unpacking nested data... (this might take a few seconds)')

    clear_stage('clean')
    clean_df, failure_counts = unpack_rows(active)
    unpack_excluded = len(active) - len(clean_df)

    before_dedup = len(clean_df)
    clean_df, dup_dropped = deduplicate_rules(clean_df)

    clean_df.to_csv(data_path('clean_parking_targets.csv'), index=False)
    _print_summary(
        clean_df=clean_df,
        unpack_excluded=unpack_excluded,
        dup_dropped=dup_dropped,
        before_dedup=before_dedup,
        failure_counts=failure_counts,
    )


if __name__ == '__main__':
    main()

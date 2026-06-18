"""Stage 1: filter raw parking dump → clean_parking_targets.csv."""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from .failure_ledger import clear_stage, record_failure
from .paths import data_path

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
}

ALLOWED_SCHEDULE_NAMES = frozenset(
    name for names in _SCHEDULE_CATEGORIES.values() for name in names
)
SCHEDULE_CATEGORY_BY_NAME = {
    name: category
    for category, names in _SCHEDULE_CATEGORIES.items()
    for name in names
}

# Optional schedules — add names to _SCHEDULE_CATEGORIES when enabling:
# winter_maintenance: Schedule 04 / SCHEDULE IV (North York winter)
# snow_storm: SCHEDULE XVIIA
# snow_streetcar: SCHEDULE XVIIB

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

    try:
        data_list = ast.literal_eval(cell_data)
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


def prohibited_times(fields: dict):
    """Map API fields to the single prohibited-times column used downstream."""
    prohibited = fields.get('Prohibited Times and/or Days')
    times = fields.get('Times and/or Days')
    if _is_blank(prohibited) and _is_blank(times):
        between = fields.get('Between')
        if between and re.search(r'\banytime\b', str(between), re.IGNORECASE):
            return 'Anytime'
    if _is_blank(prohibited):
        return times
    if _is_blank(times):
        return prohibited
    return prohibited if str(prohibited).strip() else times


def _norm_cell(val) -> str:
    return '' if _is_blank(val) else str(val).strip().casefold()


def _clean_row(raw: pd.Series, fields: dict) -> dict:
    return {
        '_id': raw['_id'],
        'scheduleName': raw['scheduleName'],
        'schedule_category': schedule_category(raw['scheduleName']),
        'Highway': fields.get('Highway'),
        'Side': fields.get('Side'),
        'Between': fields.get('Between'),
        'Prohibited Times and/or Days': prohibited_times(fields),
        'Maximum Period Permitted': fields.get('Maximum Period Permitted'),
    }


def unpack_rows(active: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Unpack ByLaw_Table for all active rows; record failures to the ledger."""
    rows: list[dict] = []
    failure_counts: Counter = Counter()

    for _, raw in active.iterrows():
        result = unpack_bylaw_table(raw['ByLaw_Table'])
        row_id = raw['_id']
        highway = result.fields.get('Highway', '')
        between = result.fields.get('Between', '')

        if not result.ok:
            record_failure(row_id, 'clean', result.reason, result.detail, highway, between)
            failure_counts[result.reason] += 1
            continue

        rows.append(_clean_row(raw, result.fields))

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS), failure_counts


def deduplicate_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep the lowest _id per DEDUP_KEYS group; drop later duplicates (not ledger failures)."""
    work = df.copy()
    work['_dedup_key'] = work[DEDUP_KEYS].apply(
        lambda row: tuple(_norm_cell(v) for v in row),
        axis=1,
    )
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
    df = pd.read_csv(data_path('toronto_raw_parking_dump.csv'))
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
    print(f'Wrote {len(clean_df)} rows to clean_parking_targets.csv')
    print(f'  Excluded by unpack failures: {unpack_excluded}')
    if dup_dropped:
        print(f'  Duplicates removed: {dup_dropped} ({before_dedup} → {len(clean_df)})')
    if failure_counts:
        print('  Clean-stage failures (see failure_ledger.csv):')
        for code, count in failure_counts.most_common():
            print(f'    {code}: {count}')
    print('Done! Clean CSV created.')


def main() -> None:
    active = load_active_rules()
    if active.empty:
        print('Wait! The filters removed all rows. Check the exact text in your CSV columns.')
        sys.exit(1)

    print(f'Success! Found {len(active)} active curb rules.')
    print(active['scheduleName'].map(schedule_category).value_counts().to_string())
    print('Unpacking nested data... (this might take a few seconds)')

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

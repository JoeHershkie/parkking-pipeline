"""Stage 2.5: resolve bylaw Highway values to TCL keys → parsed_successes.csv."""

from __future__ import annotations

import csv
from collections import Counter

import pandas as pd

from .failure_ledger import clear_stage, record_failure
from .parse_format import (
    RESOLVE_COLUMNS,
    _parse_valid_flag,
    _resolve_valid_flag,
    resolve_columns_for_row,
    row_to_parsed,
)
from .paths import data_path
from .tcl_highway_key import tcl_highway_key
from . import tcl_highway_resolve as thr

RESOLVE_STREET_NOT_FOUND = 'RESOLVE_STREET_NOT_FOUND'


def _init_resolve_index() -> None:
    """Build highway suffix-resolve index from tcl_street_names.csv (no GeoJSON load)."""
    path = data_path('tcl_street_names.csv')
    legal_keys: set[str] = set()
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            legal = (row.get('linear_name_full_legal') or '').strip()
            if legal:
                legal_keys.add(tcl_highway_key(legal))
    thr.build_index_from_csv(legal_keys=legal_keys)


def resolve_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, Counter]:
    """Add resolve columns to each row; record failures to the ledger."""
    failure_counts: Counter = Counter()
    rows: list[dict] = []

    for _, raw in df.iterrows():
        row_id = raw['_id']
        highway = raw.get('Highway', '')
        between = raw.get('Between', '')

        if 'parse_valid' in raw.index and not _parse_valid_flag(raw.get('parse_valid')):
            continue

        parsed = row_to_parsed(raw)
        resolved_cols = resolve_columns_for_row(raw, parsed)
        row = raw.to_dict()
        row.update(resolved_cols)

        if not resolved_cols.get('resolve_valid'):
            record_failure(
                row_id,
                'resolve',
                RESOLVE_STREET_NOT_FOUND,
                resolved_cols.get('resolve_error') or 'resolve failed',
                highway,
                between,
            )
            failure_counts[RESOLVE_STREET_NOT_FOUND] += 1

        rows.append(row)

    if not rows:
        out_cols = list(df.columns) + [c for c in RESOLVE_COLUMNS if c not in df.columns]
        return pd.DataFrame(columns=out_cols), failure_counts

    return pd.DataFrame(rows), failure_counts


def _print_summary(total: int, success_count: int, failure_counts: Counter) -> None:
    pct = lambda n: round((n / total) * 100, 1) if total else 0.0
    print(f'Total Rows: {total}')
    print(f'Resolved: {success_count} ({pct(success_count)}%)')
    if failure_counts:
        print('  Resolve-stage failures (see failure_ledger.csv):')
        for code, count in failure_counts.most_common():
            print(f'    {code}: {count}')


def main() -> None:
    print('Resolving Highway values to TCL keys...')
    path = data_path('parsed_successes.csv')
    df = pd.read_csv(path)

    _init_resolve_index()
    clear_stage('resolve')
    resolved_df, failure_counts = resolve_rows(df)
    if 'resolve_valid' in resolved_df.columns:
        success_count = int(resolved_df['resolve_valid'].map(_resolve_valid_flag).sum())
    else:
        success_count = 0

    resolved_df.to_csv(path, index=False)
    _print_summary(len(df), success_count, failure_counts)


if __name__ == '__main__':
    main()

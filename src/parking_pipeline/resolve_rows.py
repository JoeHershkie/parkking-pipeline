"""Stage 2.5: resolve bylaw Highway values to TCL keys → parsed_successes.csv."""

from __future__ import annotations

import csv
import logging
from collections import Counter

import pandas as pd

from . import tcl_highway_resolve as thr
from .failure_ledger import clear_stage, record_failures
from .parse_format import (
    RESOLVE_COLUMNS,
    _parse_valid_flag,
    _resolve_valid_flag,
    resolve_columns_for_row,
    row_to_parsed,
)
from .paths import data_path
from .street_names_csv import ensure_street_names_csv
from .tcl_highway_key import tcl_highway_key

log = logging.getLogger(__name__)

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
    failures_to_record: list[dict] = []

    resolve_cache: dict[tuple, dict] = {}
    col_names = df.columns.tolist()
    col_idx = {c: i for i, c in enumerate(col_names)}
    id_idx = col_idx.get('_id')
    hi_idx = col_idx.get('Highway')
    bt_idx = col_idx.get('Between')
    pv_idx = col_idx.get('parse_valid')

    for tup in df.itertuples(index=False, name=None):
        if pv_idx is not None and not _parse_valid_flag(tup[pv_idx]):
            continue

        row_id = tup[id_idx] if id_idx is not None else None
        highway = tup[hi_idx] if hi_idx is not None and not pd.isna(tup[hi_idx]) else ''
        between = tup[bt_idx] if bt_idx is not None and not pd.isna(tup[bt_idx]) else ''

        raw = dict(zip(col_names, tup, strict=True))
        parsed = row_to_parsed(raw)

        cache_key = (
            highway,
            between,
            tuple(sorted((k, v) for k, v in parsed.items() if k != '_id')),
        )
        cached = resolve_cache.get(cache_key)
        if cached is None:
            cached = resolve_columns_for_row(raw, parsed)
            resolve_cache[cache_key] = cached

        resolved_cols = dict(cached)
        raw.update(resolved_cols)

        if not resolved_cols.get('resolve_valid'):
            failures_to_record.append({
                'row_id': row_id,
                'stage': 'resolve',
                'reason_code': RESOLVE_STREET_NOT_FOUND,
                'detail': resolved_cols.get('resolve_error') or 'resolve failed',
                'highway': highway,
                'between': between,
                'between_parsed_input': '',
            })
            failure_counts[RESOLVE_STREET_NOT_FOUND] += 1

        rows.append(raw)

    if failures_to_record:
        record_failures(failures_to_record)

    if not rows:
        out_cols = list(df.columns) + [c for c in RESOLVE_COLUMNS if c not in df.columns]
        return pd.DataFrame(columns=out_cols), failure_counts

    return pd.DataFrame(rows), failure_counts


def _print_summary(total: int, success_count: int, failure_counts: Counter) -> None:
    pct = lambda n: round((n / total) * 100, 1) if total else 0.0
    log.info(f'Total Rows: {total}')
    log.info(f'Resolved: {success_count} ({pct(success_count)}%)')
    if failure_counts:
        log.info('  Resolve-stage failures (see failure_ledger.csv):')
        for code, count in failure_counts.most_common():
            log.info(f'    {code}: {count}')


def main() -> None:
    import argparse

    from .log_config import add_verbose_arg, setup_logging

    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    log.info('Resolving Highway values to TCL keys...')
    path = data_path('parsed_successes.csv')
    df = pd.read_csv(path)

    ensure_street_names_csv()
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

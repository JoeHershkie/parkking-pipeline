#!/usr/bin/env python3
"""Regression: parse_between coverage for failure_triage 338-row cohort."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from bylaw_text import preprocess_between
from parse_between import parse_between  # noqa: E402
from parse_format import validate_parsed  # noqa: E402
from paths import data_path  # noqa: E402

COHORT_HINT = 'Add Between regex in parse_between for this pattern class'


def _between_text(row: pd.Series) -> str:
    for col in ('between_parsed_input', 'between'):
        val = row.get(col, '')
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            s = str(val).strip()
            if s and s.lower() != 'nan':
                return preprocess_between(s)
    return ''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--expect-min-parsed',
        type=int,
        default=280,
        help='Minimum cohort rows that must parse+validate (default 280)',
    )
    args = parser.parse_args()

    triage = pd.read_csv(data_path('failure_triage.csv'))
    cohort = triage[
        (triage['reason_code'] == 'PARSE_NO_MATCH')
        & (triage['fix_hint'] == COHORT_HINT)
    ]
    parsed_ok = 0
    failures: list[tuple[str, str, str]] = []

    for _, row in cohort.iterrows():
        text = _between_text(row)
        parsed = parse_between(text) if text else None
        if parsed is None:
            failures.append((str(row.get('parse_between_class', '')), 'no_match', text[:120]))
            continue
        ok, err = validate_parsed(parsed)
        if ok:
            parsed_ok += 1
        else:
            failures.append((str(row.get('parse_between_class', '')), err, text[:120]))

    total = len(cohort)
    print(f'Cohort rows: {total}')
    print(f'Parsed+valid: {parsed_ok} ({100 * parsed_ok / total:.1f}%)')
    print(f'Remaining: {total - parsed_ok}')

    if failures:
        by_class = Counter(c for c, _, _ in failures)
        print('Failures by parse_between_class:', dict(by_class))
        print('Sample failures:')
        for cls, reason, snippet in failures[:10]:
            print(f'  [{cls}] {reason}: {snippet!r}')

    if parsed_ok < args.expect_min_parsed:
        print(
            f'FAIL: expected at least {args.expect_min_parsed} parsed, got {parsed_ok}',
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

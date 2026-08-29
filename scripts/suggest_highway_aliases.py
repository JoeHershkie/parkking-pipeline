#!/usr/bin/env python3
"""Suggest highway_aliases.csv entries from STREET_NOT_FOUND analysis."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from parking_pipeline import tcl_highway_resolve as thr  # noqa: E402
from parking_pipeline.paths import data_path  # noqa: E402
from parking_pipeline.tcl_highway_key import tcl_highway_key  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=data_path('highway_alias_suggestions.csv'),
    )
    args = parser.parse_args()

    analysis_path = data_path('street_failure_analysis.csv')
    if not analysis_path.exists():
        raise SystemExit('Run analyze_street_failures.py first')

    df = pd.read_csv(analysis_path)
    tcl = pd.read_csv(data_path('tcl_street_names.csv'))
    thr.build_index_from_csv(legal_keys={
        tcl_highway_key(x) for x in tcl['linear_name_full_legal'].dropna()
    })

    applied: set[str] = set()
    alias_path = data_path('highway_aliases.csv')
    if alias_path.exists():
        applied = {
            tcl_highway_key(str(r['bylaw_highway']))
            for _, r in pd.read_csv(alias_path).iterrows()
            if str(r.get('bylaw_highway', '')).strip()
        }

    rows = []
    for _, r in df.iterrows():
        highway = str(r.get('highway', '')).strip()
        if not highway:
            continue
        near = thr.gated_near_match_legals(highway)
        resolved = str(r.get('resolved_key_candidate', '')).strip()
        recommendation = 'skip'
        tcl_legal = ''
        if resolved:
            recommendation = 'alias'
            tcl_legal = resolved
        elif len(near) == 1:
            recommendation = 'review'
            tcl_legal = near[0]
        elif r.get('subcause') == 'truly_absent_from_tcl':
            recommendation = 'research'

        rows.append({
            'bylaw_highway': highway,
            'tcl_linear_name_full_legal': tcl_legal,
            'recommendation': recommendation,
            'street_category': r.get('street_category', ''),
            'near_match_count': r.get('near_match_count', 0),
            'already_applied': tcl_highway_key(highway) in applied,
        })

    out = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f'Wrote {len(out)} suggestions to {args.output}')
    print(out['recommendation'].value_counts().to_string())


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Evaluate a candidate normalizer change before merging into intersection_normalize."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable

import geopandas as gpd
import pandas as pd

from parking_pipeline import intersection_index as ix  # noqa: E402
from parking_pipeline import tcl_graph as tg  # noqa: E402
from parking_pipeline.intersection_normalize import (  # noqa: E402
    _REPLACEMENTS,
    apply_street_alias,
    clear_alias_cache,
    normalize_intersection_street,
)
from parking_pipeline.paths import data_path  # noqa: E402

# Golden pairs from tests/test_intersection_index.py
GOLDEN_PAIRS = [
    ('armadale avenue', 'Colbeck Street'),
    ('armadale avenue', 'Annette Street'),
]

MIN_DESC_HITS = 5
ONE_OFF_BYLAW_LIMIT = 5


def normalize_preserve_apostrophe(street_name: str) -> str:
    """Candidate: keep apostrophe in TCL search tokens."""
    name = str(street_name).lower().strip()
    name = name.replace('.', '')
    for pattern, replacement in _REPLACEMENTS:
        name = re.sub(pattern, replacement, name)
    return re.sub(r'\s+', ' ', name).strip()


def tokenize_no_alias(street_name: str, norm_fn: Callable[[str], str]) -> str:
    return norm_fn(str(street_name))


def tokenize_with_alias(street_name: str, norm_fn: Callable[[str], str]) -> str:
    key = str(street_name).strip().lower()
    from parking_pipeline.intersection_normalize import _load_alias_map
    alias = _load_alias_map().get(key)
    if alias:
        return alias
    return norm_fn(street_name)


def resolve_pair_custom(
    highway: str,
    cross: str,
    norm_fn: Callable[[str], str],
    *,
    use_aliases: bool,
) -> tuple[int, ...]:
    fn = tokenize_with_alias if use_aliases else tokenize_no_alias
    s1 = fn(highway, norm_fn)
    s2 = fn(cross, norm_fn)
    if not s1 or not s2:
        return ()
    ids_a = ix._ensure_token(s1)
    ids_b = ix._ensure_token(s2)
    if not ids_a or not ids_b:
        return ()
    set_b = set(ids_b)
    return tuple(i for i in ids_a if i in set_b)


def load_descs() -> list[str]:
    path = data_path('tcl_intersections.geojson')
    with path.open(encoding='utf-8') as f:
        data = json.load(f)
    return [feat['properties']['INTERSECTION_DESC'].lower() for feat in data['features']]


def desc_hit_count(token: str, descs: list[str]) -> int:
    if not token:
        return 0
    return sum(1 for d in descs if token in d)


def audit_aliases(descs: list[str]) -> list[dict]:
    import csv
    path = data_path('street_aliases.csv')
    rows: list[dict] = []
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            token = (row.get('tcl_token') or '').strip().lower()
            hits = desc_hit_count(token, descs)
            rows.append({
                'bylaw_name': row.get('bylaw_name'),
                'tcl_token': token,
                'desc_hit_count': hits,
                'ok': hits > 0,
            })
    return rows


def scoped_failures() -> pd.DataFrame:
    path = data_path('intersection_failure_analysis.csv')
    if not path.exists():
        raise SystemExit('Run analyze_intersection_failures.py first')
    df = pd.read_csv(path)
    return df[
        (df['subcause'] == 'highway_only_in_tcl')
        & (df['cross_in_tcl'] == False)
        & (df['category'] == 'normal_street_name')
    ].copy()


def recovery_count(df: pd.DataFrame, norm_fn: Callable[[str], str], *, use_aliases: bool) -> int:
    hits = 0
    for _, row in df.iterrows():
        cross_name = _bylaw_cross_name(row)
        if resolve_pair_custom(
            str(row['highway']),
            cross_name,
            norm_fn,
            use_aliases=use_aliases,
        ):
            hits += 1
    return hits


def _bylaw_cross_name(row: pd.Series) -> str:
    field = str(row.get('field') or '')
    if field == 'start' and pd.notna(row.get('start_intersection')):
        return str(row['start_intersection'])
    if field == 'end' and pd.notna(row.get('end_intersection')):
        return str(row['end_intersection'])
    if field == 'offset' and pd.notna(row.get('offset_intersection')):
        return str(row['offset_intersection'])
    return str(row['cross'])


def regression_count(
    norm_fn: Callable[[str], str],
    *,
    baseline_fn: Callable[[str], str],
    use_aliases: bool,
) -> int:
    """Pairs that matched under baseline but not under candidate."""
    reg = 0
    for a, b in GOLDEN_PAIRS:
        before = resolve_pair_custom(a, b, baseline_fn, use_aliases=use_aliases)
        after = resolve_pair_custom(a, b, norm_fn, use_aliases=use_aliases)
        if before and not after:
            reg += 1
        elif before and after and set(before) != set(after):
            reg += 1
    return reg


def evaluate_rule(
    name: str,
    candidate_fn: Callable[[str], str],
    *,
    use_aliases: bool = True,
) -> dict:
    clear_alias_cache()
    gdf = gpd.read_file(data_path('tcl_intersections.geojson'))
    tg.configure_intersections(gdf)
    ix.configure(gdf)

    baseline_fn = normalize_intersection_street
    df = scoped_failures()
    before = recovery_count(df, baseline_fn, use_aliases=use_aliases)
    after = recovery_count(df, candidate_fn, use_aliases=use_aliases)
    reg = regression_count(candidate_fn, baseline_fn=baseline_fn, use_aliases=use_aliases)

    return {
        'rule': name,
        'scoped_rows': len(df),
        'recovery_before': before,
        'recovery_after': after,
        'recovery_gain': after - before,
        'regression_count': reg,
        'pass': (after > before) and reg == 0,
        'use_aliases': use_aliases,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--rule',
        choices=['preserve_apostrophe', 'audit_aliases'],
        default='preserve_apostrophe',
    )
    args = parser.parse_args()

    descs = load_descs()

    if args.rule == 'audit_aliases':
        rows = audit_aliases(descs)
        print('=== ALIAS AUDIT ===')
        for r in rows:
            flag = 'OK' if r['ok'] else 'FIX'
            print(f"  [{flag}] {r['bylaw_name']!r} -> {r['tcl_token']!r} ({r['desc_hit_count']} hits)")
        bad = [r for r in rows if not r['ok']]
        if bad:
            print(f'\n{len(bad)} alias row(s) need fix')
            raise SystemExit(1)
        print('\nAll aliases have TCL hits')
        return

    print('=== RULE: preserve_apostrophe (aliases on) ===')
    result = evaluate_rule('preserve_apostrophe', normalize_preserve_apostrophe, use_aliases=True)
    for k, v in result.items():
        print(f'  {k}: {v}')

    print('\n=== RULE-ONLY (aliases off) ===')
    result_no_alias = evaluate_rule(
        'preserve_apostrophe',
        normalize_preserve_apostrophe,
        use_aliases=False,
    )
    for k, v in result_no_alias.items():
        print(f'  {k}: {v}')

    out = data_path('normalizer_rule_evaluation.json')
    out.write_text(json.dumps({'with_aliases': result, 'rule_only': result_no_alias}, indent=2))
    print(f'\nWrote {out}')

    if not result['pass']:
        print('\nRule did NOT pass (need recovery_gain > 0 and regression_count == 0)')
        raise SystemExit(1)
    print('\nRule PASSED — safe to merge preserve_apostrophe into normalize_intersection_street')


if __name__ == '__main__':
    main()

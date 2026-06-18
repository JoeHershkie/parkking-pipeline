#!/usr/bin/env python3
"""Classify STREET_NOT_FOUND rows in failure_ledger.csv."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from parking_pipeline.highway_categorize import categorize_highway  # noqa: E402
from parking_pipeline.lane_highway_resolve import (  # noqa: E402
    infer_lane_phrase_from_between,
    parse_lane_highway_phrase,
    resolve_lane_highway,
)
from parking_pipeline.paths import data_path  # noqa: E402
from parking_pipeline import tcl_highway_resolve as thr  # noqa: E402
from parking_pipeline.tcl_highway_key import tcl_highway_key  # noqa: E402


def _truly_absent(highway: str, tcl_names: pd.DataFrame) -> bool:
    key = thr.normalize_highway_for_lookup(highway)
    if not key:
        return True
    for col in ('linear_name_full_legal', 'linear_name_full'):
        if col not in tcl_names.columns:
            continue
        for val in tcl_names[col].dropna().astype(str):
            for part in val.split(' | '):
                if tcl_highway_key(part.strip()) == key:
                    return False
                if key in tcl_highway_key(part):
                    return False
    return True


def _anchor_street(highway: str, between: str) -> str:
    phrase = parse_lane_highway_phrase(highway)
    if phrase:
        return phrase.anchor
    inferred = infer_lane_phrase_from_between(highway, between)
    if inferred:
        return inferred.anchor
    return ''


def _suggest_fix(
    highway: str,
    between: str,
    category: str,
    lookup_key: str,
    resolved: str,
    legal_keys: frozenset[str],
    near_count: int,
    prefix_count: int,
    truly_absent: bool,
    parsed: dict | None = None,
) -> str:
    if resolved in legal_keys and resolved != lookup_key:
        return 'auto_highway_resolve'
    if category in (
        'generic_lane_highway',
        'lane_position_in_highway',
        'laneway_phrase',
        'misleading_highway_phrase',
    ):
        if resolve_lane_highway(highway, between, parsed or None) in legal_keys:
            return 'lane_infer'
        return 'skip'
    if truly_absent:
        return 'manual_alias'
    if near_count == 1 and category == 'plain_name':
        return 'manual_alias'
    if prefix_count > 1 and category == 'plain_name':
        return 'skip'
    if category in ('compound_or_slash_highway',):
        return 'skip'
    if near_count > 1:
        return 'skip'
    if category in ('parenthetical_qualifier', 'descriptor_in_name', 'leg_branch_paren'):
        if resolved in legal_keys:
            return 'auto_highway_resolve'
        return 'auto_highway_resolve'
    if category == 'ramp_service_parallel':
        return 'manual_alias'
    if resolved in legal_keys:
        return 'auto_highway_resolve'
    return 'skip'


def _suggested_tier(suggested_fix: str, truly_absent: bool) -> str:
    if suggested_fix == 'auto_highway_resolve':
        return 'B_quick'
    if suggested_fix == 'lane_infer':
        return 'C_medium'
    if suggested_fix == 'manual_alias':
        return 'C_medium' if not truly_absent else 'D_hard'
    return 'D_hard'


def _subcause(
    category: str,
    near_count: int,
    prefix_count: int,
    base_count: int,
    truly_absent: bool,
) -> str:
    if truly_absent:
        return 'truly_absent_from_tcl'
    if near_count == 1:
        return 'typo_ed1_unique'
    if near_count > 1:
        return 'typo_ed1_ambiguous'
    if prefix_count > 1:
        return 'prefix_ambiguous'
    if prefix_count == 1:
        return 'prefix_unique'
    if base_count > 1:
        return 'base_remap_ambiguous'
    if base_count == 1:
        return 'base_remap_unique'
    if category == 'parenthetical_qualifier':
        return 'parenthetical_qualifier'
    if category == 'ramp_service_parallel':
        return 'service_road_paren'
    return category


def analyze_row(
    row: pd.Series,
    legal_keys: frozenset[str],
    tcl_names: pd.DataFrame,
) -> dict[str, Any]:
    highway = str(row.get('highway', '') or '')
    between = str(row.get('between', '') or '')
    category = categorize_highway(highway)
    lookup_key = thr.tcl_lookup_key(highway)
    resolved = thr.resolve_tcl_highway(highway)
    parsed = {
        k: row[k]
        for k in (
            'start_intersection', 'end_intersection', 'offset_intersection',
            'terminus_street',
        )
        if k in row.index and pd.notna(row.get(k)) and str(row.get(k)).strip()
    }
    if parsed:
        resolved_ctx = thr.resolve_tcl_highway_with_context(highway, parsed)
        if resolved_ctx in legal_keys:
            resolved = resolved_ctx

    lane_resolved = resolve_lane_highway(highway, between, parsed or None)
    if lane_resolved:
        resolved = lane_resolved

    near = thr.gated_near_match_legals(highway)
    prefix_count = thr.prefix_match_count(highway)
    base_count = len(thr.base_remap_candidates(highway))
    absent = (
        category in ('plain_name', 'descriptor_in_name', 'compound_or_slash_highway')
        and _truly_absent(highway, tcl_names)
        and resolved not in legal_keys
    )
    fix = _suggest_fix(
        highway, between, category, lookup_key, resolved,
        legal_keys, len(near), prefix_count, absent, parsed or None,
    )
    return {
        'row_id': str(row.get('row_id', '')),
        'street_category': 'truly_absent_from_tcl' if absent else category,
        'subcause': _subcause(category, len(near), prefix_count, base_count, absent),
        'highway_lookup_key': lookup_key,
        'resolved_key_candidate': resolved if resolved in legal_keys else '',
        'near_match_count': len(near),
        'near_match_legals': ' | '.join(near[:3]),
        'prefix_match_count': prefix_count,
        'base_remap_count': base_count,
        'anchor_street': _anchor_street(highway, between),
        'rule_type': str(row.get('rule_type', '') or ''),
        'suggested_fix': fix,
        'suggested_tier': _suggested_tier(fix, absent),
    }


def _summary_dict(df: pd.DataFrame, n_parsed: int) -> dict[str, Any]:
    return {
        'parsed_successes': n_parsed,
        'street_not_found': len(df),
        'pct_of_parsed': round(100 * len(df) / n_parsed, 2) if n_parsed else 0,
        'street_category_counts': df['street_category'].value_counts().to_dict(),
        'suggested_fix_counts': df['suggested_fix'].value_counts().to_dict(),
        'suggested_tier_counts': df['suggested_tier'].value_counts().to_dict(),
        'subcause_counts': df['subcause'].value_counts().to_dict(),
        'resolvable_count': int((df['resolved_key_candidate'] != '').sum()),
        'top_highways': [
            {'highway': h, 'count': int(c)}
            for h, c in df['highway'].value_counts().head(20).items()
        ],
    }


def main() -> None:
    fl = pd.read_csv(data_path('failure_ledger.csv'))
    snf = fl[(fl['stage'] == 'geo') & (fl['reason_code'] == 'STREET_NOT_FOUND')].copy()
    if snf.empty:
        print('No STREET_NOT_FOUND rows in failure_ledger.csv')
        return

    tcl_names = pd.read_csv(data_path('tcl_street_names.csv'))
    thr.build_index_from_csv(legal_keys={
        tcl_highway_key(x) for x in tcl_names['linear_name_full_legal'].dropna()
    })
    legal_keys = getattr(thr, '_legal_keys', frozenset())

    parsed = pd.read_csv(data_path('parsed_successes.csv'))
    n_parsed = len(parsed)
    id_col = '_id' if '_id' in parsed.columns else 'row_id'
    merge_cols = [id_col, 'rule_type']
    for c in (
        'start_intersection', 'end_intersection', 'offset_intersection', 'terminus_street',
    ):
        if c in parsed.columns:
            merge_cols.append(c)
    merged = snf.merge(
        parsed[merge_cols],
        left_on='row_id',
        right_on=id_col,
        how='left',
    )

    rows = [analyze_row(merged.iloc[i], legal_keys, tcl_names) for i in range(len(merged))]
    out_df = pd.DataFrame(rows)
    out_df['highway'] = merged['highway'].values

    print(f'STREET_NOT_FOUND rows: {len(out_df)}')
    print(out_df['street_category'].value_counts().to_string())
    print('\nSuggested fix:')
    print(out_df['suggested_fix'].value_counts().to_string())
    print(f"\nResolvable with current logic: {(out_df['resolved_key_candidate'] != '').sum()}")

    out_path = data_path('street_failure_analysis.csv')
    out_df.to_csv(out_path, index=False)
    print(f'\nWrote {out_path}')

    summary = _summary_dict(out_df, n_parsed)
    summary_path = data_path('street_failure_summary.json')
    summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f'Wrote {summary_path}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Classify intersection failures: normalizer rule vs one-off alias vs skip."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

import tcl_graph as tg  # noqa: E402
from analyze_intersection_failures import categorize_cross  # noqa: E402
from intersection_normalize import apply_street_alias, normalize_intersection_street  # noqa: E402
from paths import data_path  # noqa: E402

MIN_DESC_HITS = 5
ONE_OFF_BYLAW_LIMIT = 5
MIN_TOKEN_LEN = 4
_GENERIC_TOKENS = frozenset({
    'ave', 'st', 'dr', 'rd', 'blvd', 'ct', 'pl', 'ln', 'trl', 'cres', 'ter', 'terr',
    'n', 's', 'e', 'w',
})


def load_descs() -> list[str]:
    path = data_path('tcl_intersections.geojson')
    with path.open(encoding='utf-8') as f:
        data = json.load(f)
    return [feat['properties']['INTERSECTION_DESC'].lower() for feat in data['features']]


def desc_hit_count(token: str, descs: list[str]) -> int:
    if not token or len(token) < 2:
        return 0
    return sum(1 for d in descs if token in d)


def candidate_tokens(bylaw_name: str, current_token: str) -> list[str]:
    """TCL token variants to try for a bylaw street name."""
    seen: set[str] = set()
    out: list[str] = []

    def add(t: str) -> None:
        t = t.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    add(current_token)
    add(apply_street_alias(bylaw_name))
    add(normalize_intersection_street(bylaw_name))

    base = str(bylaw_name).lower().strip().replace('.', '')
    add(base)
    for suffix in (' rd', ' dr', ' ave', ' st', ' terr', ' ter', ' cres'):
        if base.endswith(suffix.strip()):
            add(base[: -len(suffix.strip())])

    if ' terrace' in base or base.endswith(' terr'):
        add(re.sub(r'\bterr\b$', 'ter', normalize_intersection_street(bylaw_name)))
    if ' road' in base:
        add(normalize_intersection_street(bylaw_name).replace(' rd', ''))

    return out


def best_tcl_token(bylaw_name: str, current_token: str, descs: list[str]) -> tuple[str, int]:
    alias_tok = apply_street_alias(bylaw_name)
    alias_hits = desc_hit_count(alias_tok, descs)
    if alias_hits >= MIN_DESC_HITS and alias_tok not in _GENERIC_TOKENS:
        return alias_tok, alias_hits

    best, best_hits = '', 0
    for tok in candidate_tokens(bylaw_name, current_token):
        if tok in _GENERIC_TOKENS or len(tok) < MIN_TOKEN_LEN:
            continue
        hits = desc_hit_count(tok, descs)
        if hits > best_hits:
            best, best_hits = tok, hits
    return best, best_hits


def bylaw_cross_name(row: pd.Series) -> str:
    field = str(row.get('field') or '')
    if field == 'start' and pd.notna(row.get('start_intersection')):
        return str(row['start_intersection']).strip()
    if field == 'end' and pd.notna(row.get('end_intersection')):
        return str(row['end_intersection']).strip()
    if field == 'offset' and pd.notna(row.get('offset_intersection')):
        return str(row['offset_intersection']).strip()
    return str(row['cross']).strip()


def pair_recovery(
    highways: list[str],
    bylaw_name: str,
    tcl_token: str,
) -> tuple[int, int]:
    resolved = 0
    for hwy in highways:
        if tg.resolve_intersection_ids(hwy, bylaw_name):
            resolved += 1
    return resolved, len(highways)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--top', type=int, default=40, help='Max token groups to report')
    args = parser.parse_args()

    analysis_path = data_path('intersection_failure_analysis.csv')
    if not analysis_path.exists():
        raise SystemExit('Run analyze_intersection_failures.py first')

    df = pd.read_csv(analysis_path)
    scoped = df[
        (df['subcause'] == 'highway_only_in_tcl')
        & (df['cross_in_tcl'] == False)
        & (df['category'] == 'normal_street_name')
    ].copy()

    print(f'Scoped rows: {len(scoped)}')

    gdf = gpd.read_file(data_path('tcl_intersections.geojson'))
    tg.configure_intersections(gdf)
    descs = load_descs()

    # Group by bylaw cross name
    scoped['_bylaw_cross'] = scoped.apply(bylaw_cross_name, axis=1)
    groups: dict[str, list[pd.Series]] = defaultdict(list)
    for _, row in scoped.iterrows():
        groups[row['_bylaw_cross']].append(row)

    suggestions: list[dict] = []

    for bylaw_name, rows in sorted(
        groups.items(),
        key=lambda kv: -len(kv[1]),
    )[: args.top]:
        failure_rows = len(rows)
        current_token = apply_street_alias(bylaw_name)
        if current_token != normalize_intersection_street(bylaw_name):
            existing_alias = True
        else:
            existing_alias = bylaw_name.strip().lower() in _load_alias_keys()

        suggested, hits = best_tcl_token(bylaw_name, str(rows[0]['cross']), descs)
        highways = list({str(r['highway']) for r in rows})
        resolved, n_hwy = pair_recovery(highways, bylaw_name, suggested)

        # Classify
        if categorize_cross(bylaw_name) != 'normal_street_name':
            rec = 'skip'
            status = 'reject'
        elif "'" in bylaw_name and hits > 0 and suggested == normalize_intersection_street(bylaw_name):
            rec = 'skip'
            status = 'reject'
            note = 'fixed by apostrophe normalizer rule'
        elif hits < MIN_DESC_HITS:
            rec = 'skip'
            status = 'reject'
            note = f'no TCL token (>={MIN_DESC_HITS} hits)'
        elif len(groups) > 1 and _same_mismatch_pattern(bylaw_name, groups):
            rec = 'rule'
            status = 'manual'
            note = 'pattern may need normalizer rule'
        elif existing_alias and current_token == suggested:
            rec = 'skip'
            status = 'reject'
            note = 'alias already correct'
        else:
            bylaw_count = 1
            rec = 'alias' if bylaw_count <= ONE_OFF_BYLAW_LIMIT else 'rule'
            status = 'approve' if (
                rec == 'alias'
                and hits >= MIN_DESC_HITS
                and len(suggested) >= MIN_TOKEN_LEN
                and resolved > 0
                and suggested != current_token
            ) else 'manual'
            note = ''

        suggestions.append({
            'bylaw_name': bylaw_name,
            'failure_rows': failure_rows,
            'current_token': current_token,
            'suggested_token': suggested,
            'desc_hits': hits,
            'highways': n_hwy,
            'pairs_resolved': resolved,
            'recommendation': rec,
            'status': status,
            'note': note,
        })

    out_path = ROOT / 'data' / 'street_alias_suggestions.csv'
    with out_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                'bylaw_name', 'failure_rows', 'current_token', 'suggested_token',
                'desc_hits', 'highways', 'pairs_resolved',
                'recommendation', 'status', 'note',
            ],
        )
        writer.writeheader()
        writer.writerows(suggestions)

    print(f'\nWrote {out_path}')
    print('\n=== APPROVE (alias) ===')
    for s in suggestions:
        if s['status'] == 'approve':
            print(
                f"  {s['bylaw_name']!r} -> {s['suggested_token']!r} "
                f"({s['failure_rows']} rows, {s['pairs_resolved']}/{s['highways']} hwys)"
            )
    print('\n=== MANUAL REVIEW (top 10) ===')
    for s in suggestions:
        if s['status'] == 'manual':
            print(
                f"  [{s['recommendation']}] {s['bylaw_name']!r}: "
                f"{s['current_token']!r} -> {s['suggested_token']!r} ({s['note']})"
            )
            if sum(1 for x in suggestions if x['status'] == 'manual') >= 10:
                break


def _load_alias_keys() -> set[str]:
    path = data_path('street_aliases.csv')
    keys: set[str] = set()
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            k = (row.get('bylaw_name') or '').strip().lower()
            if k:
                keys.add(k)
    return keys


def _same_mismatch_pattern(bylaw_name: str, groups: dict) -> bool:
    del bylaw_name, groups
    return False


if __name__ == '__main__':
    main()

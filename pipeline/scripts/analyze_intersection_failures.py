#!/usr/bin/env python3
"""Analysis of INTERSECTION_NOT_FOUND rows in failure_ledger.csv."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import tcl_graph as tg  # noqa: E402
from intersection_normalize import apply_street_alias  # noqa: E402
from paths import data_path  # noqa: E402


def norm_production(name: str) -> str:
    return apply_street_alias(name)


# --- categorization ---
_END_RE = re.compile(
    r'\b(?:(?:north|south|east|west)(?:east|west|north|south)?)\s+end\s+of\b',
    re.I,
)
_POINT_RE = re.compile(r'^a point\b', re.I)


def categorize_cross(cross: str) -> str:
    if not cross or not str(cross).strip():
        return 'empty'
    c = str(cross).strip()
    cl = c.lower()
    if _END_RE.search(cl):
        return 'street_end_phrase'
    if _POINT_RE.match(cl) or (
        ' metres ' in cl and ('point' in cl or 'opposite' in cl)
    ):
        return 'point_metres_fragment'
    if 'leg of' in cl or 'north/south' in cl:
        return 'leg_phrase'
    if 'lane' in cl or re.search(r'\bln\b', cl) or 'ramp' in cl:
        return 'lane_description'
    if 'adjacent to' in cl:
        return 'adjacent_to'
    if 'intersection)' in cl or '(east' in cl or '(west' in cl:
        return 'parenthetical_intersection'
    if c.endswith(' and'):
        return 'trailing_and'
    if re.search(r'\bst\.\s', cl):
        return 'abbrev_with_period'
    if "'" in c:
        return 'apostrophe_name'
    if 'parkway' in cl and not re.search(
        r'parkway\s+\w+\s+(?:drive|dr|road|rd)\b', cl,
    ):
        return 'parkway_spelling'
    if re.search(r'\bgate\b', cl) and 'gateway' not in cl:
        return 'gate_spelling'
    if re.search(r'\blawn\b', cl):
        return 'lawn_spelling'
    if re.search(r'\bgardens\b', cl):
        return 'gardens_spelling'
    if 'expressway' in cl or 'gardiner' in cl or 'don valley' in cl:
        return 'major_roadway'
    if ' the ' in cl and cl.startswith('the '):
        return 'leading_the'
    return 'normal_street_name'


def attribution(category: str) -> str:
    if category in {
        'street_end_phrase', 'leg_phrase', 'point_metres_fragment',
        'trailing_and', 'adjacent_to', 'parenthetical_intersection',
    }:
        return 'new_rule_or_geometry'
    if category in {
        'abbrev_with_period', 'parkway_spelling', 'gate_spelling',
        'lawn_spelling', 'gardens_spelling', 'apostrophe_name',
    }:
        return 'normalization'
    if category == 'lane_description':
        return 'mixed'
    return 'investigate'


def load_intersection_descs_lower() -> list[str]:
    path = data_path('tcl_intersections.geojson')
    with path.open(encoding='utf-8') as f:
        data = json.load(f)
    return [feat['properties']['INTERSECTION_DESC'].lower() for feat in data['features']]


def init_tcl() -> dict[str, tg.StreetGraph]:
    """Load TCL layers and configure tcl_graph (production intersection matching)."""
    print('Loading tcl_intersections.geojson...')
    ix_gdf = gpd.read_file(data_path('tcl_intersections.geojson'))
    print('Loading tcl_streets.geojson...')
    st_gdf = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix_gdf)
    graphs = tg.build_street_graphs(st_gdf)
    print(f'  intersections: {len(ix_gdf)}, street graphs: {len(graphs)}')
    return graphs


def build_token_presence(
    names: list[str],
    descs: list[str],
    norm_fn=norm_production,
) -> dict[str, bool]:
    """For each unique normalized token, whether it appears in any INTERSECTION_DESC."""
    tokens = {norm_fn(n) for n in names if n and str(n).strip()}
    found = dict.fromkeys(tokens, False)
    if not tokens:
        return found
    for desc in descs:
        for token in tokens:
            if not found[token] and token in desc:
                found[token] = True
        if all(found.values()):
            break
    return found


@lru_cache(maxsize=65536)
def cached_resolve_pair(highway: str, cross: str) -> tuple[bool, int]:
    ids = tg.resolve_intersection_ids(highway, cross)
    return (len(ids) > 0, len(ids))


def production_pair_lookup(
    pairs: list[tuple[str, str]],
) -> tuple[list[bool], list[int]]:
    hits: list[bool] = []
    counts: list[int] = []
    for highway, cross in pairs:
        hit, n = cached_resolve_pair(str(highway), str(cross))
        hits.append(hit)
        counts.append(n)
    return hits, counts


def compute_subcause(
    highway: str,
    cross: str,
    hit_production: bool,
    cross_in_tcl: bool,
    highway_in_tcl: bool,
    highway_in_graph: bool,
) -> str:
    if hit_production:
        return 'resolved'
    cross_s = str(cross).strip()
    highway_s = str(highway).strip()
    cross_l = cross_s.lower()
    if re.search(r'\bleg\s+of\b', cross_l) or re.search(
        r'(?:north|south|east|west)/(?:north|south|east|west)', cross_l,
    ):
        return 'leg_phrase'
    if '/' in cross_s and 'leg of' not in cross_l:
        return 'slash_compound_cross'
    if cross_s.lower() == highway_s.lower():
        return 'failed_field_is_highway'
    if not highway_in_graph:
        return 'highway_not_in_street_graph'
    if cross_in_tcl:
        return 'cross_only_in_tcl'
    if highway_in_tcl:
        return 'highway_only_in_tcl'
    return 'neither_in_tcl'


def refine_attribution(row: pd.Series) -> str:
    if row['hit_production']:
        return 'resolved'
    cat = row['category']
    if cat in ('street_end_phrase', 'leg_phrase'):
        return 'new_rule_or_geometry'
    if cat == 'point_metres_fragment':
        return 'regex_misparse'
    if cat in (
        'abbrev_with_period', 'parkway_spelling', 'gate_spelling',
        'lawn_spelling', 'gardens_spelling', 'apostrophe_name',
    ):
        return 'normalization_unresolved'
    if cat == 'lane_description':
        return 'mixed'
    if cat == 'normal_street_name':
        return 'true_missing_or_complex'
    return row['attribution']


def _summary_dict(merged: pd.DataFrame, n_parsed: int) -> dict[str, Any]:
    n = len(merged)
    att = merged['attribution_final'].value_counts()
    sub = merged['subcause'].value_counts()
    true_miss = merged[merged['attribution_final'] == 'true_missing_or_complex']
    sub_true = true_miss['subcause'].value_counts() if len(true_miss) else pd.Series(dtype=int)

    top_pairs = (
        merged.groupby(['highway', 'cross'], dropna=False)
        .size()
        .sort_values(ascending=False)
        .head(20)
    )
    top_cross = merged['cross'].value_counts().head(20)

    return {
        'parsed_successes': n_parsed,
        'intersection_not_found': n,
        'pct_of_parsed': round(100 * n / n_parsed, 2) if n_parsed else 0,
        'hit_production_count': int(merged['hit_production'].sum()),
        'field_counts': merged['field'].value_counts().to_dict(),
        'category_counts': merged['category'].value_counts().to_dict(),
        'attribution_final_counts': att.to_dict(),
        'subcause_counts': sub.to_dict(),
        'subcause_within_true_missing': sub_true.to_dict(),
        'top_pairs': [
            {'highway': h, 'cross': c, 'count': int(cnt)}
            for (h, c), cnt in top_pairs.items()
        ],
        'top_cross': [
            {'cross': c, 'count': int(cnt)} for c, cnt in top_cross.items()
        ],
    }


def main() -> None:
    fl = pd.read_csv(data_path('failure_ledger.csv'))
    inter = fl[
        (fl['stage'] == 'geo') & (fl['reason_code'] == 'INTERSECTION_NOT_FOUND')
    ].copy()
    inter['field'] = inter['detail'].str.extract(
        r'^(start|end|offset)_intersection=', expand=False,
    )
    inter['cross'] = inter['detail'].str.replace(
        r'^(start|end|offset)_intersection=', '', regex=True,
    )

    parsed = pd.read_csv(data_path('parsed_successes.csv'))
    n_parsed = len(parsed)
    merged = inter.merge(
        parsed[
            [
                '_id', 'rule_type', 'Between',
                'start_intersection', 'end_intersection', 'offset_intersection',
            ]
        ],
        left_on='row_id',
        right_on='_id',
        how='left',
    )
    merged['category'] = merged['cross'].map(categorize_cross)
    merged['attribution'] = merged['category'].map(attribution)

    print('=== SUMMARY ===')
    print(f'parsed_successes: {n_parsed}')
    print(
        f'INTERSECTION_NOT_FOUND: {len(merged)} '
        f'({100 * len(merged) / n_parsed:.1f}% of parsed)',
    )
    print(f'field: {merged["field"].value_counts().to_dict()}')

    print('\n=== CATEGORIES ===')
    cat = merged['category'].value_counts()
    for k, v in cat.items():
        print(f'  {k}: {v} ({100 * v / len(merged):.1f}%)')

    print('\n=== RULE_TYPE x CATEGORY (top cats) ===')
    top_cats = cat.head(10).index.tolist()
    ct = pd.crosstab(merged['rule_type'], merged['category'])[top_cats]
    print(ct.to_string())

    print('\n=== TOP 20 CROSS ===')
    for cross, cnt in merged['cross'].value_counts().head(20).items():
        print(f'  {cnt:4d}  {str(cross)[:70]}')

    print('\n=== TOP 15 HIGHWAY ===')
    for hwy, cnt in merged['highway'].value_counts().head(15).items():
        print(f'  {cnt:4d}  {hwy}')

    print('\n=== TCL (production matching) ===')
    street_graphs = init_tcl()
    graph_keys = set(street_graphs.keys())

    pairs = list(zip(merged['highway'].astype(str), merged['cross'].astype(str)))
    print('Resolving highway × cross via tcl_graph.resolve_intersection_ids...')
    prod_hits, match_counts = production_pair_lookup(pairs)
    merged['hit_production'] = prod_hits
    merged['tcl_match_count'] = match_counts

    descs = load_intersection_descs_lower()
    print(f'INTERSECTION_DESC count: {len(descs)}')
    all_names = (
        merged['highway'].astype(str).tolist()
        + merged['cross'].astype(str).tolist()
    )
    token_presence = build_token_presence(all_names, descs)
    merged['cross_in_tcl'] = merged['cross'].map(
        lambda c: token_presence.get(norm_production(str(c)), False),
    )
    merged['highway_in_tcl'] = merged['highway'].map(
        lambda h: token_presence.get(norm_production(str(h)), False),
    )
    merged['highway_in_graph'] = merged['highway'].map(
        lambda h: str(h).strip().lower() in graph_keys,
    )

    merged['subcause'] = merged.apply(
        lambda row: compute_subcause(
            row['highway'],
            row['cross'],
            row['hit_production'],
            row['cross_in_tcl'],
            row['highway_in_tcl'],
            row['highway_in_graph'],
        ),
        axis=1,
    )
    merged['attribution_final'] = merged.apply(refine_attribution, axis=1)

    n_hit = int(merged['hit_production'].sum())
    print('\n=== RECOVERABILITY (production) ===')
    print(f'Pairs resolved via resolve_intersection_ids: {n_hit}')
    print(f'Still miss: {len(merged) - n_hit}')

    print('\n=== ATTRIBUTION (final) ===')
    att = merged['attribution_final'].value_counts()
    for k, v in att.items():
        print(f'  {k}: {v} ({100 * v / len(merged):.1f}%)')

    print('\n=== ATTRIBUTION x RULE_TYPE ===')
    att_rt = pd.crosstab(merged['attribution_final'], merged['rule_type'])
    print(att_rt.to_string())

    print('\n=== FIELD x ATTRIBUTION (final) ===')
    print(pd.crosstab(merged['field'], merged['attribution_final']).to_string())

    print('\n=== SUBCAUSE (all misses) ===')
    for k, v in merged['subcause'].value_counts().items():
        print(f'  {k}: {v} ({100 * v / len(merged):.1f}%)')

    true_miss = merged[merged['attribution_final'] == 'true_missing_or_complex']
    print(f'\n=== SUBCAUSE within true_missing_or_complex ({len(true_miss)} rows) ===')
    if len(true_miss):
        for k, v in true_miss['subcause'].value_counts().items():
            print(f'  {k}: {v} ({100 * v / len(true_miss):.1f}%)')

    print('\n=== TOP 20 (highway, cross) PAIRS ===')
    top_pairs = (
        merged.groupby(['highway', 'cross'], dropna=False)
        .size()
        .sort_values(ascending=False)
        .head(20)
    )
    for (hwy, cross), cnt in top_pairs.items():
        print(f'  {cnt:4d}  {hwy} × {str(cross)[:50]}')

    print('\n=== STILL MISSING BY CATEGORY ===')
    miss = merged[~merged['hit_production']]
    print(miss['category'].value_counts().head(15).to_string())

    out = ROOT / 'data' / 'intersection_failure_analysis.csv'
    merged.to_csv(out, index=False)
    print(f'\nWrote {out}')

    summary = _summary_dict(merged, n_parsed)
    summary_path = ROOT / 'data' / 'intersection_failure_summary.json'
    summary_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f'Wrote {summary_path}')


if __name__ == '__main__':
    main()

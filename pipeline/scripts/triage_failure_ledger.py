#!/usr/bin/env python3
"""Assign fix_tier to each failure_ledger.csv row → data/failure_triage.csv."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from analyze_intersection_failures import (  # noqa: E402
    attribution,
    categorize_cross,
)

from parking_pipeline.bylaw_text import preprocess_between  # noqa: E402
from parking_pipeline.failure_ledger import LEDGER_EXCLUDED_REASON_CODES  # noqa: E402
from parking_pipeline.paths import data_path  # noqa: E402

_FIELD_RE = re.compile(r'^(start|end|offset)_intersection=(.*)$', re.I)

# Tier order for sorting (lower = fix first).
TIER_ORDER = {
    'A_intentional': 0,
    'A_skipped': 1,
    'A_trivial': 2,
    'B_quick': 3,
    'C_medium': 4,
    'D_hard': 5,
}


def _extract_cross(detail: str) -> str:
    if not detail or not isinstance(detail, str):
        return ''
    m = _FIELD_RE.match(detail.strip())
    return m.group(2).strip() if m else ''


def _extract_field(detail: str) -> str:
    if not detail or not isinstance(detail, str):
        return ''
    m = _FIELD_RE.match(detail.strip())
    return m.group(1).lower() if m else ''


def _load_alias_names() -> set[str]:
    """Bylaw names suggested for alias that are not yet in street_aliases.csv."""
    path = data_path('street_alias_suggestions.csv')
    if not path.exists():
        return set()
    sug = pd.read_csv(path)
    if 'recommendation' not in sug.columns:
        return set()
    mask = sug['recommendation'].astype(str).str.lower() == 'alias'
    suggested = {str(n).strip() for n in sug.loc[mask, 'bylaw_name'] if str(n).strip()}

    alias_path = data_path('street_aliases.csv')
    if not alias_path.exists():
        return suggested
    applied = {
        str(n).strip().lower()
        for n in pd.read_csv(alias_path).get('bylaw_name', [])
        if str(n).strip()
    }
    return {n for n in suggested if n.strip().lower() not in applied}


def _parse_between_for_classify(row: pd.Series) -> str:
    """Text the parse stage matched against (ledger column or recomputed for old ledgers)."""
    stored = row.get('between_parsed_input', '')
    if stored is not None and not (isinstance(stored, float) and pd.isna(stored)):
        s = str(stored).strip()
        if s and s.lower() != 'nan':
            return s
    between = row.get('between', '')
    if row.get('stage') == 'parse' and row.get('reason_code') in (
        'PARSE_NO_MATCH', 'PARSE_INVALID',
    ):
        if between is None or (isinstance(between, float) and pd.isna(between)):
            return ''
        return preprocess_between(str(between).strip())
    if between is None or (isinstance(between, float) and pd.isna(between)):
        return ''
    return str(between).strip()


def _classify_parse_between(between: str) -> str:
    if between is None or (isinstance(between, float) and pd.isna(between)):
        return 'empty'
    b = str(between).strip()
    if not b or b.lower() == 'nan':
        return 'empty'
    bl = b.lower()
    if re.search(r'\blane\b', bl) and (
        'first' in bl or 'north of' in bl or 'south of' in bl or 'east of' in bl or 'west of' in bl
    ):
        return 'lane_first'
    if 'leg of' in bl or re.search(r'\b(?:north|south)/(?:north|south)\b', bl):
        return 'leg'
    if '(' in b:
        return 'parenthetical'
    if 'end of' in bl:
        return 'street_end'
    if 'opposite' in bl and ' and ' in bl:
        return 'point_opposite_and'
    if 'a point' in bl and ' and ' in bl:
        if 'intersection' in bl:
            return 'dual_intersection_paren'
        return 'point_and_street'
    if '/' in b and ' and ' not in bl:
        return 'slash_cross'
    if ' and ' in bl:
        return 'simple_and'
    if re.search(r'\b(?:north|south|east|west)\b', bl):
        return 'directional'
    return 'other_plain'


def _load_optional_analysis() -> tuple[
    pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None,
]:
    inter_path = data_path('intersection_failure_analysis.csv')
    geo_path = data_path('geometry_failure_analysis.csv')
    street_path = data_path('street_failure_analysis.csv')
    inter = pd.read_csv(inter_path) if inter_path.exists() else None
    if geo_path.exists() and geo_path.stat().st_size > 0:
        geo = pd.read_csv(geo_path)
        if geo.empty:
            geo = None
    else:
        geo = None
    street = pd.read_csv(street_path) if street_path.exists() else None
    return inter, geo, street


def _inter_cols(inter: pd.DataFrame) -> pd.DataFrame:
    keep = [
        'row_id', 'field', 'cross', 'category', 'attribution', 'attribution_final',
        'subcause', 'hit_production', 'rule_type',
    ]
    cols = [c for c in keep if c in inter.columns]
    out = inter[cols].copy()
    out['row_id'] = out['row_id'].astype(str)
    rename = {c: f'inter_{c}' if c != 'row_id' else c for c in cols if c != 'row_id'}
    return out.rename(columns=rename)


def _geo_cols(geo: pd.DataFrame) -> pd.DataFrame:
    keep = [
        'row_id', 'cause_category', 'attribution', 'fix_hint', 'rule_type',
        'between_category',
    ]
    cols = [c for c in keep if c in geo.columns]
    out = geo[cols].copy()
    out['row_id'] = out['row_id'].astype(str)
    rename = {c: f'geo_{c}' if c != 'row_id' else c for c in cols if c != 'row_id'}
    return out.rename(columns=rename)


def _street_cols(street: pd.DataFrame) -> pd.DataFrame:
    keep = [
        'row_id', 'street_category', 'subcause', 'suggested_fix', 'suggested_tier',
        'resolved_key_candidate', 'near_match_count', 'anchor_street',
    ]
    cols = [c for c in keep if c in street.columns]
    out = street[cols].copy()
    out['row_id'] = out['row_id'].astype(str)
    rename = {c: f'street_{c}' if c != 'row_id' else c for c in cols if c != 'row_id'}
    return out.rename(columns=rename)


def _attribution_final_inline(category: str, cross: str) -> str:
    if category in (
        'abbrev_with_period', 'parkway_spelling', 'gate_spelling',
        'lawn_spelling', 'gardens_spelling', 'apostrophe_name',
    ):
        return 'normalization_unresolved'
    if category == 'point_metres_fragment':
        return 'regex_misparse'
    if category in (
        'street_end_phrase', 'leg_phrase', 'trailing_and',
        'adjacent_to', 'parenthetical_intersection',
    ):
        return 'new_rule_or_geometry'
    if category == 'lane_description':
        return 'mixed'
    if '/' in str(cross):
        return 'slash_compound'
    if category == 'normal_street_name':
        return 'true_missing_or_complex'
    return attribution(category)


def assign_fix_tier(row: pd.Series, alias_names: set[str]) -> tuple[str, str, str]:
    """Return (fix_tier, fix_category, fix_hint)."""
    stage = str(row.get('stage', ''))
    reason = str(row.get('reason_code', ''))
    highway = str(row.get('highway', '') or '')
    detail = str(row.get('detail', '') or '')

    inter_attr = row.get('inter_attribution_final') or row.get('attribution_final')
    inter_cat = row.get('inter_category') or row.get('category')
    inter_sub = row.get('inter_subcause') or row.get('subcause')
    cross = row.get('inter_cross') or row.get('cross') or _extract_cross(detail)

    geo_attr = row.get('geo_attribution') or row.get('attribution')
    geo_hint = row.get('geo_fix_hint') or row.get('fix_hint')
    geo_cause = row.get('geo_cause_category') or row.get('cause_category')

    parse_cls = row.get('parse_between_class', '')

    if reason == 'ZERO_SPAN':
        return (
            'A_skipped',
            'zero_span_skip',
            'Parsed; no mappable curb segment (anchor equals terminus)',
        )

    if reason in ('PARSE_EMPTY_BETWEEN', 'PARSE_INVALID', 'SCHEDULE_EMPTY'):
        hints = {
            'PARSE_EMPTY_BETWEEN': 'Fill or drop rows with empty Between',
            'PARSE_INVALID': 'Fix anchor validation / spacing in parse_between',
            'SCHEDULE_EMPTY': 'Extend schedule parser for empty/malformed times',
        }
        return ('A_trivial', reason.lower(), hints[reason])

    if reason == 'INTERSECTION_NOT_FOUND':
        cat = str(inter_cat or categorize_cross(str(cross)))
        attr = str(inter_attr or _attribution_final_inline(cat, str(cross)))
        sub = str(inter_sub or '')

        if sub == 'slash_compound_cross' or '/' in str(cross):
            return (
                'A_trivial',
                'slash_compound_cross',
                'Split/normalize compound cross streets (A/B)',
            )
        if attr == 'normalization_unresolved':
            return (
                'A_trivial',
                f'normalizer:{cat}',
                'Extend intersection_normalize for this spelling class',
            )
        if attr == 'regex_misparse':
            return (
                'A_trivial',
                'regex_misparse',
                'Point/metre fragment in intersection field; fix parse field assignment',
            )

        for name in alias_names:
            if name and (name in str(cross) or name == highway):
                return (
                    'B_quick',
                    'street_alias',
                    f'Add or verify street_aliases.csv entry for {name!r}',
                )

        if attr == 'new_rule_or_geometry':
            return (
                'C_medium',
                f'intersection_rule:{cat}',
                'Add parse/geo rule for parenthetical, street-end, or leg phrasing',
            )
        if attr == 'mixed':
            return (
                'D_hard',
                'lane_description',
                'Lane-first or ramp Between grammar; needs new rule family',
            )
        if attr == 'true_missing_or_complex':
            sub_hint = sub.replace('_', ' ') if sub else 'cross not paired in TCL'
            return (
                'D_hard',
                f'true_missing:{sub or "unknown"}',
                f'TCL/data gap: {sub_hint}',
            )
        return (
            'D_hard',
            f'intersection:{attr}',
            'Review intersection match for this cross/highway pair',
        )

    if reason == 'GEOMETRY_ERROR':
        if geo_attr == 'valid_point_zone':
            return (
                'A_trivial',
                geo_cause or 'valid_point_zone',
                geo_hint or 'Treat anchor_equals_terminus as point zone',
            )
        if geo_attr == 'clamp_at_endpoint':
            return (
                'C_medium',
                geo_cause or 'clamp_at_endpoint',
                geo_hint or 'Emit point/buffer or extend centreline past clamp',
            )
        if geo_attr == 'centreline_geometry':
            return (
                'C_medium',
                geo_cause or 'centreline_geometry',
                geo_hint or 'Offset/intersection projections collapsed on centreline',
            )
        if geo_attr == 'investigate':
            return (
                'D_hard',
                geo_cause or 'geometry_investigate',
                geo_hint or 'Re-run analyze_geometry_failures.py for cause',
            )
        return (
            'C_medium',
            'geometry_zero_length',
            'Zero-length segment; run analyze_geometry_failures.py for cause_category',
        )

    if reason in ('PARSE_NO_MATCH', 'PARSE_INVALID'):
        cls = str(parse_cls or _classify_parse_between(_parse_between_for_classify(row)))
        if cls in (
            'parenthetical', 'street_end', 'point_opposite_and',
            'dual_intersection_paren', 'point_and_street',
        ):
            return (
                'C_medium',
                f'parse_pattern:{cls}',
                'Add Between regex in parse_between for this pattern class',
            )
        if cls in ('lane_first', 'leg'):
            return (
                'D_hard',
                f'parse_pattern:{cls}',
                'Lane/leg phrasing needs dedicated parse rule family',
            )
        if cls == 'simple_and':
            return (
                'C_medium',
                'parse_pattern:simple_and',
                'Two-clause Between (X and Y); may need dual_anchor or street-end rule',
            )
        return (
            'D_hard',
            f'parse_pattern:{cls}',
            'No pattern matched; inspect Between text manually',
        )

    if reason == 'STREET_NOT_FOUND':
        street_fix = str(row.get('street_suggested_fix') or row.get('suggested_fix') or '')
        street_cat = str(
            row.get('street_street_category') or row.get('street_category') or '',
        )
        resolved = str(
            row.get('street_resolved_key_candidate') or row.get('resolved_key_candidate') or '',
        )
        if street_cat == 'truly_absent_from_tcl':
            return (
                'D_hard',
                'street_not_in_tcl',
                'No TCL centreline; research rename or private street',
            )
        if street_fix == 'auto_highway_resolve':
            hint = f'Apply highway resolve → {resolved}' if resolved else 'Extend tcl_highway_resolve'
            return ('B_quick', 'street_resolve:auto', hint)
        if street_fix == 'lane_infer':
            anchor = str(row.get('street_anchor_street') or row.get('anchor_street') or '')
            hint = f'Lane/laneway inference near {anchor!r}' if anchor else 'Lane/laneway highway inference'
            return ('C_medium', 'street_resolve:lane', hint)
        if street_fix == 'manual_alias':
            return (
                'C_medium',
                'street_alias:needed',
                'Add verified entry to data/highway_aliases.csv',
            )
        return (
            'D_hard',
            'street_not_found',
            'Highway not in TCL (often Lane/leg/branch in Highway column)',
        )

    if reason == 'AMBIGUOUS_INTERSECTION':
        return (
            'D_hard',
            'ambiguous_intersection',
            'Multiple graph paths or qualifier disambiguation failed',
        )

    if reason == 'DISCONNECTED_BLOCK':
        return (
            'D_hard',
            'disconnected_block',
            'No graph path on highway; disjoint multi-fragment retry failed '
            '(offset intersection or true TCL gap)',
        )

    return (
        'D_hard',
        f'{stage}:{reason}'.lower(),
        'Unclassified failure; review row manually',
    )


def build_triage(
    ledger: pd.DataFrame,
    inter: pd.DataFrame | None,
    geo: pd.DataFrame | None,
    street: pd.DataFrame | None = None,
) -> pd.DataFrame:
    df = ledger.copy()
    df = df[~df['reason_code'].isin(LEDGER_EXCLUDED_REASON_CODES)].copy()
    df['row_id'] = df['row_id'].astype(str)

    if inter is not None:
        df = df.merge(_inter_cols(inter), on='row_id', how='left')
    else:
        mask = (df['stage'] == 'geo') & (df['reason_code'] == 'INTERSECTION_NOT_FOUND')
        df.loc[mask, 'inter_cross'] = df.loc[mask, 'detail'].map(_extract_cross)
        df.loc[mask, 'inter_field'] = df.loc[mask, 'detail'].map(_extract_field)
        df.loc[mask, 'inter_category'] = df.loc[mask, 'inter_cross'].map(categorize_cross)

    if geo is not None:
        df = df.merge(_geo_cols(geo), on='row_id', how='left')

    if street is not None:
        df = df.merge(_street_cols(street), on='row_id', how='left')

    parse_mask = df['reason_code'].isin(('PARSE_NO_MATCH', 'PARSE_INVALID'))
    df['parse_between_class'] = ''
    df.loc[parse_mask, 'parse_between_class'] = (
        df.loc[parse_mask].apply(
            lambda r: _classify_parse_between(_parse_between_for_classify(r)),
            axis=1,
        )
    )

    alias_names = _load_alias_names()
    tiers = df.apply(lambda r: assign_fix_tier(r, alias_names), axis=1, result_type='expand')
    df['fix_tier'] = tiers[0]
    df['fix_category'] = tiers[1]
    df['fix_hint'] = tiers[2]
    df['tier_rank'] = df['fix_tier'].map(TIER_ORDER)

    cols = [
        'row_id', 'stage', 'reason_code', 'fix_tier', 'tier_rank',
        'fix_category', 'fix_hint', 'detail', 'highway', 'between', 'between_parsed_input',
        'parse_between_class',
        'inter_field', 'inter_cross', 'inter_category', 'inter_attribution_final',
        'inter_subcause',
        'geo_cause_category', 'geo_attribution', 'geo_fix_hint',
        'street_street_category', 'street_subcause', 'street_suggested_fix',
        'street_resolved_key_candidate', 'street_anchor_street',
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ''
    return df.sort_values(['tier_rank', 'stage', 'reason_code', 'row_id'])[cols]


def _print_summary(df: pd.DataFrame) -> None:
    print('=== failure_triage summary ===')
    print(f'total rows: {len(df)}')
    print('\nby fix_tier:')
    for tier, n in df['fix_tier'].value_counts().sort_index(
        key=lambda s: s.map(lambda t: TIER_ORDER.get(t, 99)),
    ).items():
        print(f'  {tier}: {n}')
    print('\nby stage × fix_tier:')
    print(pd.crosstab(df['stage'], df['fix_tier']).to_string())
    print('\ntop fix_category (A/B tiers):')
    ab = df[df['fix_tier'].isin(('A_intentional', 'A_trivial', 'B_quick'))]
    for cat, n in ab['fix_category'].value_counts().head(15).items():
        print(f'  {n:4d}  {cat}')


def _summary_json(df: pd.DataFrame) -> dict[str, Any]:
    tier_counts = df['fix_tier'].value_counts().to_dict()
    by_stage = (
        df.groupby(['stage', 'fix_tier']).size().reset_index(name='count')
        .to_dict(orient='records')
    )
    return {
        'total_rows': len(df),
        'fix_tier_counts': tier_counts,
        'by_stage_and_tier': by_stage,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--no-analysis-join',
        action='store_true',
        help='Do not join intersection_failure_analysis / geometry_failure_analysis',
    )
    args = parser.parse_args()

    ledger_path = data_path('failure_ledger.csv')
    if not ledger_path.exists():
        raise SystemExit(f'Missing {ledger_path}; run pipeline stages first.')

    ledger = pd.read_csv(ledger_path)
    inter, geo, street = (None, None, None) if args.no_analysis_join else _load_optional_analysis()

    if inter is not None:
        print(f'Joined intersection_failure_analysis ({len(inter)} rows)')
    else:
        print('No intersection_failure_analysis.csv; using inline intersection categories')
    if geo is not None:
        print(f'Joined geometry_failure_analysis ({len(geo)} rows)')
    else:
        print('No geometry_failure_analysis.csv; geometry tiers use reason_code only')
    if street is not None:
        print(f'Joined street_failure_analysis ({len(street)} rows)')
    else:
        print('No street_failure_analysis.csv; STREET_NOT_FOUND uses default tier')

    triage = build_triage(ledger, inter, geo, street)
    out = data_path('failure_triage.csv')
    triage.to_csv(out, index=False)
    print(f'\nWrote {out}')

    summary_path = data_path('failure_triage_summary.json')
    summary_path.write_text(
        json.dumps(_summary_json(triage), indent=2),
        encoding='utf-8',
    )
    print(f'Wrote {summary_path}')
    _print_summary(triage)


if __name__ == '__main__':
    main()

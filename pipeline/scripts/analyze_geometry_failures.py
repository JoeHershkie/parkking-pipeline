#!/usr/bin/env python3
"""Analysis of GEOMETRY_ERROR (zero-length segment) rows in failure_ledger.csv."""
from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from parking_pipeline import geometry_engine as ge  # noqa: E402
from parking_pipeline.geo_indices import init_geo  # noqa: E402
from parking_pipeline.parse_format import PARSE_COLUMNS, row_to_parsed  # noqa: E402
from parking_pipeline.paths import data_path  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from analyze_intersection_failures import categorize_cross  # noqa: E402

COLLAPSE_TOL_M = 1e-3
PERP_HIGH_M = 15.0
SAME_POINT_M = 0.5

CAUSE_ATTRIBUTION: dict[str, tuple[str, str]] = {
    'block_projection_collapse': (
        'centreline_geometry',
        'Snap intersections to nearest on-line point or improve street chunk selection',
    ),
    'block_same_intersection_point': (
        'intersection_match',
        'Disambiguate intersection match; different names resolved to same TCL point',
    ),
    'block_short_centreline': (
        'centreline_geometry',
        'Street centreline shorter than block span; merge or extend TCL chunks',
    ),
    'perfect_offset_clamp_start': (
        'clamp_at_endpoint',
        'Offset extends past line start; extend centreline or emit point/buffer geometry',
    ),
    'perfect_offset_clamp_end': (
        'clamp_at_endpoint',
        'Offset extends past line end; extend centreline or emit point/buffer geometry',
    ),
    'relative_extension_clamp_end': (
        'clamp_at_endpoint',
        'Both offset points beyond line end; anchor at terminus with outward offsets',
    ),
    'relative_extension_clamp_start': (
        'clamp_at_endpoint',
        'Both offset points before line start; anchor at origin with outward offsets',
    ),
    'anchor_equals_terminus': (
        'valid_point_zone',
        'Start intersection equals computed terminus; treat as point zone or skip',
    ),
    'intersect_to_offset_collapse': (
        'centreline_geometry',
        'Start intersection projection equals offset anchor projection',
    ),
    'offset_to_intersect_collapse': (
        'clamp_at_endpoint',
        'Signed offset from anchor lands at same projection as end intersection',
    ),
    'dual_anchor_converge': (
        'centreline_geometry',
        'Two independent offset endpoints converge to one centreline distance',
    ),
    'parenthetical_block_collapse': (
        'centreline_geometry',
        'Parenthetical start and end collapse on centreline projection',
    ),
    'parenthetical_end_block_collapse': (
        'centreline_geometry',
        'Parenthetical end and start collapse on centreline projection',
    ),
    'parenthetical_dual_block_collapse': (
        'centreline_geometry',
        'Dual parenthetical start and end collapse on centreline projection',
    ),
    'parenthetical_to_terminus_collapse': (
        'valid_point_zone',
        'Parenthetical start equals computed terminus distance',
    ),
    'intersect_extension_collapse': (
        'clamp_at_endpoint',
        'Intersection extension offset collapses to anchor',
    ),
    'unexpected_nonzero': (
        'investigate',
        'Recomputed d0/d1 differ by more than tolerance; debug slice logic',
    ),
    'intersection_missing': (
        'investigate',
        'Intersection lookup failed during recompute but row logged GEOMETRY_ERROR',
    ),
    'no_street_line': (
        'investigate',
        'No TCL centreline for highway during recompute',
    ),
    'unknown_rule': (
        'investigate',
        'Unhandled rule_type during classification',
    ),
}


def _match_count(highway: str, cross: str | None) -> int | None:
    if not cross or not str(cross).strip():
        return None
    return int(ge._intersection_mask(highway, str(cross)).sum())


def _perp_m(highway: str, cross: str | None, line_m) -> float | None:
    if line_m is None or not cross:
        return None
    pt = ge._intersection_point_meters(highway, str(cross))
    if pt is None:
        return None
    return float(line_m.distance(pt))


def _point_sep_m(highway: str, cross_a: str | None, cross_b: str | None) -> float | None:
    if not cross_a or not cross_b:
        return None
    p0 = ge._intersection_point_meters(highway, str(cross_a))
    p1 = ge._intersection_point_meters(highway, str(cross_b))
    if p0 is None or p1 is None:
        return None
    return float(p0.distance(p1))


def _collapsed(d0: float | None, d1: float | None) -> bool:
    if d0 is None or d1 is None:
        return False
    return abs(d0 - d1) < COLLAPSE_TOL_M


def _block_subcause(
    start_perp: float | None,
    end_perp: float | None,
    start_matches: int | None,
    end_matches: int | None,
    line_len: float | None,
) -> str:
    parts: list[str] = []
    if start_matches is not None and start_matches > 1:
        parts.append('ambiguous_start_match')
    if end_matches is not None and end_matches > 1:
        parts.append('ambiguous_end_match')
    if (
        start_perp is not None
        and end_perp is not None
        and start_perp > PERP_HIGH_M
        and end_perp > PERP_HIGH_M
    ):
        parts.append('high_perp_both')
    elif start_perp is not None and start_perp > PERP_HIGH_M:
        parts.append('high_perp_start')
    elif end_perp is not None and end_perp > PERP_HIGH_M:
        parts.append('high_perp_end')
    if line_len is not None and line_len < 50:
        parts.append('short_centreline')
    return '|'.join(parts) if parts else 'projection_same_dist'


def _clamp_cause(raw_d0: float, raw_d1: float, d0: float, d1: float, line_len: float) -> str:
    if raw_d0 != d0 and raw_d1 != d1:
        if d0 < COLLAPSE_TOL_M and d1 < COLLAPSE_TOL_M:
            return 'relative_extension_clamp_start'
        if abs(d0 - line_len) < COLLAPSE_TOL_M and abs(d1 - line_len) < COLLAPSE_TOL_M:
            return 'relative_extension_clamp_end'
    if d0 < COLLAPSE_TOL_M and d1 < COLLAPSE_TOL_M and (raw_d0 < 0 or raw_d1 < 0):
        return 'perfect_offset_clamp_start'
    if (
        abs(d0 - line_len) < COLLAPSE_TOL_M
        and abs(d1 - line_len) < COLLAPSE_TOL_M
        and (raw_d0 > line_len or raw_d1 > line_len)
    ):
        return 'perfect_offset_clamp_end'
    if d0 < COLLAPSE_TOL_M and d1 < COLLAPSE_TOL_M:
        return 'perfect_offset_clamp_start'
    if abs(d0 - line_len) < COLLAPSE_TOL_M and abs(d1 - line_len) < COLLAPSE_TOL_M:
        return 'perfect_offset_clamp_end'
    return 'relative_extension_clamp_end'


def _cell(row: pd.Series, key: str, default: Any = '') -> Any:
    val = row[key] if key in row.index else default
    if isinstance(val, pd.Series):
        val = val.iloc[0]
    if pd.isna(val):
        return default
    return val


def diagnose_row(row: pd.Series) -> dict[str, Any]:
    highway = str(_cell(row, 'Highway'))
    parsed = row_to_parsed(row)
    rt = _cell(row, 'rule_type') or parsed.get('rule_type')
    if rt:
        parsed['rule_type'] = str(rt).strip()
    rt = parsed.get('rule_type')

    out: dict[str, Any] = {
        'row_id': _cell(row, '_id') or _cell(row, 'row_id'),
        'rule_type': rt,
        'Between': _cell(row, 'Between'),
        'highway': highway,
        'cause_category': 'unknown_rule',
        'attribution': 'investigate',
        'fix_hint': '',
        'subcause': '',
        'd0': None,
        'd1': None,
        'delta_m': None,
        'line_length_m': None,
        'raw_d0': None,
        'raw_d1': None,
        'clamp_flag': False,
        'start_perp_m': None,
        'end_perp_m': None,
        'start_end_sep_m': None,
        'intersection_match_count_start': None,
        'intersection_match_count_end': None,
    }
    for col in PARSE_COLUMNS:
        if col in row.index and col not in out:
            out[col] = _cell(row, col)

    line_m = ge.get_street_line_meters(highway)
    if line_m is None:
        out['cause_category'] = 'no_street_line'
        out['attribution'], out['fix_hint'] = CAUSE_ATTRIBUTION['no_street_line']
        return out

    out['line_length_m'] = float(line_m.length)
    L = out['line_length_m']

    def finish(d0, d1, cause: str, **extra: Any) -> dict[str, Any]:
        out['d0'] = d0
        out['d1'] = d1
        if d0 is not None and d1 is not None:
            out['delta_m'] = abs(d0 - d1)
        out['cause_category'] = cause
        out.update(extra)
        attr, hint = CAUSE_ATTRIBUTION.get(cause, ('investigate', 'Review manually'))
        out['attribution'] = attr
        out['fix_hint'] = hint
        return out

    if rt == 'block':
        start = parsed.get('start_intersection')
        end = parsed.get('end_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, end)
        out['start_perp_m'] = _perp_m(highway, start, line_m)
        out['end_perp_m'] = _perp_m(highway, end, line_m)
        out['start_end_sep_m'] = _point_sep_m(highway, start, end)

        d0, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        d1, e1 = ge.intersection_dist_on_street(highway, end, line_m)
        if e0 or e1:
            return finish(d0, d1, 'intersection_missing')

        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')

        sep = out['start_end_sep_m']
        if sep is not None and sep < SAME_POINT_M:
            cause = 'block_same_intersection_point'
        elif L < 50:
            cause = 'block_short_centreline'
        else:
            cause = 'block_projection_collapse'
        out['subcause'] = _block_subcause(
            out['start_perp_m'],
            out['end_perp_m'],
            out['intersection_match_count_start'],
            out['intersection_match_count_end'],
            L,
        )
        return finish(d0, d1, cause)

    if rt == 'block_to_terminus':
        start = parsed.get('start_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['start_perp_m'] = _perp_m(highway, start, line_m)
        d0, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        if e0:
            return finish(None, None, 'intersection_missing')
        d1 = ge._terminus_dist_on_line(line_m, parsed.get('terminus_direction', ''))
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'anchor_equals_terminus')

    if rt == 'parenthetical_block':
        start = parsed.get('start_intersection')
        end = parsed.get('end_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, end)
        out['start_perp_m'] = _perp_m(highway, start, line_m)
        out['end_perp_m'] = _perp_m(highway, end, line_m)
        d0, e0 = ge.intersection_dist_with_qualifier(
            highway, start, line_m, parsed.get('start_intersection_qualifier'),
        )
        d1, e1 = ge.intersection_dist_on_street(highway, end, line_m)
        if e0 or e1:
            return finish(d0, d1, 'intersection_missing')
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'parenthetical_block_collapse')

    if rt == 'parenthetical_end_block':
        start = parsed.get('start_intersection')
        end = parsed.get('end_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, end)
        d0, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        d1, e1 = ge.intersection_dist_with_qualifier(
            highway, end, line_m, parsed.get('end_intersection_qualifier'),
        )
        if e0 or e1:
            return finish(d0, d1, 'intersection_missing')
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'parenthetical_end_block_collapse')

    if rt == 'parenthetical_dual_block':
        start = parsed.get('start_intersection')
        end = parsed.get('end_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, end)
        out['start_perp_m'] = _perp_m(highway, start, line_m)
        out['end_perp_m'] = _perp_m(highway, end, line_m)
        d0, e0 = ge.intersection_dist_with_qualifier(
            highway, start, line_m, parsed.get('start_intersection_qualifier'),
        )
        d1, e1 = ge.intersection_dist_with_qualifier(
            highway, end, line_m, parsed.get('end_intersection_qualifier'),
        )
        if e0 or e1:
            return finish(d0, d1, 'intersection_missing')
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'parenthetical_dual_block_collapse')

    if rt == 'parenthetical_to_terminus':
        start = parsed.get('start_intersection')
        d0, e0 = ge.intersection_dist_with_qualifier(
            highway, start, line_m, parsed.get('start_intersection_qualifier'),
        )
        if e0:
            return finish(None, None, 'intersection_missing')
        d1 = ge._terminus_dist_on_line(line_m, parsed.get('terminus_direction', ''))
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'parenthetical_to_terminus_collapse')

    if rt in ('perfect_offset', 'intersect_extension'):
        start = parsed.get('start_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['start_perp_m'] = _perp_m(highway, start, line_m)
        d0, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        if e0:
            return finish(None, None, 'intersection_missing')
        dist = float(parsed.get('distance', 0))
        direction = parsed.get('direction', '')
        raw_d1 = d0 + ge.offset_sign(line_m, d0, direction) * dist
        d1 = ge.signed_offset_dist(line_m, d0, dist, direction)
        out['raw_d0'] = d0
        out['raw_d1'] = raw_d1
        out['clamp_flag'] = raw_d1 != d1 or (rt == 'relative_extension')
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        if rt == 'intersect_extension':
            return finish(d0, d1, 'intersect_extension_collapse', clamp_flag=raw_d1 != d1)
        if d0 < COLLAPSE_TOL_M and raw_d1 < 0:
            cause = 'perfect_offset_clamp_start'
        elif abs(d0 - L) < COLLAPSE_TOL_M and raw_d1 > L:
            cause = 'perfect_offset_clamp_end'
        elif d0 < COLLAPSE_TOL_M:
            cause = 'perfect_offset_clamp_start'
        else:
            cause = 'perfect_offset_clamp_end'
        return finish(d0, d1, cause, clamp_flag=True)

    if rt == 'intersect_to_offset':
        start = parsed.get('start_intersection')
        offset = parsed.get('offset_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, offset)
        d0, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        anchor, e1 = ge.intersection_dist_on_street(highway, offset, line_m)
        if e0 or e1:
            return finish(d0, anchor, 'intersection_missing')
        dist = float(parsed.get('distance', 0))
        direction = parsed.get('direction', '')
        raw_d1 = anchor + ge.offset_sign(line_m, anchor, direction) * dist
        d1 = ge.signed_offset_dist(line_m, anchor, dist, direction)
        out['raw_d0'] = d0
        out['raw_d1'] = raw_d1
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'intersect_to_offset_collapse')

    if rt == 'offset_to_intersect':
        start = parsed.get('start_intersection')
        end = parsed.get('end_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, end)
        anchor, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        if e0:
            return finish(None, None, 'intersection_missing')
        dist = float(parsed.get('distance', 0))
        direction = parsed.get('direction', '')
        raw_d0 = anchor + ge.offset_sign(line_m, anchor, direction) * dist
        d0 = ge.signed_offset_dist(line_m, anchor, dist, direction)
        d1, e1 = ge.intersection_dist_on_street(highway, end, line_m)
        if e1:
            return finish(d0, None, 'intersection_missing')
        out['raw_d0'] = raw_d0
        out['raw_d1'] = d1
        out['clamp_flag'] = raw_d0 != d0
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'offset_to_intersect_collapse', clamp_flag=out['clamp_flag'])

    if rt == 'relative_extension':
        start = parsed.get('start_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        base, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        if e0:
            return finish(None, None, 'intersection_missing')
        dist1 = float(parsed.get('dist1', 0))
        dist2 = float(parsed.get('dist2', 0))
        dir1 = parsed.get('dir1', '')
        sign = ge.offset_sign(line_m, base, dir1)
        raw_d0 = base + sign * dist1
        raw_d1 = base + sign * (dist1 + dist2)
        d0 = ge._clamp_dist(line_m, raw_d0)
        d1 = ge._clamp_dist(line_m, raw_d1)
        out['raw_d0'] = raw_d0
        out['raw_d1'] = raw_d1
        out['clamp_flag'] = raw_d0 != d0 or raw_d1 != d1
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        cause = _clamp_cause(raw_d0, raw_d1, d0, d1, L)
        return finish(d0, d1, cause, clamp_flag=True)

    if rt == 'dual_anchor':
        start = parsed.get('start_intersection')
        end = parsed.get('end_intersection')
        out['intersection_match_count_start'] = _match_count(highway, start)
        out['intersection_match_count_end'] = _match_count(highway, end)
        a0, e0 = ge.intersection_dist_on_street(highway, start, line_m)
        a1, e1 = ge.intersection_dist_on_street(highway, end, line_m)
        if e0 or e1:
            return finish(None, None, 'intersection_missing')
        d0 = ge.signed_offset_dist(
            line_m, a0, float(parsed.get('dist1', 0)), parsed.get('dir1', ''),
        )
        d1 = ge.signed_offset_dist(
            line_m, a1, float(parsed.get('dist2', 0)), parsed.get('dir2', ''),
        )
        if not _collapsed(d0, d1):
            return finish(d0, d1, 'unexpected_nonzero')
        return finish(d0, d1, 'dual_anchor_converge')

    return out


def _print_exemplars(df: pd.DataFrame, cause: str, n: int = 5) -> None:
    subset = df[df['cause_category'] == cause].head(n)
    if subset.empty:
        return
    print(f'\n--- Exemplars: {cause} ---')
    for _, r in subset.iterrows():
        sub = f' [{r["subcause"]}]' if r.get('subcause') else ''
        print(
            f'  id={r["row_id"]} {r["highway"]}: {str(r["Between"])[:72]}{sub}',
        )


def main() -> None:
    init_geo()
    fl = pd.read_csv(data_path('failure_ledger.csv'))
    geo = fl[(fl['stage'] == 'geo') & (fl['reason_code'] == 'GEOMETRY_ERROR')].copy()

    parsed = pd.read_csv(data_path('parsed_successes.csv'))
    n_parsed = len(parsed)

    parse_cols = ['_id', 'Highway', 'Between', 'rule_type', *PARSE_COLUMNS]
    parse_cols = [c for c in parse_cols if c in parsed.columns]
    merged = geo.merge(
        parsed[parse_cols],
        left_on='row_id',
        right_on='_id',
        how='left',
    )
    merged = merged.loc[:, ~merged.columns.duplicated()]

    print('=== SUMMARY ===')
    print(f'parsed_successes: {n_parsed}')
    print(f'GEOMETRY_ERROR: {len(merged)} ({100 * len(merged) / n_parsed:.1f}% of parsed)')
    print(f'detail values: {merged["detail"].value_counts().to_dict()}')

    print('\n=== DIAGNOSING ROWS ===')
    if merged.empty:
        df = pd.DataFrame()
        print('No GEOMETRY_ERROR rows to diagnose.')
    else:
        records = [diagnose_row(row) for _, row in merged.iterrows()]
        df = pd.DataFrame(records)
        df = df.loc[:, ~df.columns.duplicated()]

    if df.empty:
        out = data_path('geometry_failure_analysis.csv')
        pd.DataFrame(columns=[
            'row_id', 'cause_category', 'attribution', 'fix_hint', 'rule_type',
            'delta_m', 'between_category',
        ]).to_csv(out, index=False)
        print(f'\nWrote empty {out}')
        return

    # Sanity check
    bad_delta = df[df['delta_m'].notna() & (df['delta_m'] >= COLLAPSE_TOL_M)]
    if not bad_delta.empty:
        print(f'WARNING: {len(bad_delta)} rows with delta_m >= {COLLAPSE_TOL_M}')
        print(bad_delta[['row_id', 'rule_type', 'delta_m', 'cause_category']].head(10))
    else:
        print(f'All {len(df)} rows confirmed delta_m < {COLLAPSE_TOL_M}')

    print('\n=== CAUSE CATEGORIES ===')
    cat = df['cause_category'].value_counts()
    for k, v in cat.items():
        print(f'  {k}: {v} ({100 * v / len(df):.1f}%)')

    print('\n=== RULE_TYPE x CAUSE (top causes) ===')
    top_causes = cat.head(10).index.tolist()
    ct = pd.crosstab(df['rule_type'], df['cause_category'])[top_causes]
    print(ct.to_string())

    print('\n=== ATTRIBUTION (fix priority) ===')
    att = df['attribution'].value_counts()
    for k, v in att.items():
        print(f'  {k}: {v} ({100 * v / len(df):.1f}%)')

    print('\n=== BLOCK PROJECTION SUBCAUSES ===')
    block = df[df['cause_category'] == 'block_projection_collapse']
    if not block.empty:
        sub = block['subcause'].value_counts()
        for k, v in sub.head(12).items():
            print(f'  {k}: {v} ({100 * v / len(block):.1f}% of block_projection)')

    print('\n=== BETWEEN PATTERNS (categorize_cross) ===')
    df['between_category'] = df['Between'].astype(str).map(categorize_cross)
    bc = df['between_category'].value_counts()
    for k, v in bc.head(12).items():
        print(f'  {k}: {v} ({100 * v / len(df):.1f}%)')

    print('\n=== TOP 20 BETWEEN ===')
    for between, cnt in df['Between'].value_counts().head(20).items():
        print(f'  {cnt:4d}  {str(between)[:70]}')

    print('\n=== TOP 15 HIGHWAY ===')
    for hwy, cnt in df['highway'].value_counts().head(15).items():
        print(f'  {cnt:4d}  {hwy}')

    for cause in cat.head(6).index:
        _print_exemplars(df, cause)

    out = data_path('geometry_failure_analysis.csv')
    df.to_csv(out, index=False)
    print(f'\nWrote {out}')


if __name__ == '__main__':
    main()

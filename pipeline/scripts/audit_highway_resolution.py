#!/usr/bin/env python3
"""Audit how often enhanced highway resolution changes keys on parsed rows."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from parking_pipeline.paths import data_path  # noqa: E402
from parking_pipeline import tcl_highway_resolve as thr  # noqa: E402
from parking_pipeline.tcl_highway_key import tcl_highway_key  # noqa: E402


def _legacy_key(highway: str) -> str:
    """Pre-enhancement behaviour: strip leg parens + tcl_highway_key only."""
    return tcl_highway_key(thr.strip_highway_leg_parenthetical(highway))


def main() -> None:
    parsed = pd.read_csv(data_path('parsed_successes.csv'))
    tcl = pd.read_csv(data_path('tcl_street_names.csv'))
    thr.build_index_from_csv(legal_keys={
        tcl_highway_key(x) for x in tcl['linear_name_full_legal'].dropna()
    })
    legal = getattr(thr, '_legal_keys', frozenset())

    changed = 0
    newly_in_graph = 0
    collisions: dict[str, list[str]] = {}

    for _, row in parsed.iterrows():
        highway = str(row.get('Highway', '') or '')
        if not highway.strip():
            continue
        old = _legacy_key(highway)
        new = thr.resolve_tcl_highway(highway)
        if old != new:
            changed += 1
        if old not in legal and new in legal:
            newly_in_graph += 1
        if new in legal:
            collisions.setdefault(new, []).append(highway)

    multi = {k: v for k, v in collisions.items() if len(set(v)) > 1}
    summary = {
        'parsed_rows': len(parsed),
        'keys_changed': changed,
        'newly_resolved_to_graph': newly_in_graph,
        'multi_bylaw_same_legal': len(multi),
    }
    out = data_path('highway_resolution_audit.json')
    out.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print(f'Wrote {out}')


if __name__ == '__main__':
    main()

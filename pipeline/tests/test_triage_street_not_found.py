"""Triage tiers for STREET_NOT_FOUND with street_failure_analysis join."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from triage_failure_ledger import assign_fix_tier, build_triage  # noqa: E402


def test_street_not_found_auto_tier() -> None:
    ledger = pd.DataFrame([{
        'row_id': '1',
        'stage': 'geo',
        'reason_code': 'STREET_NOT_FOUND',
        'detail': 'Beaconsfied Avenue',
        'highway': 'Beaconsfied Avenue',
        'between': 'X and Y',
        'between_parsed_input': '',
    }])
    street = pd.DataFrame([{
        'row_id': '1',
        'street_category': 'plain_name',
        'subcause': 'typo_ed1_unique',
        'suggested_fix': 'auto_highway_resolve',
        'suggested_tier': 'B_quick',
        'resolved_key_candidate': 'beaconsfield avenue',
    }])
    triage = build_triage(ledger, None, None, street)
    assert triage.iloc[0]['fix_tier'] == 'B_quick'
    assert triage.iloc[0]['fix_category'] == 'street_resolve:auto'


def test_street_not_found_lane_infer() -> None:
    row = pd.Series({
        'stage': 'geo',
        'reason_code': 'STREET_NOT_FOUND',
        'highway': 'Lane first north of Bloor Street West',
        'detail': 'Lane first north of Bloor Street West',
        'between': 'Royal York Road and a point 50 metres west',
        'street_suggested_fix': 'lane_infer',
        'street_street_category': 'lane_position_in_highway',
        'street_resolved_key_candidate': 'ln n bloor e royal york',
        'street_anchor_street': 'Bloor Street West',
    })
    tier, cat, hint = assign_fix_tier(row, set())
    assert tier == 'C_medium'
    assert cat == 'street_resolve:lane'
    assert 'Bloor' in hint


def test_street_not_found_truly_absent() -> None:
    row = pd.Series({
        'stage': 'geo',
        'reason_code': 'STREET_NOT_FOUND',
        'highway': 'Salvation Square',
        'detail': 'Salvation Square',
        'between': '',
        'street_suggested_fix': 'manual_alias',
        'street_street_category': 'truly_absent_from_tcl',
    })
    tier, cat, _ = assign_fix_tier(row, set())
    assert tier == 'D_hard'
    assert cat == 'street_not_in_tcl'

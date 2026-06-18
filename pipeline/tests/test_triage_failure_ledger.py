"""Tests for failure triage filtering."""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from triage_failure_ledger import build_triage  # noqa: E402


def test_build_triage_excludes_duplicate_rule() -> None:
    ledger = pd.DataFrame([
        {
            'row_id': '1',
            'stage': 'clean',
            'reason_code': 'DUPLICATE_RULE',
            'detail': 'dup',
            'highway': 'H',
            'between': 'B',
            'between_parsed_input': '',
        },
        {
            'row_id': '2',
            'stage': 'parse',
            'reason_code': 'PARSE_NO_MATCH',
            'detail': 'x',
            'highway': 'H',
            'between': 'B',
            'between_parsed_input': '',
        },
    ])
    triage = build_triage(ledger, None, None)
    assert len(triage) == 1
    assert triage.iloc[0]['row_id'] == '2'

"""Regression: parse_between rule_type distribution on sample cohort."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

from parking_pipeline.bylaw_text import preprocess_between
from parking_pipeline.parse_between import parse_between
from parking_pipeline.paths import DATA_DIR

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'parse_rule_distribution.json'
SAMPLE_CSV = DATA_DIR / 'samples' / 'clean_parking_targets.csv'


def _rule_type_counts() -> dict[str, int]:
    df = pd.read_csv(SAMPLE_CSV)
    counts: Counter[str] = Counter()
    for between in df['Between']:
        text = preprocess_between(str(between).strip())
        parsed = parse_between(text)
        key = parsed['rule_type'] if parsed else '__no_match__'
        counts[key] += 1
    return dict(sorted(counts.items()))


def test_sample_cohort_rule_type_distribution_matches_baseline() -> None:
    expected = json.loads(FIXTURE.read_text(encoding='utf-8'))
    actual = _rule_type_counts()
    assert actual == expected, (
        f'rule_type distribution changed; update {FIXTURE.name} if intentional:\n'
        f'  expected: {expected}\n'
        f'  actual:   {actual}'
    )

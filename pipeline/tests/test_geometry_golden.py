"""Golden-output regression for parse -> resolve -> geo on the sample cohort."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from geometry_golden_util import (
    DEFAULT_SAMPLE_CSV,
    collect_geometry_golden_records,
    load_geometry_golden,
)

FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'geometry_golden.json'


@pytest.fixture(scope='module')
def golden_payload() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip('geometry_golden.json not found; run scripts/build_geometry_golden.py')
    return load_geometry_golden(FIXTURE_PATH)


@pytest.fixture(scope='module')
def current_records() -> list[dict]:
    if not DEFAULT_SAMPLE_CSV.exists():
        pytest.skip('sample clean_parking_targets.csv missing')
    return collect_geometry_golden_records(DEFAULT_SAMPLE_CSV)


def test_geometry_golden_matches_fixture(
    golden_payload: dict,
    current_records: list[dict],
) -> None:
    expected = golden_payload['records']
    assert len(current_records) == len(expected), (
        f'row count changed: expected {len(expected)}, got {len(current_records)}'
    )

    mismatches: list[str] = []
    for actual, exp in zip(current_records, expected, strict=True):
        row_id = actual['_id']
        if actual == exp:
            continue
        mismatches.append(
            f'{row_id}: expected {json.dumps(exp, sort_keys=True)} '
            f'!= actual {json.dumps(actual, sort_keys=True)}'
        )

    assert not mismatches, 'geometry golden mismatch:\n' + '\n'.join(mismatches[:5])


def test_geometry_golden_exercises_geo_slice(current_records: list[dict]) -> None:
    geo_rows = [record for record in current_records if record.get('stage') == 'geo']
    assert len(geo_rows) >= 5, 'expected at least 5 rows to reach geo stage on sample cohort'
    successes = [record for record in geo_rows if record.get('reason_code') is None]
    assert len(successes) >= 4, 'expected at least 4 successful geo slices on sample cohort'

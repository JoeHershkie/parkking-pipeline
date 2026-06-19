"""Parity tests for shared schedule_corpus.json (Python overlaps_membership)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parking_pipeline.schedule_format import overlaps_membership

CORPUS_PATH = Path(__file__).resolve().parent / 'fixtures' / 'schedule_corpus.json'


def _load_corpus() -> list[dict]:
    with CORPUS_PATH.open(encoding='utf-8') as f:
        data = json.load(f)
    return data['cases']


@pytest.mark.parametrize('case', _load_corpus(), ids=lambda c: c['id'])
def test_schedule_corpus_membership(case: dict) -> None:
    schedule = case['schedule']
    slot = case['slot']
    expected = case['expected']
    assert overlaps_membership(schedule, slot) == expected

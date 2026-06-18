"""Tests for clean_data helpers."""

from pathlib import Path

import pandas as pd

from parking_pipeline.clean_data import deduplicate_rules, prohibited_times  # noqa: E402


def test_prohibited_times_anytime_from_between() -> None:
    fields = {
        'Prohibited Times and/or Days': None,
        'Times and/or Days': None,
        'Between': 'Southport Street and Windermere Avenue Anytime',
    }
    assert prohibited_times(fields) == 'Anytime'


def test_deduplicate_does_not_write_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr('parking_pipeline.failure_ledger.data_path', lambda name: tmp_path / name)
    df = pd.DataFrame([
        {
            '_id': 10,
            'Highway': 'Main St',
            'Side': 'north',
            'Between': 'A and B',
            'Prohibited Times and/or Days': 'Anytime',
            'Maximum Period Permitted': '',
            'schedule_category': 'no_parking',
        },
        {
            '_id': 20,
            'Highway': 'Main St',
            'Side': 'north',
            'Between': 'A and B',
            'Prohibited Times and/or Days': 'Anytime',
            'Maximum Period Permitted': '',
            'schedule_category': 'no_parking',
        },
    ])
    kept, dropped = deduplicate_rules(df)
    assert len(kept) == 1
    assert dropped == 1
    assert not (tmp_path / 'failure_ledger.csv').exists()

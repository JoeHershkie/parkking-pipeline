"""Tests for failure_ledger.csv schema and record_failure."""

import csv
from pathlib import Path

from parking_pipeline.failure_ledger import (  # noqa: E402
    LEDGER_COLUMNS,
    LEDGER_EXCLUDED_REASON_CODES,
    clear_stage,
    record_failure,
)
from parking_pipeline.bylaw_text import preprocess_between  # noqa: E402


def test_record_failure_stores_between_parsed_input(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr('parking_pipeline.failure_ledger.data_path', lambda name: tmp_path / name)
    ledger = tmp_path / 'failure_ledger.csv'
    raw = 'Yonge Street to Victoria Street'
    parsed_input = preprocess_between(raw)
    record_failure(1, 'parse', 'PARSE_NO_MATCH', 'no pattern matched', 'Yonge', raw, parsed_input)
    with ledger.open(newline='', encoding='utf-8') as f:
        row = next(csv.DictReader(f))
    assert list(row.keys()) == LEDGER_COLUMNS
    assert row['between'] == raw
    assert row['between_parsed_input'] == 'Yonge Street and Victoria Street'
    assert row['between_parsed_input'] == parsed_input


def test_clear_stage_preserves_between_parsed_input(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr('parking_pipeline.failure_ledger.data_path', lambda name: tmp_path / name)
    record_failure(1, 'parse', 'PARSE_NO_MATCH', 'x', 'Hwy', 'raw', 'parsed')
    record_failure(2, 'geo', 'GEOMETRY_ERROR', 'x', 'Hwy', 'raw')
    clear_stage('geo')
    with (tmp_path / 'failure_ledger.csv').open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]['between_parsed_input'] == 'parsed'

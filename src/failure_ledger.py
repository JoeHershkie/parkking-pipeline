import csv
from pathlib import Path

from paths import data_path

LEDGER_COLUMNS = ['row_id', 'stage', 'reason_code', 'detail', 'highway', 'between']


def _ledger_path() -> Path:
    return data_path('failure_ledger.csv')


def clear_stage(stage: str) -> None:
    """Remove all rows for a pipeline stage so re-runs do not duplicate entries."""
    path = _ledger_path()
    if not path.exists():
        return
    with path.open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    remaining = [r for r in rows if r.get('stage') != stage]
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        writer.writerows(remaining)


def record_failure(row_id, stage, reason_code, detail, highway, between) -> None:
    path = _ledger_path()
    write_header = not path.exists()
    with path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            'row_id': row_id,
            'stage': stage,
            'reason_code': reason_code,
            'detail': detail,
            'highway': highway,
            'between': between,
        })

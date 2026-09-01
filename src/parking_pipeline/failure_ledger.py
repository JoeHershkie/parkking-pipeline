import csv
import time
from pathlib import Path

from .paths import data_path

LEDGER_COLUMNS = [
    'row_id', 'stage', 'reason_code', 'detail', 'highway', 'between', 'between_parsed_input',
]

# Intentional pipeline policy — excluded from ledger/triage (not actionable failures).
LEDGER_EXCLUDED_REASON_CODES = frozenset({'DUPLICATE_RULE'})
MAX_FILE_RETRIES = 5


def _ledger_path() -> Path:
    return data_path('failure_ledger.csv')


def clear_stage(stage: str) -> None:
    """Remove all rows for a pipeline stage so re-runs do not duplicate entries."""
    path = _ledger_path()
    if not path.exists():
        return

    rows: list[dict] = []
    for attempt in range(MAX_FILE_RETRIES):
        try:
            with path.open(newline='', encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            break
        except (TimeoutError, OSError):
            if attempt + 1 >= MAX_FILE_RETRIES:
                raise
            time.sleep(0.2 * (2 ** attempt))

    remaining = [r for r in rows if r.get('stage') != stage]
    for attempt in range(MAX_FILE_RETRIES):
        try:
            with path.open('w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
                writer.writeheader()
                writer.writerows(remaining)
            break
        except (TimeoutError, OSError):
            if attempt + 1 >= MAX_FILE_RETRIES:
                raise
            time.sleep(0.2 * (2 ** attempt))


def record_failures(failures: list[dict]) -> None:
    """Append multiple failure rows in a single atomic file append."""
    if not failures:
        return
    path = _ledger_path()
    write_header = not path.exists()
    for attempt in range(MAX_FILE_RETRIES):
        try:
            with path.open('a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
                if write_header:
                    writer.writeheader()
                    write_header = False
                for item in failures:
                    writer.writerow({
                        'row_id': item.get('row_id'),
                        'stage': item.get('stage'),
                        'reason_code': item.get('reason_code'),
                        'detail': item.get('detail'),
                        'highway': item.get('highway'),
                        'between': item.get('between'),
                        'between_parsed_input': item.get('between_parsed_input', ''),
                    })
            break
        except (TimeoutError, OSError):
            if attempt + 1 >= MAX_FILE_RETRIES:
                raise
            time.sleep(0.2 * (2 ** attempt))


def record_failure(
    row_id,
    stage,
    reason_code,
    detail,
    highway,
    between,
    between_parsed_input: str = '',
) -> None:
    """Append one failure row. ``between`` is source; ``between_parsed_input`` is parse regex input."""
    record_failures([{
        'row_id': row_id,
        'stage': stage,
        'reason_code': reason_code,
        'detail': detail,
        'highway': highway,
        'between': between,
        'between_parsed_input': between_parsed_input,
    }])

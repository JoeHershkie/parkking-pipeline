#!/usr/bin/env python3
"""Fail when core geometry modules fall below per-module coverage floors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_COVERAGE_JSON = Path('coverage.json')

# Floors derived from full-suite coverage after geometry golden regression,
# with a small buffer for normal fluctuation.
MODULE_FLOORS: dict[str, float] = {
    'geo_slice.py': 52.0,
    'geometry_engine.py': 25.0,
}


def _percent(covered: int, total: int) -> float:
    if total == 0:
        return 100.0
    return 100.0 * covered / total


def check_module_coverage(
    coverage_json: Path,
    floors: dict[str, float],
) -> list[str]:
    payload = json.loads(coverage_json.read_text(encoding='utf-8'))
    files: dict[str, dict] = payload.get('files', {})
    failures: list[str] = []

    for suffix, floor in sorted(floors.items()):
        match = next((path for path in files if path.endswith(suffix)), None)
        if match is None:
            failures.append(f'missing coverage entry for {suffix}')
            continue

        summary = files[match]['summary']
        pct = _percent(summary['covered_lines'], summary['num_statements'])
        if pct + 1e-9 < floor:
            failures.append(
                f'{suffix}: {pct:.1f}% covered < {floor:.1f}% floor '
                f'({summary["covered_lines"]}/{summary["num_statements"]} lines)',
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--coverage-json',
        type=Path,
        default=DEFAULT_COVERAGE_JSON,
        help='pytest --cov-report=json output path (default: coverage.json)',
    )
    args = parser.parse_args()

    if not args.coverage_json.exists():
        print(
            f'ERROR: {args.coverage_json} not found; run pytest with --cov-report=json first',
            file=sys.stderr,
        )
        return 1

    failures = check_module_coverage(args.coverage_json, MODULE_FLOORS)
    if failures:
        print('Module coverage check failed:', file=sys.stderr)
        for failure in failures:
            print(f'  - {failure}', file=sys.stderr)
        return 1

    for suffix, floor in MODULE_FLOORS.items():
        print(f'OK: {suffix} >= {floor:.1f}%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

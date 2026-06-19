#!/usr/bin/env python3
"""Regenerate geometry golden snapshot for sample cohort regression tests."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1] / 'tests'
SRC_DIR = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(TESTS_DIR))

from geometry_golden_util import (  # noqa: E402
    DEFAULT_OUTPUT,
    DEFAULT_SAMPLE_CSV,
    write_geometry_golden,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--input',
        type=Path,
        default=DEFAULT_SAMPLE_CSV,
        help='Sample clean_parking_targets.csv path',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=DEFAULT_OUTPUT,
        help='Output geometry_golden.json path',
    )
    args = parser.parse_args()

    records = write_geometry_golden(args.input, args.output)
    by_stage = Counter(record['stage'] for record in records if 'stage' in record)
    by_reason = Counter(
        record.get('reason_code') or 'OK'
        for record in records
    )
    print(f'Wrote {len(records)} records to {args.output}')
    print('By stage:', dict(by_stage))
    print('By outcome:', dict(by_reason))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

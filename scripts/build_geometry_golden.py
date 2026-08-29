#!/usr/bin/env python3
"""Regenerate centreline-span and final-curb golden snapshots for the sample cohort."""

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
    DEFAULT_CURB_OUTPUT,
    DEFAULT_OUTPUT,
    DEFAULT_SAMPLE_CSV,
    write_curb_geometry_golden,
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
        help='Output centreline geometry_golden.json path',
    )
    parser.add_argument(
        '--curb-output',
        type=Path,
        default=DEFAULT_CURB_OUTPUT,
        help='Output curb_geometry_golden.json path',
    )
    parser.add_argument(
        '--centreline-only',
        action='store_true',
        help='Write only the centreline-span golden',
    )
    parser.add_argument(
        '--curb-only',
        action='store_true',
        help='Write only the final-curb golden',
    )
    args = parser.parse_args()

    write_centreline = not args.curb_only
    write_curb = not args.centreline_only

    if write_centreline:
        records = write_geometry_golden(args.input, args.output)
        by_stage = Counter(record['stage'] for record in records if 'stage' in record)
        by_reason = Counter(record.get('reason_code') or 'OK' for record in records)
        print(f'Wrote {len(records)} centreline records to {args.output}')
        print('By stage:', dict(by_stage))
        print('By outcome:', dict(by_reason))

    if write_curb:
        records = write_curb_geometry_golden(args.input, args.curb_output)
        by_stage = Counter(record['stage'] for record in records if 'stage' in record)
        by_method = Counter(
            record.get('curb_geometry_method') or record.get('reason_code') or 'OK'
            for record in records
        )
        print(f'Wrote {len(records)} curb records to {args.curb_output}')
        print('By stage:', dict(by_stage))
        print('By method/outcome:', dict(by_method))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

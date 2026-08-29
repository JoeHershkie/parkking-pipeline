#!/usr/bin/env python3
"""Export a stratified visual-QA sample of curb geometry (sample cohort by default).

Samples each observed side class, road class, method, confidence band, compound
Side vocabulary, and override flag. Does not set production coverage/confidence
thresholds — those belong to a later full-city audit.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1] / 'tests'
SRC_DIR = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(TESTS_DIR))

from curb_qa_sample_util import (  # noqa: E402
    DEFAULT_PER_STRATUM,
    enrich_payloads,
    write_qa_sample_export,
)
from geometry_golden_util import (  # noqa: E402
    DEFAULT_SAMPLE_CSV,
    collect_curb_success_payloads,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--input',
        type=Path,
        default=DEFAULT_SAMPLE_CSV,
        help='Parking CSV to sample (default: committed sample cohort)',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=None,
        help='GeoJSON path (default: data/curb_geometry_qa_samples.geojson)',
    )
    parser.add_argument(
        '--summary',
        type=Path,
        default=None,
        help='Summary JSON path (default: data/curb_geometry_qa_samples_summary.json)',
    )
    parser.add_argument(
        '--per-stratum',
        type=int,
        default=DEFAULT_PER_STRATUM,
        help='Max rows to keep per observed value of each stratum dimension',
    )
    args = parser.parse_args()

    payloads = collect_curb_success_payloads(args.input)
    rows = enrich_payloads(payloads)
    geojson_path, summary_path, selected = write_qa_sample_export(
        rows,
        geojson_path=args.output,
        summary_path=args.summary,
        per_stratum=args.per_stratum,
    )
    methods = Counter(row.get('method') for row in rows)
    print(f'Source geo successes: {len(rows)} from {args.input}')
    print(f'Stratified sample: {len(selected)} -> {geojson_path}')
    print(f'Summary: {summary_path}')
    print('Methods in source:', dict(methods))
    if not rows:
        print('No geo successes to sample. Check that TCL + sample CSV resolve.')
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Export unique street names from data/tcl_streets.geojson to CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from parking_pipeline.log_config import setup_logging
from parking_pipeline.paths import data_path
from parking_pipeline.street_names_csv import export_street_names_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=data_path('tcl_street_names.csv'),
        help='Output CSV path (default: data/tcl_street_names.csv)',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Export even when CSV is newer than tcl_streets.geojson',
    )
    args = parser.parse_args()
    setup_logging()

    streets = data_path('tcl_streets.geojson')
    if not streets.exists():
        print(f'Missing {streets}', file=sys.stderr)
        return 1

    if not args.force and args.output.exists():
        if args.output.stat().st_mtime >= streets.stat().st_mtime:
            print(f'{args.output} is up to date')
            return 0

    export_street_names_csv(output=args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

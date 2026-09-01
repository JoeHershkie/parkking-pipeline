"""Benchmark pipeline execution latency and verify output exactness against baseline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def _compare_csvs(baseline_path: Path, candidate_path: Path, label: str) -> bool:
    if not baseline_path.exists():
        log.warning(f"Baseline {label} does not exist at {baseline_path}")
        return True
    if not candidate_path.exists():
        log.error(f"Candidate {label} missing at {candidate_path}")
        return False

    b_df = pd.read_csv(baseline_path)
    c_df = pd.read_csv(candidate_path)

    if len(b_df) != len(c_df):
        log.error(f"Row count mismatch in {label}: baseline={len(b_df)}, candidate={len(c_df)}")
        return False

    # Check columns
    if list(b_df.columns) != list(c_df.columns):
        log.error(f"Column mismatch in {label}:\n  Baseline: {b_df.columns.tolist()}\n  Candidate: {c_df.columns.tolist()}")
        return False

    # Check content equality after sorting by _id if present
    if '_id' in b_df.columns:
        b_sorted = b_df.sort_values('_id').reset_index(drop=True)
        c_sorted = c_df.sort_values('_id').reset_index(drop=True)
    else:
        b_sorted = b_df
        c_sorted = c_df

    # Compare NaN-filled string values
    b_str = b_sorted.fillna('').astype(str)
    c_str = c_sorted.fillna('').astype(str)

    diffs = (b_str != c_str).sum().sum()
    if diffs > 0:
        log.error(f"Content mismatch in {label}: {diffs} cell differences found!")
        return False

    log.info(f"✓ {label} exact match ({len(b_df)} rows).")
    return True


def _compare_geojson(baseline_path: Path, candidate_path: Path) -> bool:
    if not baseline_path.exists():
        log.warning(f"Baseline GeoJSON does not exist at {baseline_path}")
        return True
    if not candidate_path.exists():
        log.error(f"Candidate GeoJSON missing at {candidate_path}")
        return False

    with baseline_path.open(encoding='utf-8') as f:
        b_json = json.load(f)
    with candidate_path.open(encoding='utf-8') as f:
        c_json = json.load(f)

    b_feats = b_json.get('features', [])
    c_feats = c_json.get('features', [])

    if len(b_feats) != len(c_feats):
        log.error(f"GeoJSON feature count mismatch: baseline={len(b_feats)}, candidate={len(c_feats)}")
        return False

    # Map by _id property
    b_by_id = {f['properties']['_id']: f for f in b_feats if '_id' in f.get('properties', {})}
    c_by_id = {f['properties']['_id']: f for f in c_feats if '_id' in f.get('properties', {})}

    if set(b_by_id.keys()) != set(c_by_id.keys()):
        log.error(f"GeoJSON _id sets mismatch! Missing IDs: {set(b_by_id.keys()) ^ set(c_by_id.keys())}")
        return False

    mismatches = 0
    for row_id, b_feat in b_by_id.items():
        c_feat = c_by_id[row_id]
        # Compare properties
        b_props = {k: v for k, v in b_feat['properties'].items() if k != 'generated_at'}
        c_props = {k: v for k, v in c_feat['properties'].items() if k != 'generated_at'}
        if b_props != c_props:
            mismatches += 1
            if mismatches <= 3:
                log.error(f"Property diff in feature _id={row_id}:\n  B: {b_props}\n  C: {c_props}")

        # Compare geometry coordinates
        b_geom = b_feat.get('geometry')
        c_geom = c_feat.get('geometry')
        if b_geom != c_geom:
            mismatches += 1
            if mismatches <= 3:
                log.error(f"Geometry diff in feature _id={row_id}:\n  B: {b_geom}\n  C: {c_geom}")

    if mismatches > 0:
        log.error(f"GeoJSON verification failed: {mismatches} feature discrepancies found!")
        return False

    log.info(f"✓ final_parking_map.geojson exact match ({len(b_feats)} features).")
    return True


def verify_outputs(baseline_dir: Path, target_dir: Path) -> bool:
    log.info(f"\n--- Verifying Exactness: {target_dir} vs Baseline {baseline_dir} ---")
    all_ok = True

    for name in [
        'clean_parking_targets.csv',
        'parsed_schedules.csv',
        'parsed_successes.csv',
        'failure_ledger.csv',
        'curb_geometry_qa.csv',
    ]:
        ok = _compare_csvs(baseline_dir / name, target_dir / name, name)
        if not ok:
            all_ok = False

    ok_geo = _compare_geojson(baseline_dir / 'final_parking_map.geojson', target_dir / 'final_parking_map.geojson')
    if not ok_geo:
        all_ok = False

    return all_ok


def benchmark_pipeline() -> dict[str, float]:
    """Run stages and record execution times in seconds."""
    from parking_pipeline import (
        geometry_engine,
        parse_between,
        parse_schedule,
        resolve_rows,
    )

    timings = {}

    log.info("\n--- Benchmarking Pipeline Stages ---")

    # Stage 1: Parse Schedule
    t0 = time.perf_counter()
    parse_schedule.main()
    timings['parse_schedule'] = time.perf_counter() - t0

    # Stage 2: Parse Between
    t0 = time.perf_counter()
    parse_between.main()
    timings['parse_between'] = time.perf_counter() - t0

    # Stage 2.5: Resolve Rows
    t0 = time.perf_counter()
    resolve_rows.main()
    timings['resolve_rows'] = time.perf_counter() - t0

    # Stage 3: Geometry Engine
    t0 = time.perf_counter()
    geometry_engine.main([])
    timings['geometry_engine'] = time.perf_counter() - t0

    timings['total'] = sum(timings.values())
    return timings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path('data/baseline_run'), help='Directory of baseline artifacts')
    parser.add_argument('--target', type=Path, default=Path('data'), help='Directory of candidate artifacts')
    parser.add_argument('--verify-only', action='store_true', help='Skip benchmark run; only verify existing files')
    args = parser.parse_args()

    if not args.verify_only:
        timings = benchmark_pipeline()
        log.info("\n--- Benchmark Timings ---")
        for stage, sec in timings.items():
            log.info(f"  {stage:20s}: {sec:6.3f}s")

    ok = verify_outputs(args.baseline, args.target)
    if not ok:
        log.error("\n❌ EXACTNESS VERIFICATION FAILED!")
        return 1

    log.info("\n🎉 EXACTNESS VERIFICATION PASSED: Candidate outputs match baseline 100%!")
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Stage 3: slice parsed rows → final_parking_map.geojson (compatibility facade + runner)."""

from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import pandas as pd

from . import geo_indices
from .failure_ledger import clear_stage, record_failure

# Re-export for scripts and tests that import geometry_engine as ge.
from .geo_indices import (  # noqa: F401
    _build_street_index,
    _intersection_mask,
    _intersection_point_meters,
    _timing,
    find_intersection,
    find_intersection_ids,
    geo_ready,
    get_local_street_geometry,
    get_street_line_meters,
    init_geo,
    intersections_gdf,
    project_to_gps,
    project_to_meters,
    street_graphs,
    street_index,
    street_metre_index,
    warm_intersection_index_from_dataframe,
)
from .geo_slice import (  # noqa: F401
    AMBIGUOUS_INTERSECTION,
    BLOCK_FAMILY_RULES,
    DISCONNECTED_BLOCK,
    GEOMETRY_ERROR,
    INTERSECTION_NOT_FOUND,
    STREET_NOT_FOUND,
    SUPPORTED_RULE_TYPES,
    UNSUPPORTED_RULE_TYPE,
    ZERO_SPAN,
    SliceResult,
    _clamp_dist,
    _offset_point_dist,
    _terminus_dist_on_line,
    intersection_dist_on_street,
    intersection_dist_with_qualifier,
    offset_sign,
    signed_offset_dist,
    slice_between_distances,
    slice_block_path,
    slice_block_to_terminus_path,
    slice_street,
)
from .parse_format import _parse_valid_flag, _resolve_valid_flag, highway_from_row, row_to_parsed
from .paths import data_path
from .schedule_format import schedule_from_json

__all__ = [
    name for name in globals()
    if not name.startswith('__')
]


def _row_series_from_values(columns: pd.Index, values: tuple) -> pd.Series:
    return pd.Series(dict(zip(columns, values, strict=True)))


def _process_geo_row(args: tuple[pd.Index, tuple]) -> tuple[dict | None, dict | None]:
    """Returns (success_payload, failure_record)."""
    columns, values = args
    row_s = _row_series_from_values(columns, values)
    row_id = row_s['_id']
    between = row_s['Between']
    display_highway = row_s.get('Highway', '')
    parsed_for_highway = row_to_parsed(row_s)
    highway = highway_from_row(row_s)
    if not display_highway:
        display_highway = highway

    def failure(reason_code: str, detail: str) -> tuple[None, dict]:
        return None, {
            'row_id': row_id,
            'reason_code': reason_code,
            'detail': detail,
            'highway': display_highway,
            'between': between,
        }

    if 'parse_valid' in row_s.index and not _parse_valid_flag(row_s.get('parse_valid')):
        detail = str(row_s.get('parse_error') or 'parse_valid is false').strip()
        return failure(GEOMETRY_ERROR, detail or 'parse_valid is false')

    if 'resolve_valid' in row_s.index and not _resolve_valid_flag(row_s.get('resolve_valid')):
        detail = str(row_s.get('resolve_error') or 'resolve_valid is false').strip()
        return failure(STREET_NOT_FOUND, detail or 'resolve_valid is false')

    parsed = parsed_for_highway
    if not parsed.get('rule_type'):
        return failure(GEOMETRY_ERROR, 'missing or empty rule_type')

    if not highway:
        return failure(STREET_NOT_FOUND, 'missing highway key')

    try:
        result = slice_street(highway, parsed, bylaw_highway=display_highway)
    except Exception as e:
        return failure(GEOMETRY_ERROR, str(e)[:500])

    if result.ok and not result.geometry.is_empty:
        max_period = row_s.get('Maximum Period Permitted')
        if pd.isna(max_period):
            max_period = None
        max_minutes = row_s.get('max_minutes')
        if pd.isna(max_minutes):
            max_minutes = None
        schedule = schedule_from_json(row_s.get('schedule_json'))
        props = {
            'Highway': display_highway,
            'Rule': row_s['Prohibited Times and/or Days'],
            'schedule_category': row_s.get('schedule_category'),
            'Side': row_s.get('Side'),
            'max': max_period,
            'maxMinutes': max_minutes,
            'schedule': schedule,
            'geometry': result.geometry,
        }
        if result.geometry.geom_type == 'MultiLineString':
            props['disjoint_block'] = True
        return props, None

    if result.reason_code:
        return failure(result.reason_code, result.detail)

    return failure(GEOMETRY_ERROR, 'empty geometry')


def _geo_batch_limit(df: pd.DataFrame) -> pd.DataFrame:
    limit = os.environ.get('GEO_LIMIT', '').strip()
    if limit:
        return df.head(int(limit))
    return df


def _geo_workers() -> int:
    raw = os.environ.get('GEO_WORKERS', '').strip()
    if not raw:
        return 0
    return max(0, int(raw))


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    remainder = seconds % 60
    return f"{minutes}m {remainder:.1f}s"


def _print_timing_summary(
    *,
    row_count: int,
    workers: int,
    csv_load: float,
    slice_sec: float,
    export_sec: float,
    main_total: float,
) -> None:
    timing = geo_indices._timing
    startup = (
        timing.get('intersections_load', 0.0)
        + timing.get('streets_load', 0.0)
        + timing.get('street_graphs', 0.0)
        + timing.get('street_index', 0.0)
    )
    rows_per_sec = row_count / slice_sec if slice_sec > 0 else 0.0
    worker_label = 'sequential' if workers <= 1 else f'{workers} workers'

    print("   Timing:")
    print(f"     TCL intersections load: {_format_duration(timing.get('intersections_load', 0.0))}")
    print(f"     TCL streets load:       {_format_duration(timing.get('streets_load', 0.0))}")
    graphs_sec = timing.get('street_graphs', 0.0)
    if timing.get('street_graphs_cache'):
        print("     Street graphs:          (disk cache)")
    else:
        print(f"     Street graph build:     {_format_duration(graphs_sec)}")
    print(f"     Street index build:     {_format_duration(timing.get('street_index', 0.0))}")
    warm = timing.get('intersection_warm', 0.0)
    if warm > 0 or timing.get('intersection_warm_cache'):
        warm_label = "(disk cache)" if timing.get('intersection_warm_cache') else _format_duration(warm)
        print(f"     Intersection warm:      {warm_label}")
    print(f"     Startup (import):       {_format_duration(startup)}")
    print(f"     CSV load:               {_format_duration(csv_load)}")
    print(
        f"     Slice ({row_count} rows, {worker_label}): "
        f"{_format_duration(slice_sec)} ({rows_per_sec:.1f} rows/s)"
    )
    if export_sec > 0:
        print(f"     Export GeoJSON:         {_format_duration(export_sec)}")
    print(f"     Total (__main__):       {_format_duration(main_total)}")
    print(f"     Total (incl. import):   {_format_duration(startup + main_total)}")


if __name__ == "__main__":
    main_start = time.perf_counter()

    init_geo()

    print("3. Loading Parsed Successes CSV...")
    t0 = time.perf_counter()
    df = pd.read_csv(data_path('parsed_successes.csv'))
    if 'parse_valid' in df.columns:
        valid_mask = df['parse_valid'].map(_parse_valid_flag)
        skipped = int((~valid_mask).sum())
        if skipped:
            print(f'   Skipping {skipped} rows with parse_valid=false')
        df = df.loc[valid_mask].copy()
    if 'resolve_valid' in df.columns:
        resolve_mask = df['resolve_valid'].map(_resolve_valid_flag)
        skipped = int((~resolve_mask).sum())
        if skipped:
            print(f'   Skipping {skipped} rows with resolve_valid=false')
        df = df.loc[resolve_mask].copy()
    csv_load_sec = time.perf_counter() - t0
    batch_df = _geo_batch_limit(df)
    print(f"   Processing {len(batch_df)} of {len(df)} rows.")

    print("   Warming intersection index from CSV...")
    t0 = time.perf_counter()
    warmed = warm_intersection_index_from_dataframe(batch_df)
    geo_indices._timing['intersection_warm'] = time.perf_counter() - t0
    if geo_indices._timing.get('intersection_warm_cache'):
        print(f"   Loaded {warmed} intersection tokens from cache.")
    else:
        print(f"   Indexed {warmed} intersection search tokens (saved to cache).")

    clear_stage('geo')
    results: list[dict] = []
    failure_counts = Counter()
    print("4. Slicing Streets Locally...")

    workers = _geo_workers()
    columns = batch_df.columns
    row_args = [(columns, vals) for vals in batch_df.itertuples(index=False, name=None)]

    def _apply_row_outcome(payload, fail_rec):
        if payload is not None:
            results.append(payload)
            return
        if fail_rec is not None:
            record_failure(
                fail_rec['row_id'], 'geo', fail_rec['reason_code'], fail_rec['detail'],
                fail_rec['highway'], fail_rec['between'],
            )
            failure_counts[fail_rec['reason_code']] += 1

    t0 = time.perf_counter()
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for payload, fail_rec in pool.map(
                _process_geo_row, row_args, chunksize=64,
            ):
                _apply_row_outcome(payload, fail_rec)
    else:
        for args in row_args:
            _apply_row_outcome(*_process_geo_row(args))
    slice_sec = time.perf_counter() - t0

    print(f"\n5. Exporting {len(results)} zones to GeoJSON...")
    print(f"   Successes: {len(results)}")
    if failure_counts:
        print("   Geo failures by reason:")
        for code, count in failure_counts.most_common():
            print(f"     {code}: {count}")

    export_sec = 0.0
    if results:
        t0 = time.perf_counter()
        gdf = gpd.GeoDataFrame(results, geometry='geometry')
        gdf.set_crs(epsg=4326, inplace=True)
        out_path = data_path('final_parking_map.geojson')
        gdf.to_file(out_path, driver="GeoJSON")
        export_sec = time.perf_counter() - t0
        print(f"Done! Open '{out_path}' to see your local work.")

    main_total = time.perf_counter() - main_start
    _print_timing_summary(
        row_count=len(batch_df),
        workers=workers,
        csv_load=csv_load_sec,
        slice_sec=slice_sec,
        export_sec=export_sec,
        main_total=main_total,
    )

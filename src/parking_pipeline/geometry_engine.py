"""Stage 3: slice parsed rows → final_parking_map.geojson (compatibility facade + runner)."""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

import pandas as pd
from shapely.geometry import mapping as geom_mapping

from . import geo_indices
from .curb_geometry import (
    CONSERVATIVE_GLOBAL_OFFSET_M,
    OffsetCalibration,
    flatten_line_geometry,
    resolve_curb_geometry,
    write_curb_geometry_qa,
)
from .curb_side import load_curb_geometry_overrides, override_for_row, parse_side
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
    CENTRELINE_BLOCK_PATH,
    CENTRELINE_DISJOINT_BLOCK,
    CENTRELINE_DISTANCE_MERGE,
    DISCONNECTED_BLOCK,
    EMPTY_GEOMETRY,
    GEOMETRY_ERROR,
    GEOMETRY_EXCEPTION,
    INTERSECTION_NOT_FOUND,
    MISSING_RULE_TYPE,
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
    recover_centreline_ids,
    signed_offset_dist,
    slice_between_distances,
    slice_block_path,
    slice_block_to_terminus_path,
    slice_street,
    street_merge_dropped_component,
)
from .hydrants import HYDRANTS_FILENAME, FireHydrantIndex
from .municipal_rules import MUNICIPAL_BOUNDARIES_FILENAME, MunicipalBoundaryIndex
from .parse_between import PARSE_INVALID
from .parse_format import _parse_valid_flag, _resolve_valid_flag, highway_from_row, row_to_parsed
from .paths import data_path
from .permit_zones import PERMIT_AREAS_FILENAME, PermitZoneIndex
from .road_edges import METRE_CRS, RoadEdgesError, load_road_edge_index
from .schedule_format import schedule_from_json

log = logging.getLogger(__name__)

_curb_lock = threading.Lock()
_road_edge_index = None
_curb_overrides = None
_offset_calibration = OffsetCalibration()
_require_road_edges = False
_municipal_index = None
_permit_index = None
_hydrant_index = None

__all__ = [
    name for name in globals()
    if not name.startswith('__')
]


def _row_series_from_values(columns: pd.Index, values: tuple) -> pd.Series:
    return pd.Series(dict(zip(columns, values, strict=True)))


def configure_curb_runtime(
    *,
    road_index=None,
    overrides=None,
    calibration: OffsetCalibration | None = None,
    require_road_edges: bool = False,
    municipal_index=None,
    permit_index=None,
    hydrant_index=None,
) -> None:
    """Inject curb sources for tests or CLI startup."""
    global _road_edge_index, _curb_overrides, _offset_calibration, _require_road_edges
    global _municipal_index, _permit_index, _hydrant_index
    if road_index is not None:
        _road_edge_index = road_index
    if overrides is not None:
        _curb_overrides = overrides
    if calibration is not None:
        _offset_calibration = calibration
    _require_road_edges = require_road_edges
    if municipal_index is not None:
        _municipal_index = municipal_index
    if permit_index is not None:
        _permit_index = permit_index
    if hydrant_index is not None:
        _hydrant_index = hydrant_index


def _ensure_municipal_index():
    global _municipal_index
    if _municipal_index is not None:
        return None if _municipal_index is False else _municipal_index
    with _curb_lock:
        if _municipal_index is not None:
            return None if _municipal_index is False else _municipal_index
        path = data_path(MUNICIPAL_BOUNDARIES_FILENAME)
        if not path.exists():
            _municipal_index = False
            return None
        try:
            _municipal_index = MunicipalBoundaryIndex()
        except Exception as exc:
            log.warning('Failed to load municipal boundaries index (%s)', exc)
            _municipal_index = False
            return None
        return _municipal_index


def _ensure_permit_index():
    global _permit_index
    if _permit_index is not None:
        return None if _permit_index is False else _permit_index
    with _curb_lock:
        if _permit_index is not None:
            return None if _permit_index is False else _permit_index
        path = data_path(PERMIT_AREAS_FILENAME)
        if not path.exists():
            _permit_index = False
            return None
        try:
            _permit_index = PermitZoneIndex()
        except Exception as exc:
            log.warning('Failed to load permit zones index (%s)', exc)
            _permit_index = False
            return None
        return _permit_index


def _ensure_hydrant_index():
    global _hydrant_index
    if _hydrant_index is not None:
        return None if _hydrant_index is False else _hydrant_index
    with _curb_lock:
        if _hydrant_index is not None:
            return None if _hydrant_index is False else _hydrant_index
        path = data_path(HYDRANTS_FILENAME)
        if not path.exists():
            _hydrant_index = False
            return None
        try:
            _hydrant_index = FireHydrantIndex()
        except Exception as exc:
            log.warning('Failed to load fire hydrants index (%s)', exc)
            _hydrant_index = False
            return None
        return _hydrant_index


def _ensure_road_edge_index():
    global _road_edge_index
    if _road_edge_index is not None:
        return None if _road_edge_index is False else _road_edge_index
    with _curb_lock:
        if _road_edge_index is not None:
            return None if _road_edge_index is False else _road_edge_index
        try:
            _road_edge_index = load_road_edge_index(require=_require_road_edges)
        except RoadEdgesError:
            if _require_road_edges:
                raise
            _road_edge_index = False
            return None
        return _road_edge_index


def _ensure_curb_overrides() -> dict:
    global _curb_overrides
    if _curb_overrides is not None:
        return _curb_overrides
    with _curb_lock:
        if _curb_overrides is None:
            _curb_overrides = load_curb_geometry_overrides()
        return _curb_overrides


def _attrs_for_centrelines(
    centreline_ids: tuple[int, ...],
) -> tuple[str | None, str | None, str | None]:
    feature_classes: list[str] = []
    parity_l_vals: list[str] = []
    parity_r_vals: list[str] = []
    for cid in centreline_ids:
        meta = geo_indices.centreline_meta.get(int(cid))
        if meta is None:
            continue
        if meta.feature_code_desc:
            feature_classes.append(meta.feature_code_desc)
        if meta.parity_l:
            parity_l_vals.append(meta.parity_l)
        if meta.parity_r:
            parity_r_vals.append(meta.parity_r)
    return (
        _unique_or_none(feature_classes),
        _unique_or_none(parity_l_vals),
        _unique_or_none(parity_r_vals),
    )


def _unique_or_none(values: list[str]) -> str | None:
    unique = {value for value in values if value}
    if len(unique) == 1:
        return next(iter(unique))
    return None


def _qa_row_from_payload(payload: dict) -> dict:
    warnings = payload.get('curb_warnings') or []
    if isinstance(warnings, str):
        warning_text = warnings
    else:
        warning_text = '|'.join(str(code) for code in warnings)
    centreline_ids = payload.get('centreline_ids') or []
    object_ids = payload.get('road_edge_object_ids') or []
    return {
        'row_id': payload.get('_id'),
        'highway': payload.get('Highway'),
        'Side': payload.get('Side'),
        'side_mode': payload.get('side_mode'),
        'centreline_ids': ','.join(str(cid) for cid in centreline_ids),
        'centreline_construction': payload.get('centreline_construction'),
        'merge_dropped_component': payload.get('merge_dropped_component'),
        'curb_geometry_method': payload.get('curb_geometry_method'),
        'curb_confidence': payload.get('curb_confidence'),
        'curb_coverage': payload.get('curb_coverage'),
        'median_offset_m': payload.get('median_offset_m'),
        'road_edge_object_ids': ','.join(str(oid) for oid in object_ids),
        'curb_override': payload.get('curb_override'),
        'curb_warnings': warning_text,
    }


def _road_edge_metadata(index) -> dict:
    if index is None:
        return {
            'source': None,
            'crs': METRE_CRS,
            'manifest': {},
            'road_strip_count': 0,
            'intersection_count': 0,
        }
    return {
        'source': str(index.source_path),
        'crs': index.crs,
        'manifest': dict(index.manifest),
        'road_strip_count': int(len(index.road_strips)),
        'intersection_count': int(len(index.intersections)),
    }


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
        return failure(PARSE_INVALID, detail or 'parse_valid is false')

    if 'resolve_valid' in row_s.index and not _resolve_valid_flag(row_s.get('resolve_valid')):
        detail = str(row_s.get('resolve_error') or 'resolve_valid is false').strip()
        return failure(STREET_NOT_FOUND, detail or 'resolve_valid is false')

    parsed = parsed_for_highway
    if not parsed.get('rule_type'):
        return failure(MISSING_RULE_TYPE, 'missing or empty rule_type')

    if not highway:
        return failure(STREET_NOT_FOUND, 'missing highway key')

    try:
        result = slice_street(highway, parsed, bylaw_highway=display_highway)
    except Exception as e:
        log.debug(
            'geometry exception for row %s (%s): %s',
            row_id,
            display_highway,
            e,
            exc_info=True,
        )
        return failure(GEOMETRY_EXCEPTION, str(e)[:500])

    if result.ok and not result.geometry.is_empty:
        max_period = row_s.get('Maximum Period Permitted')
        if pd.isna(max_period):
            max_period = None
        max_minutes = row_s.get('max_minutes')
        if pd.isna(max_minutes):
            max_minutes = None
        schedule = schedule_from_json(row_s.get('schedule_json'))
        disjoint_block = result.geometry.geom_type == 'MultiLineString'
        spec = parse_side(row_s.get('Side'))
        overrides = _ensure_curb_overrides()
        override = override_for_row(row_id, spec, overrides)
        feature_class, parity_l, parity_r = _attrs_for_centrelines(result.centreline_ids)
        curb = resolve_curb_geometry(
            result.geometry,
            spec,
            road_index=_ensure_road_edge_index(),
            centreline_ids=result.centreline_ids,
            construction_method=result.construction_method,
            feature_class=feature_class,
            parity_l=parity_l,
            parity_r=parity_r,
            calibration=_offset_calibration,
            override=override,
        )
        geom = flatten_line_geometry(curb.geometry)
        if geom is None:
            geom = flatten_line_geometry(result.geometry)
        if geom is None:
            return failure(EMPTY_GEOMETRY, 'empty geometry')
        cat = row_s.get('schedule_category')
        props = {
            '_id': row_id,
            'Highway': display_highway,
            'Rule': row_s['Prohibited Times and/or Days'],
            'schedule_category': cat,
            'Side': row_s.get('Side'),
            'side_mode': spec.mode,
            'centreline_ids': [int(cid) for cid in result.centreline_ids],
            'centreline_construction': result.construction_method,
            'merge_dropped_component': bool(result.merge_dropped_component),
            'curb_geometry_method': curb.method,
            'curb_confidence': float(curb.confidence),
            'curb_coverage': float(curb.coverage),
            'median_offset_m': (
                None if curb.median_offset_m is None else float(curb.median_offset_m)
            ),
            'road_edge_object_ids': [int(oid) for oid in curb.road_edge_object_ids],
            'curb_override': bool(curb.override),
            'curb_warnings': list(curb.warnings),
            'max': max_period,
            'maxMinutes': max_minutes,
            'schedule': schedule,
            'geometry': geom,
        }
        if cat in ('snow_route', 'snow_streetcar'):
            props['is_snow_route'] = True
            if cat == 'snow_streetcar':
                props['streetcar_corridor'] = True

        mun_idx = _ensure_municipal_index()
        if mun_idx is not None:
            mun_tags = mun_idx.tag_feature(geom)
            props['former_municipality'] = mun_tags.get('former_municipality')
            if mun_tags.get('regional_winter_rule'):
                props['regional_winter_rule'] = mun_tags['regional_winter_rule']

        permit_idx = _ensure_permit_index()
        if permit_idx is not None:
            permit_tags = permit_idx.tag_feature(geom)
            props['permit_area_id'] = permit_tags.get('permit_area_id')
            if permit_tags.get('permit_parking_active'):
                props['permit_parking_active'] = True

        hydrant_idx = _ensure_hydrant_index()
        if hydrant_idx is not None:
            hydrant_tags = hydrant_idx.tag_feature(geom)
            if hydrant_tags.get('has_hydrant'):
                props['has_hydrant'] = True
                props['hydrant_count'] = hydrant_tags['hydrant_count']
                props['hydrant_setback_m'] = 3.0
        if disjoint_block:
            props['disjoint_block'] = True
        return props, None

    if result.reason_code:
        return failure(result.reason_code, result.detail)

    return failure(EMPTY_GEOMETRY, 'empty geometry')


def _geo_batch_limit(df: pd.DataFrame) -> pd.DataFrame:
    limit = os.environ.get('GEO_LIMIT', '').strip()
    if limit:
        return df.head(int(limit))
    return df


def _geo_workers() -> int:
    raw = os.environ.get('GEO_WORKERS', '').strip().lower()
    if not raw:
        return 0
    if raw in ('auto', 'all', '-1'):
        return max(1, os.cpu_count() or 1)
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _source_mtime(name: str) -> str | None:
    path = data_path(name)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _pipeline_version() -> str:
    try:
        return version('parking-pipeline')
    except PackageNotFoundError:
        return 'unknown'


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    remainder = seconds % 60
    return f"{minutes}m {remainder:.1f}s"


class _SliceProgress:
    """Per-row slice progress for the terminal (count, percent, elapsed, ETA)."""

    _LOG_INTERVAL_S = 2.0
    _PAINT_INTERVAL_S = 0.2

    def __init__(self, total: int) -> None:
        self.total = max(0, total)
        self.done = 0
        self.start = time.perf_counter()
        self._last_log_at = 0.0
        self._last_paint_at = 0.0
        self._last_logged_pct = -1

    def advance(self) -> None:
        self.done += 1
        self._emit(final=False)

    def finish(self) -> None:
        if self.total == 0:
            self.done = 0
        elif self.done < self.total:
            self.done = self.total
        self._emit(final=True)

    def _line(self) -> str:
        elapsed = time.perf_counter() - self.start
        total = self.total
        done = self.done
        pct = 100.0 if total == 0 else (100.0 * done / total)
        rate = done / elapsed if elapsed > 0 else 0.0
        parts = [
            f'Slice {done}/{total} ({pct:.1f}%)',
            f'elapsed {_format_duration(elapsed)}',
        ]
        if rate > 0:
            parts.append(f'{rate:.1f} rows/s')
            if done < total:
                parts.append(f'ETA {_format_duration((total - done) / rate)}')
        return '   ' + '  '.join(parts)

    def _emit(self, *, final: bool) -> None:
        line = self._line()
        now = time.perf_counter()
        pct = 100 if self.total == 0 else int(100 * self.done / self.total)
        commit = final or self.done == 1 or (now - self._last_log_at) >= self._LOG_INTERVAL_S
        if pct != self._last_logged_pct and pct % 5 == 0:
            commit = True
        paint = commit or (now - self._last_paint_at) >= self._PAINT_INTERVAL_S
        if not paint:
            return

        sys.stderr.write('\r' + line + '          ')
        if commit:
            sys.stderr.write('\n')
            self._last_log_at = now
            self._last_logged_pct = pct
        sys.stderr.flush()
        self._last_paint_at = now


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

    log.info("   Timing:")
    log.info(f"     TCL intersections load: {_format_duration(timing.get('intersections_load', 0.0))}")
    log.info(f"     TCL streets load:       {_format_duration(timing.get('streets_load', 0.0))}")
    graphs_sec = timing.get('street_graphs', 0.0)
    if timing.get('street_graphs_cache'):
        log.info("     Street graphs:          (disk cache)")
    else:
        log.info(f"     Street graph build:     {_format_duration(graphs_sec)}")
    log.info(f"     Street index build:     {_format_duration(timing.get('street_index', 0.0))}")
    warm = timing.get('intersection_warm', 0.0)
    if warm > 0 or timing.get('intersection_warm_cache'):
        warm_label = "(disk cache)" if timing.get('intersection_warm_cache') else _format_duration(warm)
        log.info(f"     Intersection warm:      {warm_label}")
    log.info(f"     Startup (import):       {_format_duration(startup)}")
    log.info(f"     CSV load:               {_format_duration(csv_load)}")
    log.info(
        f"     Slice ({row_count} rows, {worker_label}): "
        f"{_format_duration(slice_sec)} ({rows_per_sec:.1f} rows/s)"
    )
    if export_sec > 0:
        log.info(f"     Export GeoJSON:         {_format_duration(export_sec)}")
    log.info(f"     Total (__main__):       {_format_duration(main_total)}")
    log.info(f"     Total (incl. import):   {_format_duration(startup + main_total)}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .log_config import add_verbose_arg, setup_logging

    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    parser.add_argument(
        '-w', '--workers',
        type=str,
        default=None,
        help=(
            'Number of worker processes for geometry slicing '
            '(e.g. 4, auto; default: GEO_WORKERS env or 0 for sequential)'
        ),
    )
    parser.add_argument(
        '--require-road-edges',
        action='store_true',
        help=(
            'Fail if the local topographic Road Edge GeoPackage is missing '
            'instead of copying the sample fixture'
        ),
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)
    if args.workers is not None:
        os.environ['GEO_WORKERS'] = str(args.workers)

    main_start = time.perf_counter()

    init_geo()
    configure_curb_runtime(require_road_edges=args.require_road_edges)
    try:
        _ensure_road_edge_index()
        _ensure_curb_overrides()
    except RoadEdgesError as exc:
        log.error('%s', exc)
        return 2

    log.info("3. Loading Parsed Successes CSV...")
    t0 = time.perf_counter()
    df = pd.read_csv(data_path('parsed_successes.csv'))
    if 'parse_valid' in df.columns:
        valid_mask = df['parse_valid'].map(_parse_valid_flag)
        skipped = int((~valid_mask).sum())
        if skipped:
            log.info(f'   Skipping {skipped} rows with parse_valid=false')
        df = df.loc[valid_mask].copy()
    if 'resolve_valid' in df.columns:
        resolve_mask = df['resolve_valid'].map(_resolve_valid_flag)
        skipped = int((~resolve_mask).sum())
        if skipped:
            log.info(f'   Skipping {skipped} rows with resolve_valid=false')
        df = df.loc[resolve_mask].copy()
    csv_load_sec = time.perf_counter() - t0
    batch_df = _geo_batch_limit(df)
    log.info(f"   Processing {len(batch_df)} of {len(df)} rows.")

    log.info("   Warming intersection index from CSV...")
    t0 = time.perf_counter()
    warmed = warm_intersection_index_from_dataframe(batch_df)
    geo_indices._timing['intersection_warm'] = time.perf_counter() - t0
    if geo_indices._timing.get('intersection_warm_cache'):
        log.info(f"   Loaded {warmed} intersection tokens from cache.")
    else:
        log.info(f"   Indexed {warmed} intersection search tokens (saved to cache).")

    clear_stage('geo')
    results: list[dict] = []
    failure_counts = Counter()
    log.info("4. Slicing Streets Locally...")

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
    progress = _SliceProgress(len(row_args))
    if workers > 1:
        pool_kwargs: dict = {}
        try:
            pool_kwargs['mp_context'] = multiprocessing.get_context('fork')
        except (ValueError, AttributeError):
            pass
        with ProcessPoolExecutor(max_workers=workers, **pool_kwargs) as pool:
            for payload, fail_rec in pool.map(
                _process_geo_row, row_args, chunksize=16,
            ):
                _apply_row_outcome(payload, fail_rec)
                progress.advance()
    else:
        for args in row_args:
            _apply_row_outcome(*_process_geo_row(args))
            progress.advance()
    progress.finish()
    slice_sec = time.perf_counter() - t0

    log.info(f"\n5. Exporting {len(results)} zones to GeoJSON...")
    log.info(f"   Successes: {len(results)}")
    if failure_counts:
        log.info("   Geo failures by reason:")
        for code, count in failure_counts.most_common():
            log.info(f"     {code}: {count}")

    export_sec = 0.0
    if results:
        t0 = time.perf_counter()
        out_path = data_path('final_parking_map.geojson')
        features = [
            {
                'id': str(i),
                'type': 'Feature',
                'properties': {k: v for k, v in row.items() if k != 'geometry'},
                'geometry': geom_mapping(row['geometry']),
            }
            for i, row in enumerate(results)
        ]
        geojson = {
            'type': 'FeatureCollection',
            'features': features,
        }
        method_counts = Counter(
            str(row.get('curb_geometry_method') or '') for row in results
        )
        confidences = [
            float(row['curb_confidence'])
            for row in results
            if isinstance(row.get('curb_confidence'), int | float)
        ]
        road_index = _ensure_road_edge_index()
        qa_rows = [_qa_row_from_payload(row) for row in results]
        write_curb_geometry_qa(
            qa_rows,
            extra_summary={
                'road_edges_manifest': dict(road_index.manifest) if road_index else {},
                'conservative_global_offset_m': CONSERVATIVE_GLOBAL_OFFSET_M,
            },
        )
        geojson['metadata'] = {
            'generated_at': datetime.now(UTC).isoformat(),
            'pipeline_version': _pipeline_version(),
            'feature_count': len(results),
            'input_row_count': len(batch_df),
            'tcl_streets_mtime': _source_mtime('tcl_streets.geojson'),
            'tcl_intersections_mtime': _source_mtime('tcl_intersections.geojson'),
            'road_edges': _road_edge_metadata(road_index),
            'curb_geometry': {
                'methods': dict(method_counts),
                'mean_confidence': (
                    sum(confidences) / len(confidences) if confidences else 0.0
                ),
                'conservative_global_offset_m': CONSERVATIVE_GLOBAL_OFFSET_M,
            },
        }
        with out_path.open('w', encoding='utf-8') as f:
            json.dump(geojson, f)
        export_sec = time.perf_counter() - t0
        log.info(f"Done! Open '{out_path}' to see your local work.")

    main_total = time.perf_counter() - main_start
    _print_timing_summary(
        row_count=len(batch_df),
        workers=workers,
        csv_load=csv_load_sec,
        slice_sec=slice_sec,
        export_sec=export_sec,
        main_total=main_total,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Shared helpers for centreline-span and final-curb golden snapshots."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
from sample_data import ensure_sample_data_copies
from shapely.geometry.base import BaseGeometry

from parking_pipeline import geo_indices as gi
from parking_pipeline import geo_slice as gs
from parking_pipeline import geometry_engine as ge
from parking_pipeline.curb_geometry import OffsetCalibration
from parking_pipeline.parse_between import parse_rows
from parking_pipeline.parse_format import (
    _parse_valid_flag,
    _resolve_valid_flag,
    highway_from_row,
    row_to_parsed,
)
from parking_pipeline.paths import DATA_DIR
from parking_pipeline.resolve_rows import _init_resolve_index, resolve_rows
from parking_pipeline.road_edges import ROAD_EDGES_FILENAME, load_road_edge_index

COORD_PRECISION = 6
QA_FLOAT_PRECISION = 4
OFFSET_PRECISION = 3
FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'
DEFAULT_SAMPLE_CSV = (
    Path(__file__).resolve().parents[1] / 'data' / 'samples' / 'clean_parking_targets.csv'
)
DEFAULT_OUTPUT = FIXTURES_DIR / 'geometry_golden.json'
DEFAULT_CURB_OUTPUT = FIXTURES_DIR / 'curb_geometry_golden.json'

_CENTRELINE_KIND = 'centreline_span'
_CURB_KIND = 'curb'
_GOLDEN_SAMPLE_FILES = frozenset({
    'tcl_streets.geojson',
    'tcl_intersections.geojson',
    'tcl_street_names.csv',
    'street_aliases.csv',
    'highway_aliases.csv',
    ROAD_EDGES_FILENAME,
    f'{Path(ROAD_EDGES_FILENAME).stem}.manifest.json',
})
_DATA_PATH_PATCH_TARGETS = (
    'parking_pipeline.geo_indices.data_path',
    'parking_pipeline.tcl_highway_resolve.data_path',
    'parking_pipeline.resolve_rows.data_path',
    'parking_pipeline.road_edges.data_path',
)


def round_coord(value: float) -> float:
    return round(float(value), COORD_PRECISION)


def geometry_to_coords(geom: BaseGeometry | None) -> list[Any] | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == 'LineString':
        return [[round_coord(x), round_coord(y)] for x, y in geom.coords]
    if geom.geom_type == 'MultiLineString':
        return [
            [[round_coord(x), round_coord(y)] for x, y in part.coords]
            for part in geom.geoms
        ]
    raise ValueError(f'unsupported geometry type: {geom.geom_type}')


def reset_curb_runtime() -> None:
    """Clear process-wide curb state so earlier tests cannot leak into golden runs."""
    ge._road_edge_index = None
    ge._curb_overrides = None
    ge._require_road_edges = False
    ge._offset_calibration = OffsetCalibration()


def _sample_data_path(filename: str) -> Path:
    if filename in _GOLDEN_SAMPLE_FILES:
        sample = DATA_DIR / 'samples' / filename
        if sample.exists():
            return sample
    return DATA_DIR / filename


def _patch_sample_data_paths() -> ExitStack:
    stack = ExitStack()
    for target in _DATA_PATH_PATCH_TARGETS:
        stack.enter_context(patch(target, _sample_data_path))
    return stack


def _clear_geo_lookup_caches() -> None:
    gi.find_intersection.cache_clear()
    gi._intersection_point_meters.cache_clear()
    gs._intersection_dist_cached.cache_clear()


def setup_geo_env(*, force: bool = True) -> None:
    ensure_sample_data_copies()
    reset_curb_runtime()
    _clear_geo_lookup_caches()
    with _patch_sample_data_paths():
        gi.init_geo(force=force)
        _init_resolve_index()
        _configure_sample_road_edges()


def _configure_sample_road_edges() -> None:
    """Pin the committed sample GeoPackage so a local full extract cannot leak in."""
    sample = DATA_DIR / 'samples' / ROAD_EDGES_FILENAME
    if sample.exists():
        ge.configure_curb_runtime(road_index=load_road_edge_index(sample))


def _json_number(value: Any, digits: int) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), digits)


def _json_int_list(values: Any) -> list[int] | None:
    if values is None:
        return None
    if isinstance(values, str):
        parts = [part for part in values.split(',') if part.strip()]
        return [int(part) for part in parts]
    return [int(item) for item in values]


def _json_warnings(values: Any) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return [part for part in values.split('|') if part]
    return [str(item) for item in values]


def _base_record(raw: pd.Series) -> dict[str, Any]:
    return {
        '_id': str(raw['_id']),
        'highway': str(raw.get('Highway', '')),
        'between': str(raw.get('Between', '')),
        'side': None if pd.isna(raw.get('Side')) else str(raw.get('Side')),
    }


def _parse_resolve_rows(sample_csv: Path) -> tuple[pd.DataFrame, set[str], dict[str, pd.Series]]:
    df = pd.read_csv(sample_csv)
    setup_geo_env(force=True)
    parsed_df, _parse_failures = parse_rows(df, schedule_by_id=None)
    parsed_ids = {str(row['_id']) for _, row in parsed_df.iterrows()}
    resolved_df, _ = resolve_rows(parsed_df)
    resolved_by_id = {str(row['_id']): row for _, row in resolved_df.iterrows()}
    return df, parsed_ids, resolved_by_id


def iter_sample_pipeline_rows(
    sample_csv: Path,
) -> Iterator[tuple[pd.Series, dict[str, Any], pd.Series | None]]:
    """Yield (raw row, partial record, resolved series or None)."""
    df, parsed_ids, resolved_by_id = _parse_resolve_rows(sample_csv)
    for _, raw in df.iterrows():
        record = _base_record(raw)
        row_id = record['_id']
        if row_id not in parsed_ids:
            record.update({
                'stage': 'parse',
                'rule_type': None,
                'reason_code': 'PARSE_NO_MATCH',
            })
            yield raw, record, None
            continue

        resolved = resolved_by_id[row_id]
        rule_type = resolved.get('rule_type')
        record['rule_type'] = None if pd.isna(rule_type) else str(rule_type)

        if not _parse_valid_flag(resolved.get('parse_valid')):
            record.update({
                'stage': 'parse',
                'reason_code': 'PARSE_INVALID',
            })
            yield raw, record, resolved
            continue

        if not _resolve_valid_flag(resolved.get('resolve_valid')):
            record.update({
                'stage': 'resolve',
                'reason_code': 'RESOLVE_STREET_NOT_FOUND',
            })
            yield raw, record, resolved
            continue

        yield raw, record, resolved


def _empty_geo_fields() -> dict[str, Any]:
    return {
        'geom_type': None,
        'coords': None,
        'construction_method': None,
        'centreline_ids': None,
        'merge_dropped_component': None,
    }


def _empty_curb_fields() -> dict[str, Any]:
    return {
        **_empty_geo_fields(),
        'side_mode': None,
        'curb_geometry_method': None,
        'curb_confidence': None,
        'curb_coverage': None,
        'median_offset_m': None,
        'road_edge_object_ids': None,
        'curb_override': None,
        'curb_warnings': None,
        'centreline_construction': None,
    }


def collect_centreline_golden_records(sample_csv: Path) -> list[dict[str, Any]]:
    """Parse -> resolve -> slice_street. Records legal centreline spans only."""
    records: list[dict[str, Any]] = []
    for _raw, record, resolved in iter_sample_pipeline_rows(sample_csv):
        if resolved is None or record.get('stage') in {'parse', 'resolve'}:
            record.update(_empty_geo_fields())
            records.append(record)
            continue

        sliced = ge.slice_street(
            highway_from_row(resolved),
            row_to_parsed(resolved),
            bylaw_highway=str(resolved.get('Highway', '') or ''),
        )
        if sliced.ok and sliced.geometry is not None and not sliced.geometry.is_empty:
            record.update({
                'stage': 'geo',
                'reason_code': None,
                'geom_type': sliced.geometry.geom_type,
                'coords': geometry_to_coords(sliced.geometry),
                'construction_method': sliced.construction_method,
                'centreline_ids': list(sliced.centreline_ids),
                'merge_dropped_component': bool(sliced.merge_dropped_component),
            })
        else:
            record.update({
                'stage': 'geo',
                'reason_code': sliced.reason_code,
                **_empty_geo_fields(),
                'construction_method': sliced.construction_method,
                'centreline_ids': list(sliced.centreline_ids) if sliced.centreline_ids else None,
                'merge_dropped_component': bool(sliced.merge_dropped_component),
            })
        records.append(record)

    records.sort(key=lambda item: item['_id'])
    return records


def collect_geometry_golden_records(sample_csv: Path) -> list[dict[str, Any]]:
    """Alias kept for the centreline-span builder and existing tests."""
    return collect_centreline_golden_records(sample_csv)


def collect_resolved_geo_row_args(sample_csv: Path) -> list[tuple[pd.Index, tuple]]:
    """Row payloads accepted by ``geometry_engine._process_geo_row``."""
    args: list[tuple[pd.Index, tuple]] = []
    for _raw, record, resolved in iter_sample_pipeline_rows(sample_csv):
        if resolved is None or record.get('stage') in {'parse', 'resolve'}:
            continue
        columns = resolved.index
        args.append((columns, tuple(resolved[columns])))
    return args


def collect_curb_success_payloads(sample_csv: Path) -> list[dict[str, Any]]:
    """Run curb resolution on sample rows that pass parse + resolve."""
    payloads: list[dict[str, Any]] = []
    for columns, values in collect_resolved_geo_row_args(sample_csv):
        success, _failure = ge._process_geo_row((columns, values))
        if success is not None:
            payloads.append(success)
    return payloads


def _curb_fields_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    geom = payload.get('geometry')
    return {
        'geom_type': None if geom is None else geom.geom_type,
        'coords': geometry_to_coords(geom),
        'construction_method': payload.get('centreline_construction'),
        'centreline_construction': payload.get('centreline_construction'),
        'centreline_ids': _json_int_list(payload.get('centreline_ids')),
        'merge_dropped_component': bool(payload.get('merge_dropped_component')),
        'side_mode': payload.get('side_mode'),
        'curb_geometry_method': payload.get('curb_geometry_method'),
        'curb_confidence': _json_number(payload.get('curb_confidence'), QA_FLOAT_PRECISION),
        'curb_coverage': _json_number(payload.get('curb_coverage'), QA_FLOAT_PRECISION),
        'median_offset_m': _json_number(payload.get('median_offset_m'), OFFSET_PRECISION),
        'road_edge_object_ids': _json_int_list(payload.get('road_edge_object_ids')) or [],
        'curb_override': bool(payload.get('curb_override')),
        'curb_warnings': _json_warnings(payload.get('curb_warnings')) or [],
    }


def collect_curb_golden_records(sample_csv: Path) -> list[dict[str, Any]]:
    """Parse -> resolve -> curb geometry, including QA fields."""
    records: list[dict[str, Any]] = []
    for _raw, record, resolved in iter_sample_pipeline_rows(sample_csv):
        if resolved is None or record.get('stage') in {'parse', 'resolve'}:
            record.update(_empty_curb_fields())
            records.append(record)
            continue

        columns = resolved.index
        values = tuple(resolved[columns])
        success, failure = ge._process_geo_row((columns, values))
        if success is not None:
            record.update({
                'stage': 'geo',
                'reason_code': None,
                **_curb_fields_from_payload(success),
            })
        else:
            assert failure is not None
            record.update({
                'stage': 'geo',
                'reason_code': failure.get('reason_code'),
                **_empty_curb_fields(),
            })
        records.append(record)

    records.sort(key=lambda item: item['_id'])
    return records


def source_manifest_snapshot() -> dict[str, Any]:
    """Stable Road Edge provenance fields for the curb golden header."""
    index = ge._ensure_road_edge_index()
    if index is None:
        index = load_road_edge_index()
    manifest = dict(index.manifest)
    return {
        'is_sample_fixture': bool(manifest.get('is_sample_fixture')),
        'fetch_time': manifest.get('fetch_time'),
        'max_last_geometry_maint': manifest.get('max_last_geometry_maint'),
        'crs': manifest.get('crs') or index.crs,
        'feature_counts': manifest.get('feature_counts'),
        'catalogue_status': manifest.get('catalogue_status'),
    }


def write_geometry_golden(
    sample_csv: Path = DEFAULT_SAMPLE_CSV,
    output: Path = DEFAULT_OUTPUT,
) -> list[dict[str, Any]]:
    records = collect_centreline_golden_records(sample_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'kind': _CENTRELINE_KIND,
        'sample_csv': sample_csv.name,
        'row_count': len(records),
        'records': records,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return records


def write_curb_geometry_golden(
    sample_csv: Path = DEFAULT_SAMPLE_CSV,
    output: Path = DEFAULT_CURB_OUTPUT,
) -> list[dict[str, Any]]:
    records = collect_curb_golden_records(sample_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'kind': _CURB_KIND,
        'sample_csv': sample_csv.name,
        'row_count': len(records),
        'source_manifest': source_manifest_snapshot(),
        'records': records,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return records


def load_geometry_golden(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))

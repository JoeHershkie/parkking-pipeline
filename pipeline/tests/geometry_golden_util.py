"""Shared helpers for geometry golden snapshot regression."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry.base import BaseGeometry

from parking_pipeline import geometry_engine as ge
from parking_pipeline.geo_indices import init_geo
from parking_pipeline.parse_between import parse_rows
from parking_pipeline.parse_format import (
    _parse_valid_flag,
    _resolve_valid_flag,
)
from parking_pipeline.resolve_rows import _init_resolve_index, resolve_rows

COORD_PRECISION = 6
FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'
DEFAULT_SAMPLE_CSV = (
    Path(__file__).resolve().parents[1] / 'data' / 'samples' / 'clean_parking_targets.csv'
)
DEFAULT_OUTPUT = FIXTURES_DIR / 'geometry_golden.json'


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


def setup_geo_env(*, force: bool = True) -> None:
    init_geo(force=force)
    _init_resolve_index()


def collect_geometry_golden_records(sample_csv: Path) -> list[dict[str, Any]]:
    """Run parse -> resolve -> geo on sample rows; return stable snapshot records."""
    df = pd.read_csv(sample_csv)
    setup_geo_env(force=True)

    parsed_df, _parse_failures = parse_rows(df, schedule_by_id=None)
    parsed_ids = {str(row['_id']) for _, row in parsed_df.iterrows()}

    resolved_df, _ = resolve_rows(parsed_df)
    resolved_by_id = {
        str(row['_id']): row for _, row in resolved_df.iterrows()
    }

    records: list[dict[str, Any]] = []
    for _, raw in df.iterrows():
        row_id = str(raw['_id'])
        record: dict[str, Any] = {
            '_id': row_id,
            'highway': str(raw.get('Highway', '')),
            'between': str(raw.get('Between', '')),
        }

        if row_id not in parsed_ids:
            record.update({
                'stage': 'parse',
                'rule_type': None,
                'reason_code': 'PARSE_NO_MATCH',
                'geom_type': None,
                'coords': None,
            })
            records.append(record)
            continue

        resolved = resolved_by_id[row_id]
        rule_type = resolved.get('rule_type')
        record['rule_type'] = None if pd.isna(rule_type) else str(rule_type)

        if not _parse_valid_flag(resolved.get('parse_valid')):
            record.update({
                'stage': 'parse',
                'reason_code': 'PARSE_INVALID',
                'geom_type': None,
                'coords': None,
            })
            records.append(record)
            continue

        if not _resolve_valid_flag(resolved.get('resolve_valid')):
            record.update({
                'stage': 'resolve',
                'reason_code': 'RESOLVE_STREET_NOT_FOUND',
                'geom_type': None,
                'coords': None,
            })
            records.append(record)
            continue

        columns = resolved.index
        values = tuple(resolved[columns])
        success, failure = ge._process_geo_row((columns, values))

        if success is not None:
            geom = success['geometry']
            record.update({
                'stage': 'geo',
                'reason_code': None,
                'geom_type': geom.geom_type,
                'coords': geometry_to_coords(geom),
            })
        else:
            assert failure is not None
            record.update({
                'stage': 'geo',
                'reason_code': failure.get('reason_code'),
                'geom_type': None,
                'coords': None,
            })

        records.append(record)

    records.sort(key=lambda item: item['_id'])
    return records


def write_geometry_golden(
    sample_csv: Path = DEFAULT_SAMPLE_CSV,
    output: Path = DEFAULT_OUTPUT,
) -> list[dict[str, Any]]:
    records = collect_geometry_golden_records(sample_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'sample_csv': sample_csv.name,
        'row_count': len(records),
        'records': records,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return records


def load_geometry_golden(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))

"""Stratified visual-QA sampling for curb geometry (sample cohort, not production gates).

Confidence bands and stratum keys are for inspection coverage only. They are not
Road Edge coverage or confidence cutoffs for production.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry

from parking_pipeline import geo_indices as gi
from parking_pipeline.curb_side import parse_side
from parking_pipeline.paths import data_path

# Visual-QA bins only — do not treat these as production quality thresholds.
CONFIDENCE_BANDS: tuple[tuple[str, float, float], ...] = (
    ('0.00-0.25', 0.0, 0.25),
    ('0.25-0.50', 0.25, 0.50),
    ('0.50-0.75', 0.50, 0.75),
    ('0.75-1.00', 0.75, 1.01),
)

STRATUM_DIMS = (
    'side_class',
    'road_class',
    'method',
    'confidence_band',
    'compound_vocabulary',
    'override',
)

DEFAULT_PER_STRATUM = 2
DEFAULT_GEOJSON = 'curb_geometry_qa_samples.geojson'
DEFAULT_SUMMARY = 'curb_geometry_qa_samples_summary.json'


def confidence_band(value: object) -> str:
    if not isinstance(value, int | float):
        return 'unknown'
    conf = float(value)
    for label, lo, hi in CONFIDENCE_BANDS:
        if lo <= conf < hi:
            return label
    return 'unknown'


def compound_vocabulary(side_raw: object, side_mode: object | None = None) -> str:
    spec = parse_side(side_raw)
    mode = str(side_mode or spec.mode)
    if spec.wrapping or mode == 'wrapping':
        return 'adjacent_compound'
    if mode == 'multi':
        return 'opposing_or_both'
    if mode == 'single':
        return 'simple'
    if mode in {'parity', 'perimeter', 'specialized', 'unresolved'}:
        return mode
    return 'other'


def road_class_for_ids(centreline_ids: Sequence[int] | None) -> str:
    if not centreline_ids:
        return 'unknown'
    labels: list[str] = []
    for cid in centreline_ids:
        meta = gi.centreline_meta.get(int(cid))
        if meta is not None and meta.feature_code_desc:
            labels.append(meta.feature_code_desc)
    unique = {label for label in labels if label}
    if len(unique) == 1:
        return next(iter(unique))
    if not unique:
        return 'unknown'
    return 'mixed'


def enrich_qa_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Attach visual-QA stratum fields to a geometry-engine success payload."""
    ids = payload.get('centreline_ids') or ()
    side_raw = payload.get('Side')
    side_mode = payload.get('side_mode')
    row = {
        'row_id': str(payload.get('_id')),
        'highway': payload.get('Highway'),
        'Side': side_raw,
        'side_mode': side_mode,
        'side_class': str(side_mode or 'unknown'),
        'road_class': road_class_for_ids(tuple(int(cid) for cid in ids)),
        'method': str(payload.get('curb_geometry_method') or 'unknown'),
        'confidence_band': confidence_band(payload.get('curb_confidence')),
        'compound_vocabulary': compound_vocabulary(side_raw, side_mode),
        'override': 'override' if payload.get('curb_override') else 'default',
        'curb_confidence': payload.get('curb_confidence'),
        'curb_coverage': payload.get('curb_coverage'),
        'curb_warnings': list(payload.get('curb_warnings') or []),
        'centreline_construction': payload.get('centreline_construction'),
        'centreline_ids': [int(cid) for cid in ids],
        'geometry': payload.get('geometry'),
    }
    return row


def stratum_key(row: Mapping[str, Any], dim: str) -> str:
    value = row.get(dim)
    if value is None or value == '':
        return 'unknown'
    return str(value)


def select_stratified_samples(
    rows: Sequence[Mapping[str, Any]],
    *,
    per_stratum: int = DEFAULT_PER_STRATUM,
) -> list[dict[str, Any]]:
    """Pick up to *per_stratum* rows from each observed value of each dimension.

    Empty strata are reported by absence; this does not invent production cutoffs.
    """
    chosen: dict[str, dict[str, Any]] = {}
    for dim in STRATUM_DIMS:
        buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            buckets[stratum_key(row, dim)].append(row)
        for _label, group in sorted(buckets.items()):
            for member in group[:per_stratum]:
                chosen[str(member.get('row_id'))] = dict(member)
    return [chosen[key] for key in sorted(chosen)]


def stratum_inventory(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    inventory: dict[str, dict[str, int]] = {}
    for dim in STRATUM_DIMS:
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[stratum_key(row, dim)] += 1
        inventory[dim] = dict(sorted(counts.items()))
    return inventory


def _coords(geom: BaseGeometry) -> list[Any]:
    if geom.geom_type == 'LineString':
        return [list(coord) for coord in geom.coords]
    if geom.geom_type == 'MultiLineString':
        return [[list(coord) for coord in part.coords] for part in geom.geoms]
    raise ValueError(f'unsupported geometry type: {geom.geom_type}')


def samples_to_geojson(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for row in rows:
        geom = row.get('geometry')
        if geom is None or getattr(geom, 'is_empty', True):
            continue
        props = {
            key: value
            for key, value in row.items()
            if key != 'geometry'
        }
        features.append({
            'type': 'Feature',
            'properties': props,
            'geometry': {
                'type': geom.geom_type,
                'coordinates': _coords(geom),
            },
        })
    return {
        'type': 'FeatureCollection',
        'name': 'curb_geometry_qa_samples',
        'features': features,
    }


def write_qa_sample_export(
    rows: Sequence[Mapping[str, Any]],
    *,
    geojson_path: Path | None = None,
    summary_path: Path | None = None,
    per_stratum: int = DEFAULT_PER_STRATUM,
    sampled: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    selected = list(sampled) if sampled is not None else select_stratified_samples(
        rows, per_stratum=per_stratum,
    )
    dest_geojson = geojson_path or data_path(DEFAULT_GEOJSON)
    dest_summary = summary_path or data_path(DEFAULT_SUMMARY)
    dest_geojson.parent.mkdir(parents=True, exist_ok=True)
    payload = samples_to_geojson(selected)
    dest_geojson.write_text(json.dumps(payload) + '\n', encoding='utf-8')
    summary = {
        'note': (
            'Stratified visual-QA sample only. Confidence bands are inspection '
            'bins, not production coverage or confidence cutoffs.'
        ),
        'source_row_count': len(rows),
        'sample_count': len(selected),
        'per_stratum': per_stratum,
        'stratum_inventory': stratum_inventory(rows),
        'sampled_row_ids': [row.get('row_id') for row in selected],
        'sampled_strata': stratum_inventory(selected),
    }
    dest_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return dest_geojson, dest_summary, selected


def enrich_payloads(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_qa_row(payload) for payload in payloads]

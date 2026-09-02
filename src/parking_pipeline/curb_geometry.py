"""Measured Road Edge curb tracks with calibrated offset fallback.

Primary method: sample the projected centreline by chainage, form smoothed local
tangents with ``p(s+δ)-p(s-δ)``, and cast ± normal rays against Road Edge
polygons. Intersection polygons mask gaps. Fallback uses Shapely
``offset_curve`` with distances from measured samples, then feature-class
medians, then a documented conservative global separation.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pyproj
import shapely
from shapely import line_interpolate_point, union_all
from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, transform

from .curb_side import (
    CurbGeometryOverride,
    SideSpec,
    resolve_parity_side,
)
from .paths import data_path
from .road_edges import METRE_CRS, RoadEdgeIndex

CurbMethod = Literal['road_edge', 'offset_fallback', 'centerline_unresolved']

ROAD_EDGE_NO_MATCH = 'ROAD_EDGE_NO_MATCH'
ROAD_EDGE_LOW_COVERAGE = 'ROAD_EDGE_LOW_COVERAGE'
SIDE_AMBIGUOUS = 'SIDE_AMBIGUOUS'
CURB_INVALID = 'CURB_INVALID'
CENTERLINE_FALLBACK = 'CENTERLINE_FALLBACK'

METHOD_ROAD_EDGE = 'road_edge'
METHOD_OFFSET_FALLBACK = 'offset_fallback'
METHOD_CENTERLINE_UNRESOLVED = 'centerline_unresolved'

# Half of a typical ~7 m local roadway. Used only when no Road Edge samples
# and no feature-class median are available — not a road-class width table.
CONSERVATIVE_GLOBAL_OFFSET_M = 3.5

SAMPLE_INTERVAL_M = 2.0
TANGENT_DELTA_M = 1.0
RAY_MAX_M = 40.0
MIN_HIT_M = 0.05
# Provisional switch to fallback; full-city audit may revise this (plan §7).
MIN_ROAD_EDGE_COVERAGE = 0.4
MIN_DIRECTION_MARGIN = 0.12
JUMP_ABS_M = 8.0
JUMP_REL = 2.5
SIMPLIFY_TOL_M = 0.05
POINT_LIKE_M = 0.05
MIN_OPPOSITE_SEPARATION_M = 0.4
CLOSED_TOL_M = 0.5
FALLBACK_SAMPLE_COUNT = 21

QA_CSV_FILENAME = 'curb_geometry_qa.csv'
QA_SUMMARY_FILENAME = 'curb_geometry_qa_summary.json'

_COMPASS_ORDER = (
    'north',
    'northeast',
    'east',
    'southeast',
    'south',
    'southwest',
    'west',
    'northwest',
)

_to_m = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
_to_ll = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform


@dataclass(frozen=True)
class CurbGeometryResult:
    """Side-specific curb geometry plus QA / provenance fields."""

    geometry: LineString | MultiLineString
    method: str
    confidence: float
    coverage: float
    median_offset_m: float | None
    measured_offsets_m: tuple[float, ...]
    warnings: tuple[str, ...]
    road_edge_object_ids: tuple[int, ...]
    side_mode: str
    override: bool = False
    directional_margin: float = 0.0


@dataclass
class OffsetCalibration:
    """Deterministic offset distances. Never depends on row processing order."""

    by_centreline_id: dict[int, float] = field(default_factory=dict)
    by_feature_class: dict[str, float] = field(default_factory=dict)
    global_offset_m: float = CONSERVATIVE_GLOBAL_OFFSET_M

    def distance_for(
        self,
        centreline_ids: Sequence[int],
        feature_class: str | None,
        sample_median: float | None,
    ) -> tuple[float, str]:
        if sample_median is not None and sample_median > MIN_HIT_M:
            return float(sample_median), 'centreline_samples'
        for cid in centreline_ids:
            value = self.by_centreline_id.get(int(cid))
            if value is not None and value > MIN_HIT_M:
                return float(value), 'centreline'
        if feature_class:
            value = self.by_feature_class.get(feature_class)
            if value is not None and value > MIN_HIT_M:
                return float(value), 'feature_class'
        return float(self.global_offset_m), 'global'


@dataclass(frozen=True)
class _Hit:
    s: float
    x: float
    y: float
    offset: float
    object_id: int
    dx: float
    dy: float
    on_interior: bool = False


@dataclass
class _MeasuredTracks:
    left_parts: list[LineString]
    right_parts: list[LineString]
    left_hits: list[_Hit]
    right_hits: list[_Hit]
    coverage: float
    sample_count: int
    object_ids: tuple[int, ...]
    median_offset: float | None
    all_offsets: tuple[float, ...]


def flatten_line_geometry(geom: BaseGeometry | None) -> LineString | MultiLineString | None:
    """Return LineString / MultiLineString only. Drop points, polygons, zeros."""
    parts: list[LineString] = []
    _collect_line_parts(geom, parts)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return MultiLineString(parts)


def iter_line_parts(geom: BaseGeometry | None) -> list[LineString]:
    parts: list[LineString] = []
    _collect_line_parts(geom, parts)
    return parts


def displacement_compass(dx: float, dy: float) -> str:
    """8-way compass of a displacement. 0° is north, 90° is east."""
    if dx == 0.0 and dy == 0.0:
        return 'north'
    deg = math.degrees(math.atan2(dx, dy)) % 360.0
    idx = int((deg + 22.5) // 45.0) % 8
    return _COMPASS_ORDER[idx]


def resolve_curb_geometry(
    geometry: LineString | MultiLineString | None,
    spec: SideSpec,
    *,
    road_index: RoadEdgeIndex | None = None,
    centreline_ids: Sequence[int] = (),
    construction_method: str | None = None,
    feature_class: str | None = None,
    parity_l: str | None = None,
    parity_r: str | None = None,
    calibration: OffsetCalibration | None = None,
    override: CurbGeometryOverride | None = None,
    input_crs: str = 'EPSG:4326',
) -> CurbGeometryResult:
    """Build side-specific curb geometry from a legal centreline span."""
    calib = calibration or OffsetCalibration()
    parts = iter_line_parts(geometry)
    if not parts:
        fallback = geometry if isinstance(geometry, LineString | MultiLineString) else LineString()
        return _unresolved_result(
            fallback if not fallback.is_empty else LineString(),
            spec,
            warnings=(CURB_INVALID, CENTERLINE_FALLBACK),
            override=override is not None,
        )

    to_metres = input_crs.upper() != METRE_CRS
    parts_m = [transform(_to_m, part) if to_metres else part for part in parts]
    orientation_unambiguous = construction_method == 'block_path'

    if spec.needs_override:
        if override is not None:
            combined = _apply_override(
                parts_m,
                spec,
                override,
                road_index=road_index,
                centreline_ids=centreline_ids,
                feature_class=feature_class,
                calibration=calib,
            )
        else:
            combined = _unresolved_from_parts(
                parts_m,
                spec,
                warnings=(SIDE_AMBIGUOUS, CENTERLINE_FALLBACK),
            )
        return _project_result(combined, to_metres)

    part_results = [
        _resolve_part(
            part,
            spec,
            road_index=road_index,
            centreline_ids=centreline_ids,
            feature_class=feature_class,
            parity_l=parity_l,
            parity_r=parity_r,
            calibration=calib,
            orientation_unambiguous=orientation_unambiguous,
        )
        for part in parts_m
    ]
    combined = _combine_part_results(part_results, spec)
    return _project_result(combined, to_metres)


def write_curb_geometry_qa(
    rows: Sequence[Mapping[str, Any]],
    *,
    csv_path: Path | None = None,
    summary_path: Path | None = None,
    extra_summary: Mapping[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write per-feature QA CSV plus a compact summary JSON."""
    dest_csv = csv_path or data_path(QA_CSV_FILENAME)
    dest_summary = summary_path or data_path(QA_SUMMARY_FILENAME)
    frame = pd.DataFrame(list(rows))
    dest_csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(dest_csv, index=False)
    summary = build_qa_summary(rows)
    if extra_summary:
        summary.update(dict(extra_summary))
    dest_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    return dest_csv, dest_summary


def build_qa_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    methods: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    unresolved_sides: set[str] = set()
    offsets: list[float] = []
    low_conf = 0
    invalid = 0
    fallbacks = 0
    for row in rows:
        method = str(row.get('curb_geometry_method') or '')
        methods[method] += 1
        if method == METHOD_CENTERLINE_UNRESOLVED:
            invalid += 1
        if method in {METHOD_OFFSET_FALLBACK, METHOD_CENTERLINE_UNRESOLVED}:
            fallbacks += 1
        conf = row.get('curb_confidence')
        if isinstance(conf, int | float) and conf < 0.5:
            low_conf += 1
        side_mode = str(row.get('side_mode') or '')
        if side_mode in {'unresolved', 'specialized'} or method == METHOD_CENTERLINE_UNRESOLVED:
            raw = str(row.get('Side') or row.get('side_raw') or '').strip()
            if raw:
                unresolved_sides.add(raw)
        for code in _as_warning_list(row.get('curb_warnings')):
            warnings[code] += 1
        median = row.get('median_offset_m')
        if isinstance(median, int | float) and math.isfinite(median):
            offsets.append(float(median))
    offset_stats: dict[str, float | int]
    if offsets:
        ordered = sorted(offsets)
        offset_stats = {
            'count': len(ordered),
            'p50': _percentile(ordered, 0.5),
            'p90': _percentile(ordered, 0.9),
            'min': ordered[0],
            'max': ordered[-1],
        }
    else:
        offset_stats = {'count': 0}
    return {
        'feature_count': len(rows),
        'methods': dict(methods),
        'warnings': dict(warnings),
        'unresolved_side_values': sorted(unresolved_sides),
        'low_confidence_count': low_conf,
        'invalid_or_unresolved_count': invalid,
        'fallback_count': fallbacks,
        'measured_offset_m': offset_stats,
    }


def _resolve_part(
    line_m: LineString,
    spec: SideSpec,
    *,
    road_index: RoadEdgeIndex | None,
    centreline_ids: Sequence[int],
    feature_class: str | None,
    parity_l: str | None,
    parity_r: str | None,
    calibration: OffsetCalibration,
    orientation_unambiguous: bool,
) -> CurbGeometryResult:
    warnings: list[str] = []
    measured = _extract_measured_tracks(line_m, road_index) if road_index is not None else None

    selected: LineString | MultiLineString | None = None
    method = METHOD_CENTERLINE_UNRESOLVED
    confidence = 0.0
    margin = 0.0
    coverage = measured.coverage if measured is not None else 0.0
    object_ids: tuple[int, ...] = measured.object_ids if measured is not None else ()
    offsets = measured.all_offsets if measured is not None else ()
    median = measured.median_offset if measured is not None else None

    if measured is not None and measured.coverage >= MIN_ROAD_EDGE_COVERAGE:
        selected, pick_warnings, confidence, margin = _select_from_measured(
            line_m,
            measured,
            spec,
            parity_l=parity_l,
            parity_r=parity_r,
            orientation_unambiguous=orientation_unambiguous,
            road_index=road_index,
        )
        warnings.extend(pick_warnings)
        if selected is not None and _curb_geometry_ok(selected):
            method = METHOD_ROAD_EDGE
        else:
            selected = None
            if CURB_INVALID not in warnings:
                warnings.append(CURB_INVALID)
    elif measured is None or measured.coverage <= 0:
        warnings.append(ROAD_EDGE_NO_MATCH)
    else:
        warnings.append(ROAD_EDGE_LOW_COVERAGE)

    if selected is None:
        selected, fb_warnings, confidence, margin, median, method = _offset_fallback(
            line_m,
            spec,
            measured=measured,
            centreline_ids=centreline_ids,
            feature_class=feature_class,
            calibration=calibration,
            parity_l=parity_l,
            parity_r=parity_r,
            orientation_unambiguous=orientation_unambiguous,
        )
        warnings.extend(fb_warnings)

    if selected is None or not _curb_geometry_ok(selected):
        warnings.append(CURB_INVALID)
        warnings.append(CENTERLINE_FALLBACK)
        return CurbGeometryResult(
            geometry=line_m,
            method=METHOD_CENTERLINE_UNRESOLVED,
            confidence=0.0,
            coverage=coverage,
            median_offset_m=median,
            measured_offsets_m=offsets,
            warnings=_unique_warnings(warnings),
            road_edge_object_ids=object_ids,
            side_mode=spec.mode,
            directional_margin=margin,
        )

    return CurbGeometryResult(
        geometry=selected,
        method=method,
        confidence=float(max(0.0, min(1.0, confidence))),
        coverage=coverage,
        median_offset_m=median,
        measured_offsets_m=offsets,
        warnings=_unique_warnings(warnings),
        road_edge_object_ids=object_ids,
        side_mode=spec.mode,
        directional_margin=margin,
    )


def _extract_measured_tracks(
    line_m: LineString,
    road_index: RoadEdgeIndex,
) -> _MeasuredTracks:
    samples = _chainage_samples(line_m)
    cand_oids, cand_geoms, cand_bounds, cand_ints = road_index.query_road_candidates_within(line_m, RAY_MAX_M)
    cand_ixs = road_index.query_intersection_geoms_within(line_m, RAY_MAX_M)

    boundary_union = union_all(cand_bounds) if len(cand_bounds) > 0 else None
    if boundary_union is not None and boundary_union.is_empty:
        boundary_union = None

    ix_union = union_all(cand_ixs) if len(cand_ixs) > 0 else None
    if ix_union is not None and ix_union.is_empty:
        ix_union = None
    ix_skip = ix_union.buffer(MIN_HIT_M) if ix_union is not None else None

    left_hits: list[_Hit | None] = []
    right_hits: list[_Hit | None] = []
    for s, origin, tangent, normal in samples:
        if ix_skip is not None and ix_skip.intersects(origin):
            left_hits.append(None)
            right_hits.append(None)
            continue
        left_hits.append(
            _ray_hit(origin, s, normal, cand_oids, cand_bounds, cand_ints, boundary_union, ix_union),
        )
        right_hits.append(
            _ray_hit(origin, s, (-normal[0], -normal[1]), cand_oids, cand_bounds, cand_ints, boundary_union, ix_union),
        )

    left_hits = _reject_offset_jumps(_fill_isolated_gaps(left_hits))
    right_hits = _reject_offset_jumps(_fill_isolated_gaps(right_hits))

    left_valid = [hit for hit in left_hits if hit is not None]
    right_valid = [hit for hit in right_hits if hit is not None]
    hit_count = sum(
        1
        for left, right in zip(left_hits, right_hits, strict=True)
        if left is not None or right is not None
    )
    coverage = hit_count / len(samples) if samples else 0.0
    all_offsets = tuple(hit.offset for hit in (*left_valid, *right_valid))
    median = statistics.median(all_offsets) if all_offsets else None
    object_ids = tuple(sorted({
        hit.object_id for hit in (*left_valid, *right_valid)
    }))
    closed = _is_closed(line_m)
    return _MeasuredTracks(
        left_parts=_runs_to_lines(_stitch_runs(left_hits, closed=closed)),
        right_parts=_runs_to_lines(_stitch_runs(right_hits, closed=closed)),
        left_hits=left_valid,
        right_hits=right_valid,
        coverage=coverage,
        sample_count=len(samples),
        object_ids=object_ids,
        median_offset=float(median) if median is not None else None,
        all_offsets=all_offsets,
    )


def _select_from_measured(
    line_m: LineString,
    measured: _MeasuredTracks,
    spec: SideSpec,
    *,
    parity_l: str | None,
    parity_r: str | None,
    orientation_unambiguous: bool,
    road_index: RoadEdgeIndex | None,
) -> tuple[LineString | MultiLineString | None, list[str], float, float]:
    warnings: list[str] = []
    left_geom = _combine_lines(measured.left_parts)
    right_geom = _combine_lines(measured.right_parts)

    if spec.mode == 'perimeter' or spec.ring is not None:
        ring_geom = _select_ring(
            line_m, measured, spec, road_index=road_index,
        )
        if ring_geom is None:
            warnings.append(SIDE_AMBIGUOUS)
            return None, warnings, 0.0, 0.0
        conf = 0.5 * measured.coverage + 0.5
        return ring_geom, warnings, conf, 1.0

    if spec.mode == 'parity':
        side = resolve_parity_side(
            spec,
            parity_l=parity_l,
            parity_r=parity_r,
            orientation_unambiguous=orientation_unambiguous,
        )
        if side is None:
            warnings.append(SIDE_AMBIGUOUS)
            return None, warnings, 0.0, 0.0
        chosen = left_geom if side == 'left' else right_geom
        conf = 0.5 * measured.coverage + 0.5
        return chosen, warnings, conf, 1.0

    if spec.selects_multiple_curbs:
        combined = _combine_lines(
            [part for part in (*measured.left_parts, *measured.right_parts)],
        )
        if combined is None:
            warnings.append(SIDE_AMBIGUOUS)
            return None, warnings, 0.0, 0.0
        conf = 0.5 * measured.coverage + 0.5
        return combined, warnings, conf, 1.0

    left_score = _direction_score(measured.left_hits, spec)
    right_score = _direction_score(measured.right_hits, spec)
    margin = abs(left_score - right_score)
    if spec.wrapping:
        chosen, score = _pick_wrapping(measured, spec, left_score, right_score)
        if chosen is not None and score >= 0.2 and (len(spec.directions) <= 2 or score >= 0.99):
            conf = 0.5 * measured.coverage + 0.5 * score
            return chosen, warnings, conf, margin
        # 3+ directions (or poor cover): union per-direction runs from one curb.
        track, cover = _per_direction_track_geometry(measured, spec)
        if track is not None:
            conf = 0.5 * measured.coverage + 0.5 * cover
            return track, warnings, conf, margin
        if chosen is not None and score >= 0.2:
            conf = 0.5 * measured.coverage + 0.5 * score
            return chosen, warnings, conf, margin
        warnings.append(SIDE_AMBIGUOUS)
        return None, warnings, 0.0, margin

    if margin < MIN_DIRECTION_MARGIN:
        warnings.append(SIDE_AMBIGUOUS)
        return None, warnings, 0.0, margin
    if left_score > right_score:
        chosen, score = left_geom, left_score
    else:
        chosen, score = right_geom, right_score
    if chosen is None:
        warnings.append(SIDE_AMBIGUOUS)
        return None, warnings, 0.0, margin
    conf = 0.5 * measured.coverage + 0.5 * score
    return chosen, warnings, conf, margin


def _offset_fallback(
    line_m: LineString,
    spec: SideSpec,
    *,
    measured: _MeasuredTracks | None,
    centreline_ids: Sequence[int],
    feature_class: str | None,
    calibration: OffsetCalibration,
    parity_l: str | None,
    parity_r: str | None,
    orientation_unambiguous: bool,
) -> tuple[
    LineString | MultiLineString | None,
    list[str],
    float,
    float,
    float | None,
    str,
]:
    warnings: list[str] = []
    sample_median = measured.median_offset if measured is not None else None
    distance, _source = calibration.distance_for(
        centreline_ids, feature_class, sample_median,
    )
    left = _offset_candidate(line_m, distance)
    right = _offset_candidate(line_m, -distance)
    if not _curb_geometry_ok(left) or not _curb_geometry_ok(right):
        warnings.append(CURB_INVALID)
        return None, warnings, 0.0, 0.0, sample_median, METHOD_CENTERLINE_UNRESOLVED
    if left is None or right is None:
        warnings.append(CURB_INVALID)
        return None, warnings, 0.0, 0.0, sample_median, METHOD_CENTERLINE_UNRESOLVED
    if left.distance(right) < MIN_OPPOSITE_SEPARATION_M:
        warnings.append(CURB_INVALID)
        return None, warnings, 0.0, 0.0, sample_median, METHOD_CENTERLINE_UNRESOLVED

    if spec.mode == 'parity':
        side = resolve_parity_side(
            spec,
            parity_l=parity_l,
            parity_r=parity_r,
            orientation_unambiguous=orientation_unambiguous,
        )
        if side is None:
            warnings.append(SIDE_AMBIGUOUS)
            return None, warnings, 0.0, 0.0, sample_median, METHOD_CENTERLINE_UNRESOLVED
        chosen = left if side == 'left' else right
        warnings.append(CENTERLINE_FALLBACK)
        return chosen, warnings, 0.45, 1.0, distance, METHOD_OFFSET_FALLBACK

    if spec.selects_multiple_curbs:
        combined = _combine_lines([left, right])
        warnings.append(CENTERLINE_FALLBACK)
        return combined, warnings, 0.45, 1.0, distance, METHOD_OFFSET_FALLBACK

    left_score, right_score = _fallback_direction_scores(line_m, spec)
    margin = abs(left_score - right_score)
    if spec.wrapping:
        if len(spec.directions) >= 3 and margin < MIN_DIRECTION_MARGIN:
            # e.g. 'East, north and west': both offset curbs match some direction.
            warnings.append(SIDE_AMBIGUOUS)
            return None, warnings, 0.0, margin, sample_median, METHOD_CENTERLINE_UNRESOLVED
        if margin < MIN_DIRECTION_MARGIN:
            warnings.append(SIDE_AMBIGUOUS)
            return None, warnings, 0.0, margin, sample_median, METHOD_CENTERLINE_UNRESOLVED
        chosen = left if left_score >= right_score else right
        warnings.append(CENTERLINE_FALLBACK)
        return chosen, warnings, 0.4 + 0.2 * margin, margin, distance, METHOD_OFFSET_FALLBACK

    if margin < MIN_DIRECTION_MARGIN:
        warnings.append(SIDE_AMBIGUOUS)
        return None, warnings, 0.0, margin, sample_median, METHOD_CENTERLINE_UNRESOLVED
    chosen = left if left_score > right_score else right
    warnings.append(CENTERLINE_FALLBACK)
    return chosen, warnings, 0.4 + 0.2 * margin, margin, distance, METHOD_OFFSET_FALLBACK


def _apply_override(
    parts_m: Sequence[LineString],
    spec: SideSpec,
    override: CurbGeometryOverride,
    *,
    road_index: RoadEdgeIndex | None,
    centreline_ids: Sequence[int],
    feature_class: str | None,
    calibration: OffsetCalibration,
) -> CurbGeometryResult:
    method = override.method.strip().casefold().replace('calibrated_offset', METHOD_OFFSET_FALLBACK)
    combined_line = _combine_lines(list(parts_m)) or parts_m[0]
    if method == METHOD_ROAD_EDGE and road_index is not None:
        results = [
            _resolve_part(
                part,
                SideSpec(
                    raw=spec.raw,
                    normalized=spec.normalized or 'both',
                    mode='multi',
                    directions=spec.directions,
                ),
                road_index=road_index,
                centreline_ids=centreline_ids,
                feature_class=feature_class,
                parity_l=None,
                parity_r=None,
                calibration=calibration,
                orientation_unambiguous=False,
            )
            for part in parts_m
        ]
        result = _combine_part_results(results, spec)
        return CurbGeometryResult(
            geometry=result.geometry,
            method=result.method,
            confidence=result.confidence,
            coverage=result.coverage,
            median_offset_m=result.median_offset_m,
            measured_offsets_m=result.measured_offsets_m,
            warnings=result.warnings,
            road_edge_object_ids=result.road_edge_object_ids,
            side_mode=spec.mode,
            override=True,
            directional_margin=result.directional_margin,
        )
    if method == METHOD_OFFSET_FALLBACK:
        results = [
            _offset_fallback_result(part, spec, centreline_ids, feature_class, calibration)
            for part in parts_m
        ]
        result = _combine_part_results(results, spec)
        return CurbGeometryResult(
            geometry=result.geometry,
            method=result.method,
            confidence=result.confidence,
            coverage=result.coverage,
            median_offset_m=result.median_offset_m,
            measured_offsets_m=result.measured_offsets_m,
            warnings=result.warnings,
            road_edge_object_ids=result.road_edge_object_ids,
            side_mode=spec.mode,
            override=True,
            directional_margin=result.directional_margin,
        )
    return CurbGeometryResult(
        geometry=combined_line,
        method=METHOD_CENTERLINE_UNRESOLVED,
        confidence=0.0,
        coverage=0.0,
        median_offset_m=None,
        measured_offsets_m=(),
        warnings=(CENTERLINE_FALLBACK,),
        road_edge_object_ids=(),
        side_mode=spec.mode,
        override=True,
    )


def _offset_fallback_result(
    line_m: LineString,
    spec: SideSpec,
    centreline_ids: Sequence[int],
    feature_class: str | None,
    calibration: OffsetCalibration,
) -> CurbGeometryResult:
    both = SideSpec(
        raw=spec.raw,
        normalized='both',
        mode='multi',
        directions=(),
    )
    geom, warnings, conf, margin, median, method = _offset_fallback(
        line_m,
        both if spec.needs_override else spec,
        measured=None,
        centreline_ids=centreline_ids,
        feature_class=feature_class,
        calibration=calibration,
        parity_l=None,
        parity_r=None,
        orientation_unambiguous=False,
    )
    if geom is None or not _curb_geometry_ok(geom):
        return _unresolved_result(line_m, spec, warnings=_unique_warnings(
            list(warnings) + [CURB_INVALID, CENTERLINE_FALLBACK],
        ))
    return CurbGeometryResult(
        geometry=geom,
        method=method,
        confidence=conf,
        coverage=0.0,
        median_offset_m=median,
        measured_offsets_m=(),
        warnings=_unique_warnings(warnings),
        road_edge_object_ids=(),
        side_mode=spec.mode,
        directional_margin=margin,
    )


def _chainage_samples(
    line_m: LineString,
) -> list[tuple[float, Point, tuple[float, float], tuple[float, float]]]:
    length = line_m.length
    if length <= 0:
        return []
    closed = _is_closed(line_m)
    n = max(3, int(length / SAMPLE_INTERVAL_M) + 1)
    if closed:
        distances = [i * length / n for i in range(n)]
        delta = min(TANGENT_DELTA_M, max(length / 8.0, 0.05))
        s_lo = [(s - delta) % length for s in distances]
        s_hi = [(s + delta) % length for s in distances]
    else:
        distances = [i * length / (n - 1) for i in range(n)]
        delta = min(TANGENT_DELTA_M, max(length / 8.0, 0.05))
        s_lo = [max(0.0, s - delta) for s in distances]
        s_hi = []
        for s, lo in zip(distances, s_lo, strict=True):
            hi = min(length, s + delta)
            if hi <= lo:
                hi = min(length, lo + 1e-3)
            s_hi.append(hi)
    origins = line_interpolate_point(line_m, distances)
    p_lo = line_interpolate_point(line_m, s_lo)
    p_hi = line_interpolate_point(line_m, s_hi)
    ox = shapely.get_x(origins)
    oy = shapely.get_y(origins)
    dx = shapely.get_x(p_hi) - shapely.get_x(p_lo)
    dy = shapely.get_y(p_hi) - shapely.get_y(p_lo)
    out: list[tuple[float, Point, tuple[float, float], tuple[float, float]]] = []
    for i, s in enumerate(distances):
        tangent = (float(dx[i]), float(dy[i]))
        norm = _hypot(tangent[0], tangent[1])
        if norm < 1e-9:
            continue
        unit = (tangent[0] / norm, tangent[1] / norm)
        origin = Point(float(ox[i]), float(oy[i]))
        out.append((s, origin, unit, (-unit[1], unit[0])))
    return out


def _smoothed_tangent(line_m: LineString, s: float, length: float) -> tuple[float, float]:
    delta = min(TANGENT_DELTA_M, max(length / 8.0, 0.05))
    if _is_closed(line_m):
        s_lo = (s - delta) % length
        s_hi = (s + delta) % length
    else:
        s_lo = max(0.0, s - delta)
        s_hi = min(length, s + delta)
        if s_hi <= s_lo:
            s_hi = min(length, s_lo + 1e-3)
    p_lo = line_m.interpolate(s_lo)
    p_hi = line_m.interpolate(s_hi)
    return (p_hi.x - p_lo.x, p_hi.y - p_lo.y)


def _ray_hit(
    origin: Point,
    s: float,
    direction: tuple[float, float],
    cand_oids: np.ndarray,
    cand_bounds: np.ndarray,
    cand_ints: np.ndarray,
    boundary_union: BaseGeometry | None,
    ix_union: BaseGeometry | None,
) -> _Hit | None:
    mag = _hypot(direction[0], direction[1])
    if mag < 1e-12 or boundary_union is None or len(cand_oids) == 0:
        return None
    unit_x, unit_y = direction[0] / mag, direction[1] / mag
    end = Point(origin.x + unit_x * RAY_MAX_M, origin.y + unit_y * RAY_MAX_M)
    ray = LineString([(origin.x, origin.y), (end.x, end.y)])
    crossed = ray.intersection(boundary_union)
    if crossed.is_empty:
        return None
    best: _Hit | None = None
    best_d = RAY_MAX_M
    for pt in _iter_hit_points(crossed, origin):
        dist = _hypot(pt.x - origin.x, pt.y - origin.y)
        if dist < MIN_HIT_M or dist >= best_d:
            continue
        if _intersection_blocks(origin, pt, ix_union, dist):
            continue
        owner_idx = _owning_road_idx(pt, cand_bounds)
        if owner_idx is None:
            continue
        object_id = int(cand_oids[owner_idx])
        owner_ints = cand_ints[owner_idx]
        on_interior = False
        if owner_ints is not None:
            on_interior = bool(shapely.distance(owner_ints, pt) <= 0.08)
        best_d = dist
        best = _Hit(
            s=s,
            x=pt.x,
            y=pt.y,
            offset=dist,
            object_id=object_id,
            dx=pt.x - origin.x,
            dy=pt.y - origin.y,
            on_interior=on_interior,
        )
    return best


def _owning_road_idx(
    pt: Point,
    cand_bounds: np.ndarray,
) -> int | None:
    if len(cand_bounds) == 0:
        return None
    dists = shapely.distance(cand_bounds, pt)
    min_i = int(np.argmin(dists))
    if dists[min_i] < 0.15:
        return min_i
    return None


def _intersection_blocks(
    origin: Point,
    hit: Point,
    ix_union: BaseGeometry | None,
    hit_dist: float,
) -> bool:
    if ix_union is None or ix_union.is_empty:
        return False
    segment = LineString([(origin.x, origin.y), (hit.x, hit.y)])
    if not ix_union.intersects(segment):
        return False
    crossed = segment.intersection(ix_union)
    for pt in _iter_hit_points(crossed, origin):
        dist = _hypot(pt.x - origin.x, pt.y - origin.y)
        if MIN_HIT_M < dist < hit_dist - 0.02:
            return True
    return False


def _fill_isolated_gaps(hits: list[_Hit | None]) -> list[_Hit | None]:
    """Keep tracks continuous across single-sample misses (corners, not real gaps)."""
    if len(hits) < 3:
        return hits
    out = list(hits)
    for i in range(1, len(out) - 1):
        prev, cur, nxt = out[i - 1], out[i], out[i + 1]
        if cur is not None or prev is None or nxt is None:
            continue
        if abs(prev.offset - nxt.offset) > max(JUMP_ABS_M, JUMP_REL * max(prev.offset, nxt.offset)):
            continue
        out[i] = _Hit(
            s=(prev.s + nxt.s) / 2.0,
            x=(prev.x + nxt.x) / 2.0,
            y=(prev.y + nxt.y) / 2.0,
            offset=(prev.offset + nxt.offset) / 2.0,
            object_id=prev.object_id,
            dx=(prev.dx + nxt.dx) / 2.0,
            dy=(prev.dy + nxt.dy) / 2.0,
            on_interior=prev.on_interior and nxt.on_interior,
        )
    return out


def _reject_offset_jumps(hits: list[_Hit | None]) -> list[_Hit | None]:
    valid = [hit.offset for hit in hits if hit is not None]
    if len(valid) < 2:
        return hits
    median = statistics.median(valid)
    limit = max(JUMP_ABS_M, JUMP_REL * median if median else JUMP_ABS_M)
    out: list[_Hit | None] = []
    prev: _Hit | None = None
    for hit in hits:
        if hit is None:
            out.append(None)
            prev = None
            continue
        if prev is not None and abs(hit.offset - prev.offset) > limit:
            out.append(None)
            prev = None
            continue
        out.append(hit)
        prev = hit
    return out


def _stitch_runs(hits: Sequence[_Hit | None], *, closed: bool) -> list[list[_Hit]]:
    runs: list[list[_Hit]] = []
    current: list[_Hit] = []
    for hit in hits:
        if hit is None:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(hit)
    if current:
        runs.append(current)
    if closed and len(runs) >= 2 and hits and hits[0] is not None and hits[-1] is not None:
        merged = runs[-1] + runs[0]
        runs = [merged, *runs[1:-1]]
    return _merge_nearby_runs([run for run in runs if len(run) >= 2])


def _merge_nearby_runs(runs: list[list[_Hit]], max_gap_m: float = 4.0) -> list[list[_Hit]]:
    if len(runs) <= 1:
        return [run for run in runs if len(run) >= 2]
    merged: list[list[_Hit]] = [list(runs[0])]
    for run in runs[1:]:
        prev = merged[-1]
        gap = _hypot(prev[-1].x - run[0].x, prev[-1].y - run[0].y)
        if gap <= max_gap_m:
            merged[-1] = prev + list(run)
        else:
            merged.append(list(run))
    return [run for run in merged if len(run) >= 2]


def _runs_to_lines(runs: Sequence[Sequence[_Hit]]) -> list[LineString]:
    out: list[LineString] = []
    for run in runs:
        coords = [(hit.x, hit.y) for hit in run]
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if line.length < POINT_LIKE_M:
            continue
        simplified = line.simplify(SIMPLIFY_TOL_M, preserve_topology=True)
        if (
            simplified.geom_type == 'LineString'
            and not simplified.is_empty
            and simplified.length >= POINT_LIKE_M
            and simplified.hausdorff_distance(line) <= SIMPLIFY_TOL_M * 2
        ):
            line = simplified
        out.append(line)
    return out


def _direction_score(hits: Sequence[_Hit], spec: SideSpec) -> float:
    if not hits:
        return 0.0
    wanted = set(spec.directions)
    if not wanted:
        return 0.0
    matches = 0
    for hit in hits:
        if displacement_compass(hit.dx, hit.dy) in wanted:
            matches += 1
    return matches / len(hits)


def _pick_wrapping(
    measured: _MeasuredTracks,
    spec: SideSpec,
    left_score: float,
    right_score: float,
) -> tuple[LineString | MultiLineString | None, float]:
    left_cover = _direction_set_cover(measured.left_hits, spec)
    right_cover = _direction_set_cover(measured.right_hits, spec)
    if left_cover > right_cover or (left_cover == right_cover and left_score >= right_score):
        return _combine_lines(measured.left_parts), max(left_cover, left_score)
    return _combine_lines(measured.right_parts), max(right_cover, right_score)


def _per_direction_hits(
    hits: Sequence[_Hit],
    wanted: frozenset[str] | set[str],
) -> dict[str, list[_Hit]]:
    """Group hits whose displacement compass is one of the wanted directions."""
    grouped: dict[str, list[_Hit]] = {}
    for hit in hits:
        comp = displacement_compass(hit.dx, hit.dy)
        if comp in wanted:
            grouped.setdefault(comp, []).append(hit)
    return grouped


def _per_direction_track_geometry(
    measured: _MeasuredTracks,
    spec: SideSpec,
    *,
    min_directions: int = 2,
) -> tuple[LineString | MultiLineString | None, float]:
    """
    Union per-direction curb segments when no single curb covers all wanted directions.

    Returns the combined geometry and the fraction of wanted directions that
    matched at least one measured run, or ``(None, 0.0)`` when fewer than
    *min_directions* directions found runs on the same side.
    """
    wanted = set(spec.directions)
    if not wanted:
        return None, 0.0
    best_side_hits: Sequence[_Hit] = ()
    best_matched: set[str] = set()
    for side_hits in (measured.left_hits, measured.right_hits):
        grouped = _per_direction_hits(side_hits, wanted)
        matched = set(grouped)
        if len(matched) > len(best_matched):
            best_side_hits = side_hits
            best_matched = matched
    if len(best_matched) < min_directions:
        return None, 0.0
    parts = _runs_to_lines(_stitch_runs(
        [hit if displacement_compass(hit.dx, hit.dy) in best_matched else None
         for hit in best_side_hits],
        closed=False,
    ))
    geometry = _combine_lines(parts)
    return geometry, len(best_matched) / len(wanted)


def _direction_set_cover(hits: Sequence[_Hit], spec: SideSpec) -> float:
    wanted = set(spec.directions)
    if not wanted or not hits:
        return 0.0
    seen = {displacement_compass(hit.dx, hit.dy) for hit in hits}
    return len(wanted & seen) / len(wanted)


def _select_ring(
    line_m: LineString,
    measured: _MeasuredTracks,
    spec: SideSpec,
    *,
    road_index: RoadEdgeIndex | None,
) -> LineString | MultiLineString | None:
    if spec.ring is None:
        return None
    has_holes = False
    if road_index is not None:
        _cand_oids, _cand_geoms, _cand_bounds, cand_ints = road_index.query_road_candidates_within(line_m, RAY_MAX_M)
        has_holes = any(interiors is not None for interiors in cand_ints)
    if not has_holes:
        return None
    interior_hits = sorted(
        (hit for hit in (*measured.left_hits, *measured.right_hits) if hit.on_interior),
        key=lambda hit: hit.s,
    )
    exterior_hits = sorted(
        (hit for hit in (*measured.left_hits, *measured.right_hits) if not hit.on_interior),
        key=lambda hit: hit.s,
    )
    chosen = interior_hits if spec.ring == 'inner' else exterior_hits
    if not chosen:
        return None
    return _combine_lines(_runs_to_lines(_stitch_runs(chosen, closed=_is_closed(line_m))))


def _fallback_direction_scores(line_m: LineString, spec: SideSpec) -> tuple[float, float]:
    wanted = set(spec.directions)
    samples = _regular_samples(line_m, FALLBACK_SAMPLE_COUNT)
    if not samples or not wanted:
        return 0.0, 0.0
    left_matches = 0
    right_matches = 0
    for _s, _origin, _tangent, normal in samples:
        if displacement_compass(normal[0], normal[1]) in wanted:
            left_matches += 1
        if displacement_compass(-normal[0], -normal[1]) in wanted:
            right_matches += 1
    n = len(samples)
    return left_matches / n, right_matches / n


def _regular_samples(
    line_m: LineString,
    count: int,
) -> list[tuple[float, Point, tuple[float, float], tuple[float, float]]]:
    length = line_m.length
    if length <= 0:
        return []
    closed = _is_closed(line_m)
    n = max(3, count)
    distances = [i * length / n for i in range(n)] if closed else [
        i * length / (n - 1) for i in range(n)
    ]
    out: list[tuple[float, Point, tuple[float, float], tuple[float, float]]] = []
    for s in distances:
        origin = line_m.interpolate(s)
        tangent = _smoothed_tangent(line_m, s, length)
        mag = _hypot(tangent[0], tangent[1])
        if mag < 1e-9:
            continue
        unit = (tangent[0] / mag, tangent[1] / mag)
        out.append((s, origin, unit, (-unit[1], unit[0])))
    return out


def _offset_candidate(line_m: LineString, distance: float) -> LineString | MultiLineString | None:
    try:
        offset = line_m.offset_curve(distance, join_style='round')
    except Exception:
        return None
    return flatten_line_geometry(offset)


def _curb_geometry_ok(geom: BaseGeometry | None) -> bool:
    flat = flatten_line_geometry(geom)
    if flat is None or flat.is_empty:
        return False
    if not flat.is_valid or not _all_finite(flat):
        return False
    if flat.length < POINT_LIKE_M:
        return False
    if not flat.is_simple:
        return False
    return True


def _combine_lines(parts: Sequence[BaseGeometry | None]) -> LineString | MultiLineString | None:
    lines: list[LineString] = []
    for part in parts:
        lines.extend(iter_line_parts(part))
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    return MultiLineString(lines)


def _combine_part_results(
    results: Sequence[CurbGeometryResult],
    spec: SideSpec,
) -> CurbGeometryResult:
    if not results:
        return _unresolved_result(LineString(), spec, warnings=(CURB_INVALID, CENTERLINE_FALLBACK))
    if len(results) == 1:
        return results[0]
    geoms = [result.geometry for result in results]
    combined = _combine_lines(geoms) or results[0].geometry
    if all(result.method == METHOD_ROAD_EDGE for result in results):
        method = METHOD_ROAD_EDGE
    elif all(result.method == METHOD_CENTERLINE_UNRESOLVED for result in results):
        method = METHOD_CENTERLINE_UNRESOLVED
    else:
        method = METHOD_OFFSET_FALLBACK
    warnings: list[str] = []
    for result in results:
        warnings.extend(result.warnings)
    offsets = tuple(off for result in results for off in result.measured_offsets_m)
    medians = [result.median_offset_m for result in results if result.median_offset_m is not None]
    ids: list[int] = []
    for result in results:
        ids.extend(result.road_edge_object_ids)
    coverage = sum(result.coverage for result in results) / len(results)
    confidence = min(result.confidence for result in results)
    return CurbGeometryResult(
        geometry=combined,
        method=method,
        confidence=confidence,
        coverage=coverage,
        median_offset_m=statistics.median(medians) if medians else None,
        measured_offsets_m=offsets,
        warnings=_unique_warnings(warnings),
        road_edge_object_ids=tuple(sorted(set(ids))),
        side_mode=spec.mode,
        override=any(result.override for result in results),
        directional_margin=min(result.directional_margin for result in results),
    )


def _unresolved_from_parts(
    parts_m: Sequence[LineString],
    spec: SideSpec,
    *,
    warnings: tuple[str, ...],
) -> CurbGeometryResult:
    geom = _combine_lines(list(parts_m)) or LineString()
    return _unresolved_result(geom, spec, warnings=warnings)


def _unresolved_result(
    geometry: LineString | MultiLineString,
    spec: SideSpec,
    *,
    warnings: Iterable[str],
    override: bool = False,
) -> CurbGeometryResult:
    geom = flatten_line_geometry(geometry) or geometry
    return CurbGeometryResult(
        geometry=geom if isinstance(geom, LineString | MultiLineString) else LineString(),
        method=METHOD_CENTERLINE_UNRESOLVED,
        confidence=0.0,
        coverage=0.0,
        median_offset_m=None,
        measured_offsets_m=(),
        warnings=_unique_warnings(warnings),
        road_edge_object_ids=(),
        side_mode=spec.mode,
        override=override,
    )


def _project_result(result: CurbGeometryResult, to_metres: bool) -> CurbGeometryResult:
    geom = flatten_line_geometry(result.geometry) or result.geometry
    if to_metres and geom is not None and not geom.is_empty:
        geom = transform(_to_ll, geom)
        flat = flatten_line_geometry(geom)
        geom = flat if flat is not None else geom
    if not isinstance(geom, LineString | MultiLineString):
        geom = result.geometry
    return CurbGeometryResult(
        geometry=geom,
        method=result.method,
        confidence=result.confidence,
        coverage=result.coverage,
        median_offset_m=result.median_offset_m,
        measured_offsets_m=result.measured_offsets_m,
        warnings=result.warnings,
        road_edge_object_ids=result.road_edge_object_ids,
        side_mode=result.side_mode,
        override=result.override,
        directional_margin=result.directional_margin,
    )


def _collect_line_parts(geom: BaseGeometry | None, out: list[LineString]) -> None:
    if geom is None or geom.is_empty:
        return
    geom_type = geom.geom_type
    if geom_type in {'LineString', 'LinearRing'}:
        # Drop only collapsed parts. Do not apply the metre POINT_LIKE_M cutoff
        # here: flatten also runs on EPSG:4326 output whose length is in degrees.
        if _all_finite(geom) and len(geom.coords) >= 2 and geom.length > 0:
            coords = [(float(x), float(y)) for x, y, *_ in geom.coords]
            line = LineString(coords)
            if line.length > 0:
                out.append(line)
        return
    if geom_type == 'MultiLineString':
        for part in geom.geoms:
            _collect_line_parts(part, out)
        return
    if geom_type == 'GeometryCollection':
        for part in geom.geoms:
            _collect_line_parts(part, out)


def _iter_hit_points(geom: BaseGeometry | None, origin: Point) -> Iterable[Point]:
    if geom is None or geom.is_empty:
        return
    geom_type = geom.geom_type
    if geom_type == 'Point':
        yield geom
        return
    if geom_type == 'MultiPoint':
        yield from geom.geoms
        return
    if geom_type in {'LineString', 'MultiLineString', 'GeometryCollection'}:
        if geom_type == 'GeometryCollection':
            for part in geom.geoms:
                yield from _iter_hit_points(part, origin)
            return
        nearest = nearest_points(origin, geom)[1]
        yield nearest


def _row_object_id(row: Any) -> int:
    for key in ('OBJECTID', 'objectid', 'OBJECT_ID'):
        if key in getattr(row, 'index', ()):
            val = row[key]
            if val is None:
                continue
            try:
                if pd.isna(val):
                    continue
            except (TypeError, ValueError):
                pass
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    name = getattr(row, 'name', 0)
    try:
        return int(name)
    except (TypeError, ValueError):
        return 0


def _is_closed(line_m: LineString) -> bool:
    if line_m.is_ring:
        return True
    c0 = line_m.coords[0]
    c1 = line_m.coords[-1]
    return _hypot(c0[0] - c1[0], c0[1] - c1[1]) <= CLOSED_TOL_M


def _all_finite(geom: BaseGeometry) -> bool:
    for coord in _iter_coords(geom):
        if not math.isfinite(coord[0]) or not math.isfinite(coord[1]):
            return False
    return True


def _iter_coords(geom: BaseGeometry) -> Iterable[tuple[float, float]]:
    geom_type = geom.geom_type
    if geom_type in {'LineString', 'LinearRing', 'Point'}:
        for x, y, *_ in geom.coords:
            yield (float(x), float(y))
        return
    if hasattr(geom, 'geoms'):
        for part in geom.geoms:
            yield from _iter_coords(part)


def _unique_warnings(codes: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return tuple(out)


def _as_warning_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.split('|') if part]
    if isinstance(value, list | tuple):
        return [str(part) for part in value if part]
    return []


def _percentile(ordered: Sequence[float], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _hypot(dx: float, dy: float) -> float:
    return math.hypot(dx, dy)

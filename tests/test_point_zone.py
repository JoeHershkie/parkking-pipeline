"""Tests for the point-zone fallback when both endpoints collapse to one point."""

from __future__ import annotations

import pyproj
from shapely.geometry import LineString
from shapely.ops import transform

from parking_pipeline.geo_slice import (
    CENTRELINE_POINT_ZONE,
    ZERO_SPAN,
    _point_zone_slice,
    slice_between_distances,
)

_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform


def _synthetic_line(length_m: float = 300.0) -> LineString:
    # Metric coordinates (EPSG:32617); only along-line lengths matter here.
    return LineString([(500000.0, y) for y in (0.0, length_m)])


def _length_m(result) -> float:
    return transform(_to_meters, result.geometry).length


def test_point_zone_mid_line_returns_5m_zone() -> None:
    result = _point_zone_slice('synthetic street', _synthetic_line(), 150.0)
    assert result.ok, result.reason_code
    assert result.construction_method == CENTRELINE_POINT_ZONE
    assert 4.0 <= _length_m(result) <= 6.0


def test_point_zone_at_start_clamps_to_line_start() -> None:
    result = _point_zone_slice('synthetic street', _synthetic_line(), 0.0)
    assert result.ok, result.reason_code
    assert 4.0 <= _length_m(result) <= 6.0


def test_point_zone_at_end_prefers_inbound_zone() -> None:
    result = _point_zone_slice('synthetic street', _synthetic_line(60.0), 60.0)
    assert result.ok, result.reason_code
    assert 4.0 <= _length_m(result) <= 6.0


def test_point_zone_short_line_clamps_to_full_length() -> None:
    line = LineString([(500000.0, 0.0), (500000.0, 3.0)])
    result = _point_zone_slice('synthetic street', line, 1.5)
    assert result.ok, result.reason_code
    assert 2.0 <= _length_m(result) <= 3.0


def test_point_zone_degenerate_line_yields_zero_span() -> None:
    line = LineString([(500000.0, 0.0), (500000.0, 0.0005)])
    result = _point_zone_slice('synthetic street', line, 0.00025)
    assert not result.ok
    assert result.reason_code == ZERO_SPAN


def test_slice_between_distances_still_zero_span_on_collapse() -> None:
    line = _synthetic_line()
    result = slice_between_distances(line, line, 100.0, 100.0)
    assert not result.ok
    assert result.reason_code == ZERO_SPAN


def test_slice_between_distances_point_substring_recovery() -> None:
    """Substring degeneration (returns non-LineString) recovers with point zone."""
    line = _synthetic_line()
    # Simulate a degenerate substring by asking for a span at the exact end.
    result = slice_between_distances(line, line, 300.0, 300.0 + 1e-9, highway='synthetic street')
    assert result.ok, result.reason_code
    assert result.construction_method == CENTRELINE_POINT_ZONE

"""Tests for ZERO_SPAN geometry outcome."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from geometry_engine import ZERO_SPAN, SliceResult  # noqa: E402


def test_zero_span_result_not_ok() -> None:
    result = SliceResult(None, ZERO_SPAN, 'anchor equals terminus; no mappable span')
    assert not result.ok
    assert result.reason_code == ZERO_SPAN

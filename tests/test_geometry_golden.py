"""Golden-output regression for centreline slicing and final curb geometry."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from geometry_golden_util import (
    DEFAULT_SAMPLE_CSV,
    _clear_geo_lookup_caches,
    collect_centreline_golden_records,
    collect_curb_golden_records,
    collect_resolved_geo_row_args,
    geometry_to_coords,
    load_geometry_golden,
    reset_curb_runtime,
    setup_geo_env,
)
from sample_data import using_sample_tcl
from shapely.geometry.base import BaseGeometry

from parking_pipeline import geo_indices as gi
from parking_pipeline import geometry_engine as ge
from parking_pipeline.resolve_rows import _init_resolve_index

FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'geometry_golden.json'
CURB_FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'curb_geometry_golden.json'
LINE_TYPES = {'LineString', 'MultiLineString'}


@pytest.fixture(scope='module', autouse=True)
def restore_full_tcl_after_sample_golden() -> None:
    """Golden helpers pin sample TCL; reload local full extracts afterwards."""
    yield
    if using_sample_tcl():
        return
    reset_curb_runtime()
    _clear_geo_lookup_caches()
    gi.init_geo(force=True)
    _init_resolve_index()


@pytest.fixture(scope='module')
def golden_payload() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip('geometry_golden.json not found; run scripts/build_geometry_golden.py')
    return load_geometry_golden(FIXTURE_PATH)


@pytest.fixture(scope='module')
def current_records() -> list[dict]:
    if not DEFAULT_SAMPLE_CSV.exists():
        pytest.skip('sample clean_parking_targets.csv missing')
    return collect_centreline_golden_records(DEFAULT_SAMPLE_CSV)


@pytest.fixture(scope='module')
def curb_golden_payload() -> dict:
    if not CURB_FIXTURE_PATH.exists():
        pytest.skip('curb_geometry_golden.json not found; run scripts/build_geometry_golden.py')
    return load_geometry_golden(CURB_FIXTURE_PATH)


@pytest.fixture(scope='module')
def current_curb_records() -> list[dict]:
    if not DEFAULT_SAMPLE_CSV.exists():
        pytest.skip('sample clean_parking_targets.csv missing')
    return collect_curb_golden_records(DEFAULT_SAMPLE_CSV)


def _assert_records_match(actual_records: list[dict], expected_records: list[dict]) -> None:
    assert len(actual_records) == len(expected_records), (
        f'row count changed: expected {len(expected_records)}, got {len(actual_records)}'
    )
    mismatches: list[str] = []
    for actual, exp in zip(actual_records, expected_records, strict=True):
        if actual == exp:
            continue
        mismatches.append(
            f'{actual["_id"]}: expected {json.dumps(exp, sort_keys=True)} '
            f'!= actual {json.dumps(actual, sort_keys=True)}'
        )
    assert not mismatches, 'geometry golden mismatch:\n' + '\n'.join(mismatches[:5])


def test_geometry_golden_matches_fixture(
    golden_payload: dict,
    current_records: list[dict],
) -> None:
    assert golden_payload.get('kind', 'centreline_span') == 'centreline_span'
    _assert_records_match(current_records, golden_payload['records'])


def test_geometry_golden_exercises_geo_slice(current_records: list[dict]) -> None:
    geo_rows = [record for record in current_records if record.get('stage') == 'geo']
    assert len(geo_rows) >= 5, 'expected at least 5 rows to reach geo stage on sample cohort'
    successes = [record for record in geo_rows if record.get('reason_code') is None]
    assert len(successes) >= 4, 'expected at least 4 successful geo slices on sample cohort'
    with_ids = [record for record in successes if record.get('centreline_ids')]
    assert with_ids, 'centreline golden should record recovered or path edge IDs'


def test_curb_geometry_golden_matches_fixture(
    curb_golden_payload: dict,
    current_curb_records: list[dict],
) -> None:
    assert curb_golden_payload.get('kind') == 'curb'
    manifest = curb_golden_payload.get('source_manifest') or {}
    assert manifest.get('is_sample_fixture') is True
    assert manifest.get('fetch_time')
    _assert_records_match(current_curb_records, curb_golden_payload['records'])


def test_curb_geometry_golden_has_qa_fields(current_curb_records: list[dict]) -> None:
    successes = [
        record for record in current_curb_records
        if record.get('stage') == 'geo' and record.get('reason_code') is None
    ]
    assert len(successes) >= 4
    for record in successes:
        assert record['curb_geometry_method'] in {
            'road_edge', 'offset_fallback', 'centerline_unresolved',
        }
        assert isinstance(record['curb_confidence'], float)
        assert isinstance(record['curb_coverage'], float)
        assert record['side_mode']
        assert record['curb_warnings'] is not None
        assert record['curb_override'] is False or record['curb_override'] is True
        assert record['geom_type'] in LINE_TYPES
        assert record['coords']


def test_curb_geometry_golden_line_only(current_curb_records: list[dict]) -> None:
    for record in current_curb_records:
        if record.get('geom_type') is None:
            continue
        assert record['geom_type'] in LINE_TYPES
        assert record['geom_type'] not in {'Point', 'MultiPoint', 'GeometryCollection', 'Polygon'}


def _payload_snapshot(payload: dict | None, failure: dict | None) -> dict:
    if payload is None:
        return {'ok': False, 'failure': failure}
    geom: BaseGeometry = payload['geometry']
    return {
        'ok': True,
        '_id': payload['_id'],
        'geom_type': geom.geom_type,
        'coords': geometry_to_coords(geom),
        'curb_geometry_method': payload.get('curb_geometry_method'),
        'curb_confidence': payload.get('curb_confidence'),
        'curb_coverage': payload.get('curb_coverage'),
        'curb_warnings': list(payload.get('curb_warnings') or []),
        'centreline_ids': list(payload.get('centreline_ids') or []),
        'curb_override': bool(payload.get('curb_override')),
    }


def test_geo_workers_do_not_change_geometry() -> None:
    if not DEFAULT_SAMPLE_CSV.exists():
        pytest.skip('sample clean_parking_targets.csv missing')
    setup_geo_env(force=True)
    row_args = collect_resolved_geo_row_args(DEFAULT_SAMPLE_CSV)
    assert row_args, 'expected resolved sample rows for worker determinism'

    sequential = [_payload_snapshot(*ge._process_geo_row(args)) for args in row_args]
    with ThreadPoolExecutor(max_workers=2) as pool:
        parallel = [
            _payload_snapshot(*outcome)
            for outcome in pool.map(ge._process_geo_row, row_args)
        ]
    assert sequential == parallel

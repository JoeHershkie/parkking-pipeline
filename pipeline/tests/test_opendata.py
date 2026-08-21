"""Tests for Toronto Open Data CKAN dump refresh."""

from __future__ import annotations

import io
import json
from urllib.error import URLError
from urllib.request import Request

import pytest

from parking_pipeline import fullrun
from parking_pipeline import opendata as od
from parking_pipeline.opendata import RawDumpError

DUMP_CSV = (
    '_id,Latest_Action,scheduleName,ByLaw_Table\n'
    '1,Enacted,Schedule 13: No Parking,"[{""key"": ""Highway"", ""value"": ""Main St""}]"\n'
)

SIDECAR_RESOURCE = {
    'id': 'files-json-id',
    'name': 'Traffic and parking by-law schedules',
    'datastore_active': False,
    'format': 'JSON',
    'size': 796_980_526,
    'url': 'https://example.invalid/files.json',
    'last_modified': '2026-08-20T13:59:23',
}

DATASTORE_RESOURCE = {
    'id': 'datastore-id',
    'name': 'Traffic and parking by-law schedules data',
    'datastore_active': True,
    'format': 'JSON',
    'record_count': 76_849,
    'last_modified': None,
    'metadata_modified': '2026-05-18T02:27:53.675515',
    'datastore_cache_last_update': '2026-05-18T02:27:53.243962',
}


def _package(**resource_overrides) -> dict:
    resource = {**DATASTORE_RESOURCE, **resource_overrides}
    return {
        'name': od.PACKAGE_ID,
        'last_refreshed': '2026-08-20 13:59:23',
        'resources': [resource, SIDECAR_RESOURCE],
    }


class FakeResponse:
    def __init__(self, body: bytes):
        self._buf = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read() if n < 0 else self._buf.read(n)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture
def dump_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(od, 'data_path', lambda name: tmp_path / name)
    return tmp_path


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(od.time, 'sleep', lambda _s: None)


def _install_http(monkeypatch, mapping: dict[str, bytes | Exception]):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        for prefix, payload in mapping.items():
            if prefix in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError(f'unexpected URL {url}')

    monkeypatch.setattr(od, 'urlopen', fake_urlopen)


def _ckan_body(package: dict) -> bytes:
    return json.dumps({'success': True, 'result': package}).encode()


def test_select_dump_resource_ignores_uploaded_sidecar():
    resource = od.select_dump_resource(_package())
    assert resource['id'] == 'datastore-id'
    assert resource['datastore_active'] is True


def test_select_dump_resource_requires_datastore():
    package = _package()
    package['resources'] = [SIDECAR_RESOURCE]
    with pytest.raises(RawDumpError, match='datastore_active'):
        od.select_dump_resource(package)


def test_ensure_downloads_when_missing(dump_dir, monkeypatch, no_sleep):
    _install_http(
        monkeypatch,
        {
            'package_show': _ckan_body(_package()),
            'datastore/dump/datastore-id': DUMP_CSV.encode(),
        },
    )
    path = od.ensure_raw_parking_dump()
    assert path.exists()
    header = path.read_text(encoding='utf-8').splitlines()[0]
    assert 'ByLaw_Table' in header
    manifest = json.loads((dump_dir / od.RAW_DUMP_MANIFEST_FILENAME).read_text())
    assert manifest['resource_id'] == 'datastore-id'
    assert manifest['record_count'] == 76_849


def test_ensure_skips_download_when_current(dump_dir, monkeypatch, no_sleep):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')
    fingerprint = od.resource_fingerprint(_package(), DATASTORE_RESOURCE)
    od.write_manifest(fingerprint)

    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        calls.append(url)
        if 'package_show' in url:
            return FakeResponse(_ckan_body(_package()))
        raise AssertionError(f'dump should not be downloaded: {url}')

    monkeypatch.setattr(od, 'urlopen', fake_urlopen)
    od.ensure_raw_parking_dump()
    assert calls and all('datastore/dump' not in url for url in calls)


def test_ensure_redownloads_when_record_count_changes(dump_dir, monkeypatch, no_sleep):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')
    od.write_manifest(od.resource_fingerprint(_package(), DATASTORE_RESOURCE))

    _install_http(
        monkeypatch,
        {
            'package_show': _ckan_body(_package(record_count=77_000)),
            'datastore/dump/datastore-id': DUMP_CSV.encode(),
        },
    )
    od.ensure_raw_parking_dump()
    manifest = json.loads((dump_dir / od.RAW_DUMP_MANIFEST_FILENAME).read_text())
    assert manifest['record_count'] == 77_000


def test_force_redownloads_current_dump(dump_dir, monkeypatch, no_sleep):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')
    od.write_manifest(od.resource_fingerprint(_package(), DATASTORE_RESOURCE))
    downloaded = {'n': 0}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        if 'package_show' in url:
            return FakeResponse(_ckan_body(_package()))
        if 'datastore/dump' in url:
            downloaded['n'] += 1
            return FakeResponse(DUMP_CSV.encode())
        raise AssertionError(url)

    monkeypatch.setattr(od, 'urlopen', fake_urlopen)
    od.ensure_raw_parking_dump(force=True)
    assert downloaded['n'] == 1


def test_skip_uses_local_file(dump_dir, monkeypatch):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')

    def boom(*_a, **_k):
        raise AssertionError('network should not be used')

    monkeypatch.setattr(od, 'urlopen', boom)
    assert od.ensure_raw_parking_dump(skip=True) == dest


def test_skip_without_local_file_raises(dump_dir):
    with pytest.raises(RawDumpError, match='Missing'):
        od.ensure_raw_parking_dump(skip=True)


def test_skip_env_flag(dump_dir, monkeypatch):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')
    monkeypatch.setenv('PARKING_SKIP_OPENDATA', '1')

    def boom(*_a, **_k):
        raise AssertionError('network should not be used')

    monkeypatch.setattr(od, 'urlopen', boom)
    od.ensure_raw_parking_dump()


def test_metadata_failure_keeps_existing_dump(dump_dir, monkeypatch, no_sleep):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')
    _install_http(monkeypatch, {'package_show': URLError('offline')})
    assert od.ensure_raw_parking_dump() == dest


def test_metadata_failure_without_dump_raises(dump_dir, monkeypatch, no_sleep):
    _install_http(monkeypatch, {'package_show': URLError('offline')})
    with pytest.raises(RawDumpError, match='Failed to GET'):
        od.ensure_raw_parking_dump()


def test_invalid_csv_does_not_replace_existing(dump_dir, monkeypatch, no_sleep):
    dest = dump_dir / od.RAW_DUMP_FILENAME
    dest.write_text(DUMP_CSV, encoding='utf-8')
    _install_http(
        monkeypatch,
        {
            'package_show': _ckan_body(_package(record_count=99)),
            'datastore/dump/datastore-id': b'not,a,valid,parking,dump\n1,2,3\n',
        },
    )
    path = od.ensure_raw_parking_dump()
    assert path.read_text(encoding='utf-8') == DUMP_CSV


def test_fullrun_sets_refresh_env(monkeypatch):
    monkeypatch.delenv('PARKING_SKIP_OPENDATA', raising=False)
    monkeypatch.delenv('PARKING_FORCE_OPENDATA', raising=False)
    fullrun.apply_refresh_env(skip=True, force=True, verbose=True)
    assert fullrun.os.environ['PARKING_SKIP_OPENDATA'] == '1'
    assert fullrun.os.environ['PARKING_FORCE_OPENDATA'] == '1'
    assert fullrun.os.environ['PARKING_VERBOSE'] == '1'


def test_fullrun_parses_refresh_flags(monkeypatch):
    monkeypatch.setattr(fullrun, 'PIPELINE_MODULES', ())
    monkeypatch.setattr(fullrun, 'ANALYSIS_SCRIPTS', ())
    monkeypatch.delenv('PARKING_SKIP_OPENDATA', raising=False)
    assert fullrun.main(['--skip-refresh', '--keep-going']) == 0
    assert fullrun.os.environ['PARKING_SKIP_OPENDATA'] == '1'

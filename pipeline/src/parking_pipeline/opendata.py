"""Fetch the Toronto parking bylaw dump from CKAN (Open Data).

``parking-clean`` / ``parking-run`` call :func:`ensure_raw_parking_dump` so a
pipeline run refreshes ``data/toronto_raw_parking_dump.csv`` when the catalogue
copy is newer than the local file. Geocoding still uses local TCL / Road Edge
files; this module only downloads the bylaw datastore CSV.

Catalogue: https://open.toronto.ca/dataset/traffic-and-parking-by-law-schedules/
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .paths import data_path

log = logging.getLogger(__name__)

CKAN_BASE_URL = 'https://ckan0.cf.opendata.inter.prod-toronto.ca'
PACKAGE_ID = 'traffic-and-parking-by-law-schedules'
CATALOGUE_URL = 'https://open.toronto.ca/dataset/traffic-and-parking-by-law-schedules/'
RAW_DUMP_FILENAME = 'toronto_raw_parking_dump.csv'
RAW_DUMP_MANIFEST_FILENAME = 'toronto_raw_parking_dump.manifest.json'
USER_AGENT = 'parking-pipeline/opendata'
REQUIRED_COLUMNS = ('_id', 'Latest_Action', 'scheduleName', 'ByLaw_Table')
PACKAGE_TIMEOUT_SEC = 30
DUMP_TIMEOUT_SEC = 300
DATASTORE_PAGE_SIZE = 10000
CHUNK_SIZE = 1024 * 1024


class RawDumpError(Exception):
    """Raised when the CKAN dump cannot be fetched or validated."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').lower() in {'1', 'true', 'yes'}


def skip_opendata_refresh() -> bool:
    return _env_flag('PARKING_SKIP_OPENDATA')


def force_opendata_refresh() -> bool:
    return _env_flag('PARKING_FORCE_OPENDATA')


def dump_path() -> Path:
    return data_path(RAW_DUMP_FILENAME)


def manifest_path() -> Path:
    return data_path(RAW_DUMP_MANIFEST_FILENAME)


def _http_open(url: str, *, timeout: int, retries: int = 4):
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={'User-Agent': USER_AGENT})
            return urlopen(req, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_err = exc
            if attempt + 1 >= retries:
                break
            delay = 2 ** attempt
            log.warning('Request failed (%s); retrying in %ss', exc, delay)
            time.sleep(delay)
    raise RawDumpError(f'Failed to GET {url}: {last_err}') from last_err


def _http_get(url: str, *, timeout: int, retries: int = 4) -> bytes:
    with _http_open(url, timeout=timeout, retries=retries) as resp:
        chunks: list[bytes] = []
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        return b''.join(chunks)


def _http_download_to(url: str, dest: Path, *, timeout: int, retries: int = 4) -> None:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with _http_open(url, timeout=timeout, retries=1) as resp, dest.open('wb') as out:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
            return
        except (HTTPError, URLError, TimeoutError, OSError, RawDumpError) as exc:
            last_err = exc
            delay = 2 ** attempt
            log.warning('Download failed (%s); retrying in %ss', exc, delay)
            if attempt + 1 >= retries:
                break
            time.sleep(delay)
    raise RawDumpError(f'Failed to download {url}: {last_err}') from last_err


def _http_get_json(url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    full = url if not params else f'{url}?{urlencode(params)}'
    try:
        payload = json.loads(_http_get(full, timeout=PACKAGE_TIMEOUT_SEC).decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RawDumpError(f'CKAN response was not JSON: {full}') from exc
    if not isinstance(payload, dict) or not payload.get('success'):
        raise RawDumpError(f'CKAN request failed: {full}')
    result = payload.get('result')
    if not isinstance(result, dict):
        raise RawDumpError(f'CKAN response missing result: {full}')
    return result


def fetch_package() -> dict[str, Any]:
    return _http_get_json(
        f'{CKAN_BASE_URL}/api/3/action/package_show',
        {'id': PACKAGE_ID},
    )


def select_dump_resource(package: dict[str, Any]) -> dict[str, Any]:
    """Pick the datastore CSV resource; ignore uploaded sidecar files."""
    resources = package.get('resources') or []
    active = [r for r in resources if isinstance(r, dict) and r.get('datastore_active')]
    if not active:
        raise RawDumpError(
            f'CKAN package {PACKAGE_ID!r} has no datastore_active resource'
        )
    active.sort(key=lambda r: int(r.get('record_count') or 0), reverse=True)
    return active[0]


def dump_url_for(resource: dict[str, Any]) -> str:
    resource_id = resource.get('id')
    if not resource_id:
        raise RawDumpError('Datastore resource is missing an id')
    return f'{CKAN_BASE_URL}/datastore/dump/{resource_id}'


def resource_fingerprint(package: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
    return {
        'package_id': package.get('name') or PACKAGE_ID,
        'resource_id': resource.get('id'),
        'resource_name': resource.get('name'),
        'record_count': resource.get('record_count'),
        'last_modified': resource.get('last_modified'),
        'metadata_modified': resource.get('metadata_modified'),
        'datastore_cache_last_update': resource.get('datastore_cache_last_update'),
        'package_last_refreshed': package.get('last_refreshed'),
        'source_url': dump_url_for(resource),
        'catalogue_url': CATALOGUE_URL,
    }


def _fingerprint_keys() -> tuple[str, ...]:
    return (
        'resource_id',
        'record_count',
        'last_modified',
        'metadata_modified',
        'datastore_cache_last_update',
    )


def load_manifest(path: Path | None = None) -> dict[str, Any] | None:
    target = path or manifest_path()
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_manifest(fingerprint: dict[str, Any], *, dest: Path | None = None) -> Path:
    target = dest or manifest_path()
    payload = {**fingerprint, 'fetched_at': datetime.now(UTC).isoformat()}
    target.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return target


def dump_is_stale(manifest: dict[str, Any] | None, fingerprint: dict[str, Any]) -> bool:
    if manifest is None:
        return True
    return any(manifest.get(key) != fingerprint.get(key) for key in _fingerprint_keys())


def _csv_header(path: Path) -> list[str]:
    with path.open(encoding='utf-8', newline='') as handle:
        first = handle.readline()
    if not first.strip():
        return []
    return [col.strip().lstrip('\ufeff') for col in first.strip().split(',')]


def validate_dump_csv(path: Path, *, expected_records: int | None = None) -> int:
    """Validate that the CSV exists, contains required columns, and is not truncated."""
    if not path.exists() or path.stat().st_size == 0:
        raise RawDumpError(f'Downloaded dump is empty: {path}')
    header = _csv_header(path)
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise RawDumpError(
            f'Downloaded dump is missing columns {missing}; header={header[:12]}'
        )

    row_count = 0
    try:
        with path.open(encoding='utf-8', newline='', errors='replace') as handle:
            reader = csv.reader(handle, strict=True)
            next(reader, None)  # header
            for _ in reader:
                row_count += 1
    except Exception as exc:
        raise RawDumpError(f'Downloaded dump is malformed or truncated: {exc}') from exc

    if row_count == 0:
        raise RawDumpError(f'Downloaded dump contains no data rows: {path}')

    if expected_records is not None and expected_records > 0:
        if row_count != expected_records:
            raise RawDumpError(
                f'Downloaded dump record count mismatch: expected {expected_records}, got {row_count}'
            )

    return row_count


def _download_datastore_csv(
    resource_id: str,
    dest: Path,
    *,
    expected_count: int | None = None,
    page_size: int = DATASTORE_PAGE_SIZE,
) -> None:
    """Download records via CKAN's datastore_search API in pages and stream to CSV."""
    offset = 0
    writer: csv.DictWriter | None = None
    total = expected_count

    with dest.open('w', encoding='utf-8', newline='') as out:
        while True:
            params = {
                'resource_id': resource_id,
                'limit': str(page_size),
                'offset': str(offset),
            }
            url = f'{CKAN_BASE_URL}/api/3/action/datastore_search'
            data = _http_get_json(url, params)
            records = data.get('records', [])
            if total is None and 'total' in data:
                try:
                    total = int(data['total'])
                except (ValueError, TypeError):
                    total = None

            if writer is None:
                fields = data.get('fields', [])
                if fields and isinstance(fields, list):
                    fieldnames = [
                        f['id'] if isinstance(f, dict) and 'id' in f else str(f)
                        for f in fields
                    ]
                elif records:
                    fieldnames = list(records[0].keys())
                else:
                    fieldnames = list(REQUIRED_COLUMNS)

                writer = csv.DictWriter(
                    out,
                    fieldnames=fieldnames,
                    extrasaction='ignore',
                    lineterminator='\n',
                )
                writer.writeheader()

            for rec in records:
                writer.writerow(rec)

            count_fetched = len(records)
            offset += count_fetched

            if total:
                log.info('Fetched %d/%d records...', min(offset, total), total)
            else:
                log.info('Fetched %d records...', offset)

            if count_fetched == 0:
                break
            if total is not None and offset >= total:
                break


def _download_dump(resource: dict[str, Any], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + '.partial')
    resource_id = resource.get('id')
    expected_count = int(resource.get('record_count') or 0) or None

    try:
        downloaded = False
        if resource.get('datastore_active') and resource_id:
            try:
                _download_datastore_csv(
                    resource_id,
                    partial,
                    expected_count=expected_count,
                )
                downloaded = True
            except RawDumpError as exc:
                log.warning('datastore_search failed (%s); trying direct dump URL', exc)

        if not downloaded:
            url = dump_url_for(resource)
            _http_download_to(url, partial, timeout=DUMP_TIMEOUT_SEC)

        validate_dump_csv(partial, expected_records=expected_count)
        partial.replace(dest)
    finally:
        if partial.exists():
            partial.unlink(missing_ok=True)


def ensure_raw_parking_dump(*, force: bool = False, skip: bool = False) -> Path:
    """Return the local dump path, refreshing from CKAN when needed."""
    dest = dump_path()
    skip = skip or skip_opendata_refresh()
    force = force or force_opendata_refresh()

    if skip:
        if dest.exists():
            log.info('Skipping Open Data refresh; using %s', dest)
            return dest
        raise RawDumpError(
            f'Missing {dest}. Re-run without --skip-refresh, or download the '
            f'datastore CSV from {CATALOGUE_URL}'
        )

    try:
        package = fetch_package()
        resource = select_dump_resource(package)
        fingerprint = resource_fingerprint(package, resource)
    except RawDumpError:
        if dest.exists():
            log.warning('Open Data metadata unavailable; using existing %s', dest)
            return dest
        raise

    if dest.exists() and not force and not dump_is_stale(load_manifest(), fingerprint):
        log.info(
            'Raw dump is current (%s records, resource %s)',
            fingerprint.get('record_count'),
            fingerprint.get('resource_id'),
        )
        return dest

    log.info(
        'Refreshing %s from Toronto Open Data (%s records)...',
        dest.name,
        fingerprint.get('record_count'),
    )
    try:
        _download_dump(resource, dest)
    except RawDumpError:
        if dest.exists() and not force:
            log.warning('Open Data dump download failed; using existing %s', dest)
            return dest
        raise

    write_manifest(fingerprint)
    log.info('Wrote %s', dest)
    return dest

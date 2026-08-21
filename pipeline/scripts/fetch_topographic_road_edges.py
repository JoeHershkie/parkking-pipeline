#!/usr/bin/env python3
"""Download Toronto topographic Road Edge / Intersection polygons.

The Open Data catalogue page for this dataset is retired
(https://open.toronto.ca/dataset/topographic-mapping-edge-of-road/) but the
official FeatureServer remains live. This script is a one-time / refresh
downloader only — parking-run never calls it.

Fetches layer 3 of cot_geospatial3 in object-ID batches, keeps Road Edge and
Intersection polygons, and writes:

  pipeline/data/topographic_road_edges.gpkg
  pipeline/data/topographic_road_edges.manifest.json

The live service currently reports 34,445 Road Edge features. Counts are
validated before an existing snapshot is replaced.

Usage (from pipeline/, venv active):

  python scripts/fetch_topographic_road_edges.py
  python scripts/fetch_topographic_road_edges.py --force
  python scripts/fetch_topographic_road_edges.py --write-sample
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, box
from shapely.ops import transform as shapely_transform

_SRC = Path(__file__).resolve().parents[1] / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from parking_pipeline.log_config import add_verbose_arg, setup_logging  # noqa: E402
from parking_pipeline.paths import DATA_DIR, data_path  # noqa: E402
from parking_pipeline.road_edges import (  # noqa: E402
    GPKG_LAYER,
    INTERSECTION_SUBTYPE,
    KEEP_SUBTYPES,
    LAYER_URL,
    ROAD_EDGE_SUBTYPE,
    ROAD_EDGES_FILENAME,
    ROAD_EDGES_MANIFEST_FILENAME,
    manifest_path_for,
)

log = logging.getLogger(__name__)

QUERY_URL = f'{LAYER_URL}/query'
CATALOGUE_URL = 'https://open.toronto.ca/dataset/topographic-mapping-edge-of-road/'
OUTPUT_CRS = 'EPSG:4326'
USER_AGENT = 'parking-pipeline/road-edges'
DEFAULT_BATCH_SIZE = 2000
MIN_ROAD_EDGE_COUNT = 34_000
MIN_INTERSECTION_COUNT = 19_000
SERVICE_COUNT_RATIO = 0.99
EXISTING_COUNT_RATIO = 0.95
KEEP_WHERE = (
    "SUBTYPE_DESC IN ('Road Edge','Intersection')"
)
OUT_FIELDS = (
    'OBJECTID,SUBTYPE_CODE,SUBTYPE_DESC,ELEVATION,'
    'LAST_GEOMETRY_MAINT,LAST_ATTRIBUTE_MAINT,LONGITUDE,LATITUDE'
)

# Live service snapshot used as a documented floor (see module docstring).
EXPECTED_ROAD_EDGE_COUNT = 34_445


def _request_json(url: str, params: dict[str, Any], *, retries: int = 4) -> dict[str, Any]:
    query = urlencode(params)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(
                f'{url}?{query}',
                headers={'User-Agent': USER_AGENT},
            )
            with urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
            if isinstance(payload, dict) and payload.get('error'):
                raise RuntimeError(payload['error'])
            if not isinstance(payload, dict):
                raise RuntimeError(f'unexpected response type {type(payload)!r}')
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_err = exc
            delay = 2 ** attempt
            log.warning('Query failed (%s); retrying in %ss', exc, delay)
            time.sleep(delay)
    raise RuntimeError(f'Failed to query {url}: {last_err}') from last_err


def query_service_counts() -> dict[str, int]:
    payload = _request_json(QUERY_URL, {
        'where': KEEP_WHERE,
        'groupByFieldsForStatistics': 'SUBTYPE_DESC',
        'outStatistics': json.dumps([
            {
                'statisticType': 'count',
                'onStatisticField': 'OBJECTID',
                'outStatisticFieldName': 'cnt',
            },
        ]),
        'f': 'json',
    })
    counts = {subtype: 0 for subtype in KEEP_SUBTYPES}
    for feat in payload.get('features', []):
        attrs = feat.get('attributes') or {}
        subtype = attrs.get('SUBTYPE_DESC')
        if subtype in counts:
            counts[subtype] = int(attrs.get('cnt') or attrs.get('CNT') or 0)
    return counts


def query_oid_extent() -> tuple[int, int]:
    payload = _request_json(QUERY_URL, {
        'where': KEEP_WHERE,
        'outStatistics': json.dumps([
            {
                'statisticType': 'min',
                'onStatisticField': 'OBJECTID',
                'outStatisticFieldName': 'min_oid',
            },
            {
                'statisticType': 'max',
                'onStatisticField': 'OBJECTID',
                'outStatisticFieldName': 'max_oid',
            },
        ]),
        'f': 'json',
    })
    features = payload.get('features') or []
    if not features:
        raise RuntimeError('FeatureServer returned no OBJECTID extent')
    attrs = features[0].get('attributes') or {}
    min_oid = attrs.get('min_oid', attrs.get('MIN_OID'))
    max_oid = attrs.get('max_oid', attrs.get('MAX_OID'))
    if min_oid is None or max_oid is None:
        raise RuntimeError(f'FeatureServer OBJECTID extent missing: {attrs}')
    return int(min_oid), int(max_oid)


def iter_oid_windows(min_oid: int, max_oid: int, batch_size: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    start = min_oid
    while start <= max_oid:
        end = min(start + batch_size - 1, max_oid)
        windows.append((start, end))
        start = end + 1
    return windows


def fetch_oid_window(start: int, end: int, *, batch_size: int) -> gpd.GeoDataFrame:
    where = f'OBJECTID >= {start} AND OBJECTID <= {end} AND {KEEP_WHERE}'
    payload = _request_json(QUERY_URL, {
        'where': where,
        'outFields': OUT_FIELDS,
        'returnGeometry': 'true',
        'returnTrueCurves': 'false',
        'outSR': '4326',
        'resultRecordCount': str(batch_size),
        'f': 'geojson',
    })
    if payload.get('exceededTransferLimit'):
        raise RuntimeError(
            f'OBJECTID window {start}-{end} exceeded transfer limit; reduce --batch-size',
        )
    features = payload.get('features') or []
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs=OUTPUT_CRS)
    return gpd.GeoDataFrame.from_features(features, crs=OUTPUT_CRS)


def feature_counts(gdf: gpd.GeoDataFrame) -> dict[str, int]:
    counts = {subtype: 0 for subtype in (ROAD_EDGE_SUBTYPE, INTERSECTION_SUBTYPE)}
    if not gdf.empty and 'SUBTYPE_DESC' in gdf.columns:
        vc = gdf['SUBTYPE_DESC'].value_counts()
        for subtype in counts:
            counts[subtype] = int(vc.get(subtype, 0))
    counts['total'] = int(sum(counts.values()))
    return counts


def existing_counts(gpkg_path: Path) -> dict[str, int] | None:
    sidecar = manifest_path_for(gpkg_path)
    if sidecar.is_file():
        try:
            payload = json.loads(sidecar.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            stored = payload.get('feature_counts')
            if isinstance(stored, dict) and ROAD_EDGE_SUBTYPE in stored:
                return {
                    ROAD_EDGE_SUBTYPE: int(stored.get(ROAD_EDGE_SUBTYPE, 0)),
                    INTERSECTION_SUBTYPE: int(stored.get(INTERSECTION_SUBTYPE, 0)),
                    'total': int(stored.get('total') or 0),
                }
    if not gpkg_path.exists():
        return None
    try:
        existing = gpd.read_file(gpkg_path)
    except Exception as exc:
        log.warning('Could not read existing snapshot %s: %s', gpkg_path, exc)
        return None
    return feature_counts(existing)


def validate_snapshot_counts(
    new_counts: dict[str, int],
    *,
    service_counts: dict[str, int] | None,
    previous_counts: dict[str, int] | None,
    force: bool,
) -> None:
    road_n = new_counts.get(ROAD_EDGE_SUBTYPE, 0)
    ix_n = new_counts.get(INTERSECTION_SUBTYPE, 0)
    if road_n == 0:
        raise RuntimeError('Fetched snapshot has 0 Road Edge features')

    if force:
        log.warning('--force: skipping count floors (Road Edge=%s Intersection=%s)', road_n, ix_n)
        return

    if road_n < MIN_ROAD_EDGE_COUNT:
        raise RuntimeError(
            f'Refusing to write snapshot: {road_n} Road Edge features '
            f'< floor {MIN_ROAD_EDGE_COUNT} (live service typically {EXPECTED_ROAD_EDGE_COUNT})',
        )
    if ix_n < MIN_INTERSECTION_COUNT:
        raise RuntimeError(
            f'Refusing to write snapshot: {ix_n} Intersection features '
            f'< floor {MIN_INTERSECTION_COUNT}',
        )

    if service_counts:
        for subtype, ratio in (
            (ROAD_EDGE_SUBTYPE, SERVICE_COUNT_RATIO),
            (INTERSECTION_SUBTYPE, SERVICE_COUNT_RATIO),
        ):
            expected = int(service_counts.get(subtype, 0))
            got = int(new_counts.get(subtype, 0))
            if expected and got < expected * ratio:
                raise RuntimeError(
                    f'Refusing to write snapshot: {subtype} count {got} is below '
                    f'{ratio:.0%} of live service count {expected}',
                )

    if previous_counts:
        for subtype, ratio in (
            (ROAD_EDGE_SUBTYPE, EXISTING_COUNT_RATIO),
            (INTERSECTION_SUBTYPE, EXISTING_COUNT_RATIO),
        ):
            previous = int(previous_counts.get(subtype, 0))
            got = int(new_counts.get(subtype, 0))
            if previous and got < previous * ratio:
                raise RuntimeError(
                    f'Refusing to replace existing snapshot: {subtype} count {got} is below '
                    f'{ratio:.0%} of existing {previous}. Re-run with --force to override.',
                )


def max_last_geometry_maint(gdf: gpd.GeoDataFrame) -> str | None:
    if gdf.empty or 'LAST_GEOMETRY_MAINT' not in gdf.columns:
        return None
    series = gdf['LAST_GEOMETRY_MAINT'].dropna()
    if series.empty:
        return None
    if pd.api.types.is_numeric_dtype(series):
        parsed = pd.to_datetime(series, unit='ms', utc=True, errors='coerce')
    else:
        parsed = pd.to_datetime(series, utc=True, errors='coerce')
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    return parsed.max().isoformat()


def build_manifest(
    *,
    counts: dict[str, int],
    min_oid: int,
    max_oid: int,
    batch_size: int,
    crs: str,
    max_maint: str | None,
    fetched_at: str,
) -> dict[str, Any]:
    return {
        'service_url': LAYER_URL,
        'query': KEEP_WHERE,
        'fetch_time': fetched_at,
        'feature_counts': counts,
        'crs': crs,
        'max_last_geometry_maint': max_maint,
        'object_id_min': min_oid,
        'object_id_max': max_oid,
        'batch_size': batch_size,
        'layer_name': 'Road Line',
        'catalogue_url': CATALOGUE_URL,
        'catalogue_status': 'retired',
    }


def write_snapshot(gdf: gpd.GeoDataFrame, gpkg_path: Path, manifest: dict[str, Any]) -> None:
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = gpkg_path.with_name(gpkg_path.stem + '.tmp.gpkg')
    if tmp.exists():
        tmp.unlink()
    gdf.to_file(tmp, driver='GPKG', layer=GPKG_LAYER)
    tmp.replace(gpkg_path)

    sidecar = manifest_path_for(gpkg_path)
    sidecar.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


def fetch_and_write(
    *,
    output: Path,
    batch_size: int,
    force: bool,
) -> int:
    log.info('Querying %s', LAYER_URL)
    log.info(
        'Catalogue page is retired (%s); using the live FeatureServer.',
        CATALOGUE_URL,
    )
    service_counts = query_service_counts()
    log.info(
        'Live service counts: Road Edge=%s Intersection=%s',
        service_counts.get(ROAD_EDGE_SUBTYPE, 0),
        service_counts.get(INTERSECTION_SUBTYPE, 0),
    )
    min_oid, max_oid = query_oid_extent()
    windows = iter_oid_windows(min_oid, max_oid, batch_size)
    log.info(
        'Fetching OBJECTID %s–%s in %s windows of %s',
        min_oid,
        max_oid,
        len(windows),
        batch_size,
    )

    parts: list[gpd.GeoDataFrame] = []
    fetched_at = datetime.now(UTC).isoformat()
    for i, (start, end) in enumerate(windows, start=1):
        part = fetch_oid_window(start, end, batch_size=batch_size)
        if not part.empty:
            parts.append(part)
        if i == 1 or i == len(windows) or i % 10 == 0:
            log.info('  window %s/%s (OBJECTID %s–%s): %s features', i, len(windows), start, end, len(part))

    if not parts:
        raise RuntimeError('FeatureServer returned no Road Edge / Intersection features')

    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=OUTPUT_CRS)
    gdf = gdf.loc[gdf['SUBTYPE_DESC'].isin(KEEP_SUBTYPES)].copy()
    if 'OBJECTID' in gdf.columns:
        gdf = gdf.drop_duplicates(subset=['OBJECTID'], keep='first')

    counts = feature_counts(gdf)
    previous = existing_counts(output)
    validate_snapshot_counts(
        counts,
        service_counts=service_counts,
        previous_counts=previous,
        force=force,
    )

    manifest = build_manifest(
        counts=counts,
        min_oid=min_oid,
        max_oid=max_oid,
        batch_size=batch_size,
        crs=OUTPUT_CRS,
        max_maint=max_last_geometry_maint(gdf),
        fetched_at=fetched_at,
    )
    write_snapshot(gdf, output, manifest)
    log.info(
        'Wrote %s (%s Road Edge, %s Intersection) and %s',
        output,
        counts[ROAD_EDGE_SUBTYPE],
        counts[INTERSECTION_SUBTYPE],
        manifest_path_for(output),
    )
    return 0


def write_sample_fixture(out_dir: Path) -> None:
    """Write a tiny committed fixture covering the four geometry cases."""
    import pyproj

    to_ll = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform

    def as_wgs84(geom):
        return shapely_transform(to_ll, geom)

    records = [
        (1, ROAD_EDGE_SUBTYPE, 1, 'straight', box(630000, 4835000, 630150, 4835014)),
        (
            2,
            ROAD_EDGE_SUBTYPE,
            1,
            'curved',
            LineString([
                (630400, 4835040),
                (630460, 4835120),
                (630540, 4835040),
            ]).buffer(7, cap_style='flat', join_style='round'),
        ),
        (3, INTERSECTION_SUBTYPE, 2, 'intersection', box(630800, 4835000, 630840, 4835040)),
        (4, ROAD_EDGE_SUBTYPE, 1, 'divided_north', box(630000, 4835328, 630160, 4835338)),
        (5, ROAD_EDGE_SUBTYPE, 1, 'divided_south', box(630000, 4835300, 630160, 4835310)),
        (6, 'Highway Edge', 3, 'ignored', box(630000, 4835600, 630040, 4835610)),
    ]
    gdf = gpd.GeoDataFrame(
        {
            'OBJECTID': [row[0] for row in records],
            'SUBTYPE_CODE': [row[2] for row in records],
            'SUBTYPE_DESC': [row[1] for row in records],
            'FIXTURE_CASE': [row[3] for row in records],
            'LAST_GEOMETRY_MAINT': pd.Timestamp('2024-06-01T00:00:00Z'),
            'geometry': [as_wgs84(row[4]) for row in records],
        },
        crs=OUTPUT_CRS,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = out_dir / ROAD_EDGES_FILENAME
    if gpkg_path.exists():
        gpkg_path.unlink()
    gdf.to_file(gpkg_path, driver='GPKG', layer=GPKG_LAYER)

    kept = gdf.loc[gdf['SUBTYPE_DESC'].isin(KEEP_SUBTYPES)]
    manifest = {
        'service_url': LAYER_URL,
        'query': KEEP_WHERE,
        'fetch_time': 'sample-fixture',
        'feature_counts': feature_counts(kept),
        'crs': OUTPUT_CRS,
        'max_last_geometry_maint': '2024-06-01T00:00:00+00:00',
        'is_sample_fixture': True,
        'fixture_cases': ['straight', 'curved', 'intersection', 'divided_north', 'divided_south'],
        'catalogue_url': CATALOGUE_URL,
        'catalogue_status': 'retired',
        'notes': (
            'Synthetic fixture covering straight, curved, intersection, and divided-road '
            'cases. Not a live FeatureServer extract.'
        ),
    }
    (out_dir / ROAD_EDGES_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2) + '\n',
        encoding='utf-8',
    )
    log.info('Wrote sample fixture %s (%s features)', gpkg_path, len(gdf))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        default=data_path(ROAD_EDGES_FILENAME),
        help=f'Output GeoPackage (default: data/{ROAD_EDGES_FILENAME})',
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f'OBJECTID window size (default: {DEFAULT_BATCH_SIZE}, layer maxRecordCount)',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Replace an existing snapshot even when counts drop below validation floors',
    )
    parser.add_argument(
        '--write-sample',
        action='store_true',
        help='Write the committed CI fixture under data/samples/ (no network)',
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    if args.batch_size < 1:
        log.error('--batch-size must be >= 1')
        return 2

    try:
        if args.write_sample:
            write_sample_fixture(DATA_DIR / 'samples')
            return 0
        return fetch_and_write(output=args.output, batch_size=args.batch_size, force=args.force)
    except Exception as exc:
        log.error('%s', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

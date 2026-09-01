"""On-street residential permit parking zones (Chapter 925)."""

from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import shapely.geometry
from shapely.strtree import STRtree

from .opendata import RawDumpError, _http_get
from .paths import data_path

log = logging.getLogger(__name__)

PERMIT_AREAS_FILENAME = 'on_street_permit_parking_areas.geojson'
PERMIT_AREAS_RESOURCE_ID = '9b1a3a7b-b732-49cb-a2d7-c31f4fb11a06'
DUMP_URL = f'https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/{PERMIT_AREAS_RESOURCE_ID}'

DEFAULT_PERMIT_HOURS = '12:01 a.m. to 7:00 a.m.'


def download_permit_areas(dest: Path) -> Path:
    """Download on-street permit parking areas from Open Data datastore and write GeoJSON."""
    log.info('Fetching permit parking areas from Open Data...')
    try:
        raw_bytes = _http_get(DUMP_URL, timeout=60)
        csv_text = raw_bytes.decode('utf-8')
        df = pd.read_csv(StringIO(csv_text))
        geometries = [shapely.geometry.shape(json.loads(g)) for g in df['geometry']]
        gdf = gpd.GeoDataFrame(df.drop(columns=['geometry']), geometry=geometries, crs='EPSG:4326')
        dest.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(dest, driver='GeoJSON')
        log.info('Wrote %d permit parking areas to %s', len(gdf), dest)
        return dest
    except Exception as exc:
        raise RawDumpError(f'Failed to fetch permit parking areas: {exc}') from exc


def ensure_permit_parking_areas(*, force: bool = False, skip: bool = False) -> Path:
    """Ensure local GeoJSON of permit parking area boundaries exists."""
    target = data_path(PERMIT_AREAS_FILENAME)
    if skip:
        if target.exists():
            return target
        raise RawDumpError(f'Missing {target} and skip refresh requested')
    if target.exists() and not force:
        return target
    return download_permit_areas(target)


def load_permit_parking_areas(path: Path | None = None) -> gpd.GeoDataFrame:
    target = path or data_path(PERMIT_AREAS_FILENAME)
    if not target.exists():
        target = ensure_permit_parking_areas()
    return gpd.read_file(target)


class PermitZoneIndex:
    """Spatial index for 97 on-street residential permit parking zones."""

    def __init__(self, gdf: gpd.GeoDataFrame | None = None) -> None:
        self.gdf = gdf if gdf is not None else load_permit_parking_areas()
        self.geometries = list(self.gdf.geometry)
        self.area_codes = list(self.gdf['AREA_LONG_CODE'].fillna(self.gdf['AREA_NAME']).astype(str))
        self.tree = STRtree(self.geometries)

    def find_permit_area(self, geom: shapely.geometry.base.BaseGeometry) -> str | None:
        """Return the permit area code (e.g. '1C', '12A') covering the geometry."""
        if geom.is_empty:
            return None
        candidates = self.tree.query(geom)
        for idx in candidates:
            poly = self.geometries[idx]
            if poly.intersects(geom):
                return self.area_codes[idx]
        return None

    def tag_feature(self, geom: shapely.geometry.base.BaseGeometry) -> dict[str, Any]:
        """Return permit zone properties for a curb segment."""
        area_id = self.find_permit_area(geom)
        return {
            'permit_area_id': area_id,
            'permit_parking_active': area_id is not None,
            'permit_hours_default': DEFAULT_PERMIT_HOURS if area_id else None,
        }

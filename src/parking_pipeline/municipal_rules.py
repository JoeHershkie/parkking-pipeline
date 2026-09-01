"""Former municipality boundaries and regional default bylaws."""

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

MUNICIPAL_BOUNDARIES_FILENAME = 'former_municipality_boundaries.geojson'
MUNICIPAL_BOUNDARIES_MANIFEST = 'former_municipality_boundaries.manifest.json'
MUNICIPAL_RESOURCE_ID = 'f82dbe76-928e-4cec-8147-a21882f575e2'
DUMP_URL = f'https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/{MUNICIPAL_RESOURCE_ID}'

REGIONAL_WINTER_RULES: dict[str, dict[str, Any]] = {
    'SCARBOROUGH': {
        'rule_name': 'Scarborough Winter Overnight Prohibition',
        'prohibited_times': '2:00 a.m. to 6:00 a.m. from Nov. 1 to Mar. 31',
        'schedule_category': 'winter_maintenance',
        'bylaw_ref': 'Scarborough Code § 214-34',
        'is_regional_default': True,
    },
    'ETOBICOKE': {
        'rule_name': 'Etobicoke Winter Overnight Prohibition',
        'prohibited_times': '2:00 a.m. to 6:00 a.m. from Oct. 16 to Apr. 14',
        'schedule_category': 'winter_maintenance',
        'bylaw_ref': 'Etobicoke Code § 240-27',
        'is_regional_default': True,
    },
    'NORTH YORK': {
        'rule_name': 'North York Winter Maintenance',
        'prohibited_times': '2:00 a.m. to 6:00 a.m. from Dec. 1 to Mar. 31',
        'schedule_category': 'winter_maintenance',
        'bylaw_ref': 'Toronto Municipal Code § 950-400D(9)',
        'is_regional_default': True,
    },
}

CITY_WIDE_DEFAULT_RULE = {
    'rule_name': 'General Unsigned Parking Limit',
    'prohibited_times': 'Anytime (Max 3 hours)',
    'schedule_category': 'restricted_periods',
    'max_minutes': 180,
    'bylaw_ref': 'Toronto Municipal Code § 950-400D(1)',
    'is_general_default': True,
}


def download_municipal_boundaries(dest: Path) -> Path:
    """Download municipal boundaries from Open Data datastore and write GeoJSON."""
    log.info('Fetching former municipality boundaries from Open Data...')
    try:
        raw_bytes = _http_get(DUMP_URL, timeout=60)
        csv_text = raw_bytes.decode('utf-8')
        df = pd.read_csv(StringIO(csv_text))
        geometries = [shapely.geometry.shape(json.loads(g)) for g in df['geometry']]
        gdf = gpd.GeoDataFrame(df.drop(columns=['geometry']), geometry=geometries, crs='EPSG:4326')
        dest.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(dest, driver='GeoJSON')
        log.info('Wrote %d municipal boundaries to %s', len(gdf), dest)
        return dest
    except Exception as exc:
        raise RawDumpError(f'Failed to fetch municipal boundaries: {exc}') from exc


def ensure_former_municipality_boundaries(*, force: bool = False, skip: bool = False) -> Path:
    """Ensure local GeoJSON of municipal boundaries exists."""
    target = data_path(MUNICIPAL_BOUNDARIES_FILENAME)
    if skip:
        if target.exists():
            return target
        raise RawDumpError(f'Missing {target} and skip refresh requested')
    if target.exists() and not force:
        return target
    return download_municipal_boundaries(target)


def load_municipal_boundaries(path: Path | None = None) -> gpd.GeoDataFrame:
    target = path or data_path(MUNICIPAL_BOUNDARIES_FILENAME)
    if not target.exists():
        target = ensure_former_municipality_boundaries()
    return gpd.read_file(target)


class MunicipalBoundaryIndex:
    """Spatial index for former municipality boundaries."""

    def __init__(self, gdf: gpd.GeoDataFrame | None = None) -> None:
        self.gdf = gdf if gdf is not None else load_municipal_boundaries()
        self.geometries = list(self.gdf.geometry)
        self.names = list(self.gdf['AREA_NAME'].str.upper())
        self.tree = STRtree(self.geometries)

    def find_municipality(self, geom: shapely.geometry.base.BaseGeometry) -> str | None:
        """Find the former municipality containing or intersecting the given geometry."""
        if geom.is_empty:
            return None
        candidates = self.tree.query(geom)
        for idx in candidates:
            poly = self.geometries[idx]
            if poly.intersects(geom):
                return self.names[idx]
        return None

    def get_regional_winter_rule(self, municipality: str | None) -> dict[str, Any] | None:
        if not municipality:
            return None
        return REGIONAL_WINTER_RULES.get(municipality.upper())

    def tag_feature(self, geom: shapely.geometry.base.BaseGeometry) -> dict[str, Any]:
        """Return boundary tag attributes for a feature."""
        mun = self.find_municipality(geom)
        rule = self.get_regional_winter_rule(mun)
        return {
            'former_municipality': mun,
            'regional_winter_rule': rule['prohibited_times'] if rule else None,
            'regional_winter_bylaw': rule['bylaw_ref'] if rule else None,
        }

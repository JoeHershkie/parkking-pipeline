"""Fire hydrant physical setbacks and 3m curb exclusion zones."""

from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyproj
import shapely.geometry
import shapely.ops
from shapely.strtree import STRtree

from .opendata import RawDumpError, _http_get
from .paths import data_path

log = logging.getLogger(__name__)

HYDRANTS_FILENAME = 'fire_hydrants.geojson'
HYDRANTS_RESOURCE_ID = 'beaaa552-6338-4c81-95be-411e6cef6b89'
DUMP_URL = f'https://ckan0.cf.opendata.inter.prod-toronto.ca/datastore/dump/{HYDRANTS_RESOURCE_ID}'

PROJECTED_CRS = 'EPSG:32617'  # UTM 17N (metres)
WGS84_CRS = 'EPSG:4326'

_to_projected = pyproj.Transformer.from_crs(WGS84_CRS, PROJECTED_CRS, always_xy=True).transform
_to_wgs84 = pyproj.Transformer.from_crs(PROJECTED_CRS, WGS84_CRS, always_xy=True).transform


def project_to_utm(geom: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    return shapely.ops.transform(_to_projected, geom)


def project_to_wgs84(geom: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    return shapely.ops.transform(_to_wgs84, geom)


def download_fire_hydrants(dest: Path) -> Path:
    """Download fire hydrants from Open Data datastore and write GeoJSON."""
    log.info('Fetching fire hydrants from Open Data...')
    try:
        raw_bytes = _http_get(DUMP_URL, timeout=120)
        csv_text = raw_bytes.decode('utf-8')
        df = pd.read_csv(StringIO(csv_text))
        geometries = [shapely.geometry.shape(json.loads(g)) for g in df['geometry']]
        gdf = gpd.GeoDataFrame(df.drop(columns=['geometry']), geometry=geometries, crs=WGS84_CRS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(dest, driver='GeoJSON')
        log.info('Wrote %d fire hydrants to %s', len(gdf), dest)
        return dest
    except Exception as exc:
        raise RawDumpError(f'Failed to fetch fire hydrants: {exc}') from exc


def ensure_fire_hydrants(*, force: bool = False, skip: bool = False) -> Path:
    """Ensure local GeoJSON of fire hydrants exists."""
    target = data_path(HYDRANTS_FILENAME)
    if skip:
        if target.exists():
            return target
        raise RawDumpError(f'Missing {target} and skip refresh requested')
    if target.exists() and not force:
        return target
    return download_fire_hydrants(target)


def load_fire_hydrants(path: Path | None = None) -> gpd.GeoDataFrame:
    target = path or data_path(HYDRANTS_FILENAME)
    if not target.exists():
        target = ensure_fire_hydrants()
    return gpd.read_file(target)


class FireHydrantIndex:
    """Spatial index for city fire hydrants with 3m setback calculations."""

    def __init__(self, gdf: gpd.GeoDataFrame | None = None) -> None:
        self.gdf = gdf if gdf is not None else load_fire_hydrants()
        # Ensure projected coordinates in UTM 17N for accurate metric distance queries
        if self.gdf.crs != PROJECTED_CRS:
            self.gdf_proj = self.gdf.to_crs(PROJECTED_CRS)
        else:
            self.gdf_proj = self.gdf
        self.geometries = list(self.gdf_proj.geometry)
        self.facility_ids = list(self.gdf_proj['FACILITYID'].fillna(self.gdf_proj['_id'].astype(str)))
        self.tree = STRtree(self.geometries)

    def find_nearby_hydrants(
        self,
        geom_proj: shapely.geometry.base.BaseGeometry,
        max_snap_m: float = 12.0,
    ) -> list[dict[str, Any]]:
        """Find hydrants within max_snap_m of the projected geometry."""
        if geom_proj.is_empty:
            return []
        search_geom = geom_proj.buffer(max_snap_m)
        candidates = self.tree.query(search_geom)
        nearby: list[dict[str, Any]] = []
        for idx in candidates:
            pt = self.geometries[idx]
            dist = geom_proj.distance(pt)
            if dist <= max_snap_m:
                nearby.append({
                    'index': idx,
                    'facility_id': self.facility_ids[idx],
                    'geometry': pt,
                    'distance_m': dist,
                })
        nearby.sort(key=lambda x: x['distance_m'])
        return nearby

    def compute_curb_exclusions(
        self,
        curb_line_wgs84: shapely.geometry.base.BaseGeometry,
        setback_m: float = 3.0,
        max_snap_m: float = 12.0,
    ) -> list[shapely.geometry.base.BaseGeometry]:
        """
        Compute 3m curb exclusion sub-segments (WGS84) for all hydrants within max_snap_m of curb.
        """
        if curb_line_wgs84.is_empty or not isinstance(
            curb_line_wgs84, shapely.geometry.LineString | shapely.geometry.MultiLineString
        ):
            return []

        curb_proj = project_to_utm(curb_line_wgs84)
        lines = [curb_proj] if isinstance(curb_proj, shapely.geometry.LineString) else list(curb_proj.geoms)

        exclusions_proj: list[shapely.geometry.LineString] = []
        for line in lines:
            if line.length == 0:
                continue
            nearby = self.find_nearby_hydrants(line, max_snap_m=max_snap_m)
            for h in nearby:
                proj_dist = line.project(h['geometry'])
                start = max(0.0, proj_dist - setback_m)
                end = min(line.length, proj_dist + setback_m)
                if end > start:
                    sub = shapely.ops.substring(line, start, end)
                    if not sub.is_empty and sub.length > 0:
                        exclusions_proj.append(sub)

        if not exclusions_proj:
            return []

        merged_proj = shapely.ops.unary_union(exclusions_proj)
        return [project_to_wgs84(merged_proj)]

    def tag_feature(
        self,
        curb_line_wgs84: shapely.geometry.base.BaseGeometry,
        max_snap_m: float = 12.0,
    ) -> dict[str, Any]:
        """Return hydrant setback annotations for a curb feature."""
        if curb_line_wgs84.is_empty:
            return {'has_hydrant': False, 'hydrant_count': 0, 'hydrant_facility_ids': []}
        curb_proj = project_to_utm(curb_line_wgs84)
        nearby = self.find_nearby_hydrants(curb_proj, max_snap_m=max_snap_m)
        return {
            'has_hydrant': len(nearby) > 0,
            'hydrant_count': len(nearby),
            'hydrant_facility_ids': [h['facility_id'] for h in nearby],
            'hydrant_setback_m': 3.0 if nearby else None,
        }

"""Load, validate, project, and spatially index topographic Road Edge polygons."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import shapely
from shapely import STRtree, make_valid
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import geo_cache as gc
from .paths import DATA_DIR, data_path

LAYER_URL = 'https://gis.toronto.ca/arcgis/rest/services/cot_geospatial3/FeatureServer/3'
KEEP_SUBTYPES = frozenset({'Road Edge', 'Intersection'})
ROAD_EDGE_SUBTYPE = 'Road Edge'
INTERSECTION_SUBTYPE = 'Intersection'
ROAD_EDGES_FILENAME = 'topographic_road_edges.gpkg'
ROAD_EDGES_MANIFEST_FILENAME = 'topographic_road_edges.manifest.json'
GPKG_LAYER = 'road_edges'
METRE_CRS = 'EPSG:32617'

# Generous UTM zone 17N envelope covering the City of Toronto.
_TORONTO_UTM_BOUNDS = (580_000.0, 4_780_000.0, 720_000.0, 4_890_000.0)


class RoadEdgesError(ValueError):
    """Invalid, empty, or missing topographic road-edge source."""


@dataclass(frozen=True, eq=False)
class RoadEdgeIndex:
    """Read-only projected Road Edge strips and intersection masks."""

    source_path: Path
    crs: str
    road_strips: gpd.GeoDataFrame
    intersections: gpd.GeoDataFrame
    manifest: Mapping[str, Any]
    _road_tree: STRtree = field(repr=False, compare=False)
    _ix_tree: STRtree = field(repr=False, compare=False)
    _road_geoms: np.ndarray = field(repr=False, compare=False, default=None)
    _road_boundaries: np.ndarray = field(repr=False, compare=False, default=None)
    _road_oids: np.ndarray = field(repr=False, compare=False, default=None)
    _road_interiors: np.ndarray = field(repr=False, compare=False, default=None)
    _ix_geoms: np.ndarray = field(repr=False, compare=False, default=None)

    def query_road_strips(self, geom: BaseGeometry) -> gpd.GeoDataFrame:
        """Return Road Edge rows whose polygons intersect *geom* (EPSG:32617)."""
        return _rows_intersecting(self.road_strips, self._road_tree, geom)

    def query_intersections(self, geom: BaseGeometry) -> gpd.GeoDataFrame:
        """Return Intersection rows whose polygons intersect *geom* (EPSG:32617)."""
        return _rows_intersecting(self.intersections, self._ix_tree, geom)

    def query_road_strips_within(self, geom: BaseGeometry, distance: float) -> gpd.GeoDataFrame:
        """Road Edge rows within *distance* metres of *geom* (no buffer polygon)."""
        return _rows_within(self.road_strips, self._road_tree, geom, distance)

    def query_intersections_within(self, geom: BaseGeometry, distance: float) -> gpd.GeoDataFrame:
        """Intersection rows within *distance* metres of *geom* (no buffer polygon)."""
        return _rows_within(self.intersections, self._ix_tree, geom, distance)

    def query_road_candidates_within(
        self, geom: BaseGeometry, distance: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return (oids, geoms, boundaries, interiors) within *distance* metres."""
        if self._road_geoms is None or len(self._road_geoms) == 0:
            empty_obj = np.empty(0, dtype=object)
            return np.empty(0, dtype=int), empty_obj, empty_obj, empty_obj
        try:
            idxs = self._road_tree.query(geom, predicate='dwithin', distance=float(distance))
        except (TypeError, ValueError):
            idxs = self._road_tree.query(geom.buffer(distance), predicate='intersects')
        if len(idxs) == 0:
            empty_obj = np.empty(0, dtype=object)
            return np.empty(0, dtype=int), empty_obj, empty_obj, empty_obj
        return (
            self._road_oids[idxs],
            self._road_geoms[idxs],
            self._road_boundaries[idxs],
            self._road_interiors[idxs],
        )

    def query_intersection_geoms_within(
        self, geom: BaseGeometry, distance: float,
    ) -> np.ndarray:
        """Return array of intersection polygon geometries within *distance* metres."""
        if self._ix_geoms is None or len(self._ix_geoms) == 0:
            return np.empty(0, dtype=object)
        try:
            idxs = self._ix_tree.query(geom, predicate='dwithin', distance=float(distance))
        except (TypeError, ValueError):
            idxs = self._ix_tree.query(geom.buffer(distance), predicate='intersects')
        if len(idxs) == 0:
            return np.empty(0, dtype=object)
        return self._ix_geoms[idxs]


def road_edges_path() -> Path:
    return data_path(ROAD_EDGES_FILENAME)


def manifest_path_for(gpkg_path: Path) -> Path:
    return gpkg_path.with_suffix('.manifest.json')


def load_road_edge_index(
    path: Path | None = None,
    *,
    require: bool = False,
) -> RoadEdgeIndex:
    """Load the local GeoPackage (or sample copy) and return a cached spatial index.

    If *require* is true and the source file is missing, raise ``RoadEdgesError``
    instead of copying the committed sample fixture. That is the hook for a later
    ``parking-geo --require-road-edges`` flag.
    """
    source = _resolve_source_path(path, require=require)
    cached = gc.load_road_edges(source)
    if cached is not None:
        return _index_from_frames(
            source,
            cached['road_strips'],
            cached['intersections'],
            cached.get('manifest') or {},
        )

    gdf = _read_gpkg(source)
    index = build_road_edge_index(
        gdf,
        source_path=source,
        manifest=_read_manifest(source),
    )
    gc.save_road_edges(
        source,
        road_strips=index.road_strips,
        intersections=index.intersections,
        manifest=dict(index.manifest),
    )
    return index


def build_road_edge_index(
    gdf: gpd.GeoDataFrame,
    *,
    source_path: Path,
    manifest: Mapping[str, Any] | None = None,
) -> RoadEdgeIndex:
    """Validate, project, and index an in-memory topographic road-edge frame."""
    prepared = _prepare(gdf, source_path=source_path)
    roads = prepared.loc[prepared['SUBTYPE_DESC'] == ROAD_EDGE_SUBTYPE].reset_index(drop=True)
    ixs = prepared.loc[prepared['SUBTYPE_DESC'] == INTERSECTION_SUBTYPE].reset_index(drop=True)
    if roads.empty:
        raise RoadEdgesError(
            f'{source_path}: no {ROAD_EDGE_SUBTYPE} polygons after filtering '
            f'to {sorted(KEEP_SUBTYPES)}',
        )
    return _index_from_frames(source_path, roads, ixs, dict(manifest or {}))


def _resolve_source_path(path: Path | None, *, require: bool) -> Path:
    if path is not None:
        source = Path(path)
        if source.exists():
            return source
        raise RoadEdgesError(_missing_message(source, require=require))

    dest = road_edges_path()
    if dest.exists():
        return dest
    if require:
        raise RoadEdgesError(_missing_message(dest, require=True))
    if _copy_sample_fixture(dest):
        return dest
    raise RoadEdgesError(_missing_message(dest, require=False))


def _copy_sample_fixture(dest: Path) -> bool:
    sample = DATA_DIR / 'samples' / ROAD_EDGES_FILENAME
    if not sample.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(sample, dest)
    sample_manifest = DATA_DIR / 'samples' / ROAD_EDGES_MANIFEST_FILENAME
    sidecar = manifest_path_for(dest)
    if sample_manifest.exists() and not sidecar.exists():
        shutil.copy(sample_manifest, sidecar)
    return True


def _missing_message(path: Path, *, require: bool) -> str:
    base = (
        f'Missing topographic road edges source: {path}. '
        'Download it with pipeline/scripts/fetch_topographic_road_edges.py. '
        'The Open Data catalogue page is retired, but the official FeatureServer remains live.'
    )
    if require:
        return (
            f'{base} require=True / --require-road-edges is set, so the sample fixture '
            'was not copied.'
        )
    return f'{base} No committed sample fixture was available to copy.'


def _read_gpkg(path: Path) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path, layer=GPKG_LAYER)
    except Exception:
        try:
            return gpd.read_file(path)
        except Exception as exc:
            raise RoadEdgesError(f'{path}: cannot read GeoPackage: {exc}') from exc


def _read_manifest(gpkg_path: Path) -> dict[str, Any]:
    sidecar = manifest_path_for(gpkg_path)
    if not sidecar.is_file():
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoadEdgesError(f'{sidecar}: cannot read provenance manifest: {exc}') from exc
    if not isinstance(payload, dict):
        raise RoadEdgesError(f'{sidecar}: provenance manifest must be a JSON object')
    return payload


def _prepare(gdf: gpd.GeoDataFrame, *, source_path: Path) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        raise RoadEdgesError(f'{source_path}: source has no features')
    if 'SUBTYPE_DESC' not in gdf.columns:
        raise RoadEdgesError(f'{source_path}: missing SUBTYPE_DESC column')
    if gdf.crs is None:
        raise RoadEdgesError(f'{source_path}: source has no CRS')

    kept = gdf.loc[gdf['SUBTYPE_DESC'].isin(KEEP_SUBTYPES)].copy()
    if kept.empty:
        observed = sorted({str(v) for v in gdf['SUBTYPE_DESC'].dropna().unique()})
        raise RoadEdgesError(
            f'{source_path}: no features with SUBTYPE_DESC in {sorted(KEEP_SUBTYPES)}; '
            f'observed {observed}',
        )

    repaired = [_polygonal(geom) for geom in kept.geometry]
    kept = kept.set_geometry(repaired, crs=kept.crs)
    kept = kept.loc[kept.geometry.notna() & ~kept.geometry.is_empty]
    if kept.empty:
        raise RoadEdgesError(
            f'{source_path}: no valid polygonal geometries after make_valid',
        )

    try:
        projected = kept.to_crs(METRE_CRS)
    except Exception as exc:
        raise RoadEdgesError(
            f'{source_path}: cannot project CRS {kept.crs} to {METRE_CRS}: {exc}',
        ) from exc

    if not _any_in_toronto(projected):
        raise RoadEdgesError(
            f'{source_path}: projected bounds {tuple(float(v) for v in projected.total_bounds)} '
            f'are outside the Toronto UTM envelope {_TORONTO_UTM_BOUNDS} '
            f'(source CRS {gdf.crs}; expected a Toronto geographic or projected CRS)',
        )
    return projected.reset_index(drop=True)


def _polygonal(geom: BaseGeometry | None) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    repaired = geom if geom.is_valid else make_valid(geom)
    if repaired is None or repaired.is_empty:
        return None
    if repaired.geom_type in ('Polygon', 'MultiPolygon'):
        return repaired
    if repaired.geom_type == 'GeometryCollection':
        parts = [_polygonal(part) for part in repaired.geoms]
        parts = [part for part in parts if part is not None]
        if not parts:
            return None
        merged = unary_union(parts)
        if merged.geom_type in ('Polygon', 'MultiPolygon'):
            return merged
        return None
    return None


def _any_in_toronto(projected: gpd.GeoDataFrame) -> bool:
    west, south, east, north = _TORONTO_UTM_BOUNDS
    centroids = projected.geometry.centroid
    inside = (
        (centroids.x >= west)
        & (centroids.x <= east)
        & (centroids.y >= south)
        & (centroids.y <= north)
    )
    return bool(inside.any())


def _extract_interiors(geom: BaseGeometry) -> BaseGeometry | None:
    polys = geom.geoms if geom.geom_type == 'MultiPolygon' else (geom,)
    rings: list[LineString] = []
    for p in polys:
        if p.geom_type == 'Polygon':
            for interior in p.interiors:
                rings.append(LineString(interior.coords))
    if not rings:
        return None
    return MultiLineString(rings) if len(rings) > 1 else rings[0]


def _index_from_frames(
    source_path: Path,
    roads: gpd.GeoDataFrame,
    ixs: gpd.GeoDataFrame,
    manifest: Mapping[str, Any],
) -> RoadEdgeIndex:
    road_geoms = roads.geometry.values if not roads.empty else np.empty(0, dtype=object)
    road_bounds = shapely.boundary(road_geoms) if len(road_geoms) > 0 else np.empty(0, dtype=object)

    if 'OBJECTID' in roads.columns:
        road_oids = roads['OBJECTID'].to_numpy(dtype=int, na_value=0)
    else:
        road_oids = (
            np.array([int(r.Index if hasattr(r, 'Index') else 0) for r in roads.itertuples()], dtype=int)
            if not roads.empty
            else np.empty(0, dtype=int)
        )

    road_ints = (
        np.array([_extract_interiors(g) for g in road_geoms], dtype=object)
        if len(road_geoms) > 0
        else np.empty(0, dtype=object)
    )
    ix_geoms = ixs.geometry.values if not ixs.empty else np.empty(0, dtype=object)

    return RoadEdgeIndex(
        source_path=source_path,
        crs=METRE_CRS,
        road_strips=roads,
        intersections=ixs,
        manifest=manifest,
        _road_tree=STRtree(list(road_geoms)),
        _ix_tree=STRtree(list(ix_geoms)),
        _road_geoms=road_geoms,
        _road_boundaries=road_bounds,
        _road_oids=road_oids,
        _road_interiors=road_ints,
        _ix_geoms=ix_geoms,
    )


def _rows_intersecting(
    gdf: gpd.GeoDataFrame,
    tree: STRtree,
    geom: BaseGeometry,
) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf.iloc[0:0]
    idxs = tree.query(geom, predicate='intersects')
    if len(idxs) == 0:
        return gdf.iloc[0:0]
    return gdf.iloc[idxs]


def _rows_within(
    gdf: gpd.GeoDataFrame,
    tree: STRtree,
    geom: BaseGeometry,
    distance: float,
) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf.iloc[0:0]
    try:
        idxs = tree.query(geom, predicate='dwithin', distance=float(distance))
    except (TypeError, ValueError):
        idxs = tree.query(geom.buffer(distance), predicate='intersects')
    if len(idxs) == 0:
        return gdf.iloc[0:0]
    return gdf.iloc[idxs]

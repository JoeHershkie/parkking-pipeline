"""TCL street/intersection index loading and lookup."""

from __future__ import annotations

import math
import time
from functools import lru_cache

import geopandas as gpd
import pandas as pd
import pyproj
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, transform

from . import geo_cache as gc
from . import intersection_index as ix_index
from .parse_format import PARSE_COLUMNS, highway_from_row
from . import tcl_highway_resolve as thr
from .paths import data_path
from . import tcl_graph as tg
from .tcl_graph import StreetGraph

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
project_to_gps = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform

street_index: dict[str, LineString] = {}
street_metre_index: dict[str, LineString] = {}
street_graphs: dict[str, StreetGraph] = {}
intersections_gdf: gpd.GeoDataFrame | None = None
_timing: dict[str, float] = {}

_CROSS_STREET_KEYS = (
    'start_intersection',
    'end_intersection',
    'offset_intersection',
)

_geo_initialized = False


def geo_ready() -> bool:
    return _geo_initialized


def _geo_read_kwargs() -> dict:
    try:
        import pyogrio  # noqa: F401
        return {'engine': 'pyogrio'}
    except ImportError:
        return {}


def _geo_read_engine_label() -> str:
    return 'pyogrio' if _geo_read_kwargs() else 'fiona'


def _merge_street_geoms(geoms: list) -> LineString | None:
    if not geoms:
        return None
    merged = linemerge(MultiLineString(geoms))
    if merged.geom_type == 'MultiLineString':
        return max(merged.geoms, key=lambda x: x.length)
    return merged


def _build_street_index(gdf: gpd.GeoDataFrame) -> dict[str, LineString]:
    """Pre-merge TCL chunks per legal street name (lowercased key)."""
    name_lower = gdf['LINEAR_NAME_FULL_LEGAL'].str.lower()
    index: dict[str, LineString] = {}
    for s_name, group in gdf.groupby(name_lower, sort=False):
        geoms = []
        for g in group.geometry:
            if g.geom_type == 'LineString':
                geoms.append(g)
            elif g.geom_type == 'MultiLineString':
                geoms.extend(g.geoms)
        line = _merge_street_geoms(geoms)
        if line is not None:
            index[s_name] = line
    return index


def _load_tcl() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    read_kwargs = _geo_read_kwargs()
    engine = _geo_read_engine_label()
    print(f"   GeoJSON reader: {engine}")
    print("1. Loading Local Intersection Database...")
    t0 = time.perf_counter()
    ix_gdf = gpd.read_file(data_path('tcl_intersections.geojson'), **read_kwargs)
    _timing['intersections_load'] = time.perf_counter() - t0

    print("2. Loading Local Street Database (This might take a moment)...")
    t0 = time.perf_counter()
    st_gdf = gpd.read_file(data_path('tcl_streets.geojson'), **read_kwargs)
    _timing['streets_load'] = time.perf_counter() - t0
    return ix_gdf, st_gdf


def _init_indexes(ix_gdf: gpd.GeoDataFrame, st_gdf: gpd.GeoDataFrame) -> None:
    global intersections_gdf, street_index, street_graphs, street_metre_index
    intersections_gdf = ix_gdf
    tg.configure_intersections(ix_gdf)

    streets_path = data_path('tcl_streets.geojson')
    cached_graphs = gc.load_street_graphs(streets_path)
    if cached_graphs is not None:
        street_graphs = cached_graphs
        _timing['street_graphs'] = 0.0
        _timing['street_graphs_cache'] = 1.0
        print(f"   Loaded {len(street_graphs)} street graphs from cache.")
    else:
        print("   Building street graphs...")
        t0 = time.perf_counter()
        street_graphs = tg.build_street_graphs(st_gdf)
        _timing['street_graphs'] = time.perf_counter() - t0
        gc.save_street_graphs(streets_path, street_graphs)
        print(f"   Graphs for {len(street_graphs)} streets (saved to cache).")

    print("   Building street index (legacy merge-longest)...")
    t0 = time.perf_counter()
    street_index = _build_street_index(st_gdf)
    street_metre_index = {}
    _timing['street_index'] = time.perf_counter() - t0
    print(f"   Indexed {len(street_index)} streets.")

    thr.build_index_from_csv(legal_keys=set(street_graphs.keys()))
    print(f"   Highway suffix-resolve index ready ({thr.legal_key_count()} legals).")


def init_geo(*, force: bool = False) -> None:
    """Load TCL GeoJSON and build street/intersection indexes."""
    global _geo_initialized
    if _geo_initialized and not force:
        return
    ix_gdf, st_gdf = _load_tcl()
    _init_indexes(ix_gdf, st_gdf)
    _geo_initialized = True


def _intersection_mask(street_1: str, street_2: str) -> pd.Series:
    """Boolean mask over intersections_gdf rows (legacy helper for analysis scripts)."""
    ids = tg.resolve_intersection_ids(street_1, street_2)
    return intersections_gdf['INTERSECTION_ID'].isin(ids)


@lru_cache(maxsize=32768)
def find_intersection(street_1: str, street_2: str) -> Point | None:
    if not street_1 or not street_2:
        return None
    ids = tg.resolve_intersection_ids(street_1, street_2)
    if not ids:
        return None
    pt = tg.node_point_gps(ids[0])
    return pt


def find_intersection_ids(highway: str, cross: str) -> list[int]:
    return tg.resolve_intersection_ids(highway, cross)


def get_local_street_geometry(street_name: str) -> LineString | None:
    """O(1) lookup in pre-built street index."""
    return street_index.get(thr.tcl_lookup_key(street_name))


def get_street_line_meters(highway: str) -> LineString | None:
    """Cached EPSG:32617 centreline for a highway."""
    s_name = thr.tcl_lookup_key(highway)
    cached = street_metre_index.get(s_name)
    if cached is not None:
        return cached
    street_line_gps = get_local_street_geometry(highway)
    if street_line_gps is None:
        return None
    street_line_m = transform(project_to_meters, street_line_gps)
    street_metre_index[s_name] = street_line_m
    return street_line_m


@lru_cache(maxsize=32768)
def _intersection_point_meters(highway: str, cross_street: str) -> Point | None:
    pt_gps = find_intersection(highway, cross_street)
    if pt_gps is None:
        return None
    return transform(project_to_meters, pt_gps)


def _parsed_from_row_tuple(
    tup: tuple,
    col_idx: dict[str, int],
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for col in PARSE_COLUMNS:
        idx = col_idx.get(col)
        if idx is None:
            continue
        val = tup[idx]
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        text = str(val).strip()
        if text:
            parsed[col] = text
    return parsed


def _intersection_lookup_pairs(df: pd.DataFrame) -> list[tuple[str, str]]:
    """(highway_key, cross_street) pairs for intersection index warm-up."""
    if df.empty:
        return []

    col_idx = {c: df.columns.get_loc(c) for c in df.columns}
    hi = col_idx.get('Highway')
    if hi is None:
        return []

    cross_cols = [c for c in _CROSS_STREET_KEYS if c in col_idx]
    pairs: list[tuple[str, str]] = []

    for tup in df.itertuples(index=False, name=None):
        row = pd.Series({c: tup[col_idx[c]] for c in col_idx})
        highway = highway_from_row(row)
        if not highway:
            continue
        parsed = _parsed_from_row_tuple(tup, col_idx)
        for key in cross_cols:
            cross = parsed.get(key)
            if cross:
                pairs.append((highway, cross))
    return pairs


def warm_intersection_index_from_dataframe(df: pd.DataFrame) -> int:
    """Pre-index intersection search tokens used by rows in *df*."""
    pairs = _intersection_lookup_pairs(df)
    tokens = ix_index.collect_tokens_from_pairs(pairs)
    csv_path = data_path('parsed_successes.csv')
    ix_path = data_path('tcl_intersections.geojson')

    cached = gc.load_intersection_postings(ix_path, csv_path)
    if cached is not None:
        ix_index.install_postings(cached)
        _timing['intersection_warm_cache'] = 1.0
        return sum(1 for t in tokens if t in cached)

    warmed = ix_index.warm_tokens(tokens)
    gc.save_intersection_postings(ix_path, csv_path, ix_index.postings_snapshot())
    return warmed

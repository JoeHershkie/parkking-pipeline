"""Tests for topographic road-edge loading, indexing, cache, and sample copy."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from sample_data import ensure_sample_data_copies, using_sample_road_edges
from shapely.geometry import LineString, box

from parking_pipeline import geo_cache as gc
from parking_pipeline.road_edges import (
    METRE_CRS,
    ROAD_EDGES_FILENAME,
    ROAD_EDGES_MANIFEST_FILENAME,
    RoadEdgesError,
    build_road_edge_index,
    load_road_edge_index,
)

SAMPLES = Path(__file__).resolve().parents[1] / 'data' / 'samples'
SAMPLE_GPKG = SAMPLES / ROAD_EDGES_FILENAME


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('GEO_CACHE', '1')
    monkeypatch.setattr(gc, 'cache_dir', lambda: tmp_path / '.geo_cache')
    yield


@pytest.fixture
def sample_copy(tmp_path) -> Path:
    dest = tmp_path / ROAD_EDGES_FILENAME
    shutil.copy(SAMPLE_GPKG, dest)
    shutil.copy(SAMPLES / ROAD_EDGES_MANIFEST_FILENAME, tmp_path / ROAD_EDGES_MANIFEST_FILENAME)
    return dest


def _toronto_frame(rows: list[dict], *, crs: str | None = METRE_CRS) -> gpd.GeoDataFrame:
    gdf = gpd.GeoDataFrame(rows, crs=crs)
    return gdf


def test_sample_fixture_covers_required_cases(sample_copy):
    index = load_road_edge_index(sample_copy)
    assert index.crs == METRE_CRS
    assert str(index.road_strips.crs) == METRE_CRS
    cases = set(index.road_strips['FIXTURE_CASE'])
    assert cases == {'straight', 'curved', 'divided_north', 'divided_south'}
    assert set(index.intersections['FIXTURE_CASE']) == {'intersection'}
    assert 'ignored' not in set(index.road_strips['FIXTURE_CASE'])
    assert index.manifest.get('is_sample_fixture') is True
    assert all(index.road_strips.geometry.is_valid)
    assert all(index.intersections.geometry.is_valid)


def test_spatial_index_straight_and_intersection(sample_copy):
    index = load_road_edge_index(sample_copy)
    straight = index.road_strips.loc[index.road_strips['FIXTURE_CASE'] == 'straight'].iloc[0]
    hits = index.query_road_strips(straight.geometry.centroid)
    assert set(hits['FIXTURE_CASE']) == {'straight'}

    ix_row = index.intersections.iloc[0]
    ix_hits = index.query_intersections(ix_row.geometry.centroid)
    assert len(ix_hits) == 1
    assert index.query_road_strips(ix_row.geometry.centroid).empty


def test_spatial_index_curved_and_divided(sample_copy):
    index = load_road_edge_index(sample_copy)
    curved = index.road_strips.loc[index.road_strips['FIXTURE_CASE'] == 'curved'].iloc[0]
    curved_hits = index.query_road_strips(curved.geometry.representative_point())
    assert set(curved_hits['FIXTURE_CASE']) == {'curved'}

    north = index.road_strips.loc[index.road_strips['FIXTURE_CASE'] == 'divided_north'].iloc[0]
    south = index.road_strips.loc[index.road_strips['FIXTURE_CASE'] == 'divided_south'].iloc[0]
    corridor = north.geometry.union(south.geometry).envelope
    hits = index.query_road_strips(corridor)
    assert set(hits['FIXTURE_CASE']) >= {'divided_north', 'divided_south'}
    assert north.geometry.intersects(south.geometry) is False


def test_cache_roundtrip_avoids_reread(sample_copy, monkeypatch):
    first = load_road_edge_index(sample_copy)
    assert first.manifest.get('is_sample_fixture') is True

    def boom(*_args, **_kwargs):
        raise AssertionError('gpd.read_file should not run on a cache hit')

    monkeypatch.setattr('parking_pipeline.road_edges.gpd.read_file', boom)
    second = load_road_edge_index(sample_copy)
    assert len(second.road_strips) == len(first.road_strips)
    assert set(second.road_strips['FIXTURE_CASE']) == set(first.road_strips['FIXTURE_CASE'])
    centroid = second.road_strips.iloc[0].geometry.centroid
    assert not second.query_road_strips(centroid).empty


def test_cache_disabled_rereads(sample_copy, monkeypatch):
    load_road_edge_index(sample_copy)
    monkeypatch.setenv('GEO_CACHE', '0')
    calls = {'n': 0}
    real_read = gpd.read_file

    def wrapped(*args, **kwargs):
        calls['n'] += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr('parking_pipeline.road_edges.gpd.read_file', wrapped)
    load_road_edge_index(sample_copy)
    assert calls['n'] >= 1


def test_cache_invalidates_on_fingerprint_change(sample_copy):
    load_road_edge_index(sample_copy)
    cached = gc.load_road_edges(sample_copy)
    assert cached is not None
    sample_copy.write_bytes(sample_copy.read_bytes() + b'\x00')
    assert gc.load_road_edges(sample_copy) is None


def test_require_missing_file_raises(tmp_path):
    missing = tmp_path / 'missing.gpkg'
    with pytest.raises(RoadEdgesError, match='fetch_topographic_road_edges'):
        load_road_edge_index(missing, require=True)
    with pytest.raises(RoadEdgesError, match='fetch_topographic_road_edges'):
        load_road_edge_index(missing, require=False)


def test_default_path_copies_sample_unless_required(tmp_path, monkeypatch):
    samples = tmp_path / 'samples'
    samples.mkdir()
    shutil.copy(SAMPLE_GPKG, samples / ROAD_EDGES_FILENAME)
    shutil.copy(SAMPLES / ROAD_EDGES_MANIFEST_FILENAME, samples / ROAD_EDGES_MANIFEST_FILENAME)

    monkeypatch.setattr('parking_pipeline.road_edges.DATA_DIR', tmp_path)
    monkeypatch.setattr('parking_pipeline.road_edges.data_path', lambda name: tmp_path / name)

    dest = tmp_path / ROAD_EDGES_FILENAME
    with pytest.raises(RoadEdgesError, match='require=True'):
        load_road_edge_index(require=True)
    assert not dest.exists()

    index = load_road_edge_index(require=False)
    assert dest.exists()
    assert (tmp_path / ROAD_EDGES_MANIFEST_FILENAME).exists()
    assert len(index.road_strips) == 4
    assert len(index.intersections) == 1


def test_ensure_sample_data_copies_road_edges(tmp_path, monkeypatch):
    import sample_data as sd

    samples = tmp_path / 'samples'
    samples.mkdir()
    shutil.copy(SAMPLE_GPKG, samples / ROAD_EDGES_FILENAME)
    shutil.copy(SAMPLES / ROAD_EDGES_MANIFEST_FILENAME, samples / ROAD_EDGES_MANIFEST_FILENAME)
    monkeypatch.setattr(sd, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(sd, 'data_path', lambda name: tmp_path / name)
    monkeypatch.setattr('parking_pipeline.paths.DATA_DIR', tmp_path)
    monkeypatch.setattr('parking_pipeline.paths.data_path', lambda name: tmp_path / name)

    assert not using_sample_road_edges()
    ensure_sample_data_copies()
    assert (tmp_path / ROAD_EDGES_FILENAME).exists()
    assert (tmp_path / ROAD_EDGES_MANIFEST_FILENAME).exists()
    assert using_sample_road_edges()


def test_reject_empty_after_subtype_filter():
    gdf = _toronto_frame([
        {
            'SUBTYPE_DESC': 'Highway Edge',
            'geometry': box(630000, 4835000, 630040, 4835010),
        },
    ])
    with pytest.raises(RoadEdgesError, match='observed'):
        build_road_edge_index(gdf, source_path=Path('memory.gpkg'))


def test_reject_empty_frame():
    gdf = gpd.GeoDataFrame({'SUBTYPE_DESC': pd.Series(dtype=str)}, geometry=[], crs=METRE_CRS)
    with pytest.raises(RoadEdgesError, match='no features'):
        build_road_edge_index(gdf, source_path=Path('empty.gpkg'))


def test_reject_missing_crs():
    gdf = gpd.GeoDataFrame(
        {
            'SUBTYPE_DESC': ['Road Edge'],
            'geometry': [box(630000, 4835000, 630040, 4835010)],
        },
    )
    with pytest.raises(RoadEdgesError, match='no CRS'):
        build_road_edge_index(gdf, source_path=Path('nocrs.gpkg'))


def test_reject_wrong_crs_out_of_toronto():
    gdf = gpd.GeoDataFrame(
        {
            'SUBTYPE_DESC': ['Road Edge'],
            'geometry': [box(-79.4, 43.7, -79.3, 43.8)],
        },
        crs=METRE_CRS,
    )
    with pytest.raises(RoadEdgesError, match='outside the Toronto UTM envelope'):
        build_road_edge_index(gdf, source_path=Path('wrong-crs.gpkg'))


def test_reject_non_polygonal_geometries():
    gdf = _toronto_frame([
        {
            'SUBTYPE_DESC': 'Road Edge',
            'geometry': LineString([(630000, 4835000), (630100, 4835000)]),
        },
    ])
    with pytest.raises(RoadEdgesError, match='no valid polygonal'):
        build_road_edge_index(gdf, source_path=Path('lines.gpkg'))


def test_make_valid_repairs_bowtie():
    from shapely.geometry import Polygon

    invalid = Polygon([
        (630000, 4835000),
        (630020, 4835020),
        (630000, 4835020),
        (630020, 4835000),
        (630000, 4835000),
    ])
    assert invalid.is_valid is False
    gdf = _toronto_frame([
        {'SUBTYPE_DESC': 'Road Edge', 'geometry': invalid},
        {'SUBTYPE_DESC': 'Intersection', 'geometry': box(630100, 4835000, 630120, 4835020)},
    ])
    index = build_road_edge_index(gdf, source_path=Path('bowtie.gpkg'))
    assert len(index.road_strips) == 1
    assert index.road_strips.geometry.iloc[0].is_valid


def test_reject_missing_subtype_column():
    gdf = gpd.GeoDataFrame({'geometry': [box(630000, 4835000, 630010, 4835010)]}, crs=METRE_CRS)
    with pytest.raises(RoadEdgesError, match='SUBTYPE_DESC'):
        build_road_edge_index(gdf, source_path=Path('nocol.gpkg'))


def test_unreadable_manifest_raises(sample_copy):
    sidecar = sample_copy.with_suffix('.manifest.json')
    sidecar.write_text('{not json', encoding='utf-8')
    with pytest.raises(RoadEdgesError, match='manifest'):
        load_road_edge_index(sample_copy)


def test_sample_manifest_is_json_object():
    payload = json.loads((SAMPLES / ROAD_EDGES_MANIFEST_FILENAME).read_text(encoding='utf-8'))
    assert payload['catalogue_status'] == 'retired'
    assert payload['feature_counts']['Road Edge'] == 4
    assert payload['feature_counts']['Intersection'] == 1

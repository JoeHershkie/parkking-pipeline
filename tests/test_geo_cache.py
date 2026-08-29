"""Tests for geometry-engine disk cache."""

from __future__ import annotations

import pytest

from parking_pipeline import geo_cache as gc


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('GEO_CACHE', '1')
    monkeypatch.setattr(gc, 'cache_dir', lambda: tmp_path / '.geo_cache')
    yield


def test_street_graph_roundtrip(tmp_path):
    streets = tmp_path / 'streets.geojson'
    streets.write_text('{}', encoding='utf-8')
    graphs = {'main st': {'edge_count': 3}}
    gc.save_street_graphs(streets, graphs)
    loaded = gc.load_street_graphs(streets)
    assert loaded == graphs


def test_postings_roundtrip(tmp_path):
    ix_path = tmp_path / 'ix.geojson'
    csv_path = tmp_path / 'parsed.csv'
    ix_path.write_text('x', encoding='utf-8')
    csv_path.write_text('y', encoding='utf-8')
    postings = {'main st': (1, 2), 'oak ave': (3,)}
    gc.save_intersection_postings(ix_path, csv_path, postings)
    loaded = gc.load_intersection_postings(ix_path, csv_path)
    assert loaded == postings


def test_cache_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv('GEO_CACHE', '0')
    streets = tmp_path / 'streets.geojson'
    streets.write_text('{}', encoding='utf-8')
    gc.save_street_graphs(streets, {'a': 1})
    assert gc.load_street_graphs(streets) is None


def test_fingerprint_invalidates_on_change(tmp_path):
    streets = tmp_path / 'streets.geojson'
    streets.write_text('v1', encoding='utf-8')
    gc.save_street_graphs(streets, {'a': 1})
    streets.write_text('v2', encoding='utf-8')
    assert gc.load_street_graphs(streets) is None

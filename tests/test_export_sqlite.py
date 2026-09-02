"""Tests for SQLite database compilation, R-Tree spatial indexing, and manifest generation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from shapely import from_wkb
from shapely.geometry import LineString, MultiLineString

from parking_pipeline.export_sqlite import (
    MANIFEST_FILENAME,
    SQLITE_FILENAME,
    create_parking_database,
    export_geojson_to_sqlite,
    main,
)


@pytest.fixture
def sample_features() -> list[dict]:
    return [
        {
            "_id": "feat_1",
            "Highway": "QUEEN ST W",
            "Rule": "No Parking Anytime",
            "schedule_category": "no_parking",
            "Side": "North",
            "side_mode": "single",
            "max": None,
            "maxMinutes": None,
            "schedule": {"days": ["Mon", "Tue"], "times": ["00:00-23:59"]},
            "is_snow_route": False,
            "streetcar_corridor": True,
            "former_municipality": "Toronto",
            "regional_winter_rule": None,
            "permit_area_id": "8A",
            "permit_parking_active": True,
            "has_hydrant": True,
            "hydrant_count": 2,
            "hydrant_setback_m": 3.0,
            "curb_geometry_method": "road_edge_matched",
            "curb_confidence": 0.95,
            "curb_coverage": 1.0,
            "median_offset_m": 4.2,
            "centreline_ids": [1001, 1002],
            "geometry": LineString([(-79.4000, 43.6500), (-79.3900, 43.6505)]),
        },
        {
            "_id": "feat_2",
            "Highway": "BAY ST",
            "Rule": "2 Hour Parking 8am-6pm",
            "schedule_category": "time_limit",
            "Side": "East",
            "side_mode": "single",
            "max": "2 hours",
            "maxMinutes": 120,
            "schedule": {"days": ["Mon-Fri"], "times": ["08:00-18:00"]},
            "is_snow_route": True,
            "streetcar_corridor": False,
            "former_municipality": "Toronto",
            "regional_winter_rule": None,
            "permit_area_id": None,
            "permit_parking_active": False,
            "has_hydrant": False,
            "hydrant_count": 0,
            "hydrant_setback_m": None,
            "curb_geometry_method": "centreline_fallback",
            "curb_confidence": 0.5,
            "curb_coverage": 0.8,
            "median_offset_m": None,
            "centreline_ids": [2001],
            "geometry": MultiLineString(
                [
                    [(-79.3850, 43.6550), (-79.3855, 43.6600)],
                    [(-79.3855, 43.6600), (-79.3860, 43.6650)],
                ]
            ),
        },
    ]


def test_create_parking_database_schema_and_query(tmp_path: Path, sample_features: list[dict]):
    db_path = tmp_path / SQLITE_FILENAME
    manifest_path = tmp_path / MANIFEST_FILENAME

    manifest = create_parking_database(
        sample_features,
        db_path,
        manifest_path=manifest_path,
        pipeline_version="1.2.3",
        extra_metadata={"test_run": True},
    )

    assert db_path.exists()
    assert manifest_path.exists()
    assert manifest["format"] == "sqlite3_rtree_wkb"
    assert manifest["schema_version"] == 1
    assert manifest["pipeline_version"] == "1.2.3"
    assert manifest["feature_count"] == 2
    assert manifest["metadata"] == {"test_run": True}

    # Verify SHA-256 hash matches disk file
    expected_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert manifest["sha256"] == expected_hash

    # Connect to SQLite and verify schema
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA user_version;")
    assert cur.fetchone()[0] == 1

    cur.execute("PRAGMA page_size;")
    assert cur.fetchone()[0] == 4096

    # Verify features table columns and rows
    cur.execute(
        "SELECT id, highway, rule, schedule_category, max_minutes, geometry_wkb FROM features ORDER BY id;"
    )
    rows = cur.fetchall()
    assert len(rows) == 2

    # Row 1 check
    r1 = rows[0]
    assert r1[0] == "feat_1"
    assert r1[1] == "QUEEN ST W"
    assert r1[2] == "No Parking Anytime"
    assert r1[3] == "no_parking"
    assert r1[4] is None
    geom1 = from_wkb(r1[5])
    assert isinstance(geom1, LineString)
    assert pytest.approx(geom1.coords[0][0]) == -79.4000
    assert pytest.approx(geom1.coords[0][1]) == 43.6500

    # Row 2 check
    r2 = rows[1]
    assert r2[0] == "feat_2"
    assert r2[1] == "BAY ST"
    assert r2[4] == 120
    geom2 = from_wkb(r2[5])
    assert isinstance(geom2, MultiLineString)

    # Spatial R-Tree Bounding Box Query:
    # Query box covering only Queen St W (-79.41 to -79.389, 43.649 to 43.652)
    spatial_sql = """
        SELECT f.id FROM features f
        JOIN rtree_features_idx r ON f.rowid = r.id
        WHERE r.min_lng <= ? AND r.max_lng >= ?
          AND r.min_lat <= ? AND r.max_lat >= ?;
    """
    cur.execute(spatial_sql, (-79.389, -79.41, 43.652, 43.649))
    matched_ids = [row[0] for row in cur.fetchall()]
    assert matched_ids == ["feat_1"]

    # Spatial query for entire downtown area (should match both)
    cur.execute(spatial_sql, (-79.38, -79.41, 43.67, 43.64))
    matched_all = [row[0] for row in cur.fetchall()]
    assert sorted(matched_all) == ["feat_1", "feat_2"]

    # Spatial query disjoint bounding box (should match none)
    cur.execute(spatial_sql, (-79.10, -79.20, 43.80, 43.70))
    matched_none = cur.fetchall()
    assert len(matched_none) == 0

    # Verify secondary indexes exist
    cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
    indexes = {row[0] for row in cur.fetchall()}
    assert "idx_features_category" in indexes
    assert "idx_features_highway" in indexes
    assert "idx_features_permit" in indexes

    conn.close()


def test_export_geojson_to_sqlite(tmp_path: Path):
    geojson_path = tmp_path / "test_map.geojson"
    sqlite_path = tmp_path / "output.sqlite"
    manifest_path = tmp_path / "output.manifest.json"

    geojson_data = {
        "type": "FeatureCollection",
        "metadata": {"source": "unit_test"},
        "features": [
            {
                "id": "1",
                "type": "Feature",
                "properties": {
                    "_id": "1",
                    "Highway": "YONGE ST",
                    "Rule": "No Stopping",
                    "schedule_category": "no_stopping",
                    "Side": "Both",
                    "max": None,
                    "maxMinutes": None,
                    "schedule": None,
                    "is_snow_route": True,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-79.388, 43.65], [-79.388, 43.66]],
                },
            }
        ],
    }
    geojson_path.write_text(json.dumps(geojson_data), encoding="utf-8")

    manifest = export_geojson_to_sqlite(
        geojson_path,
        sqlite_path,
        manifest_path=manifest_path,
    )

    assert sqlite_path.exists()
    assert manifest["feature_count"] == 1
    assert manifest["metadata"] == {"source": "unit_test"}

    conn = sqlite3.connect(sqlite_path)
    cur = conn.cursor()
    cur.execute("SELECT highway, is_snow_route FROM features WHERE id = '1';")
    row = cur.fetchone()
    assert row == ("YONGE ST", 1)
    conn.close()


def test_export_sqlite_cli(tmp_path: Path, monkeypatch):
    geojson_path = tmp_path / "sample.geojson"
    sqlite_path = tmp_path / "sample.sqlite"
    manifest_path = tmp_path / "sample.manifest.json"

    geojson_data = {
        "type": "FeatureCollection",
        "features": [
            {
                "id": "10",
                "type": "Feature",
                "properties": {
                    "_id": "10",
                    "Highway": "KING ST W",
                    "Rule": "No Parking",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-79.39, 43.64], [-79.38, 43.64]],
                },
            }
        ],
    }
    geojson_path.write_text(json.dumps(geojson_data), encoding="utf-8")

    # Run CLI main with arguments
    code = main(
        [
            "--input",
            str(geojson_path),
            "--output",
            str(sqlite_path),
            "--manifest",
            str(manifest_path),
        ]
    )
    assert code == 0
    assert sqlite_path.exists()
    assert manifest_path.exists()

    # Non-existent input should return exit code 1
    err_code = main(["--input", str(tmp_path / "non_existent.geojson")])
    assert err_code == 1

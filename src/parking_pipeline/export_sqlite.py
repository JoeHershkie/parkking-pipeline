"""parking_pipeline/export_sqlite.py - Compile features into indexed SQLite & Manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from shapely import to_wkb
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry

from .log_config import add_verbose_arg, setup_logging
from .paths import data_path

log = logging.getLogger(__name__)

SQLITE_FILENAME = "parking_map.sqlite"
MANIFEST_FILENAME = "parking_data_manifest.json"


def _pipeline_version() -> str:
    try:
        return version("parking-pipeline")
    except PackageNotFoundError:
        return "0.1.0"


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_bool_int(val: Any) -> int:
    return 1 if bool(val) else 0


def _safe_json_dumps(val: Any) -> str | None:
    if val is None:
        return None
    try:
        return json.dumps(val, separators=(",", ":"), ensure_ascii=False)
    except (ValueError, TypeError):
        return None


def _extract_geometry(feat: dict[str, Any]) -> BaseGeometry | None:
    geom = feat.get("geometry")
    if geom is None:
        return None
    if isinstance(geom, BaseGeometry):
        return geom
    if isinstance(geom, dict):
        try:
            return shapely_shape(geom)
        except Exception:
            return None
    return None


def create_parking_database(
    features: Iterable[dict[str, Any]],
    output_db_path: Path,
    *,
    manifest_path: Path | None = None,
    pipeline_version: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compiles features with Shapely or GeoJSON geometries into an indexed SQLite database.

    Creates:
      - `features` table containing attributes and WKB geometry blob.
      - `rtree_features_idx` virtual table (R-Tree spatial index on bounding box coordinates).
      - Secondary B-tree indexes for fast attribute lookups.
      - Release manifest sidecar (`parking_data_manifest.json`) containing SHA-256 fingerprint,
        feature counts, bounds, and build provenance.
    """
    output_db_path = Path(output_db_path)
    if output_db_path.exists():
        output_db_path.unlink()

    output_db_path.parent.mkdir(parents=True, exist_ok=True)

    db_version = pipeline_version or _pipeline_version()

    # Sort deterministically by id / _id
    def _sort_key(item: dict[str, Any]) -> str:
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        fid = item.get("_id") or item.get("id") or props.get("_id") or props.get("id") or ""
        return str(fid)

    sorted_features = sorted(features, key=_sort_key)

    conn = sqlite3.connect(output_db_path)
    cur = conn.cursor()

    # Optimization pragmas for read-only bundled databases
    cur.execute("PRAGMA page_size = 4096;")
    cur.execute("PRAGMA journal_mode = OFF;")
    cur.execute("PRAGMA synchronous = OFF;")
    cur.execute("PRAGMA user_version = 1;")

    # 1. Create main relational features table
    cur.execute(
        """
        CREATE TABLE features (
            rowid INTEGER PRIMARY KEY,
            id TEXT NOT NULL UNIQUE,
            highway TEXT,
            rule TEXT,
            schedule_category TEXT,
            side TEXT,
            side_mode TEXT,
            max TEXT,
            max_minutes INTEGER,
            schedule_json TEXT,
            is_snow_route INTEGER DEFAULT 0,
            streetcar_corridor INTEGER DEFAULT 0,
            former_municipality TEXT,
            regional_winter_rule TEXT,
            permit_area_id TEXT,
            permit_parking_active INTEGER DEFAULT 0,
            has_hydrant INTEGER DEFAULT 0,
            hydrant_count INTEGER DEFAULT 0,
            hydrant_setback_m REAL,
            curb_geometry_method TEXT,
            curb_confidence REAL,
            curb_coverage REAL,
            median_offset_m REAL,
            centreline_ids_json TEXT,
            geometry_wkb BLOB NOT NULL
        );
        """
    )

    # 2. Create SQLite R-Tree spatial index table
    cur.execute(
        """
        CREATE VIRTUAL TABLE rtree_features_idx USING rtree(
            id,
            min_lng,
            max_lng,
            min_lat,
            max_lat
        );
        """
    )

    feature_insert_sql = """
        INSERT INTO features (
            rowid, id, highway, rule, schedule_category, side, side_mode,
            max, max_minutes, schedule_json, is_snow_route, streetcar_corridor,
            former_municipality, regional_winter_rule, permit_area_id, permit_parking_active,
            has_hydrant, hydrant_count, hydrant_setback_m, curb_geometry_method,
            curb_confidence, curb_coverage, median_offset_m, centreline_ids_json, geometry_wkb
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        );
    """

    rtree_insert_sql = """
        INSERT INTO rtree_features_idx (id, min_lng, max_lng, min_lat, max_lat)
        VALUES (?, ?, ?, ?, ?);
    """

    total_bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    valid_feature_count = 0

    for rowid, item in enumerate(sorted_features, start=1):
        if "properties" in item and isinstance(item["properties"], dict):
            props = dict(item["properties"])
            feat_id = str(item.get("id") or props.get("_id") or rowid)
        else:
            props = dict(item)
            feat_id = str(props.get("_id") or props.get("id") or rowid)

        geom = _extract_geometry(item)
        if geom is None or geom.is_empty:
            continue

        min_lng, min_lat, max_lng, max_lat = geom.bounds
        total_bounds[0] = min(total_bounds[0], min_lng)
        total_bounds[1] = min(total_bounds[1], min_lat)
        total_bounds[2] = max(total_bounds[2], max_lng)
        total_bounds[3] = max(total_bounds[3], max_lat)

        wkb_bytes = to_wkb(geom, hex=False)

        schedule_data = props.get("schedule")
        schedule_json_str = _safe_json_dumps(schedule_data)

        centreline_ids = props.get("centreline_ids")
        centreline_ids_str = _safe_json_dumps(centreline_ids)

        cur.execute(
            feature_insert_sql,
            (
                rowid,
                feat_id,
                props.get("Highway") or props.get("highway"),
                props.get("Rule") or props.get("rule"),
                props.get("schedule_category"),
                props.get("Side") or props.get("side"),
                props.get("side_mode"),
                props.get("max"),
                _safe_int(props.get("maxMinutes") or props.get("max_minutes")),
                schedule_json_str,
                _safe_bool_int(props.get("is_snow_route")),
                _safe_bool_int(props.get("streetcar_corridor")),
                props.get("former_municipality"),
                props.get("regional_winter_rule"),
                props.get("permit_area_id"),
                _safe_bool_int(props.get("permit_parking_active")),
                _safe_bool_int(props.get("has_hydrant")),
                _safe_int(props.get("hydrant_count")) or 0,
                _safe_float(props.get("hydrant_setback_m")),
                props.get("curb_geometry_method"),
                _safe_float(props.get("curb_confidence")),
                _safe_float(props.get("curb_coverage")),
                _safe_float(props.get("median_offset_m")),
                centreline_ids_str,
                wkb_bytes,
            ),
        )

        cur.execute(rtree_insert_sql, (rowid, min_lng, max_lng, min_lat, max_lat))
        valid_feature_count += 1

    # 3. Create secondary B-tree indexes
    cur.execute("CREATE INDEX idx_features_category ON features(schedule_category);")
    cur.execute("CREATE INDEX idx_features_highway ON features(highway);")
    cur.execute("CREATE INDEX idx_features_permit ON features(permit_area_id);")

    conn.commit()

    # VACUUM ensures a clean, defragmented single-file database
    cur.execute("VACUUM;")
    conn.close()

    # 4. Generate SHA-256 Checksum & Release Manifest
    db_bytes = output_db_path.read_bytes()
    sha256_hash = hashlib.sha256(db_bytes).hexdigest()

    bounding_box = {
        "min_lng": total_bounds[0] if valid_feature_count > 0 else 0.0,
        "min_lat": total_bounds[1] if valid_feature_count > 0 else 0.0,
        "max_lng": total_bounds[2] if valid_feature_count > 0 else 0.0,
        "max_lat": total_bounds[3] if valid_feature_count > 0 else 0.0,
    }

    manifest: dict[str, Any] = {
        "artifact": output_db_path.name,
        "format": "sqlite3_rtree_wkb",
        "schema_version": 1,
        "pipeline_version": db_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "sha256": sha256_hash,
        "file_size_bytes": len(db_bytes),
        "feature_count": valid_feature_count,
        "bounding_box": bounding_box,
    }

    if extra_metadata:
        manifest["metadata"] = extra_metadata

    if manifest_path is None:
        manifest_path = output_db_path.with_name(MANIFEST_FILENAME)

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log.info(
        "Created SQLite database '%s' (%s features, %d bytes, SHA-256: %s)",
        output_db_path,
        valid_feature_count,
        len(db_bytes),
        sha256_hash[:12],
    )
    return manifest


def export_geojson_to_sqlite(
    geojson_path: Path,
    output_db_path: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Converts an existing GeoJSON FeatureCollection file into an indexed SQLite database."""
    log.info("Loading GeoJSON features from %s...", geojson_path)
    with geojson_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    extra_meta = data.get("metadata")
    return create_parking_database(
        features,
        output_db_path,
        manifest_path=manifest_path,
        extra_metadata=extra_meta,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile GeoJSON features or pipeline dataset into an indexed SQLite database."
    )
    add_verbose_arg(parser)
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Input GeoJSON file path (default: data/final_parking_map.geojson)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output SQLite database path (default: data/parking_map.sqlite)",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Output manifest JSON path (default: data/parking_data_manifest.json)",
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    input_path = Path(args.input) if args.input else data_path("final_parking_map.geojson")
    output_path = Path(args.output) if args.output else data_path(SQLITE_FILENAME)
    manifest_path = Path(args.manifest) if args.manifest else data_path(MANIFEST_FILENAME)

    if not input_path.exists():
        log.error("Input file not found: %s", input_path)
        return 1

    try:
        export_geojson_to_sqlite(input_path, output_path, manifest_path=manifest_path)
        log.info("✓ SQLite compilation completed successfully: %s", output_path)
        return 0
    except Exception:
        log.exception("✗ Failed to compile SQLite database")
        return 1


if __name__ == "__main__":
    sys.exit(main())

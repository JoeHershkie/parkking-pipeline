"""Copy committed sample data files when full downloads are absent (CI / fresh clone)."""

from __future__ import annotations

import shutil

from parking_pipeline.paths import DATA_DIR, data_path
from parking_pipeline.road_edges import (
    ROAD_EDGES_FILENAME,
    ROAD_EDGES_MANIFEST_FILENAME,
)

_SAMPLE_DATA_FILES = (
    'tcl_streets.geojson',
    'tcl_intersections.geojson',
    'tcl_street_names.csv',
    'street_aliases.csv',
    ROAD_EDGES_FILENAME,
)


def ensure_sample_data_copies() -> bool:
    """Copy sample TCL/alias/road-edge files into data/ when missing."""
    for name in _SAMPLE_DATA_FILES:
        target = data_path(name)
        sample = DATA_DIR / 'samples' / name
        if not target.exists() and sample.exists():
            shutil.copy(sample, target)
    _copy_road_edges_sample_manifest()
    return using_sample_tcl()


def _copy_road_edges_sample_manifest() -> None:
    """Copy the sample sidecar only when the active GeoPackage is the sample."""
    if not using_sample_road_edges():
        return
    target = data_path(ROAD_EDGES_MANIFEST_FILENAME)
    sample = DATA_DIR / 'samples' / ROAD_EDGES_MANIFEST_FILENAME
    if not target.exists() and sample.exists():
        shutil.copy(sample, target)


def using_sample_tcl() -> bool:
    """True when the active streets layer matches the committed sample fixture."""
    streets = data_path('tcl_streets.geojson')
    sample = DATA_DIR / 'samples' / 'tcl_streets.geojson'
    if not streets.exists() or not sample.exists():
        return False
    return streets.stat().st_size == sample.stat().st_size


def using_sample_road_edges() -> bool:
    """True when the active road-edge GeoPackage matches the committed sample."""
    gpkg = data_path(ROAD_EDGES_FILENAME)
    sample = DATA_DIR / 'samples' / ROAD_EDGES_FILENAME
    if not gpkg.exists() or not sample.exists():
        return False
    return gpkg.stat().st_size == sample.stat().st_size

"""Copy committed sample data files when full downloads are absent (CI / fresh clone)."""

from __future__ import annotations

import shutil

from parking_pipeline.paths import DATA_DIR, data_path

_SAMPLE_DATA_FILES = (
    'tcl_streets.geojson',
    'tcl_intersections.geojson',
    'tcl_street_names.csv',
    'street_aliases.csv',
)


def ensure_sample_data_copies() -> bool:
    """Copy sample TCL/alias files into data/ when missing."""
    for name in _SAMPLE_DATA_FILES:
        target = data_path(name)
        sample = DATA_DIR / 'samples' / name
        if not target.exists() and sample.exists():
            shutil.copy(sample, target)
    return using_sample_tcl()


def using_sample_tcl() -> bool:
    """True when the active streets layer matches the committed sample fixture."""
    streets = data_path('tcl_streets.geojson')
    sample = DATA_DIR / 'samples' / 'tcl_streets.geojson'
    if not streets.exists() or not sample.exists():
        return False
    return streets.stat().st_size == sample.stat().st_size

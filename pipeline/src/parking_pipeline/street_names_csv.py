"""Build or refresh data/tcl_street_names.csv from tcl_streets.geojson."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .paths import data_path

log = logging.getLogger(__name__)


def _agg_street_names(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per LINEAR_NAME_FULL_LEGAL with name variants and segment stats."""
    g = gdf.groupby('LINEAR_NAME_FULL_LEGAL', dropna=False)

    rows = []
    for legal, grp in g:
        full_vals = sorted({str(x) for x in grp['LINEAR_NAME_FULL'].dropna().unique()})
        label_vals = sorted({str(x) for x in grp['LINEAR_NAME_LABEL'].dropna().unique()})
        base_vals = sorted({str(x) for x in grp['LINEAR_NAME'].dropna().unique()})
        type_vals = sorted(
            {str(x) for x in grp['LINEAR_NAME_TYPE'].dropna().unique() if str(x) != 'None'}
        )
        dir_vals = sorted(
            {str(x) for x in grp['LINEAR_NAME_DIR'].dropna().unique() if str(x) != 'None'}
        )
        desc_vals = sorted(
            {str(x) for x in grp['LINEAR_NAME_DESC'].dropna().unique() if str(x) != 'None'}
        )
        feature_vals = sorted({str(x) for x in grp['FEATURE_CODE_DESC'].dropna().unique()})

        rows.append({
            'linear_name_full_legal': legal,
            'linear_name_full': ' | '.join(full_vals),
            'linear_name_label': ' | '.join(label_vals),
            'linear_name_base': ' | '.join(base_vals),
            'linear_name_type': ' | '.join(type_vals),
            'linear_name_dir': ' | '.join(dir_vals),
            'linear_name_desc': ' | '.join(desc_vals),
            'feature_code_desc': ' | '.join(feature_vals),
            'segment_count': len(grp),
            'linear_name_id_count': grp['LINEAR_NAME_ID'].nunique(),
        })

    return pd.DataFrame(rows).sort_values('linear_name_full_legal').reset_index(drop=True)


def export_street_names_csv(
    *,
    output: Path | None = None,
    streets_path: Path | None = None,
) -> Path:
    """Write unique TCL legal street names to CSV. Returns output path."""
    src = streets_path or data_path('tcl_streets.geojson')
    out = output or data_path('tcl_street_names.csv')
    if not src.exists():
        raise FileNotFoundError(f'Missing {src}')

    log.info('Reading %s...', src)
    gdf = gpd.read_file(src)
    log.info('  %d centreline segments', len(gdf))
    df = _agg_street_names(gdf)
    log.info('  %d unique LINEAR_NAME_FULL_LEGAL names', len(df))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    log.info('Wrote %s', out)
    return out


def street_names_csv_stale() -> bool:
    """True when CSV is missing or older than tcl_streets.geojson."""
    csv_path = data_path('tcl_street_names.csv')
    streets_path = data_path('tcl_streets.geojson')
    if not streets_path.exists():
        return False
    if not csv_path.exists():
        return True
    return csv_path.stat().st_mtime < streets_path.stat().st_mtime


def ensure_street_names_csv() -> Path | None:
    """Regenerate tcl_street_names.csv when missing or stale. Returns path or None."""
    streets_path = data_path('tcl_streets.geojson')
    if not streets_path.exists():
        log.warning('Skipping street-name export: %s not found', streets_path)
        return None
    if not street_names_csv_stale():
        return data_path('tcl_street_names.csv')
    log.info('tcl_street_names.csv missing or older than tcl_streets.geojson — regenerating')
    return export_street_names_csv()

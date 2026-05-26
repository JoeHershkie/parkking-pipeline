#!/usr/bin/env python3
"""Export unique street names from data/tcl_streets.geojson to CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from paths import data_path  # noqa: E402


def _agg_street_names(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per LINEAR_NAME_FULL_LEGAL with name variants and segment stats."""
    g = gdf.groupby('LINEAR_NAME_FULL_LEGAL', dropna=False)

    rows = []
    for legal, grp in g:
        full_vals = sorted({str(x) for x in grp['LINEAR_NAME_FULL'].dropna().unique()})
        label_vals = sorted({str(x) for x in grp['LINEAR_NAME_LABEL'].dropna().unique()})
        base_vals = sorted({str(x) for x in grp['LINEAR_NAME'].dropna().unique()})
        type_vals = sorted({str(x) for x in grp['LINEAR_NAME_TYPE'].dropna().unique() if str(x) != 'None'})
        dir_vals = sorted({str(x) for x in grp['LINEAR_NAME_DIR'].dropna().unique() if str(x) != 'None'})
        desc_vals = sorted({str(x) for x in grp['LINEAR_NAME_DESC'].dropna().unique() if str(x) != 'None'})
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

    out = pd.DataFrame(rows)
    return out.sort_values('linear_name_full_legal').reset_index(drop=True)


def _agg_segments(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per centreline segment (optional detailed export)."""
    cols = [
        'CENTRELINE_ID',
        'LINEAR_NAME_ID',
        'LINEAR_NAME_FULL',
        'LINEAR_NAME_FULL_LEGAL',
        'LINEAR_NAME',
        'LINEAR_NAME_TYPE',
        'LINEAR_NAME_DIR',
        'LINEAR_NAME_DESC',
        'LINEAR_NAME_LABEL',
        'FEATURE_CODE_DESC',
        'FROM_INTERSECTION_ID',
        'TO_INTERSECTION_ID',
    ]
    return gdf[cols].sort_values(['LINEAR_NAME_FULL_LEGAL', 'CENTRELINE_ID']).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=data_path('tcl_street_names.csv'),
        help='Output CSV path (default: data/tcl_street_names.csv)',
    )
    parser.add_argument(
        '--segments',
        action='store_true',
        help='Export every centreline segment instead of unique legal names',
    )
    args = parser.parse_args()

    src = data_path('tcl_streets.geojson')
    if not src.exists():
        raise SystemExit(f'Missing {src}')

    print(f'Reading {src}...')
    gdf = gpd.read_file(src)
    print(f'  {len(gdf)} centreline segments')

    if args.segments:
        df = _agg_segments(gdf)
        print(f'  writing {len(df)} segment rows')
    else:
        df = _agg_street_names(gdf)
        print(f'  {len(df)} unique LINEAR_NAME_FULL_LEGAL names')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f'Wrote {args.output}')


if __name__ == '__main__':
    main()

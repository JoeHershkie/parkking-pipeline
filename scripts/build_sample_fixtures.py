"""Build small TCL + parking CSV fixtures under data/samples/ for CI and offline tests."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'data' / 'samples'

# Streets needed by golden-path geometry / highway / intersection tests.
STREET_KEYWORDS = (
    'armadale',
    'colbeck',
    'annette',
    'manning',
    'harbord',
    'dupont',
    'joyce parkway',
    'joyce trimmer',
    'spadina',
    'baxter',
    'yonge',
    'elm',
    'bond',
    'duncairn',
    'fairfield',
    'cardiff',
    'bloor',
    'christie',
    'royal york',
    'beaumont',
    'sibley',
    'victoria',
    "o'connor",
    'sandra',
)


def _street_matches(name: str) -> bool:
    lower = (name or '').lower()
    return any(kw in lower for kw in STREET_KEYWORDS)


def build_tcl_samples(streets_path: Path, ix_path: Path, out_dir: Path) -> None:
    streets = gpd.read_file(streets_path)
    ix = gpd.read_file(ix_path)

    name_col = 'LINEAR_NAME_FULL_LEGAL'
    mask = streets[name_col].fillna('').map(_street_matches)
    seed = streets.loc[mask].copy()
    ix_ids: set[int] = set()
    for col in ('FROM_INTERSECTION_ID', 'TO_INTERSECTION_ID'):
        ix_ids.update(int(v) for v in seed[col].dropna().astype(int))

    # One-hop expansion: any segment touching a seed intersection.
    expanded = streets[
        streets['FROM_INTERSECTION_ID'].astype('Int64').isin(ix_ids)
        | streets['TO_INTERSECTION_ID'].astype('Int64').isin(ix_ids)
    ].copy()
    for col in ('FROM_INTERSECTION_ID', 'TO_INTERSECTION_ID'):
        ix_ids.update(int(v) for v in expanded[col].dropna().astype(int))

    sample_streets = expanded.drop_duplicates(subset=['CENTRELINE_ID'], keep='first')
    sample_ix = ix[ix['INTERSECTION_ID'].astype(int).isin(ix_ids)].copy()

    out_dir.mkdir(parents=True, exist_ok=True)
    sample_streets.to_file(out_dir / 'tcl_streets.geojson', driver='GeoJSON')
    sample_ix.to_file(out_dir / 'tcl_intersections.geojson', driver='GeoJSON')
    print(
        f'TCL samples: {len(sample_streets)} street segments, '
        f'{len(sample_ix)} intersections -> {out_dir}',
    )


def build_street_names_sample(streets_path: Path, out_dir: Path) -> None:
    streets = gpd.read_file(streets_path)
    names = (
        streets['LINEAR_NAME_FULL_LEGAL']
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s.map(_street_matches)]
        .drop_duplicates()
        .sort_values()
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({'linear_name_full_legal': names}).to_csv(
        out_dir / 'tcl_street_names.csv', index=False,
    )
    print(f'Street names sample: {len(names)} rows -> {out_dir / "tcl_street_names.csv"}')


def build_parse_cohort_sample(targets_path: Path, out_dir: Path, n: int = 50) -> None:
    if not targets_path.exists():
        print(f'Skip parse cohort sample — missing {targets_path}')
        return
    df = pd.read_csv(targets_path, nrows=5000)
    # Prefer rows whose Between text mentions our fixture streets.
    pattern = '|'.join(STREET_KEYWORDS)
    mask = df['Between'].fillna('').str.lower().str.contains(pattern, regex=True)
    sample = df.loc[mask].head(n)
    if len(sample) < n:
        sample = pd.concat([sample, df.head(n - len(sample))]).drop_duplicates(subset=['_id'])
    out_dir.mkdir(parents=True, exist_ok=True)
    sample.to_csv(out_dir / 'clean_parking_targets.csv', index=False)
    print(f'Parse cohort sample: {len(sample)} rows -> {out_dir / "clean_parking_targets.csv"}')


def main() -> None:
    data = ROOT / 'data'
    streets = data / 'tcl_streets.geojson'
    ix = data / 'tcl_intersections.geojson'
    if not streets.exists():
        streets = ROOT / 'tcl_streets.geojson'
        ix = ROOT / 'tcl_intersections.geojson'
    if not streets.exists():
        raise SystemExit(f'TCL streets not found under {data} or repo root')

    build_tcl_samples(streets, ix, SAMPLES)
    build_street_names_sample(streets, SAMPLES)
    build_parse_cohort_sample(data / 'clean_parking_targets.csv', SAMPLES)


if __name__ == '__main__':
    main()

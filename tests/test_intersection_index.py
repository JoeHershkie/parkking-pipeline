"""Tests for fast TCL intersection substring index."""

from __future__ import annotations

import random

import geopandas as gpd
import pytest

from parking_pipeline import intersection_index as ix
from parking_pipeline import tcl_graph as tg
from parking_pipeline.intersection_normalize import (
    expand_cross_lookup_names,
    tcl_search_tokens,
)
from parking_pipeline.paths import data_path

ARMADALE = 'armadale avenue'
COLBECK = 'Colbeck Street'
ANNETTE = 'Annette Street'
NORTHERN_COLBECK_ID = 13466437
SOUTHERN_COLBECK_ID = 13466420


@pytest.fixture(scope='module')
def intersections_gdf():
    return gpd.read_file(data_path('tcl_intersections.geojson'))


@pytest.fixture(scope='module')
def configured(intersections_gdf):
    tg.configure_intersections(intersections_gdf)
    return intersections_gdf


def _brute_pair_ids(gdf: gpd.GeoDataFrame, street_1: str, street_2: str) -> list[int]:
    """Brute-force pair match using the same token/cross expansion as production."""
    desc = gdf['INTERSECTION_DESC'].str.lower()
    id_order = {
        int(raw_id): i
        for i, raw_id in enumerate(gdf['INTERSECTION_ID'].tolist())
    }

    def names_for(side: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for part in expand_cross_lookup_names(side):
            key = part.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out or [side]

    for n1 in names_for(street_1):
        tokens_1 = tcl_search_tokens(n1)
        for n2 in names_for(street_2):
            tokens_2 = tcl_search_tokens(n2)
            for t1 in tokens_1:
                mask_a = desc.str.contains(t1, regex=False, na=False)
                if not mask_a.any():
                    continue
                for t2 in tokens_2:
                    mask = mask_a & desc.str.contains(t2, regex=False, na=False)
                    if not mask.any():
                        continue
                    matched = sorted(
                        {int(x) for x in gdf.loc[mask, 'INTERSECTION_ID'].tolist()},
                        key=id_order.__getitem__,
                    )
                    return matched
    return []


def test_resolve_matches_legacy_scan(configured):
    gdf = configured
    pairs = [
        (ARMADALE, COLBECK),
        (ARMADALE, ANNETTE),
        ('Windermere Avenue', 'st johns rd'),
        ('Pears Avenue', 'ave rd'),
        ('nonexistent highway xyz', 'also missing'),
    ]
    for a, b in pairs:
        expected = _brute_pair_ids(gdf, a, b)
        assert list(ix.resolve_pair_ids(a, b)) == expected


def test_ahocorasick_warm_matches_brute(configured):
    gdf = configured
    ix.configure(gdf)
    tokens = ix.collect_tokens_from_pairs([
        (ARMADALE, COLBECK),
        (ARMADALE, ANNETTE),
        ('Pears Avenue', 'ave rd'),
    ])
    ix.configure(gdf)
    ix.warm_tokens(tokens)
    ac_postings = ix.postings_snapshot()

    ix.configure(gdf)
    for token in tokens:
        brute = tuple(
            int(row['INTERSECTION_ID'])
            for _, row in gdf.iterrows()
            if token in str(row['INTERSECTION_DESC']).lower()
        )
        assert ac_postings[token] == brute


def test_warm_tokens_same_as_lazy(configured):
    gdf = configured
    ix.configure(gdf)
    cold = ix.resolve_pair_ids(ARMADALE, COLBECK)
    ix.configure(gdf)
    tokens = ix.collect_tokens_from_pairs([(ARMADALE, COLBECK), (ARMADALE, ANNETTE)])
    warmed = ix.warm_tokens(tokens)
    assert warmed == len(tokens)
    warm = ix.resolve_pair_ids(ARMADALE, COLBECK)
    assert warm == cold


def test_resolve_intersection_ids_colbeck(configured):
    del configured
    ids = tg.resolve_intersection_ids(ARMADALE, COLBECK)
    assert NORTHERN_COLBECK_ID in ids
    assert SOUTHERN_COLBECK_ID in ids


def test_random_sample_equiv_legacy(configured):
    gdf = configured
    rng = random.Random(42)
    for _ in range(40):
        row = gdf.iloc[rng.randrange(len(gdf))]
        parts = [p.strip() for p in str(row['INTERSECTION_DESC']).split('/')]
        if len(parts) < 2:
            continue
        assert list(ix.resolve_pair_ids(parts[0], parts[-1])) == _brute_pair_ids(
            gdf, parts[0], parts[-1],
        )


def test_match_count(configured):
    del configured
    assert ix.match_count(ARMADALE, COLBECK) == len(
        tg.resolve_intersection_ids(ARMADALE, COLBECK),
    )

"""Tests for root×root intersection pair disambiguation."""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import intersection_index as ix
import intersection_pair_resolve as ipr
import tcl_graph as tg
import tcl_highway_resolve as thr
from paths import data_path


@pytest.fixture(scope='module')
def configured():
    ix_gdf = gpd.read_file(data_path('tcl_intersections.geojson'))
    st_gdf = gpd.read_file(data_path('tcl_streets.geojson'))
    tg.configure_intersections(ix_gdf)
    legal = set(st_gdf['LINEAR_NAME_FULL_LEGAL'].str.lower().unique())
    thr.build_index_from_csv(legal_keys=legal)
    return ix_gdf


def test_resolve_pair_via_roots_cardiff_fairfield(configured):
    match = ipr.resolve_pair_via_roots('Cardiff Avenue', 'Fairfield Avenue')
    assert match is not None
    assert match.intersection_id == 13456248
    assert match.street_a_token == 'cardiff road'
    assert match.street_b_token == 'fairfield road'
    assert 'fairfield' in match.intersection_desc and 'cardiff' in match.intersection_desc


def test_resolve_pair_ids_root_fallback(configured):
    ids = ix.resolve_pair_ids('Cardiff Avenue', 'Fairfield Avenue')
    assert list(ids) == [13456248]


def test_context_disambiguate_fairfield_with_cardiff_cross(configured):
    parsed = {
        'start_intersection': 'Cardiff Avenue',
        'end_intersection': 'Fairfield Avenue',
    }
    assert thr.resolve_tcl_highway_with_context('Fairfield Avenue', parsed) == 'fairfield road'


def test_highway_lookup_ambiguous(configured):
    assert thr.highway_lookup_ambiguous('Fairfield Avenue') is True
    assert thr.highway_lookup_ambiguous('Beaumont Street') is False


BOND_DUNCAIRN_ID = 13450942


def test_resolve_pair_bond_avenue_duncairn_via_root_or_variant(configured):
    """Bylaw Bond Avenue × Duncairn; TCL junction is bond park trl × duncairn."""
    ids = ix.resolve_pair_ids('Bond Avenue', 'Duncairn Road')
    assert list(ids) == [BOND_DUNCAIRN_ID]

    match = ipr.resolve_pair_via_roots('Bond Avenue', 'Duncairn Road')
    assert match is not None
    assert match.intersection_id == BOND_DUNCAIRN_ID
    assert match.street_a_token == 'bond park trail'
    assert 'duncairn' in match.intersection_desc
    assert 'bond park' in match.intersection_desc


def test_resolve_pair_unique_legal_variant_bond(configured):
    hit = ipr.resolve_pair_via_unique_legal_variant('Bond Avenue', 'Duncairn Road')
    assert list(hit) == [BOND_DUNCAIRN_ID]


def test_unique_remap_for_partner_bond_duncairn(configured):
    partner = thr.resolve_tcl_highway('Duncairn Road')
    assert thr._unique_remap_for_partner(partner, 'Bond Avenue') == 'bond park trail'

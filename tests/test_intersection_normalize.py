"""Tests for intersection name normalization."""

import sys
from pathlib import Path

import geopandas as gpd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from intersection_normalize import (  # noqa: E402
    apply_street_alias,
    clear_alias_cache,
    expand_cross_lookup_names,
    normalize_intersection_street,
    strip_lookup_prefixes,
    tcl_search_tokens,
)


def test_old_weston_not_corrupted():
    assert normalize_intersection_street('Old Weston Road') == 'old weston rd'
    assert 'won' not in normalize_intersection_street('Old Weston Road')


def test_st_clair_period_stripped():
    n = normalize_intersection_street('St. Clair Avenue West')
    assert 'st clair ave w' == n
    assert '.' not in n


def test_parkway_gate_lawn():
    assert 'pkwy' in normalize_intersection_street('Oriole Parkway')
    assert normalize_intersection_street('Ardwold Gate') == 'ardwold gt'
    assert 'lwn' in normalize_intersection_street('Alfresco Lawn')


def test_weston_road_unchanged():
    assert normalize_intersection_street('Weston Road') == 'weston rd'


def test_avenue_road_alias_matches_tcl():
    clear_alias_cache()
    assert apply_street_alias('Avenue Road') == 'avenue rd'
    assert apply_street_alias('avenue road') == 'avenue rd'


def test_apostrophe_preserved_in_normalizer():
    assert normalize_intersection_street("O'Connor Drive") == "o'connor dr"
    assert "'" in normalize_intersection_street("O'Hara Avenue")


def test_phase_a_aliases():
    clear_alias_cache()
    assert apply_street_alias("St. John's Road") == 'st johns'
    assert apply_street_alias('Indian Road Crescent') == 'indian rd'
    assert apply_street_alias('Austin Terrace') == 'austin ter'


def test_tcl_search_tokens_includes_gate_spelling_variant():
    tokens = tcl_search_tokens('York Gate Boulevard')
    assert 'york gt blvd' in tokens
    assert 'york gate blvd' in tokens


def test_tcl_search_tokens_includes_gardens_spelling_variant():
    tokens = tcl_search_tokens('Locust Lodge Gardens')
    assert 'locust lodge gdns' in tokens
    assert 'locust lodge gardens' in tokens


def test_tcl_search_tokens_includes_parkway_spelling_variant():
    tokens = tcl_search_tokens('Murray Ross Parkway')
    assert 'murray ross pkwy' in tokens
    assert 'murray ross parkway' in tokens


def test_expand_cross_lookup_skips_leg_slash_compound():
    leg = expand_cross_lookup_names('Pape Avenue/Donlands Avenue')
    assert 'Pape Avenue' in leg
    assert 'Donlands Avenue' in leg
    embedded = expand_cross_lookup_names(
        'Public lane first west of the north/south leg of Rankin Crescent',
    )
    assert 'Rankin Crescent' in embedded


def test_tcl_search_tokens_apostrophe_variant():
    tokens = tcl_search_tokens("St. Anne's Road")
    assert "st anne's rd" in tokens or 'st annes rd' in tokens
    assert 'st annes rd' in tokens


def test_expand_cross_lookup_names_slash_and_leg():
    assert expand_cross_lookup_names('Apsley Road/Saunders Street') == (
        'Apsley Road/Saunders Street',
        'Apsley Road',
        'Saunders Street',
    )
    leg = expand_cross_lookup_names('the north/south leg of Coatsworth Crescent')
    assert 'Coatsworth Crescent' in leg
    assert 'the north/south leg of Coatsworth Crescent' in leg


def test_st_possessive_aliases():
    clear_alias_cache()
    assert apply_street_alias('St. Patrick Square') == 'st patricks sq'
    assert apply_street_alias("St. Olaves Road") == "st olave's rd"
    assert apply_street_alias('Pengelly Court') == 'pengelly crt'


def test_tcl_search_tokens_spelled_direction():
    tokens = tcl_search_tokens('North Bonnington Avenue')
    assert 'north bonnington ave' in tokens
    assert 'n bonnington ave' in tokens


def test_tcl_search_tokens_st_clair_ave_short():
    tokens = tcl_search_tokens('St. Clair Avenue West')
    assert 'st clair ave w' in tokens
    assert 'st clair w' in tokens


def test_strip_lookup_prefixes():
    assert strip_lookup_prefixes('From St. Clair Avenue West') == 'St. Clair Avenue West'
    assert (
        strip_lookup_prefixes("the east curb line of St. Hilda's Avenue")
        == "St. Hilda's Avenue"
    )


def test_tcl_search_tokens_st_clair_west_alias():
    clear_alias_cache()
    tokens = tcl_search_tokens('St. Clair West')
    assert 'st clair ave w' in tokens
    assert 'st clair w' in tokens


@pytest.fixture(scope='module')
def tcl_street_index():
    import tcl_highway_resolve as thr
    from paths import data_path

    st = gpd.read_file(data_path('tcl_streets.geojson'))
    legal = set(st['LINEAR_NAME_FULL_LEGAL'].str.lower())
    thr.build_index_from_csv(legal_keys=legal)
    return thr


def test_tcl_search_tokens_suffix_remap_from_street_index(tcl_street_index):
    del tcl_street_index
    tokens = tcl_search_tokens('Kelso Street')
    assert 'kelso st' in tokens
    assert 'kelso avenue' in tokens
    assert 'kelso ave' in tokens


def test_tcl_search_tokens_typo_via_resolve(tcl_street_index):
    del tcl_street_index
    tokens = tcl_search_tokens('Younge Street')
    assert 'yonge street' in tokens


if __name__ == '__main__':
    test_old_weston_not_corrupted()
    test_st_clair_period_stripped()
    test_parkway_gate_lawn()
    test_weston_road_unchanged()
    test_apostrophe_preserved_in_normalizer()
    test_phase_a_aliases()
    print('ok')

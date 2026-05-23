"""Tests for intersection name normalization."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from intersection_normalize import (  # noqa: E402
    apply_street_alias,
    clear_alias_cache,
    normalize_intersection_street,
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


if __name__ == '__main__':
    test_old_weston_not_corrupted()
    test_st_clair_period_stripped()
    test_parkway_gate_lawn()
    test_weston_road_unchanged()
    test_apostrophe_preserved_in_normalizer()
    test_phase_a_aliases()
    print('ok')

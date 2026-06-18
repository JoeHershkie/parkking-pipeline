"""Tests for bylaw Highway → TCL legal name resolution."""

from pathlib import Path

from parking_pipeline import tcl_highway_resolve as thr  # noqa: E402
from parking_pipeline.tcl_highway_key import tcl_highway_key  # noqa: E402


def _install_mini_index() -> None:
    legals = [
        'Beaumont Road',
        'Moss Park Trail',
        'Lady Sarah Crescent',
        'Don Valley Drive',
        'Don Valley Parkway N',
        'Don Valley Parkway S',
        'Braeside Crescent',
        'Braeside Road',
        'Kenilworth Avenue',
        'Spadina Avenue',
        'Beaconsfield Avenue',
        'Mc Farland Avenue',
        'Epic Lane Road',
        'St Cuthberts Road',
        'Antibes Drive',
    ]
    keys = {tcl_highway_key(name) for name in legals}
    base_to_legals = {
        'Beaumont': ['Beaumont Road'],
        'Moss Park': ['Moss Park Trail'],
        'Lady Sarah': ['Lady Sarah Crescent'],
        'Don Valley': ['Don Valley Drive'],
        'Don Valley Parkway N': ['Don Valley Parkway N'],
        'Don Valley Parkway S': ['Don Valley Parkway S'],
        'Braeside': ['Braeside Crescent', 'Braeside Road'],
        'Kenilworth': ['Kenilworth Avenue'],
        'Spadina': ['Spadina Avenue'],
        'Beaconsfield': ['Beaconsfield Avenue'],
        'Mc Farland': ['Mc Farland Avenue'],
        'Epic': ['Epic Lane Road'],
    }
    variant = {
        'Beaconsfield Avenue': 'Beaconsfield Avenue',
        'Mc Farland Avenue': 'Mc Farland Avenue',
    }
    thr.build_index(
        legal_keys=keys,
        base_to_legals=base_to_legals,
        variant_to_legal=variant,
    )


def setup_function() -> None:
    _install_mini_index()


def test_exact_legal_unchanged():
    assert thr.resolve_tcl_highway('Spadina Avenue') == 'spadina avenue'


def test_suffix_remap_single_base():
    assert thr.resolve_tcl_highway('Beaumont Street') == 'beaumont road'
    assert thr.resolve_tcl_highway('Moss Park Place') == 'moss park trail'
    assert thr.resolve_tcl_highway('Lady Sarah') == 'lady sarah crescent'


def test_multi_prefix_blocks_dvp():
    assert thr.resolve_tcl_highway('Don Valley Parkway') == 'don valley parkway'


def test_multi_base_no_remap():
    assert thr.resolve_tcl_highway('Braeside Avenue') == 'braeside avenue'


def test_strip_paren_before_suffix():
    assert thr.resolve_tcl_highway('Kenilworth Avenue (west branch)') == 'kenilworth avenue'


def test_strip_street_suffix():
    assert thr.strip_street_suffix('St. Clair Avenue West') == 'st clair'
    assert thr.strip_street_suffix('Gunns Road (west branch)') == 'gunns'


def test_strip_highway_leg_parenthetical():
    assert thr.strip_highway_leg_parenthetical('Joyce Parkway (south leg)') == 'Joyce Parkway'
    assert thr.strip_highway_leg_parenthetical('Kenilworth Avenue (west branch)') == (
        'Kenilworth Avenue'
    )
    assert thr.strip_highway_leg_parenthetical('Old Orchard Grove (NY)') == 'Old Orchard Grove'


def test_highway_leg_compass():
    assert thr.highway_leg_compass('Joyce Parkway (south leg)') == 'south'
    assert thr.highway_leg_compass('Joyce Parkway (north leg)') == 'north'
    assert thr.highway_leg_compass('Spadina Avenue') is None


def test_joyce_parkway_leg_resolves_to_tcl_legal():
    _install_mini_index()
    thr.build_index(
        legal_keys={tcl_highway_key('Joyce Parkway'), tcl_highway_key('Joyce Trimmer Park Trail')},
        base_to_legals={'Joyce': ['Joyce Parkway'], 'Joyce Trimmer Park': ['Joyce Trimmer Park Trail']},
    )
    assert thr.resolve_tcl_highway('Joyce Parkway (south leg)') == 'joyce parkway'


def test_strip_segment_parenthetical():
    assert thr.resolve_tcl_highway(
        'Antibes Drive (eastbound one-way segment)',
    ) == 'antibes drive'


def test_gated_typo_beaconsfied():
    assert thr.resolve_tcl_highway('Beaconsfied Avenue') == 'beaconsfield avenue'


def test_mc_spacing_mcfarland():
    assert thr.resolve_tcl_highway('McFarland Avenue') == 'mc farland avenue'


def test_epic_lane_base_remap():
    assert thr.resolve_tcl_highway('Epic Lane') == 'epic lane road'


def test_strip_descriptor_cul_de_sac():
    key = thr.normalize_highway_for_lookup('Broadmead Avenue cul-de-sac')
    assert 'cul-de-sac' not in key


def test_highway_lookup_ambiguous_multi_base():
    assert thr.highway_lookup_ambiguous('Braeside Avenue') is True
    assert thr.highway_lookup_ambiguous('Spadina Avenue') is False

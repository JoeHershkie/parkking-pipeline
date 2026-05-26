"""Tests for bylaw Highway → TCL legal name resolution."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import tcl_highway_resolve as thr  # noqa: E402
from tcl_highway_key import tcl_highway_key  # noqa: E402


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
    }
    thr.build_index(legal_keys=keys, base_to_legals=base_to_legals)


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

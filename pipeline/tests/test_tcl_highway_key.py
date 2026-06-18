"""Tests for TCL street-index highway key normalization."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from tcl_highway_key import tcl_highway_key  # noqa: E402


def test_plain_name_unchanged():
    assert tcl_highway_key('Spadina Avenue') == 'spadina avenue'


def test_strip_borough_suffix():
    assert tcl_highway_key('Victoria Street (TO)') == 'victoria street'
    assert tcl_highway_key('John Street (YK)') == 'john street'


def test_strip_st_period():
    assert tcl_highway_key('St. Clair Avenue West') == 'st clair avenue west'
    assert tcl_highway_key("St. John's Road") == "st john's road"


def test_borough_and_st_period():
    assert tcl_highway_key('St. Dennis Drive (NY)') == 'st dennis drive'


def test_empty():
    assert tcl_highway_key('') == ''
    assert tcl_highway_key('   ') == ''

"""Shared regex fragments and compiled patterns for Between text parsing."""

from __future__ import annotations

import re

_COMPASS = r'north|south|east|west|northeast|northwest|southeast|southwest'
_COMPASS_END = (
    r'north|south|east|west|northeast|northwest|southeast|southwest'
    r'|northerly|southerly|easterly|westerly'
)
_APPROX = r'(?:approximately )?'
_METRES = r'\d+(?:\.\d+)?'
_DIR = r'north|south|east|west'
_COMPOUND_DIR = (
    rf'(?:{_COMPASS})(?:\s+and\s+(?:{_COMPASS})|\s+(?:{_COMPASS}))?'
)
_OPT_PAREN_IN_ANCHOR = r'(?:\s*\([^)]+\))?'

_FURTHER_TAIL = (
    rf'(?:further {_COMPOUND_DIR}(?:\s+thereof)?'
    rf'|{_COMPOUND_DIR} thereof)'
)

STREET_END_RE = re.compile(rf'\b(?:{_COMPASS_END})\s+end\s+of\b', re.IGNORECASE)
PAREN_QUALIFIER_RE = re.compile(r'\([^)]*intersection[^)]*\)', re.IGNORECASE)
A_POINT_RE = re.compile(r'^a point\b', re.IGNORECASE)
POINT_METRES_FRAGMENT_RE = re.compile(r'^a point\s+.*\bmetres\b', re.IGNORECASE)
METRIC_OF_STREET_RE = re.compile(
    rf'^a\s+point\s+{_APPROX}(?P<distance>{_METRES})\s+metres\s+'
    rf'(?P<direction>{_COMPOUND_DIR})\s+of\s+'
    rf'(?P<street>.+)$',
    re.IGNORECASE,
)
METRIC_ONLY_RE = re.compile(
    rf'^a\s+point\s+{_APPROX}(?P<distance>{_METRES})\s+metres\s+'
    rf'(?P<direction>{_COMPOUND_DIR})\s*$',
    re.IGNORECASE,
)
A_POINT_OPPOSITE_LIMIT_RE = re.compile(
    r'^a\s+point\s+opposite\s+the\s+.+?\blimit\s+of\s+(?P<street>.+)$',
    re.IGNORECASE,
)
A_POINT_OPPOSITE_RE = re.compile(r'^a\s+point\s+opposite\s+(?P<street>.+)$', re.IGNORECASE)
THE_LIMIT_RE = re.compile(r'^the\s+.+?\blimit\s+of\s+(?P<street>.+)$', re.IGNORECASE)
SCHEDULE_IN_BETWEEN_RE = re.compile(r'\d{1,2}:\d{2}\s*[ap]\.m\.', re.IGNORECASE)
ADJACENT_TO_RE = re.compile(r'^Adjacent\s+to\b', re.IGNORECASE)
TERMINUS_ANCHOR_RE = re.compile(
    rf'^the\s+(?:{_COMPASS_END})\s+end\s+of\s+(?P<street>.+)$',
    re.IGNORECASE,
)

ANCHOR_FIELDS = frozenset({
    'start_intersection',
    'end_intersection',
    'offset_intersection',
})

# Re-export fragments for pattern compilation in parse_between / bylaw_text.
COMPASS = _COMPASS
COMPASS_END = _COMPASS_END
APPROX = _APPROX
METRES = _METRES
DIR = _DIR
COMPOUND_DIR = _COMPOUND_DIR
OPT_PAREN_IN_ANCHOR = _OPT_PAREN_IN_ANCHOR
FURTHER_TAIL = _FURTHER_TAIL

# Terminal street-type token plus optional trailing cardinal, so that
# 'Queen Street West King Street' splits after 'West', not after 'Street'.
STREET_HEAD = (
    r"[A-Za-z'’.-]+(?:\s+[A-Za-z'’.-]+)*?"  # street name words (lazy)
    r'(?:\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr'
    r'|crescent|cres|court|ct|place|pl|terrace|terr|trail|trl|circle|crcl'
    r'|parkway|pkwy|square|sq|gate|gt|lawn|lwn|gardens|gdns|lane|ln|way|grove))'
    r'(?:\s+(?:north|south|east|west))?'
)

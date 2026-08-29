"""Normalize bylaw Highway values to TCL LINEAR_NAME_FULL_LEGAL index keys."""

from __future__ import annotations

import re

# Municipal disambiguators in bylaw data: Victoria Street (TO), etc.
_BOROUGH_SUFFIX_RE = re.compile(r'\s*\([A-Za-z]{2}\)\s*$')
# TCL legal names use "st clair", not "st. clair"
_ST_PERIOD_RE = re.compile(r'\bst\.\s*', re.IGNORECASE)


def tcl_highway_key(highway: str) -> str:
    """Map a bylaw ``Highway`` to the lowercased TCL street-index key."""
    name = str(highway).strip().lower()
    if not name:
        return ''
    name = _BOROUGH_SUFFIX_RE.sub('', name).strip()
    name = _ST_PERIOD_RE.sub('st ', name)
    return re.sub(r'\s+', ' ', name).strip()

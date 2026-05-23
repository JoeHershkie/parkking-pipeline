"""Street name normalization for TCL INTERSECTION_DESC substring matching."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from paths import data_path

# Word-boundary suffix and direction replacements (order matters for multi-word types).
_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r'\bstreet\b', 'st'),
    (r'\broad\b', 'rd'),
    (r'\bavenue\b', 'ave'),
    (r'\bboulevard\b', 'blvd'),
    (r'\bdrive\b', 'dr'),
    (r'\bcrescent\b', 'cres'),
    (r'\bcourt\b', 'ct'),
    (r'\bplace\b', 'pl'),
    (r'\bsquare\b', 'sq'),
    (r'\bterrace\b', 'terr'),
    (r'\btrail\b', 'trl'),
    (r'\bcircle\b', 'crcl'),
    (r'\bparkway\b', 'pkwy'),
    (r'\bgate\b', 'gt'),
    (r'\blawn\b', 'lwn'),
    (r'\bgardens\b', 'gdns'),
    (r'\bwest\b', 'w'),
    (r'\beast\b', 'e'),
    (r'\bnorth\b', 'n'),
    (r'\bsouth\b', 's'),
)


def normalize_intersection_street(street_name: str) -> str:
    """Normalize a bylaw street name for TCL INTERSECTION_DESC lookup."""
    name = str(street_name).lower().strip()
    name = name.replace('.', '')
    for pattern, replacement in _REPLACEMENTS:
        name = re.sub(pattern, replacement, name)
    # Collapse whitespace
    return re.sub(r'\s+', ' ', name).strip()


@lru_cache(maxsize=1)
def _load_alias_map() -> dict[str, str]:
    """Map lowercased bylaw names to TCL search tokens (already abbreviated)."""
    path = data_path('street_aliases.csv')
    if not path.exists():
        return {}
    mapping: dict[str, str] = {}
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            bylaw = (row.get('bylaw_name') or '').strip().lower()
            token = (row.get('tcl_token') or '').strip().lower()
            if bylaw and token:
                mapping[bylaw] = token
    return mapping


def apply_street_alias(street_name: str) -> str:
    """Return TCL token if a curated alias exists, else normalized name."""
    key = str(street_name).strip().lower()
    alias = _load_alias_map().get(key)
    if alias:
        return alias
    return normalize_intersection_street(street_name)


def clear_alias_cache() -> None:
    _load_alias_map.cache_clear()

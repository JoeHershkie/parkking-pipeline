"""Resolve bylaw Highway strings to TCL LINEAR_NAME_FULL_LEGAL index keys."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from paths import data_path
from tcl_highway_key import tcl_highway_key

# Terminal street-type tokens (longest first).
_STREET_TYPES: tuple[str, ...] = tuple(
    sorted(
        {
            'street', 'st', 'road', 'rd', 'avenue', 'ave', 'boulevard', 'blvd',
            'drive', 'dr', 'crescent', 'cres', 'court', 'ct', 'place', 'pl',
            'circle', 'crcl', 'terrace', 'terr', 'ter', 'parkway', 'pkwy',
            'square', 'sq', 'trail', 'trl', 'lane', 'ln', 'way', 'mews',
            'path', 'gate', 'gt', 'gardens', 'gdns', 'lawn', 'lwn', 'alley',
            'bridge', 'bdge', 'ramp', 'circuit', 'crct',
        },
        key=len,
        reverse=True,
    )
)
_TYPE_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in _STREET_TYPES) + r')\b\s*$',
    re.IGNORECASE,
)
_CARD_TYPE_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in _STREET_TYPES) + r')'
    r'\s+(north|south|east|west)\s*$',
    re.IGNORECASE,
)
# Bylaw highway-only: leg/branch/roadway/section parentheticals (not borough "(TO)").
_HIGHWAY_LEG_PAREN_RE = re.compile(
    r'\s*\([^)]*\b(?:leg|legs|branch|branches|roadway|roadways|section|sections)\b[^)]*\)\s*$',
    re.IGNORECASE,
)

_legal_keys: frozenset[str] = frozenset()
_base_to_legals: dict[str, list[str]] = {}
_index_ready = False


def strip_highway_leg_parenthetical(highway: str) -> str:
    """Remove trailing leg/branch/roadway/section qualifiers from a bylaw highway name."""
    s = str(highway).strip()
    prev = None
    while s != prev:
        prev = s
        s = _HIGHWAY_LEG_PAREN_RE.sub('', s).strip()
    return s


def highway_leg_compass(highway: str) -> str | None:
    """
    Compass hint from a highway leg parenthetical, e.g. ``(south leg)`` → ``south``.
    """
    raw = str(highway).strip()
    m = re.search(r'\(([^)]+)\)\s*$', raw, re.IGNORECASE)
    if not m or not re.search(
        r'\b(?:leg|legs|branch|branches|roadway|roadways|section|sections)\b',
        m.group(1),
        re.IGNORECASE,
    ):
        return None
    text = m.group(1).lower()
    for direction in ('north', 'south', 'east', 'west'):
        if re.search(rf'\b{direction}\b', text):
            return direction
    return None


def strip_street_suffix(name: str) -> str:
    """Bylaw/TCL name with terminal street-type tokens removed (leg parens stripped first)."""
    s = tcl_highway_key(strip_highway_leg_parenthetical(name))
    s = re.sub(r'\s+', ' ', s)
    prev = None
    while s != prev:
        prev = s
        s = _CARD_TYPE_RE.sub('', s).strip()
        s = _TYPE_RE.sub('', s).strip()
    return re.sub(r'\s+', ' ', s).strip()


def _prefix_matches(root: str) -> list[str]:
    if not root:
        return []
    prefix = root + ' '
    return sorted(
        key for key in _legal_keys
        if key == root or key.startswith(prefix)
    )


def build_index(
    *,
    legal_keys: set[str] | frozenset[str],
    base_to_legals: dict[str, list[str]],
) -> None:
    """Install lookup tables (call once after TCL streets are loaded)."""
    global _legal_keys, _base_to_legals, _index_ready
    _legal_keys = frozenset(legal_keys)
    _base_to_legals = {
        tcl_highway_key(base): [
            legal for legal in legals
            if tcl_highway_key(legal) in _legal_keys
        ]
        for base, legals in base_to_legals.items()
    }
    _index_ready = True


def legal_key_count() -> int:
    return len(_legal_keys)


def build_index_from_csv(
    csv_path: Path | None = None,
    *,
    legal_keys: set[str] | frozenset[str],
) -> None:
    """Load ``linear_name_base`` groupings from ``tcl_street_names.csv``."""
    path = csv_path or data_path('tcl_street_names.csv')
    base_to_legals: dict[str, list[str]] = {}
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            legal = (row.get('linear_name_full_legal') or '').strip()
            if not legal:
                continue
            for base in (row.get('linear_name_base') or '').split(' | '):
                base = base.strip()
                if base:
                    base_to_legals.setdefault(base, []).append(legal)
    build_index(legal_keys=legal_keys, base_to_legals=base_to_legals)


def _ensure_index() -> None:
    if _index_ready:
        return
    path = data_path('tcl_street_names.csv')
    if not path.exists():
        return
    legal_keys: set[str] = set()
    base_to_legals: dict[str, list[str]] = {}
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            legal = (row.get('linear_name_full_legal') or '').strip()
            if not legal:
                continue
            legal_keys.add(tcl_highway_key(legal))
            for base in (row.get('linear_name_base') or '').split(' | '):
                base = base.strip()
                if base:
                    base_to_legals.setdefault(base, []).append(legal)
    if legal_keys:
        build_index(legal_keys=legal_keys, base_to_legals=base_to_legals)


def resolve_tcl_highway(highway: str) -> str:
    """
    Return the ``street_index`` / ``street_graphs`` lookup key for a bylaw highway.

    Exact TCL legal match is returned unchanged. Otherwise, when the stripped
    name root is not shared by multiple TCL legal prefixes and exactly one
    ``linear_name_base`` maps into *legal_keys*, return that legal name's key.
    """
    _ensure_index()
    normalized = strip_highway_leg_parenthetical(highway)
    key = tcl_highway_key(normalized)
    if not key or key in _legal_keys:
        return key

    root = strip_street_suffix(normalized)
    if not root:
        return key

    if len(_prefix_matches(root)) > 1:
        return key

    legals = sorted({tcl_highway_key(legal) for legal in _base_to_legals.get(root, [])})
    if len(legals) == 1:
        return legals[0]
    return key


def tcl_lookup_key(highway: str) -> str:
    """Alias for :func:`resolve_tcl_highway` (geometry / graph lookups)."""
    return resolve_tcl_highway(highway)

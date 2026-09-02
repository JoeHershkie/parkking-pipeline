"""Street name normalization for TCL INTERSECTION_DESC substring matching."""

from __future__ import annotations

import csv
import re
from functools import lru_cache

from .paths import data_path

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
    (r'\bgrove\b', 'grv'),
    (r'\bheights\b', 'hts'),
    (r'\bcrest\b', 'crct'),
    (r'\bwest\b', 'w'),
    (r'\beast\b', 'e'),
    (r'\bnorth\b', 'n'),
    (r'\bsouth\b', 's'),
)

_SKIP_GT_LWN_GDNS = frozenset({r'\bgate\b', r'\blawn\b', r'\bgardens\b', r'\bparkway\b'})
_SKIP_DIRECTIONS = frozenset({
    r'\bwest\b', r'\beast\b', r'\bnorth\b', r'\bsouth\b',
})

_MAX_SEARCH_TOKENS = 12

_LEG_OF_RE = re.compile(
    r'^\s*the\s+(?:(?:north|south|east|west)(?:ern|erly)?\s+)?'
    r'(?:north/south|east/west|north|south|east|west)\s+leg\s+of\s+(.+)$',
    re.I,
)
_EMBEDDED_LEG_RE = re.compile(
    r'(?:north/south|east/west|north|south|east|west)\s+leg\s+of\s+(.+?)(?:\s*$|\s+and\b)',
    re.I,
)
_FROM_PREFIX_RE = re.compile(r'^\s*from\s+', re.I)
_CURB_LINE_PREFIX_RE = re.compile(
    r'^\s*the\s+(?:east|west|north|south)\s+curb\s+line\s+of\s+',
    re.I,
)
_BRANCH_SIDE_LEG_PREFIX_RE = re.compile(
    r'^\s*the\s+(?:east|west|north|south)(?:ern|erly)?\s+'
    r'(?:branch|side|leg)\s+of\s+',
    re.I,
)
_TERMINUS_STREET_PREFIX_RE = re.compile(
    r'^\s*the\s+(?:easterly|westerly|northerly|southerly)\s+terminus\s+street\b\s*',
    re.I,
)
_LEADING_THE_RE = re.compile(r'^\s*the\s+(.+)$', re.I)
_STREET_TYPE_TOKENS = frozenset({
    'street', 'st', 'road', 'rd', 'avenue', 'ave', 'boulevard', 'blvd',
    'drive', 'dr', 'crescent', 'cres', 'court', 'ct', 'place', 'pl',
    'square', 'sq', 'terrace', 'terr', 'trail', 'trl', 'circle', 'crcl',
    'parkway', 'pkwy', 'gate', 'gt', 'lawn', 'lwn', 'gardens', 'gdns',
    'lane', 'ln', 'way', 'mews', 'path', 'close', 'heights', 'height',
    'hill', 'view', 'walk', 'line', 'grove', 'grv', 'garden', 'crest',
    'crct',
})
_ST_CLAIR_AVE_RE = re.compile(r'\bst clair ave ([ew])\b')
_ST_CLAIR_SHORT_RE = re.compile(r'\bst clair ([ew])\b(?! ave\b)')


def _apply_replacements(name: str, *, skip_patterns: frozenset[str] = frozenset()) -> str:
    out = name
    for pattern, replacement in _REPLACEMENTS:
        if pattern in skip_patterns:
            continue
        out = re.sub(pattern, replacement, out)
    return re.sub(r'\s+', ' ', out).strip()


def normalize_intersection_street(street_name: str) -> str:
    """Normalize a bylaw street name for TCL INTERSECTION_DESC lookup."""
    name = str(street_name).lower().strip()
    name = name.replace('.', '')
    return _apply_replacements(name)


def _normalize_with_tcl_spelling_variants(street_name: str) -> str:
    """Same as normalize but keep gate/lawn/gardens spelled out for TCL descs."""
    name = str(street_name).lower().strip()
    name = name.replace('.', '')
    return _apply_replacements(name, skip_patterns=_SKIP_GT_LWN_GDNS)


def _normalize_with_spelled_directions(street_name: str) -> str:
    """Normalize but keep north/south/east/west spelled out (TCL cross-street style)."""
    name = str(street_name).lower().strip()
    name = name.replace('.', '')
    return _apply_replacements(
        name,
        skip_patterns=_SKIP_GT_LWN_GDNS | _SKIP_DIRECTIONS,
    )


_CARDINAL_TAILS = frozenset({'w', 'e', 'n', 's', 'west', 'east', 'north', 'south'})


def _looks_like_street_tail(text: str) -> bool:
    """True when the text ends in a recognizable street-type token."""
    words = re.sub(r'\s+', ' ', text.strip().lower()).split()
    if not words:
        return False
    if words[-1] in _STREET_TYPE_TOKENS:
        return True
    # 'Queen Street West' / 'Bloor Street East' style trailing cardinals.
    return len(words) >= 2 and words[-1] in _CARDINAL_TAILS and words[-2] in _STREET_TYPE_TOKENS


_DESCRIPTIVE_TAIL_RE = re.compile(
    r'^(?:north|south|east|west|northern|southern|eastern|western|'
    r'north/south|east/west)\b.*\bleg\s+of\b',
    re.I,
)


def strip_lookup_prefixes(street_name: str) -> str:
    """Remove bylaw phrasing that is not part of the TCL street token."""
    text = str(street_name).strip()
    text = _FROM_PREFIX_RE.sub('', text)
    text = _CURB_LINE_PREFIX_RE.sub('', text)
    text = _BRANCH_SIDE_LEG_PREFIX_RE.sub('', text)
    text = _TERMINUS_STREET_PREFIX_RE.sub('', text).strip()
    m = _LEADING_THE_RE.match(text)
    if m:
        rest = m.group(1).strip()
        if _looks_like_street_tail(rest) and not _DESCRIPTIVE_TAIL_RE.match(rest):
            text = rest
    return text.strip()


def _apostrophe_variant(token: str) -> str | None:
    if "'" not in token:
        return None
    variant = token.replace("'", '')
    variant = re.sub(r'\s+', ' ', variant).strip()
    return variant if variant and variant != token else None


def _st_clair_variants(token: str) -> tuple[str, ...]:
    """TCL INTERSECTION_DESC often drops ``ave`` on St. Clair (e/w)."""
    out: list[str] = []
    m = _ST_CLAIR_AVE_RE.search(token)
    if m:
        short = _ST_CLAIR_AVE_RE.sub(f'st clair {m.group(1)}', token)
        if short != token:
            out.append(short)
    m2 = _ST_CLAIR_SHORT_RE.search(token)
    if m2:
        long = _ST_CLAIR_SHORT_RE.sub(f'st clair ave {m2.group(1)}', token)
        if long != token:
            out.append(long)
    return tuple(out)


def _court_crt_variant(token: str) -> str | None:
    if token.endswith(' ct'):
        return token[:-3] + ' crt'
    return None


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


@lru_cache(maxsize=65536)
def tcl_search_tokens(street_name: str) -> tuple[str, ...]:
    """
    Ordered lookup tokens for substring matching in INTERSECTION_DESC.
    Primary alias/normalized token first, then TCL street-index suffix/base remap
    (same logic as highway graph resolution), then spelling variants.
    """
    if not street_name or not str(street_name).strip():
        return ()

    raw = strip_lookup_prefixes(str(street_name).strip())
    seen: set[str] = set()
    out: list[str] = []

    def add(token: str) -> None:
        t = token.strip().lower()
        if t and t not in seen and len(out) < _MAX_SEARCH_TOKENS:
            seen.add(t)
            out.append(t)

    add(apply_street_alias(raw))
    plain = normalize_intersection_street(raw)

    # Suffix/base remap from tcl_street_names (e.g. bylaw Street → TCL Road).
    from .tcl_highway_resolve import intersection_resolve_tokens

    for legal_token in intersection_resolve_tokens(raw):
        add(legal_token)
        normalized_legal = normalize_intersection_street(legal_token)
        if normalized_legal != plain:
            add(normalized_legal)

    spelled_dirs = _normalize_with_spelled_directions(raw)
    add(spelled_dirs)

    spelled = _normalize_with_tcl_spelling_variants(raw)
    add(spelled)

    add(plain)

    bases = list(dict.fromkeys(out))
    for base in bases:
        if not base:
            continue
        apost = _apostrophe_variant(base)
        if apost:
            add(apost)
        crt = _court_crt_variant(base)
        if crt:
            add(crt)
        for variant in _st_clair_variants(base):
            add(variant)

    return tuple(out)


def expand_cross_lookup_names(cross: str) -> tuple[str, ...]:
    """
    Expand compound cross-street phrases into lookup name candidates.
    Original first, then slash segments and leg-of-street stems.
    """
    if not cross or not str(cross).strip():
        return ()

    text = strip_lookup_prefixes(str(cross).strip())
    seen: set[str] = set()
    out: list[str] = []

    def add(name: str) -> None:
        n = strip_lookup_prefixes(name.strip())
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    add(text)

    leg = _LEG_OF_RE.match(text)
    if leg:
        add(leg.group(1).strip())

    emb = _EMBEDDED_LEG_RE.search(text)
    if emb:
        add(emb.group(1).strip())

    if '/' in text and 'leg of' not in text.lower():
        for part in text.split('/'):
            add(part.strip())

    return tuple(out)


def clear_alias_cache() -> None:
    _load_alias_map.cache_clear()

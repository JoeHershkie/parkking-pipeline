"""Resolve bylaw Highway strings to TCL LINEAR_NAME_FULL_LEGAL index keys."""

from __future__ import annotations

import csv
import functools
import re
from pathlib import Path

from .paths import data_path
from .tcl_highway_key import tcl_highway_key

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
_HIGHWAY_LEG_PAREN_RE = re.compile(
    r'\s*\([^)]*\b(?:leg|legs|branch|branches|roadway|roadways|section|sections)\b[^)]*\)\s*$',
    re.IGNORECASE,
)
_BOROUGH_PAREN_RE = re.compile(r'\s*\((?:TO|NY|ON)\)\s*$', re.IGNORECASE)
# Non-leg trailing parentheticals (end qualifier, segment, intersection hint, service road).
_OTHER_PAREN_RE = re.compile(r'\s*\([^)]+\)\s*$', re.IGNORECASE)
_DESCRIPTOR_RE = re.compile(
    r'\s+(?:cul-de-sac|bus\s+loop|traffic\s+circle)\s*$',
    re.IGNORECASE,
)
_CARDINAL_WORDS = frozenset({'north', 'south', 'east', 'west'})
_MC_PREFIX_RE = re.compile(r'^(mc|mac)([a-z])', re.IGNORECASE)
_APOSTROPHE_RE = re.compile(r"[''`]")

_legal_keys: frozenset[str] = frozenset()
_base_to_legals: dict[str, list[str]] = {}
_variant_to_legal: dict[str, str] = {}
_highway_aliases: dict[str, str] = {}
_index_ready = False


def strip_highway_leg_parenthetical(highway: str) -> str:
    """Remove trailing leg/branch/roadway/section qualifiers from a bylaw highway name."""
    s = str(highway).strip()
    prev = None
    while s != prev:
        prev = s
        s = _BOROUGH_PAREN_RE.sub('', s).strip()
        s = _HIGHWAY_LEG_PAREN_RE.sub('', s).strip()
    return s


def strip_highway_qualifiers(highway: str) -> str:
    """
    Strip leg/branch parens, borough suffixes, and other trailing parentheticals.

    Other parens (service road, one-way segment, north end, intersection hints) are
    removed iteratively; leg/branch parens use :func:`strip_highway_leg_parenthetical`.
    """
    s = strip_highway_leg_parenthetical(highway)
    prev = None
    while s != prev:
        prev = s
        s = _OTHER_PAREN_RE.sub('', s).strip()
    return s


def strip_trailing_descriptors(highway: str) -> str:
    """Remove trailing cul-de-sac / bus loop / traffic circle from highway text."""
    s = str(highway).strip()
    prev = None
    while s != prev:
        prev = s
        s = _DESCRIPTOR_RE.sub('', s).strip()
    return s


def normalize_highway_for_lookup(highway: str) -> str:
    """Full bylaw→lookup normalization before keying (strips, no fuzzy)."""
    s = strip_trailing_descriptors(strip_highway_qualifiers(highway))
    return tcl_highway_key(s)


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


@functools.lru_cache(maxsize=32768)
def _name_tokens(name: str) -> tuple[tuple[str, ...], str | None, str | None]:
    """Split into base word tokens, terminal type token, terminal direction."""
    raw = tcl_highway_key(strip_highway_qualifiers(name))
    if not raw:
        return (), None, None
    parts = raw.split()
    direction = None
    if parts and parts[-1] in _CARDINAL_WORDS:
        direction = parts.pop()
    street_type = None
    if parts and parts[-1] in _STREET_TYPES:
        street_type = parts.pop()
    return tuple(parts), street_type, direction


def _edit_distance_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long = (a, b) if la < lb else (b, a)
    i = j = edits = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            j += 1
    return edits + (len(long) - j) <= 1


def _prefix_matches(root: str) -> list[str]:
    if not root:
        return []
    prefix = root + ' '
    return sorted(
        key for key in _legal_keys
        if key == root or key.startswith(prefix)
    )


def _load_highway_aliases() -> dict[str, str]:
    path = data_path('highway_aliases.csv')
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            bylaw = (row.get('bylaw_highway') or '').strip()
            legal = (row.get('tcl_linear_name_full_legal') or '').strip()
            if bylaw and legal:
                out[tcl_highway_key(bylaw)] = tcl_highway_key(legal)
    return out


def _spacing_variant_keys(root: str) -> list[str]:
    """McConnell ↔ Mc Connell style keys present in TCL."""
    if not root or ' ' in root:
        return []
    candidates: list[str] = []
    m = _MC_PREFIX_RE.match(root)
    if m:
        spaced = f'{m.group(1).lower()} {m.group(2)}{root[m.end():]}'
        candidates.append(spaced)
    else:
        parts = root.split()
        if len(parts) >= 2 and parts[0] in ('mc', 'mac') and len(parts[1]) > 1:
            collapsed = parts[0] + parts[1] + (' ' + ' '.join(parts[2:]) if len(parts) > 2 else '')
            candidates.append(collapsed.strip())
    return [c for c in candidates if c in _legal_keys]


def _hyphen_spacing_variants(name: str) -> list[str]:
    """de-savery ↔ de savery against known legals."""
    key = tcl_highway_key(name)
    hits: list[str] = []
    if '-' in key:
        alt = key.replace('-', ' ')
        if alt in _legal_keys:
            hits.append(alt)
    if ' ' in key:
        parts = key.split()
        for i in range(1, len(parts)):
            hyphened = '-'.join(parts[:i]) + ' ' + ' '.join(parts[i:])
            if hyphened in _legal_keys:
                hits.append(hyphened)
    return hits


def _apostrophe_variants(name: str) -> list[str]:
    key = tcl_highway_key(name)
    if "'" in key or "'" in name:
        return []
    deapo = _APOSTROPHE_RE.sub('', key)
    if deapo in _legal_keys and deapo != key:
        return [deapo]
    return []


def _gated_ed1_legals(highway: str) -> list[str]:
    """TCL legal keys within edit-distance-1 on base tokens with type/direction gates."""
    tokens, stype, direction = _name_tokens(highway)
    if not tokens:
        return []
    core = ' '.join(tokens)
    if len(core) < 4:
        return []
    hits: list[str] = []
    for legal in _legal_keys:
        lt, lst, ldir = _name_tokens(legal)
        if not lt:
            continue
        lcore = ' '.join(lt)
        if stype and lst and stype != lst:
            continue
        if direction and ldir and direction != ldir:
            continue
        if direction and not ldir:
            continue
        if stype and not lst:
            continue
        if _edit_distance_le1(core, lcore):
            hits.append(legal)
    return sorted(set(hits))


def _expand_type_suffix_remap(root: str, highway: str) -> str | None:
    """
    When bylaw ends with ``lane`` and exactly one base maps to a single legal
    (e.g. Epic Lane → Epic Lane Road).
    """
    hl = tcl_highway_key(highway)
    if not hl.endswith(' lane') and not hl.endswith(' ln'):
        return None
    legals = sorted({tcl_highway_key(legal) for legal in _base_to_legals.get(root, [])})
    if len(legals) == 1:
        return legals[0]
    return None


def build_index(
    *,
    legal_keys: set[str] | frozenset[str],
    base_to_legals: dict[str, list[str]],
    variant_to_legal: dict[str, str] | None = None,
    highway_aliases: dict[str, str] | None = None,
) -> None:
    """Install lookup tables (call once after TCL streets are loaded)."""
    global _legal_keys, _base_to_legals, _variant_to_legal, _highway_aliases, _index_ready
    _legal_keys = frozenset(legal_keys)
    _base_to_legals = {
        tcl_highway_key(base): [
            legal for legal in legals
            if tcl_highway_key(legal) in _legal_keys
        ]
        for base, legals in base_to_legals.items()
    }
    _variant_to_legal = {
        tcl_highway_key(k): tcl_highway_key(v)
        for k, v in (variant_to_legal or {}).items()
        if tcl_highway_key(v) in _legal_keys
    }
    _highway_aliases = dict(highway_aliases or {})
    _index_ready = True
    resolve_tcl_highway.cache_clear()
    _resolve_tcl_highway_with_context_cached.cache_clear()
    intersection_resolve_tokens.cache_clear()
    try:
        from .intersection_normalize import tcl_search_tokens

        tcl_search_tokens.cache_clear()
    except ImportError:
        pass
    try:
        from .intersection_pair_resolve import clear_pair_root_cache

        clear_pair_root_cache()
    except ImportError:
        pass
    try:
        from .lane_highway_resolve import reset_lane_resolve_caches

        reset_lane_resolve_caches()
    except ImportError:
        pass


def legal_key_count() -> int:
    return len(_legal_keys)


def build_index_from_csv(
    csv_path: Path | None = None,
    *,
    legal_keys: set[str] | frozenset[str],
) -> None:
    """Load ``linear_name_base`` groupings and name variants from ``tcl_street_names.csv``."""
    path = csv_path or data_path('tcl_street_names.csv')
    base_to_legals: dict[str, list[str]] = {}
    variant_to_legal: dict[str, str] = {}
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            legal = (row.get('linear_name_full_legal') or '').strip()
            if not legal:
                continue
            for base in (row.get('linear_name_base') or '').split(' | '):
                base = base.strip()
                if base:
                    base_to_legals.setdefault(base, []).append(legal)
            for full in (row.get('linear_name_full') or '').split(' | '):
                full = full.strip()
                if full:
                    variant_to_legal[full] = legal
            variant_to_legal[legal] = legal
    aliases = _load_highway_aliases()
    build_index(
        legal_keys=legal_keys,
        base_to_legals=base_to_legals,
        variant_to_legal=variant_to_legal,
        highway_aliases=aliases,
    )


def _ensure_index() -> None:
    if _index_ready:
        return
    path = data_path('tcl_street_names.csv')
    if not path.exists():
        return
    legal_keys: set[str] = set()
    base_to_legals: dict[str, list[str]] = {}
    variant_to_legal: dict[str, str] = {}
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
            for full in (row.get('linear_name_full') or '').split(' | '):
                full = full.strip()
                if full:
                    variant_to_legal[full] = legal
            variant_to_legal[legal] = legal
    if legal_keys:
        build_index(
            legal_keys=legal_keys,
            base_to_legals=base_to_legals,
            variant_to_legal=variant_to_legal,
            highway_aliases=_load_highway_aliases(),
        )


@functools.lru_cache(maxsize=65536)
def resolve_tcl_highway(highway: str) -> str:
    """
    Return the ``street_index`` / ``street_graphs`` lookup key for a bylaw highway.

    Resolution order: curated alias → normalized exact/variant → base remap (unique) →
    spacing/hyphen/apostrophe → gated edit-distance-1 (unique).
    """
    _ensure_index()
    raw_key = tcl_highway_key(str(highway).strip())
    if raw_key in _highway_aliases:
        return _highway_aliases[raw_key]

    normalized = normalize_highway_for_lookup(highway)
    key = tcl_highway_key(normalized)
    if not key:
        return key
    if key in _legal_keys:
        return key

    if key in _variant_to_legal:
        return _variant_to_legal[key]

    root = strip_street_suffix(normalized)
    if not root:
        return key

    prefix_hits = _prefix_matches(root)
    if len(prefix_hits) <= 1:
        legals = sorted({tcl_highway_key(legal) for legal in _base_to_legals.get(root, [])})
        if len(legals) == 1:
            return legals[0]
        expanded = _expand_type_suffix_remap(root, normalized)
        if expanded:
            return expanded
    elif len(prefix_hits) > 1:
        return key

    for variant_fn in (_spacing_variant_keys,):
        for root_try in (root, strip_street_suffix(highway)):
            for hit in variant_fn(root_try):
                if hit in _legal_keys:
                    return hit

    for hit in _hyphen_spacing_variants(normalized):
        if hit in _legal_keys:
            return hit

    for hit in _apostrophe_variants(normalized):
        if hit in _legal_keys:
            return hit

    ed1 = _gated_ed1_legals(normalized)
    if len(ed1) == 1:
        return ed1[0]

    return key


def _freeze_context_parsed(parsed: dict | None) -> tuple[tuple[str, str], ...]:
    if not parsed:
        return ()
    keys = (
        'start_intersection', 'end_intersection', 'offset_intersection', 'terminus_street',
    )
    return tuple(
        (k, str(parsed[k]).strip())
        for k in keys
        if k in parsed and parsed[k] is not None and str(parsed[k]).strip()
    )


def _parsed_cross_streets(
    parsed: dict | None,
    *,
    for_highway: str | None = None,
) -> tuple[str, ...]:
    if not parsed:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for field in (
        'start_intersection', 'end_intersection', 'offset_intersection',
        'terminus_street',
    ):
        val = parsed.get(field)
        if val and str(val).strip():
            text = str(val).strip()
            if text not in seen:
                seen.add(text)
                out.append(text)
    if not for_highway:
        return tuple(out)
    hw_root = strip_street_suffix(normalize_highway_for_lookup(for_highway))
    if not hw_root:
        return tuple(out)
    return tuple(
        cross for cross in out
        if strip_street_suffix(normalize_highway_for_lookup(cross)) != hw_root
    )


def highway_lookup_ambiguous(highway: str) -> bool:
    """True when multiple TCL legals share this highway's ``linear_name_base``."""
    _ensure_index()
    return len(base_remap_candidates(highway)) > 1


def _highway_disambiguation_candidates(highway: str, key: str) -> list[str]:
    """Legal keys to try when disambiguating *highway* with parsed crosses."""
    _ensure_index()
    root = strip_street_suffix(normalize_highway_for_lookup(highway))
    candidates = sorted({
        tcl_highway_key(cand)
        for cand in set(_prefix_matches(root)) | set(base_remap_candidates(highway))
        if tcl_highway_key(cand) in _legal_keys
    })
    if key and tcl_highway_key(key) in _legal_keys:
        resolved_key = tcl_highway_key(key)
        if resolved_key not in candidates:
            candidates.append(resolved_key)
    return candidates


def _resolved_partner_keys(crosses: tuple[str, ...]) -> tuple[str, ...]:
    partners: list[str] = []
    seen: set[str] = set()
    for cross in crosses:
        partner = resolve_tcl_highway(cross)
        if partner and partner in _legal_keys and partner not in seen:
            seen.add(partner)
            partners.append(partner)
    return tuple(partners)


def _unique_remap_for_partner(partner_key: str, street: str) -> str | None:
    """Single TCL legal for *street* that intersects *partner_key*, if unique."""
    try:
        from . import intersection_index as ix
        from .intersection_pair_resolve import _legal_variants_for_street
    except ImportError:
        return None

    if ix.resolve_pair_ids_tokens(partner_key, street):
        resolved = resolve_tcl_highway(street)
        return resolved if resolved in _legal_keys else None

    hits = [
        cand
        for cand in _legal_variants_for_street(street)
        if ix.resolve_pair_ids_tokens(partner_key, cand)
    ]
    if len(hits) == 1:
        return hits[0]
    return None


def _disambiguate_highway_with_crosses(
    highway: str,
    key: str,
    crosses: tuple[str, ...],
) -> str:
    candidates = _highway_disambiguation_candidates(highway, key)
    if len(candidates) <= 1:
        return candidates[0] if candidates else key

    try:
        from . import intersection_index as ix
        from .intersection_pair_resolve import resolve_pair_via_roots
    except ImportError:
        return key

    hw_root = strip_street_suffix(normalize_highway_for_lookup(highway))
    other_crosses = tuple(
        cross for cross in crosses
        if strip_street_suffix(normalize_highway_for_lookup(cross)) != hw_root
    )
    partner_keys = tuple(
        partner for partner in _resolved_partner_keys(crosses)
        if strip_street_suffix(normalize_highway_for_lookup(partner)) != hw_root
    )

    def add_match(bucket: list[str], cand: str) -> None:
        if cand in candidates and cand not in bucket:
            bucket.append(cand)

    strong: list[str] = []
    for cand in candidates:
        for partner in partner_keys:
            if ix.resolve_pair_ids_tokens(partner, cand):
                add_match(strong, cand)
                break
        else:
            for cross in other_crosses:
                if ix.resolve_pair_ids_tokens(cand, cross):
                    add_match(strong, cand)
                    break

    if len(strong) == 1:
        return strong[0]

    weak: list[str] = []
    for cand in candidates:
        for partner in partner_keys:
            remap = _unique_remap_for_partner(partner, highway)
            if remap == cand:
                add_match(weak, cand)
                break
            for cross in other_crosses:
                remap = _unique_remap_for_partner(partner, cross)
                if remap == cand:
                    add_match(weak, cand)
                    break
            else:
                continue
            break
        else:
            for cross in other_crosses:
                root_match = resolve_pair_via_roots(highway, cross)
                if root_match is not None and root_match.street_a_token == cand:
                    add_match(weak, cand)
                    break

    if len(strong) > 1:
        unique_strong = sorted(set(strong))
        if len(unique_strong) == 1:
            return unique_strong[0]
    if len(weak) == 1:
        return weak[0]
    return key


@functools.lru_cache(maxsize=65536)
def _resolve_tcl_highway_with_context_cached(
    highway: str,
    parsed_frozen: tuple[tuple[str, str], ...],
) -> str:
    parsed = dict(parsed_frozen) if parsed_frozen else None
    key = resolve_tcl_highway(highway)
    crosses = _parsed_cross_streets(parsed, for_highway=highway)

    if not crosses:
        return key

    _ensure_index()
    if not highway_lookup_ambiguous(highway):
        return key

    return _disambiguate_highway_with_crosses(highway, key, crosses)


def resolve_tcl_highway_with_context(
    highway: str,
    parsed: dict | None = None,
) -> str:
    """Resolve highway key; when ambiguous prefix, disambiguate using parsed cross streets."""
    return _resolve_tcl_highway_with_context_cached(
        str(highway).strip(),
        _freeze_context_parsed(parsed),
    )


@functools.lru_cache(maxsize=65536)
def intersection_resolve_tokens(street_name: str) -> tuple[str, ...]:
    """
    Extra INTERSECTION_DESC search tokens from the TCL street index.

    Applies the same base/suffix remap as :func:`resolve_tcl_highway` (unique
    ``linear_name_base`` match and resolved legal key). Used for cross streets
    parsed from Between, not only the Highway column.
    """
    _ensure_index()
    raw = str(street_name).strip()
    if not raw:
        return ()

    seen: set[str] = set()
    out: list[str] = []

    def add(token: str) -> None:
        t = token.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    norm = normalize_highway_for_lookup(raw)
    root = strip_street_suffix(norm)
    if root:
        legals = sorted({
            tcl_highway_key(legal)
            for legal in _base_to_legals.get(root, [])
            if tcl_highway_key(legal) in _legal_keys
        })
        if len(legals) == 1:
            add(legals[0])
            return tuple(out)

    resolved = resolve_tcl_highway(raw)
    if resolved:
        add(resolved)

    return tuple(out)


def tcl_lookup_key(highway: str) -> str:
    """Alias for :func:`resolve_tcl_highway` (geometry / graph lookups)."""
    return resolve_tcl_highway(highway)


def prefix_match_count(highway: str) -> int:
    """Count of TCL legal keys sharing the stripped name root (for analysis)."""
    _ensure_index()
    root = strip_street_suffix(normalize_highway_for_lookup(highway))
    return len(_prefix_matches(root))


def base_remap_candidates(highway: str) -> list[str]:
    """Legal keys reachable via ``linear_name_base`` for this highway root."""
    _ensure_index()
    root = strip_street_suffix(normalize_highway_for_lookup(highway))
    return sorted({
        tcl_highway_key(legal)
        for legal in _base_to_legals.get(root, [])
        if tcl_highway_key(legal) in _legal_keys
    })


def gated_near_match_legals(highway: str) -> list[str]:
    """Gated edit-distance-1 candidates (analysis / alias suggestions)."""
    _ensure_index()
    return _gated_ed1_legals(normalize_highway_for_lookup(highway))

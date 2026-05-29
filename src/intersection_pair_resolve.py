"""Disambiguate street suffixes via unique root×root intersection matches."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from intersection_normalize import normalize_intersection_street
from tcl_highway_key import tcl_highway_key
from tcl_highway_resolve import (
    base_remap_candidates,
    normalize_highway_for_lookup,
    resolve_tcl_highway,
    strip_street_suffix,
)


@dataclass(frozen=True)
class PairRootMatch:
    intersection_id: int
    street_a_token: str
    street_b_token: str
    intersection_desc: str


def street_name_root(name: str) -> str:
    """Stripped name root for substring matching in INTERSECTION_DESC."""
    return strip_street_suffix(normalize_highway_for_lookup(name))


def parse_intersection_desc_legs(desc: str) -> tuple[str, str] | None:
    """Split a two-arm ``INTERSECTION_DESC`` (``A / B``)."""
    parts = [p.strip() for p in str(desc).split('/')]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def parse_intersection_desc_legs_all(desc: str) -> tuple[str, ...]:
    """All arms of an ``INTERSECTION_DESC`` (``A / B`` or ``A / B / C``)."""
    return tuple(p.strip() for p in str(desc).split('/') if p.strip())


def _root_matches_input(input_street: str, leg: str) -> bool:
    """True when *leg* is the same street family as *input_street* (e.g. bond / bond park)."""
    input_root = street_name_root(input_street)
    leg_root = street_name_root(leg)
    if not input_root or not leg_root:
        return False
    if leg_root == input_root:
        return True
    return leg_root.startswith(input_root + ' ')


def _leg_token_for_input(leg: str, input_street: str) -> str | None:
    if not _root_matches_input(input_street, leg):
        return None
    resolved = resolve_tcl_highway(leg)
    if resolved:
        return resolved
    return normalize_intersection_street(leg)


def _legal_variants_for_street(street: str) -> tuple[str, ...]:
    """
    TCL ``LINEAR_NAME_FULL_LEGAL`` keys that may be the same street family as *street*.

    Uses prefix-on-root (e.g. bond → bond avenue, bond park trail) plus base remaps.
    """
    from tcl_highway_resolve import _ensure_index, _legal_keys, _prefix_matches

    _ensure_index()
    root = street_name_root(street)
    if not root:
        return ()

    seen: set[str] = set()
    out: list[str] = []

    def add(key: str) -> None:
        k = tcl_highway_key(key)
        if k and k in _legal_keys and k not in seen:
            seen.add(k)
            out.append(k)

    resolved = resolve_tcl_highway(street)
    if resolved:
        add(resolved)
    for cand in base_remap_candidates(street):
        add(cand)
    for cand in _prefix_matches(root):
        add(cand)
    return tuple(out)


def _unique_intersection_ids_for_variants(
    variants: tuple[str, ...],
    street_other: str,
) -> tuple[int, ...] | None:
    """
    Pair each legal variant with *street_other*; return IDs when exactly one intersection.

    Returns ``None`` when variants disagree on multiple intersections (ambiguous).
    """
    import intersection_index as ix

    ids: set[int] = set()
    for cand in variants:
        for name_other in ix._lookup_name_candidates(street_other):
            hit = ix.resolve_pair_ids_tokens(cand, name_other)
            if len(hit) == 1:
                ids.add(hit[0])
            elif len(hit) > 1:
                return None
        partner = resolve_tcl_highway(street_other)
        if partner:
            hit = ix.resolve_pair_ids_tokens(cand, partner)
            if len(hit) == 1:
                ids.add(hit[0])
            elif len(hit) > 1:
                return None
    if len(ids) == 1:
        return (next(iter(ids)),)
    return ()


@lru_cache(maxsize=65536)
def resolve_pair_via_unique_legal_variant(
    street_a: str,
    street_b: str,
) -> tuple[int, ...]:
    """
    When token and root lookup fail, try TCL legals in the same name family.

    Remaps one side at a time (prefix/base variants); accepts only a single
    ``INTERSECTION_ID`` across all successful variant pairings.
    """
    var_a = _legal_variants_for_street(street_a)
    var_b = _legal_variants_for_street(street_b)
    default_a = resolve_tcl_highway(street_a)
    default_b = resolve_tcl_highway(street_b)

    if len(var_a) > 1:
        alts = tuple(v for v in var_a if v != default_a) or var_a
        hit = _unique_intersection_ids_for_variants(alts, street_b)
        if hit:
            return hit

    if len(var_b) > 1:
        alts = tuple(v for v in var_b if v != default_b) or var_b
        hit = _unique_intersection_ids_for_variants(alts, street_a)
        if hit:
            return hit

    return ()


@lru_cache(maxsize=65536)
def resolve_pair_via_roots(street_a: str, street_b: str) -> PairRootMatch | None:
    """
    When full-token pair lookup fails, match stripped roots in INTERSECTION_DESC.

    Requires exactly one intersection containing both roots; maps each input to
    the corresponding desc leg (TCL type suffix).
    """
    root_a = street_name_root(street_a)
    root_b = street_name_root(street_b)
    if not root_a or not root_b or root_a == root_b:
        return None

    import intersection_index as ix

    ids_a = ix._ensure_token(root_a)
    if not ids_a:
        return None
    matched = sorted(
        set(ids_a) & set(ix._ensure_token(root_b)),
        key=ix._id_order.__getitem__,
    )
    if len(matched) != 1:
        return None

    iid = matched[0]
    desc = ix.intersection_desc(iid)
    if not desc:
        return None

    legs = parse_intersection_desc_legs_all(desc)
    if len(legs) < 2:
        return None

    token_a = None
    token_b = None
    for leg in legs:
        if token_a is None:
            token_a = _leg_token_for_input(leg, street_a)
        if token_b is None:
            token_b = _leg_token_for_input(leg, street_b)
    if not token_a or not token_b:
        return None

    return PairRootMatch(
        intersection_id=iid,
        street_a_token=token_a,
        street_b_token=token_b,
        intersection_desc=desc,
    )


def clear_pair_root_cache() -> None:
    resolve_pair_via_roots.cache_clear()
    resolve_pair_via_unique_legal_variant.cache_clear()

"""Disambiguate street suffixes via unique root×root intersection matches."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from intersection_normalize import normalize_intersection_street
from tcl_highway_key import tcl_highway_key
from tcl_highway_resolve import (
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


def _leg_token_for_input(leg: str, input_street: str) -> str | None:
    if street_name_root(leg) != street_name_root(input_street):
        return None
    resolved = resolve_tcl_highway(leg)
    if resolved:
        return resolved
    return normalize_intersection_street(leg)


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

    legs = parse_intersection_desc_legs(desc)
    if legs is None:
        return None

    token_a = _leg_token_for_input(legs[0], street_a) or _leg_token_for_input(legs[1], street_a)
    token_b = _leg_token_for_input(legs[0], street_b) or _leg_token_for_input(legs[1], street_b)
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

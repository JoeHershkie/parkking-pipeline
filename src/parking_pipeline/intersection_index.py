"""Fast substring intersection lookup for TCL INTERSECTION_DESC rows."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

import ahocorasick
import geopandas as gpd

from .intersection_normalize import (
    expand_cross_lookup_names,
    tcl_search_tokens,
)

_ids: list[int] = []
_descs: list[str] = []
_desc_by_id: dict[int, str] = {}
_id_order: dict[int, int] = {}
_postings: dict[str, tuple[int, ...]] = {}


def configure(ix_gdf: gpd.GeoDataFrame) -> None:
    """Load parallel id/desc arrays and clear token postings."""
    global _ids, _descs, _desc_by_id, _id_order, _postings
    _ids = [int(x) for x in ix_gdf['INTERSECTION_ID'].tolist()]
    _descs = ix_gdf['INTERSECTION_DESC'].str.lower().tolist()
    _desc_by_id = {
        int(ix_id): desc
        for ix_id, desc in zip(_ids, _descs, strict=True)
    }
    _id_order = {ix_id: i for i, ix_id in enumerate(_ids)}
    _postings = {}
    resolve_pair_ids.cache_clear()
    resolve_pair_ids_tokens.cache_clear()
    _clear_pair_root_cache()


def postings_snapshot() -> dict[str, tuple[int, ...]]:
    return dict(_postings)


def install_postings(postings: dict[str, tuple[int, ...]]) -> None:
    """Restore cached token postings (e.g. from geo_cache)."""
    global _postings
    _postings = dict(postings)
    resolve_pair_ids.cache_clear()
    resolve_pair_ids_tokens.cache_clear()
    _clear_pair_root_cache()


def intersection_desc(intersection_id: int) -> str:
    """Lowercased ``INTERSECTION_DESC`` for an id (empty if unknown)."""
    return _desc_by_id.get(int(intersection_id), '')


def _clear_pair_root_cache() -> None:
    try:
        from .intersection_pair_resolve import clear_pair_root_cache

        clear_pair_root_cache()
    except ImportError:
        pass


def _ensure_token(token: str) -> tuple[int, ...]:
    if not token:
        return ()
    cached = _postings.get(token)
    if cached is not None:
        return cached
    ids = tuple(
        ix_id for ix_id, desc in zip(_ids, _descs, strict=True) if token in desc
    )
    _postings[token] = ids
    return ids


def warm_tokens(tokens: Iterable[str]) -> int:
    """
    One pass over intersection descriptions (Aho–Corasick) to fill token postings.
    Returns the number of tokens newly indexed.
    """
    pending = {t for t in tokens if t and t not in _postings}
    if not pending:
        return 0

    automaton = ahocorasick.Automaton()
    for token in pending:
        automaton.add_word(token, token)
    automaton.make_automaton()

    buckets: dict[str, list[int]] = {t: [] for t in pending}
    for ix_id, desc in zip(_ids, _descs, strict=True):
        for _, token in automaton.iter(desc):
            buckets[token].append(ix_id)

    for token, bucket in buckets.items():
        _postings[token] = tuple(bucket)
    return len(pending)


def collect_tokens_from_pairs(pairs: Iterable[tuple[str, str]]) -> set[str]:
    """Normalized search tokens for a set of (street_a, street_b) lookups."""
    names: set[str] = set()
    for a, b in pairs:
        for name in expand_cross_lookup_names(a) if a else ():
            key = name.strip()
            if key:
                names.add(key)
        for name in expand_cross_lookup_names(b) if b else ():
            key = name.strip()
            if key:
                names.add(key)
    tokens: set[str] = set()
    for name in names:
        tokens.update(tcl_search_tokens(name))
    return tokens


def _lookup_name_candidates(street: str) -> tuple[str, ...]:
    if not street or not str(street).strip():
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for part in expand_cross_lookup_names(street):
        key = part.strip()
        if key and key not in seen:
            seen.add(key)
            names.append(key)
    return tuple(names)


def _resolve_with_tokens(tokens_1: tuple[str, ...], tokens_2: tuple[str, ...]) -> tuple[int, ...]:
    for t1 in tokens_1:
        ids_a = _ensure_token(t1)
        if not ids_a:
            continue
        set_a = set(ids_a)
        for t2 in tokens_2:
            ids_b = _ensure_token(t2)
            if not ids_b:
                continue
            matched = set_a & set(ids_b)
            if matched:
                return tuple(sorted(matched, key=_id_order.__getitem__))
    return ()


@lru_cache(maxsize=65536)
def resolve_pair_ids_tokens(street_1: str, street_2: str) -> tuple[int, ...]:
    """INTERSECTION_IDs matched by normalized tokens only (no root fallback)."""
    if not street_1 or not street_2:
        return ()

    for name_1 in _lookup_name_candidates(street_1):
        tokens_1 = tcl_search_tokens(name_1)
        if not tokens_1:
            continue
        for name_2 in _lookup_name_candidates(street_2):
            tokens_2 = tcl_search_tokens(name_2)
            if not tokens_2:
                continue
            hit = _resolve_with_tokens(tokens_1, tokens_2)
            if hit:
                return hit
    return ()


@lru_cache(maxsize=65536)
def resolve_pair_ids(street_1: str, street_2: str) -> tuple[int, ...]:
    """INTERSECTION_IDs whose description contains both normalized street tokens."""
    hit = resolve_pair_ids_tokens(street_1, street_2)
    if hit:
        return hit

    from .intersection_pair_resolve import (
        resolve_pair_via_roots,
        resolve_pair_via_unique_legal_variant,
    )

    for name_1 in _lookup_name_candidates(street_1):
        for name_2 in _lookup_name_candidates(street_2):
            root_match = resolve_pair_via_roots(name_1, name_2)
            if root_match is not None:
                return (root_match.intersection_id,)

    for name_1 in _lookup_name_candidates(street_1):
        for name_2 in _lookup_name_candidates(street_2):
            variant_hit = resolve_pair_via_unique_legal_variant(name_1, name_2)
            if variant_hit:
                return variant_hit
    return ()


def match_count(street_1: str, street_2: str) -> int:
    return len(resolve_pair_ids(street_1, street_2))

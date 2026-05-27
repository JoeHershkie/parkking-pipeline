"""Resolve placeholder Lane / Laneway bylaw Highway values to TCL legal street keys."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from tcl_highway_key import tcl_highway_key
import tcl_highway_resolve as thr

_GENERIC_LANE_RE = re.compile(r'^lane$', re.I)
_LANE_POSITION_RE = re.compile(
    r'^lane\s*,?\s*(?:(first|second|third)\s+)?'
    r'(north|south|east|west)\s+of\s+(.+)$',
    re.I,
)
_LANEWAY_POSITION_RE = re.compile(
    r'^laneway\s+(?:(\d+)\s+metres?\s+)?(north|south|east|west)\s+of\s+(.+)$',
    re.I,
)
_LANEWAY_SIMPLE_RE = re.compile(r'^laneway$', re.I)
_BETWEEN_LANE_FIRST_RE = re.compile(
    r'(?:lane\s*,?\s*)?(?:(first|second|third)\s+)?'
    r'(north|south|east|west)\s+of\s+([^,;]+)',
    re.I,
)
_POINT_AND_RE = re.compile(r'^(.+?)\s+and\s+a\s+point\b', re.I)
_LN_P1_RE = re.compile(
    r'^ln(?:\s+(\d+))?\s+([nsew])\s+(.+?)\s+([ew])\s+(.+)$',
)
_LN_P2_RE = re.compile(
    r'^ln(?:\s+(\d+))?\s+([ew])\s+(.+?)\s+([nsew])\s+(.+)$',
)

_DIR_TO_LETTER = {
    'north': 'n',
    'south': 's',
    'east': 'e',
    'west': 'w',
}
_ORDINAL_TO_NUM = {
    'first': '1',
    'second': '2',
    'third': '3',
}
_NS = frozenset({'north', 'south'})
_EW = frozenset({'east', 'west'})
_ANCHOR_TRIM_RE = re.compile(r'^(.+?)\s+and\s+(?:a\s+point|the\b)', re.I)
_LANE_PLACEHOLDER_RE = re.compile(
    r'^(?:lane\s*,?\s*(?:(?:first|second|third)\s+)?(?:north|south|east|west)\s+of|laneway\b|lane)$',
    re.I,
)

_LN_P1_INDEX: list[tuple[str | None, str, str, str, str, str]] = []
_LN_P2_INDEX: list[tuple[str | None, str, str, str, str, str]] = []
_LN_INDEX_READY = False


@dataclass(frozen=True)
class LanePhrase:
    ordinal: str | None
    direction: str
    anchor: str


def reset_lane_resolve_caches() -> None:
    """Clear caches after TCL street index rebuild (tests / hot reload)."""
    global _LN_INDEX_READY
    _LN_INDEX_READY = False
    _lookup_highway_key_cached.cache_clear()


def is_lane_placeholder_highway(highway: str, between: str = '') -> bool:
    """True when ``Highway`` / ``Between`` need abbreviated ``ln …`` inference."""
    h = str(highway).strip()
    if not h:
        return False
    if _GENERIC_LANE_RE.match(h) or _LANEWAY_SIMPLE_RE.match(h):
        return True
    if _LANE_PLACEHOLDER_RE.match(h):
        return True
    if parse_lane_highway_phrase(h) is not None:
        return True
    if between and infer_lane_phrase_from_between(h, between) is not None:
        return True
    return False


def _ensure_abbrev_ln_index() -> None:
    global _LN_INDEX_READY, _LN_P1_INDEX, _LN_P2_INDEX
    if _LN_INDEX_READY:
        return
    thr._ensure_index()
    legal = getattr(thr, '_legal_keys', frozenset())
    p1: list[tuple[str | None, str, str, str, str, str]] = []
    p2: list[tuple[str | None, str, str, str, str, str]] = []
    for lane_key in legal:
        if not str(lane_key).startswith('ln '):
            continue
        m1 = _LN_P1_RE.match(str(lane_key))
        if m1:
            p1.append((
                m1.group(1), m1.group(2), m1.group(3), m1.group(4), m1.group(5), str(lane_key),
            ))
            continue
        m2 = _LN_P2_RE.match(str(lane_key))
        if m2:
            p2.append((
                m2.group(1), m2.group(2), m2.group(3), m2.group(4), m2.group(5), str(lane_key),
            ))
    _LN_P1_INDEX = p1
    _LN_P2_INDEX = p2
    _LN_INDEX_READY = True


def _trim_lane_anchor(anchor: str) -> str:
    text = str(anchor).strip()
    m = _ANCHOR_TRIM_RE.match(text)
    if m:
        return m.group(1).strip()
    return text


def parse_lane_highway_phrase(highway: str) -> LanePhrase | None:
    """Parse ``Lane first north of Bloor Street West`` and similar."""
    h = str(highway).strip()
    if not h:
        return None
    m = _LANE_POSITION_RE.match(h)
    if m:
        return LanePhrase(
            ordinal=(m.group(1) or '').lower() or None,
            direction=m.group(2).lower(),
            anchor=_trim_lane_anchor(m.group(3)),
        )
    m = _LANEWAY_POSITION_RE.match(h)
    if m:
        return LanePhrase(
            ordinal=None,
            direction=m.group(2).lower(),
            anchor=_trim_lane_anchor(m.group(3)),
        )
    return None


def infer_lane_phrase_from_between(highway: str, between: str) -> LanePhrase | None:
    """When ``Highway`` is generic ``Lane``, extract position from ``Between`` text."""
    if not _GENERIC_LANE_RE.match(str(highway).strip()):
        return None
    if not between or not str(between).strip():
        return None
    m = _BETWEEN_LANE_FIRST_RE.search(str(between))
    if not m:
        return None
    return LanePhrase(
        ordinal=(m.group(1) or '').lower() or None,
        direction=m.group(2).lower(),
        anchor=_trim_lane_anchor(m.group(3)),
    )


def lane_phrases_from_between(between: str) -> list[LanePhrase]:
    """All ``Lane first … of …`` fragments in ``Between``."""
    if not between or not str(between).strip():
        return []
    out: list[LanePhrase] = []
    for m in _BETWEEN_LANE_FIRST_RE.finditer(str(between)):
        out.append(
            LanePhrase(
                ordinal=(m.group(1) or '').lower() or None,
                direction=m.group(2).lower(),
                anchor=_trim_lane_anchor(m.group(3)),
            )
        )
    return out


def _component_matches(ln_component: str, resolved_key: str) -> bool:
    if not ln_component or not resolved_key:
        return False
    return resolved_key == ln_component or resolved_key.startswith(ln_component + ' ')


def _resolve_cross_keys(
    between: str,
    parsed: dict | None,
    phrases: list[LanePhrase],
) -> list[str]:
    """Resolved TCL keys for cross-street hints (other anchors, plain ``X and a point``)."""
    hints: list[str] = []
    bt = str(between or '')
    m = _POINT_AND_RE.match(bt)
    if m:
        hints.append(m.group(1).strip())

    if parsed:
        for col in ('start_intersection', 'end_intersection', 'terminus_street'):
            val = parsed.get(col)
            if val and str(val).strip():
                hints.append(str(val).strip())

    for phrase in phrases:
        for other in phrases:
            if other.anchor.strip().lower() != phrase.anchor.strip().lower():
                hints.append(other.anchor)

    resolved: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        key = thr.resolve_tcl_highway(hint)
        if not key or key in seen:
            continue
        seen.add(key)
        resolved.append(key)
    return resolved


def _collect_phrases(
    highway: str,
    between: str,
    parsed: dict | None,
) -> list[LanePhrase]:
    h = str(highway).strip()
    phrases: list[LanePhrase] = []
    if _GENERIC_LANE_RE.match(h):
        phrase = infer_lane_phrase_from_between(h, between)
        if phrase:
            phrases.append(phrase)
    else:
        phrase = parse_lane_highway_phrase(h)
        if phrase:
            phrases.append(phrase)

    phrases.extend(lane_phrases_from_between(between))

    if not phrases and parsed:
        anchor = parsed.get('start_intersection') or parsed.get('end_intersection')
        if anchor and _GENERIC_LANE_RE.match(h):
            phrases.append(LanePhrase(ordinal=None, direction='north', anchor=str(anchor)))

    deduped: list[LanePhrase] = []
    seen: set[tuple[str | None, str, str]] = set()
    for p in phrases:
        key = (p.ordinal, p.direction, p.anchor.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped


def _apply_ordinal(candidates: list[str], ordinal: str | None) -> list[str]:
    if not ordinal or len(candidates) <= 1:
        return candidates
    num = _ORDINAL_TO_NUM.get(ordinal)
    if not num:
        return candidates
    numbered = [k for k in candidates if re.match(rf'^ln\s+{num}\s+', k)]
    if len(numbered) == 1:
        return numbered
    if numbered:
        return numbered
    return candidates


def _filter_by_cross(
    candidates: list[str],
    cross_keys: list[str],
) -> list[str]:
    if not cross_keys or not candidates:
        return candidates
    out: list[str] = []
    for key in candidates:
        m1 = _LN_P1_RE.match(key)
        if m1:
            cross_part = m1.group(5)
            if any(_component_matches(cross_part, ck) for ck in cross_keys):
                out.append(key)
            continue
        m2 = _LN_P2_RE.match(key)
        if m2:
            cross_part = m2.group(5)
            if any(_component_matches(cross_part, ck) for ck in cross_keys):
                out.append(key)
    return out


def _candidates_p1(
    phrase: LanePhrase,
    legal_keys: set[str] | frozenset[str],
    cross_keys: list[str],
) -> list[str]:
    pos = _DIR_TO_LETTER.get(phrase.direction)
    if not pos:
        return []
    anchor_key = thr.resolve_tcl_highway(phrase.anchor)
    if not anchor_key:
        return []

    _ensure_abbrev_ln_index()
    out: list[str] = []
    for _ord, p, street, _ew, _cross, lane_key in _LN_P1_INDEX:
        if lane_key not in legal_keys or p != pos:
            continue
        if _component_matches(street, anchor_key):
            out.append(lane_key)
    return _filter_by_cross(out, cross_keys)


def _candidates_p2(
    phrase: LanePhrase,
    legal_keys: set[str] | frozenset[str],
    cross_keys: list[str],
) -> list[str]:
    side = _DIR_TO_LETTER.get(phrase.direction)
    if not side:
        return []
    anchor_key = thr.resolve_tcl_highway(phrase.anchor)
    if not anchor_key:
        return []

    _ensure_abbrev_ln_index()
    out: list[str] = []
    for _ord, ew, street, _ns, _cross, lane_key in _LN_P2_INDEX:
        if lane_key not in legal_keys or ew != side:
            continue
        if _component_matches(street, anchor_key):
            out.append(lane_key)
    return _filter_by_cross(out, cross_keys)


def _candidates_for_phrase(
    phrase: LanePhrase,
    legal_keys: set[str] | frozenset[str],
    cross_keys: list[str],
) -> list[str]:
    if phrase.direction in _NS:
        raw = _candidates_p1(phrase, legal_keys, cross_keys)
    elif phrase.direction in _EW:
        raw = _candidates_p2(phrase, legal_keys, cross_keys)
    else:
        return []
    return _apply_ordinal(raw, phrase.ordinal)


def _named_lane_graph_keys() -> list[str]:
    """TCL keys like ``Jack Christie Lane`` (not abbreviated ``ln …``)."""
    thr._ensure_index()
    keys = getattr(thr, '_legal_keys', frozenset())
    out: list[str] = []
    for k in keys:
        if k == 'lane':
            continue
        if str(k).startswith('ln '):
            continue
        if k.endswith(' lane') or k.endswith(' ln'):
            out.append(k)
    return out


def _resolve_named_lane_graph(
    phrase: LanePhrase,
    legal_keys: set[str] | frozenset[str],
) -> str | None:
    anchor_key = thr.resolve_tcl_highway(phrase.anchor)
    if not anchor_key or anchor_key not in legal_keys:
        return None
    try:
        import tcl_graph as tg
    except ImportError:
        return None

    candidates: list[str] = []
    for lane_key in _named_lane_graph_keys():
        if lane_key not in legal_keys:
            continue
        if tg.resolve_intersection_ids(lane_key, phrase.anchor):
            candidates.append(lane_key)
        elif tg.resolve_intersection_ids(phrase.anchor, lane_key):
            candidates.append(lane_key)

    unique = sorted(set(candidates))
    if len(unique) == 1:
        return unique[0]
    return None


def resolve_lane_legal(
    phrase: LanePhrase,
    *,
    between: str = '',
    parsed: dict | None = None,
    phrases: list[LanePhrase] | None = None,
    legal_keys: set[str] | frozenset[str] | None = None,
) -> str | None:
    """
  Pick a TCL ``LINEAR_NAME_FULL_LEGAL`` key for a lane phrase.

  Prefers abbreviated ``ln …`` grammar matching; falls back to graph adjacency
  for named ``* Lane`` legals when exactly one intersects the anchor.
    """
    keys = legal_keys if legal_keys is not None else getattr(thr, '_legal_keys', frozenset())
    all_phrases = list(phrases or [phrase])
    if phrase not in all_phrases:
        all_phrases.insert(0, phrase)

    cross_keys = _resolve_cross_keys(between, parsed, all_phrases)
    # Other phrase anchors are crosses for this phrase.
    phrase_cross = [
        ck
        for ck in cross_keys
        if ck != thr.resolve_tcl_highway(phrase.anchor)
    ]

    per_phrase: list[list[str]] = []
    for p in all_phrases:
        p_cross = [
            ck
            for ck in cross_keys
            if ck != thr.resolve_tcl_highway(p.anchor)
        ]
        per_phrase.append(_candidates_for_phrase(p, keys, p_cross))

    if len(per_phrase) == 1:
        unique = sorted(set(per_phrase[0]))
    else:
        shared = set(per_phrase[0])
        for group in per_phrase[1:]:
            shared &= set(group)
        unique = sorted(shared) if shared else []

    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        narrowed = _apply_ordinal(unique, phrase.ordinal)
        if len(narrowed) == 1:
            return narrowed[0]
        return None

    # One phrase matched (others eliminated by cross-street constraints).
    nonempty = [sorted(set(group)) for group in per_phrase if group]
    if len(nonempty) == 1:
        only = nonempty[0]
        if len(only) == 1:
            return only[0]
        for p, group in zip(all_phrases, per_phrase, strict=False):
            if group:
                narrowed = _apply_ordinal(only, p.ordinal)
                if len(narrowed) == 1:
                    return narrowed[0]
        return None

    return _resolve_named_lane_graph(phrase, keys)


def resolve_lane_highway(
    highway: str,
    between: str = '',
    parsed: dict | None = None,
) -> str | None:
    """
    Resolve placeholder lane/laneway highway strings to a TCL legal key.

    Returns ``None`` when inference is unsafe (ambiguous or no lane in TCL).
    """
    h = str(highway).strip()
    if not h:
        return None
    if _LANEWAY_SIMPLE_RE.match(h):
        return None

    phrases = _collect_phrases(h, between, parsed)
    if not phrases:
        return None

    thr._ensure_index()
    keys = getattr(thr, '_legal_keys', frozenset())
    return resolve_lane_legal(
        phrases[0],
        between=between,
        parsed=parsed,
        phrases=phrases,
        legal_keys=keys,
    )


def _freeze_parsed(parsed: dict | None) -> tuple[tuple[str, str], ...]:
    if not parsed:
        return ()
    keys = (
        'start_intersection', 'end_intersection', 'offset_intersection',
        'terminus_street', 'rule_type',
    )
    return tuple(
        (k, str(parsed[k]).strip())
        for k in keys
        if k in parsed and parsed[k] is not None and str(parsed[k]).strip()
    )


def lookup_highway_key_fast(
    highway: str,
    between: str = '',
    parsed: dict | None = None,
) -> str:
    """Resolve to TCL key without abbreviated-laneway inference (for index warm-up)."""
    if parsed:
        return thr.resolve_tcl_highway_with_context(highway, parsed)
    return thr.resolve_tcl_highway(highway)


@lru_cache(maxsize=65536)
def _lookup_highway_key_cached(
    highway: str,
    between: str,
    parsed_frozen: tuple[tuple[str, str], ...],
    infer_lane: bool,
) -> str:
    parsed = dict(parsed_frozen) if parsed_frozen else None
    if parsed:
        key = thr.resolve_tcl_highway_with_context(highway, parsed)
    else:
        key = thr.resolve_tcl_highway(highway)

    thr._ensure_index()
    legal = getattr(thr, '_legal_keys', frozenset())
    if key in legal:
        return key

    if not infer_lane:
        return key

    lane_key = resolve_lane_highway(highway, between, parsed)
    if lane_key:
        return lane_key

    return key


def lookup_highway_key(
    highway: str,
    between: str = '',
    parsed: dict | None = None,
) -> str:
    """
    Full highway lookup: standard resolve, then lane inference when needed.
    """
    infer_lane = is_lane_placeholder_highway(highway, between)
    return _lookup_highway_key_cached(
        str(highway or '').strip(),
        str(between or '').strip(),
        _freeze_parsed(parsed),
        infer_lane,
    )

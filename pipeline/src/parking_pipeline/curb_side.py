"""Normalize bylaw ``Side`` values into a typed curb-side specification.

The four cardinals plus ``Both`` cover almost every row. Remaining vocabulary is
classified explicitly: adjacent compounds may wrap as one curb, opposing
directions and All/Both select multiple curbs, and island/median/lay-by/leg/
centre/blank/unsupported cases stay specialized or unresolved rather than guessed.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paths import data_path

SideMode = Literal[
    'single',
    'wrapping',
    'multi',
    'parity',
    'perimeter',
    'specialized',
    'unresolved',
]
CompassDir = Literal[
    'north',
    'south',
    'east',
    'west',
    'northeast',
    'northwest',
    'southeast',
    'southwest',
]
Parity = Literal['odd', 'even']
Ring = Literal['inner', 'outer']
SpecializedKind = Literal[
    'island',
    'median',
    'lay_by',
    'leg',
    'centre',
    'cul_de_sac',
    'roadway',
    'end',
    'unsupported',
]
ParitySide = Literal['left', 'right']

OVERRIDE_COLUMNS = ('row_id', 'reason', 'method', 'notes')
OVERRIDE_FILENAME = 'curb_geometry_overrides.csv'

_CARDINALS = frozenset({'north', 'south', 'east', 'west'})
_COMPASS_INDEX = {
    'north': 0,
    'northeast': 1,
    'east': 2,
    'southeast': 3,
    'south': 4,
    'southwest': 5,
    'west': 6,
    'northwest': 7,
}
_COMPASS_CANON = {
    'north': 'north',
    'northerly': 'north',
    'northern': 'north',
    'south': 'south',
    'southerly': 'south',
    'southern': 'south',
    'east': 'east',
    'easterly': 'east',
    'eastern': 'east',
    'west': 'west',
    'westerly': 'west',
    'western': 'west',
    'northeast': 'northeast',
    'northeasterly': 'northeast',
    'northwest': 'northwest',
    'northwesterly': 'northwest',
    'southeast': 'southeast',
    'southeasterly': 'southeast',
    'southwest': 'southwest',
    'southwesterly': 'southwest',
}

_DASH_TRANS = str.maketrans({
    '\u2013': '-',
    '\u2014': '-',
    '\u2212': '-',
    '\u2018': "'",
    '\u2019': "'",
})

_COMPASS_RE = re.compile(
    r'\b('
    r'north(?:east|west)erly|south(?:east|west)erly|'
    r'north(?:east|west)|south(?:east|west)|'
    r'northerly|southerly|easterly|westerly|'
    r'northern|southern|eastern|western|'
    r'north|south|east|west'
    r')\b(?!bound)'
)
_HYPHEN_COMPASS_RE = re.compile(r'\b(north|south)\s*-\s*(east|west)\b')
_TWO_WORD_COMPASS_RE = re.compile(r'\b(north|south)\s+(east|west)\b')
_SLASH_RE = re.compile(r'\s*/\s*')
_AND_RE = re.compile(r'(?:\s*\band\s*)+')
_CENTER_RE = re.compile(r'\bcenter\b')
_PERIMITER_RE = re.compile(r'\bperimiter\b')

_BOTH_RE = re.compile(r'^both(?:\s+sides?)?$')
_ALL_RE = re.compile(r'^all(?:\s+sides?)?$')
_ODD_RE = re.compile(r'^odd(?:(?:\s+numbered)?\s+sides?)?$')
_EVEN_RE = re.compile(r'^even(?:(?:\s+numbered)?\s+sides?)?$')

_RING_PATTERNS: tuple[tuple[re.Pattern[str], Ring, str], ...] = (
    (re.compile(r'\binner\s+perimeter\b'), 'inner', 'perimeter'),
    (re.compile(r'\binside\s+perimeter\b'), 'inner', 'perimeter'),
    (re.compile(r'\bouter\s+perimeter\b'), 'outer', 'perimeter'),
    (re.compile(r'\boutside\s+perimeter\b'), 'outer', 'perimeter'),
    (re.compile(r'\binner\s+circle\b'), 'inner', 'circle'),
    (re.compile(r'\bouter\s+circle\b'), 'outer', 'circle'),
    (re.compile(r'\binner\s+radius\b'), 'inner', 'radius'),
    (re.compile(r'\bouter\s+radius\b'), 'outer', 'radius'),
    (re.compile(r'\binner\s+side\b'), 'inner', 'side'),
    (re.compile(r'\bouter\s+side\b'), 'outer', 'side'),
)
_ISLAND_RE = re.compile(r'\bislands?\b')
_MEDIAN_RE = re.compile(r'\bmedians?\b')
_LAY_BY_RE = re.compile(r'\blay(?:\s*|-)?bys?\b')
_CUL_DE_SAC_RE = re.compile(r'\bcul(?:\s*|-)+de(?:\s*|-)+sacs?\b')
_LEG_RE = re.compile(r'\blegs?\b')
_LEG_DIR_RE = re.compile(r'\b(north|south|east|west)\s+leg\b')
_ROADWAY_RE = re.compile(r'\broadways?\b')
_BOUND_RE = re.compile(r'\b(north|south|east|west)bound\b')
_CENTRE_RE = re.compile(r'\bcentre\b')
_END_RE = re.compile(r'\b(north|south|east|west)\s+end\b')
_ADJACENT_RE = re.compile(r'\badjacent\b')
_DIRECTION_MASK_RE = re.compile(
    r'\b(?:'
    r'(?:north|south|east|west)\s+leg|'
    r'(?:north|south|east|west)bound|'
    r'(?:north|south|east|west)\s+end|'
    r'(?:north|south|east|west)\s+of\s+the\s+median'
    r')\b'
)

_BLANK_STRINGS = frozenset({'', 'nan', 'none', '<na>', '<nat>'})


@dataclass(frozen=True)
class SideSpec:
    """Parsed curb-side semantics; ``raw`` is the unmodified source value."""

    raw: str
    normalized: str
    mode: SideMode
    directions: tuple[str, ...]
    wrapping: bool = False
    parity: Parity | None = None
    ring: Ring | None = None
    radius: bool = False
    specialized_kind: SpecializedKind | None = None
    qualifiers: tuple[str, ...] = ()
    unresolved_reason: str | None = None

    @property
    def needs_override(self) -> bool:
        """True when compass/parity/perimeter semantics cannot be used as-is."""
        return self.mode in {'specialized', 'unresolved'}

    @property
    def selects_multiple_curbs(self) -> bool:
        return self.mode == 'multi'


@dataclass(frozen=True)
class CurbGeometryOverride:
    """Curated per-row geometry fallback keyed by source ``_id``."""

    row_id: str
    reason: str
    method: str
    notes: str = ''


def parse_side(raw: object) -> SideSpec:
    """Classify a bylaw ``Side`` string. Always returns a spec; never raises on junk."""
    raw_s, blank = _coerce_raw(raw)
    if blank:
        return _spec(raw_s, mode='unresolved', unresolved_reason='blank')

    text = _normalize_text(raw_s)
    ring, radius, ring_tags = _extract_ring(text)
    extra_tags = _extra_qualifier_tags(text)
    special_tags = _specialized_tags(text)
    if special_tags:
        kind = special_tags[0]
        return _spec(
            raw_s,
            normalized=kind.replace('_', ' '),
            mode='specialized',
            directions=_extract_directions(text),
            ring=ring,
            radius=radius,
            specialized_kind=kind,
            qualifiers=_unique_preserve(ring_tags + extra_tags + special_tags),
        )

    if _BOTH_RE.fullmatch(text):
        return _spec(raw_s, normalized='both', mode='multi')
    if _ALL_RE.fullmatch(text):
        return _spec(raw_s, normalized='all', mode='multi')
    if _ODD_RE.fullmatch(text):
        return _spec(raw_s, normalized='odd', mode='parity', parity='odd')
    if _EVEN_RE.fullmatch(text):
        return _spec(raw_s, normalized='even', mode='parity', parity='even')

    directions = _extract_directions(text)
    if not directions:
        if ring is not None:
            shape = 'radius' if radius else (ring_tags[0] if ring_tags else 'perimeter')
            label = f'{ring} {shape}' if shape != 'side' else f'{ring} perimeter'
            return _spec(
                raw_s,
                normalized=label,
                mode='perimeter',
                ring=ring,
                radius=radius,
                qualifiers=_unique_preserve(ring_tags + extra_tags),
            )
        return _spec(
            raw_s,
            mode='unresolved',
            specialized_kind='unsupported',
            qualifiers=extra_tags,
            unresolved_reason='unsupported',
        )

    mode, wrapping = _classify_compass(directions)
    if mode == 'multi' and _CARDINALS <= set(directions):
        normalized = 'all'
    else:
        normalized = ' and '.join(directions)
    return _spec(
        raw_s,
        normalized=normalized,
        mode=mode,
        directions=directions,
        wrapping=wrapping,
        ring=ring,
        radius=radius,
        qualifiers=_unique_preserve(ring_tags + extra_tags),
    )


def resolve_parity_side(
    spec: SideSpec,
    *,
    parity_l: str | None,
    parity_r: str | None,
    orientation_unambiguous: bool = True,
) -> ParitySide | None:
    """Map Odd/Even to TCL left/right using ``PARITY_L`` / ``PARITY_R``.

    Does not require Road Edge data. Returns ``None`` when orientation is
    ambiguous, parity codes are missing, or both/neither sides match.
    """
    if spec.mode != 'parity' or spec.parity is None:
        return None
    if not orientation_unambiguous:
        return None
    want = 'O' if spec.parity == 'odd' else 'E'
    left = _parity_matches(parity_l, want)
    right = _parity_matches(parity_r, want)
    if left == right:
        return None
    return 'left' if left else 'right'


def load_curb_geometry_overrides(path: Path | None = None) -> dict[str, CurbGeometryOverride]:
    """Load curated overrides. Missing files yield an empty mapping."""
    src = path or data_path(OVERRIDE_FILENAME)
    if not src.exists():
        return {}
    out: dict[str, CurbGeometryOverride] = {}
    for row in _iter_override_rows(src):
        row_id = (row.get('row_id') or '').strip()
        reason = (row.get('reason') or '').strip()
        method = (row.get('method') or '').strip()
        notes = (row.get('notes') or '').strip()
        if not row_id or not reason or not method or row_id in out:
            continue
        out[row_id] = CurbGeometryOverride(
            row_id=row_id,
            reason=reason,
            method=method,
            notes=notes,
        )
    return out


def override_for_row(
    row_id: object,
    spec: SideSpec,
    overrides: dict[str, CurbGeometryOverride] | None = None,
) -> CurbGeometryOverride | None:
    """Return a curated override only after deterministic side resolution fails."""
    if not spec.needs_override:
        return None
    table = overrides if overrides is not None else load_curb_geometry_overrides()
    key = str(row_id).strip()
    if not key or key.lower() in _BLANK_STRINGS:
        return None
    return table.get(key)


def _spec(
    raw: str,
    *,
    normalized: str = '',
    mode: SideMode,
    directions: tuple[str, ...] = (),
    wrapping: bool = False,
    parity: Parity | None = None,
    ring: Ring | None = None,
    radius: bool = False,
    specialized_kind: SpecializedKind | None = None,
    qualifiers: tuple[str, ...] = (),
    unresolved_reason: str | None = None,
) -> SideSpec:
    return SideSpec(
        raw=raw,
        normalized=normalized,
        mode=mode,
        directions=directions,
        wrapping=wrapping,
        parity=parity,
        ring=ring,
        radius=radius,
        specialized_kind=specialized_kind,
        qualifiers=qualifiers,
        unresolved_reason=unresolved_reason,
    )


def _coerce_raw(raw: object) -> tuple[str, bool]:
    if raw is None:
        return '', True
    if isinstance(raw, float) and math.isnan(raw):
        return '', True
    text = str(raw)
    return text, not text.strip() or text.strip().casefold() in _BLANK_STRINGS


def _normalize_text(raw: str) -> str:
    s = raw.strip().casefold().translate(_DASH_TRANS)
    s = _PERIMITER_RE.sub('perimeter', s)
    s = _CENTER_RE.sub('centre', s)
    s = s.replace('&', ' and ')
    s = s.replace('.', ' ')
    s = s.replace(';', ' ')
    s = s.replace(',', ' and ')
    s = _SLASH_RE.sub(' and ', s)
    s = _HYPHEN_COMPASS_RE.sub(r'\1\2', s)
    s = _TWO_WORD_COMPASS_RE.sub(r'\1\2', s)
    s = _AND_RE.sub(' and ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'^(?:and\s+)+|(?:\s+and)+$', '', s)
    return s.strip()


def _extract_ring(text: str) -> tuple[Ring | None, bool, tuple[str, ...]]:
    ring: Ring | None = None
    radius = False
    tags: list[str] = []
    for pattern, found_ring, shape in _RING_PATTERNS:
        if not pattern.search(text):
            continue
        if ring is None:
            ring = found_ring
        tags.append(shape)
        if shape == 'radius':
            radius = True
    return ring, radius, tuple(tags)


def _extra_qualifier_tags(text: str) -> tuple[str, ...]:
    tags: list[str] = []
    for match in _LEG_DIR_RE.finditer(text):
        tags.append(f'{match.group(1)}_leg')
    for match in _BOUND_RE.finditer(text):
        tags.append(match.group(0))
    if _ADJACENT_RE.search(text):
        tags.append('adjacent')
    return tuple(tags)


def _specialized_tags(text: str) -> tuple[SpecializedKind, ...]:
    tags: list[SpecializedKind] = []
    if _ISLAND_RE.search(text):
        tags.append('island')
    if _MEDIAN_RE.search(text):
        tags.append('median')
    if _LAY_BY_RE.search(text):
        tags.append('lay_by')
    if _CUL_DE_SAC_RE.search(text):
        tags.append('cul_de_sac')
    if _LEG_RE.search(text):
        tags.append('leg')
    if _ROADWAY_RE.search(text) or _BOUND_RE.search(text):
        tags.append('roadway')
    if _END_RE.search(text):
        tags.append('end')
    if _CENTRE_RE.search(text):
        tags.append('centre')
    return tuple(tags)


def _extract_directions(text: str) -> tuple[str, ...]:
    found: list[str] = []
    masked = _DIRECTION_MASK_RE.sub(' ', text)
    for match in _COMPASS_RE.finditer(masked):
        token = match.group(1).replace('-', '').replace(' ', '')
        canon = _COMPASS_CANON.get(token)
        if canon is None:
            continue
        if found and found[-1] == canon:
            continue
        found.append(canon)
    return _unique_preserve(found)


def _classify_compass(directions: tuple[str, ...]) -> tuple[SideMode, bool]:
    unique = directions
    if len(unique) == 1:
        return 'single', False
    if _CARDINALS <= set(unique):
        return 'multi', False
    idxs = {_COMPASS_INDEX[d] for d in unique}
    arc = _covering_arc_len(idxs, 8)
    if len(unique) == 2 and arc == 4:
        return 'multi', False
    return 'wrapping', True


def _covering_arc_len(idxs: set[int], n: int) -> int:
    if not idxs:
        return 0
    ordered = sorted(idxs)
    doubled = ordered + [i + n for i in ordered]
    k = len(ordered)
    return min(doubled[i + k - 1] - doubled[i] for i in range(k))


def _parity_matches(code: str | None, want: str) -> bool:
    if code is None:
        return False
    token = str(code).strip().upper()
    if not token or token in {'N', 'NONE', 'NULL'}:
        return False
    if want == 'O':
        return token in {'O', 'OE', 'EO'}
    return token in {'E', 'OE', 'EO'}


def _unique_preserve(items: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def _iter_override_rows(path: Path) -> csv.DictReader:
    lines = [
        line for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    return csv.DictReader(io.StringIO('\n'.join(lines) + '\n'))

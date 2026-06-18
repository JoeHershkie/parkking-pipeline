"""Lexical cleanup of bylaw Between text before pattern matching."""

from __future__ import annotations

import re

from between_patterns import (
    ADJACENT_TO_RE,
    COMPASS,
    COMPOUND_DIR,
    DIR,
    METRES,
    SCHEDULE_IN_BETWEEN_RE,
)


def preprocess_between(text: str) -> str:
    """Fix common spacing typos and delimiter wording before pattern matching."""
    out = str(text).strip()
    out = re.sub(r'^between\s+', '', out, flags=re.IGNORECASE)
    out = re.sub(r'^from\s+a\s+point\b', 'a point', out, flags=re.IGNORECASE)
    out = re.sub(
        r'^adjacent\s+.+?\s+between\s+',
        '',
        out,
        flags=re.IGNORECASE,
    )
    out = out.rstrip(' .')
    out = re.sub(
        rf'\b(north|south|east|west)-(north|south|east|west)\b',
        r'\1\2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r'\bmetres\s+metres\b', 'metres', out, flags=re.IGNORECASE)
    out = re.sub(
        rf'\ba\s+point\s+of\s+({METRES})\s+metres\b',
        r'a point \1 metres',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        rf'\b({METRES})m\s+({COMPASS})\b',
        r'\1 metres \2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r'\bthereof\s+thereof\b', 'thereof', out, flags=re.IGNORECASE)
    out = re.sub(
        r'\b(north|south|east|west),\s*(north|south|east|west)\b',
        r'\1 and \2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r'\b(north|south|east|west)erly/(north|south|east|west)erly\b',
        r'\1 and \2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r'(\d)(metres\b)', r'\1 \2', out, flags=re.IGNORECASE)
    out = re.sub(r'\bmetres(?=[a-z])', 'metres ', out, flags=re.IGNORECASE)
    out = re.sub(r'\bppposite\b', 'opposite', out, flags=re.IGNORECASE)
    out = re.sub(r'\boposite\b', 'opposite', out, flags=re.IGNORECASE)
    out = re.sub(r'\booint\b', 'point', out, flags=re.IGNORECASE)
    out = re.sub(r'\bappoint\b', 'a point', out, flags=re.IGNORECASE)
    out = re.sub(r'\bnother\b', 'north', out, flags=re.IGNORECASE)
    out = re.sub(r'\bnroth\b', 'north', out, flags=re.IGNORECASE)
    out = re.sub(r'\beas\s+tof\b', 'east of', out, flags=re.IGNORECASE)
    out = re.sub(r'\btherof\b', 'thereof', out, flags=re.IGNORECASE)
    out = re.sub(r'\bmetes\b', 'metres', out, flags=re.IGNORECASE)
    out = re.sub(
        rf'\b({METRES})\s+metre\s+({DIR})\b',
        r'\1 metres \2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r'\bmeters\b', 'metres', out, flags=re.IGNORECASE)
    out = re.sub(
        r'\band\s+a\s+(\d+(?:\.\d+)?)\s+metres\b',
        r'and a point \1 metres',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r'\band\s+a\s+point\s+(\d+(?:\.\d+)?)\s+(north|south|east|west)\s*$',
        r'and a point \1 metres \2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r'\s+and\s*$', '', out, flags=re.IGNORECASE)
    out = re.sub(r'\band\s+and\b', 'and', out, flags=re.IGNORECASE)
    out = re.sub(
        r'\((west|east|north|south)\s+intersection(?!\))',
        r'(\1 intersection)',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r'\)\s+(added|on the inside perimeter|to)\s*$',
        ')',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r'\beaster\s+thereof\b', 'east thereof', out, flags=re.IGNORECASE)
    out = re.sub(r'\ba point\s+a point\b', 'a point', out, flags=re.IGNORECASE)
    out = re.sub(r'\band point (\d)', r'and a point \1', out, flags=re.IGNORECASE)
    out = re.sub(r'^point (\d)', r'a point \1', out, flags=re.IGNORECASE)
    out = re.sub(
        rf'\b({METRES})\s+(?!metres\b)({COMPASS})\s+of\b',
        r'\1 metres \2 of',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        rf'\b(south|north|east|west)/(south|north|east|west)\b',
        r'\1 and \2',
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        rf'\bmetres\s+({COMPOUND_DIR})\s+(?!of\b)((?-i:[A-Z])[A-Za-z\'.,-]*)',
        r'metres \1 of \2',
        out,
        flags=re.IGNORECASE,
    )
    if not re.search(
        rf'\bmetres\s+(?:south|north)\s+and\s+(?-i:[A-Z])'
        rf'(?:[A-Za-z\'.,-]+\s+)*'
        r'(?:street|st|avenue|ave|road|rd|drive|dr|boulevard|blvd|crescent|cres|court|ct|way|gate|grove|lane|ln)\b',
        out,
        flags=re.IGNORECASE,
    ):
        out = re.sub(
            rf'\bmetres\s+(south|north)\s+and\s+((?-i:[A-Z])[A-Za-z\'.,-]*)',
            r'metres \1 and east of \2',
            out,
            flags=re.IGNORECASE,
        )
    if not SCHEDULE_IN_BETWEEN_RE.search(out) and not ADJACENT_TO_RE.match(out):
        out = re.sub(r'\s+to\s+', ' and ', out, flags=re.IGNORECASE)
    return out.strip()

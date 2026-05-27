"""Categorize bylaw Highway strings for STREET_NOT_FOUND analysis and triage."""

from __future__ import annotations

import re

_LEG_PAREN_RE = re.compile(
    r'\([^)]*\b(?:leg|legs|branch|branches|roadway|roadways|section|sections)\b[^)]*\)',
    re.IGNORECASE,
)
_LANE_FIRST_RE = re.compile(
    r'lane\s*,?\s*first\b|lane\s+first\b',
    re.IGNORECASE,
)
_LANEWAY_RE = re.compile(r'\blaneway\b', re.IGNORECASE)


def categorize_highway(highway: str) -> str:
    """
    Primary bucket for a bylaw ``Highway`` value (STREET_NOT_FOUND taxonomy).

    Returns one of: generic_lane_highway, lane_position_in_highway, laneway_phrase,
    leg_branch_paren, parenthetical_qualifier, ramp_service_parallel,
    compound_or_slash_highway, descriptor_in_name, misleading_highway_phrase,
    plain_name.
    """
    if not highway or not str(highway).strip():
        return 'empty'
    h = str(highway).strip()
    hl = h.lower()

    if hl == 'lane':
        return 'generic_lane_highway'
    if _LANE_FIRST_RE.search(hl):
        return 'lane_position_in_highway'
    if _LANEWAY_RE.search(hl):
        return 'laneway_phrase'
    if 'running parallel' in hl or 'parallel to' in hl:
        return 'misleading_highway_phrase'
    if _LEG_PAREN_RE.search(h):
        return 'leg_branch_paren'
    if re.search(
        r'\b(?:cul-de-sac|bus loop|traffic circle)\b',
        hl,
    ):
        return 'descriptor_in_name'
    if '(' in h:
        return 'parenthetical_qualifier'
    if (
        'service road' in hl
        or 'off-ramp' in hl
        or re.search(r'\bramp\b', hl)
    ):
        return 'ramp_service_parallel'
    if '/' in h and ' and ' not in hl:
        return 'compound_or_slash_highway'
    if ' and ' in hl:
        return 'compound_or_slash_highway'
    if re.search(
        r'\b(?:one-way|eastbound|westbound|northbound|southbound)\b',
        hl,
    ):
        return 'parenthetical_qualifier'
    return 'plain_name'

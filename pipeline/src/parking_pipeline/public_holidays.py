"""Ontario public holidays for schedule membership (observed dates)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import holidays

# Bylaw "public holidays" → Ontario statutory holidays (not City of Toronto HR extras).
_ON = holidays.country_holidays


@lru_cache(maxsize=64)
def _holidays_for_year(year: int) -> holidays.HolidayBase:
    return _ON('CA', subdiv='ON', years=year, observed=True)


def is_public_holiday(slot: dict[str, int]) -> bool:
    """
  Return whether *slot* falls on an Ontario public holiday (observed date).

  Requires ``year`` in *slot* for correct weekend/substitution rules.
  """
    year = slot.get('year')
    if year is None:
        return False
    try:
        d = date(int(year), int(slot['month']), int(slot['dayOfMonth']))
    except (TypeError, ValueError):
        return False
    return d in _holidays_for_year(int(year))

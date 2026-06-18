import { describe, expect, it } from 'vitest'
import type { ParkingFeature } from '../types/parking'
import { slotInCalendar } from './schedule/calendar'
import { isPublicHoliday } from './schedule/publicHolidays'
import {
  evaluateAtSlot,
  evaluateInRange,
  ruleMatchesFilter,
} from './schedule/evaluate'
import {
  overlapsMembership,
  overlapsMembershipInRange,
} from './schedule/membership'
import { scheduleStatusHints } from './schedule/display'
import type { Schedule, Slot } from './schedule/types'

const MON_FRI_8_6: Schedule = {
  v: 1,
  status: 'ok',
  source: 'Mon–Fri 8am–6pm',
  windows: [
    {
      days: [1, 2, 3, 4, 5],
      startMinute: 480,
      endMinute: 1080,
      crossesMidnight: false,
    },
  ],
}

const TUE_3PM: Slot = {
  dayOfWeek: 2,
  minuteOfDay: 900,
  month: 5,
  dayOfMonth: 20,
  year: 2025,
}

const SAT_3PM: Slot = {
  dayOfWeek: 6,
  minuteOfDay: 900,
  month: 5,
  dayOfMonth: 24,
  year: 2025,
}

function feature(
  category: string,
  schedule: Schedule | undefined,
): ParkingFeature {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
    properties: {
      Highway: 'Test St',
      Rule: 'test',
      schedule_category: category,
      Side: 'North',
      max: null,
      schedule,
    },
  }
}

describe('overlapsMembership', () => {
  it('matches Tue 3pm in Mon–Fri 8–6', () => {
    expect(overlapsMembership(MON_FRI_8_6, TUE_3PM)).toBe(true)
  })

  it('does not match Sat 3pm in Mon–Fri 8–6', () => {
    expect(overlapsMembership(MON_FRI_8_6, SAT_3PM)).toBe(false)
  })

  it('returns true for anytime without calendar', () => {
    const sched: Schedule = { v: 1, status: 'anytime', source: 'anytime', windows: [] }
    expect(overlapsMembership(sched, SAT_3PM)).toBe(true)
  })

  it('anytime respects schedule-level calendar', () => {
    const winter: Schedule = {
      v: 1,
      status: 'anytime',
      source: 'winter',
      windows: [],
      calendar: {
        monthRanges: [
          { startMonth: 12, startDay: 1, endMonth: 3, endDay: 31 },
        ],
      },
    }
    expect(
      overlapsMembership(winter, {
        dayOfWeek: 2,
        minuteOfDay: 600,
        month: 1,
        dayOfMonth: 15,
        year: 2025,
      }),
    ).toBe(true)
    expect(
      overlapsMembership(winter, {
        dayOfWeek: 2,
        minuteOfDay: 600,
        month: 7,
        dayOfMonth: 15,
        year: 2025,
      }),
    ).toBe(false)
  })

  it('returns false for failed', () => {
    const sched: Schedule = { v: 1, status: 'failed', source: 'Apr–Nov', windows: [] }
    expect(overlapsMembership(sched, TUE_3PM)).toBe(false)
  })

  it('handles crossesMidnight', () => {
    const sched: Schedule = {
      v: 1,
      status: 'ok',
      source: 'overnight',
      windows: [
        {
          days: [1],
          startMinute: 1320,
          endMinute: 360,
          crossesMidnight: true,
        },
      ],
    }
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 1,
        minuteOfDay: 1380,
        month: 1,
        dayOfMonth: 1,
        year: 2025,
      }),
    ).toBe(true)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 1,
        minuteOfDay: 300,
        month: 1,
        dayOfMonth: 1,
        year: 2025,
      }),
    ).toBe(true)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 1,
        minuteOfDay: 720,
        month: 1,
        dayOfMonth: 1,
        year: 2025,
      }),
    ).toBe(false)
  })

  it('weekday window except public holidays', () => {
    const sched: Schedule = {
      v: 1,
      status: 'ok',
      source: '4–6pm weekdays except holidays',
      windows: [
        {
          days: [1, 2, 3, 4, 5],
          startMinute: 960,
          endMinute: 1080,
          crossesMidnight: false,
        },
      ],
      flags: { exceptPublicHolidays: true },
    }
    const canadaDay: Slot = {
      dayOfWeek: 2,
      minuteOfDay: 1020,
      month: 7,
      dayOfMonth: 1,
      year: 2025,
    }
    expect(isPublicHoliday(canadaDay)).toBe(true)
    expect(overlapsMembership(sched, canadaDay)).toBe(false)
    expect(
      overlapsMembership(sched, {
        ...canadaDay,
        month: 7,
        dayOfMonth: 2,
        dayOfWeek: 3,
      }),
    ).toBe(true)
  })

  it('inverted Sunday and holidays', () => {
    const sched: Schedule = {
      v: 1,
      status: 'ok',
      source: 'anytime except Sun',
      inverted: true,
      windows: [
        {
          days: [0],
          startMinute: 0,
          endMinute: 1439,
          crossesMidnight: false,
        },
      ],
      flags: { exceptPublicHolidays: true },
    }
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 0,
        minuteOfDay: 600,
        month: 6,
        dayOfMonth: 8,
        year: 2025,
      }),
    ).toBe(false)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 1,
        minuteOfDay: 600,
        month: 6,
        dayOfMonth: 9,
        year: 2025,
      }),
    ).toBe(true)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 2,
        minuteOfDay: 600,
        month: 7,
        dayOfMonth: 1,
        year: 2025,
      }),
    ).toBe(false)
  })

  it('winter month range on window', () => {
    const sched: Schedule = {
      v: 1,
      status: 'ok',
      source: 'Dec–Mar',
      windows: [
        {
          days: [0, 1, 2, 3, 4, 5, 6],
          startMinute: 0,
          endMinute: 1439,
          crossesMidnight: false,
          calendar: {
            monthRanges: [
              { startMonth: 12, startDay: 1, endMonth: 3, endDay: 31 },
            ],
          },
        },
      ],
    }
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 3,
        minuteOfDay: 720,
        month: 2,
        dayOfMonth: 10,
        year: 2025,
      }),
    ).toBe(true)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 3,
        minuteOfDay: 720,
        month: 8,
        dayOfMonth: 10,
        year: 2025,
      }),
    ).toBe(false)
  })

  it('partial uses parsed windows', () => {
    const sched: Schedule = {
      v: 1,
      status: 'partial',
      source: 'partial',
      windows: MON_FRI_8_6.windows,
      unparsedClauses: ['some other clause'],
    }
    expect(overlapsMembership(sched, TUE_3PM)).toBe(true)
    expect(overlapsMembership(sched, SAT_3PM)).toBe(false)
  })
})

describe('slotInCalendar dayOfMonth last', () => {
  it('matches last day of February in leap year', () => {
    const cal = { dayOfMonthRanges: [{ start: 28, end: 'last' as const }] }
    expect(
      slotInCalendar(cal, {
        dayOfWeek: 0,
        minuteOfDay: 0,
        month: 2,
        dayOfMonth: 29,
        year: 2024,
      }),
    ).toBe(true)
    expect(
      slotInCalendar(cal, {
        dayOfWeek: 0,
        minuteOfDay: 0,
        month: 2,
        dayOfMonth: 28,
        year: 2025,
      }),
    ).toBe(true)
    expect(
      slotInCalendar(cal, {
        dayOfWeek: 0,
        minuteOfDay: 0,
        month: 2,
        dayOfMonth: 27,
        year: 2025,
      }),
    ).toBe(false)
  })
})

describe('ruleMatchesFilter', () => {
  it('no_parking matches on Tue 3pm', () => {
    expect(ruleMatchesFilter(feature('no_parking', MON_FRI_8_6), TUE_3PM, true)).toBe(
      true,
    )
  })

  it('failed always included in filter results', () => {
    const f = feature('no_parking', {
      v: 1,
      status: 'failed',
      source: 'seasonal',
      windows: [],
    })
    expect(ruleMatchesFilter(f, TUE_3PM, false)).toBe(true)
    expect(ruleMatchesFilter(f, TUE_3PM, true)).toBe(true)
  })

  it('missing schedule respects includeUnknown', () => {
    const f = feature('no_parking', undefined)
    expect(ruleMatchesFilter(f, TUE_3PM, false)).toBe(false)
    expect(ruleMatchesFilter(f, TUE_3PM, true)).toBe(true)
  })
})

describe('overlapsMembershipInRange', () => {
  it('matches range inside Mon–Fri 8–6', () => {
    expect(overlapsMembershipInRange(MON_FRI_8_6, TUE_3PM, 960)).toBe(true)
  })
})

describe('evaluateAtSlot', () => {
  it('no_parking Tue 3pm → restricted', () => {
    const r = evaluateAtSlot(
      feature('no_parking', MON_FRI_8_6).properties,
      TUE_3PM,
      true,
    )
    expect(r).toEqual({
      visible: true,
      polarity: 'restricted',
      unparsed: false,
    })
  })

  it('no_parking Sat 3pm → inactive', () => {
    const r = evaluateAtSlot(
      feature('no_parking', MON_FRI_8_6).properties,
      SAT_3PM,
      true,
    )
    expect(r).toEqual({
      visible: true,
      polarity: 'inactive',
      unparsed: false,
    })
  })

  it('failed visible with unknown when includeUnknown false', () => {
    const r = evaluateAtSlot(
      feature('no_parking', { v: 1, status: 'failed', source: 'x', windows: [] })
        .properties,
      TUE_3PM,
      false,
    )
    expect(r.visible).toBe(true)
    expect(r.polarity).toBe('unknown')
    expect(r.failed).toBe(true)
  })

  it('partial uses windows for polarity', () => {
    const r = evaluateAtSlot(
      feature('no_parking', {
        v: 1,
        status: 'partial',
        source: 'x',
        windows: MON_FRI_8_6.windows,
        unparsedClauses: ['extra'],
      }).properties,
      TUE_3PM,
      true,
    )
    expect(r.visible).toBe(true)
    expect(r.polarity).toBe('restricted')
    expect(r.partial).toBe(true)
    expect(r.unparsed).toBe(true)
  })

  it('missing schedule hidden when toggle off', () => {
    const r = evaluateAtSlot(
      feature('no_parking', undefined).properties,
      TUE_3PM,
      false,
    )
    expect(r.visible).toBe(false)
  })
})

describe('evaluateInRange', () => {
  it('restricted_periods permitted when range fully inside window', () => {
    const r = evaluateInRange(
      feature('restricted_periods', MON_FRI_8_6).properties,
      { ...TUE_3PM, minuteOfDay: 540 },
      600,
      true,
    )
    expect(r.polarity).toBe('permitted')
  })
})

describe('scheduleStatusHints', () => {
  it('includes failed and partial hints', () => {
    expect(
      scheduleStatusHints({ v: 1, status: 'failed', source: 'x', windows: [] }),
    ).toEqual([
      {
        kind: 'failed',
        text: 'Schedule not parsed — times may be incomplete',
      },
    ])
    const partial = scheduleStatusHints({
      v: 1,
      status: 'partial',
      source: 'x',
      windows: [],
      unparsedClauses: ['clause a'],
    })
    expect(partial[0]?.kind).toBe('partial')
    expect(partial[0]?.title).toBe('clause a')
  })
})

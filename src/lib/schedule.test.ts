import { describe, expect, it } from 'vitest'
import type { ParkingFeature } from '../types/parking'
import {
  evaluateAtSlot,
  overlapsMembership,
  ruleMatchesFilter,
  type Schedule,
  type Slot,
} from './schedule'

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
}

const SAT_3PM: Slot = {
  dayOfWeek: 6,
  minuteOfDay: 900,
  month: 5,
  dayOfMonth: 24,
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

  it('returns true for anytime', () => {
    const sched: Schedule = { v: 1, status: 'anytime', source: 'anytime' }
    expect(overlapsMembership(sched, SAT_3PM)).toBe(true)
  })

  it('returns false for failed', () => {
    const sched: Schedule = { v: 1, status: 'failed', source: 'Apr–Nov' }
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
      }),
    ).toBe(true)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 1,
        minuteOfDay: 300,
        month: 1,
        dayOfMonth: 1,
      }),
    ).toBe(true)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 1,
        minuteOfDay: 720,
        month: 1,
        dayOfMonth: 1,
      }),
    ).toBe(false)
  })

  it('wraps when end <= start without midnight flag', () => {
    const sched: Schedule = {
      v: 1,
      status: 'ok',
      source: 'wrap',
      windows: [
        {
          days: [2],
          startMinute: 1320,
          endMinute: 360,
          crossesMidnight: false,
        },
      ],
    }
    expect(overlapsMembership(sched, TUE_3PM)).toBe(false)
    expect(
      overlapsMembership(sched, {
        dayOfWeek: 2,
        minuteOfDay: 1380,
        month: 1,
        dayOfMonth: 1,
      }),
    ).toBe(true)
  })
})

describe('ruleMatchesFilter', () => {
  it('no_parking matches on Tue 3pm', () => {
    const f = feature('no_parking', MON_FRI_8_6)
    expect(ruleMatchesFilter(f, TUE_3PM, true)).toBe(true)
  })

  it('no_parking does not match on Sat 3pm', () => {
    const f = feature('no_parking', MON_FRI_8_6)
    expect(ruleMatchesFilter(f, SAT_3PM, true)).toBe(false)
  })

  it('restricted_periods matches on Tue 3pm', () => {
    const f = feature('restricted_periods', MON_FRI_8_6)
    expect(ruleMatchesFilter(f, TUE_3PM, true)).toBe(true)
  })

  it('restricted_periods does not match on Sat 3pm', () => {
    const f = feature('restricted_periods', MON_FRI_8_6)
    expect(ruleMatchesFilter(f, SAT_3PM, true)).toBe(false)
  })

  it('failed hidden when includeUnknown false', () => {
    const f = feature('no_parking', {
      v: 1,
      status: 'failed',
      source: 'seasonal',
    })
    expect(ruleMatchesFilter(f, TUE_3PM, false)).toBe(false)
    expect(ruleMatchesFilter(f, TUE_3PM, true)).toBe(true)
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

  it('restricted_periods Tue 3pm → permitted', () => {
    const r = evaluateAtSlot(
      feature('restricted_periods', MON_FRI_8_6).properties,
      TUE_3PM,
      true,
    )
    expect(r).toEqual({
      visible: true,
      polarity: 'permitted',
      unparsed: false,
    })
  })

  it('restricted_periods Sat 3pm → not_permitted', () => {
    const r = evaluateAtSlot(
      feature('restricted_periods', MON_FRI_8_6).properties,
      SAT_3PM,
      true,
    )
    expect(r).toEqual({
      visible: true,
      polarity: 'not_permitted',
      unparsed: false,
    })
  })

  it('anytime no_parking → restricted', () => {
    const r = evaluateAtSlot(
      feature('no_parking', { v: 1, status: 'anytime', source: 'any' })
        .properties,
      SAT_3PM,
      true,
    )
    expect(r.polarity).toBe('restricted')
    expect(r.visible).toBe(true)
  })

  it('anytime restricted_periods → permitted', () => {
    const r = evaluateAtSlot(
      feature('restricted_periods', {
        v: 1,
        status: 'anytime',
        source: 'any',
      }).properties,
      SAT_3PM,
      true,
    )
    expect(r.polarity).toBe('permitted')
    expect(r.visible).toBe(true)
  })

  it('failed hidden when toggle off', () => {
    const r = evaluateAtSlot(
      feature('no_parking', { v: 1, status: 'failed', source: 'x' })
        .properties,
      TUE_3PM,
      false,
    )
    expect(r.visible).toBe(false)
    expect(r.polarity).toBe('unknown')
  })

  it('partial shown as unknown when toggle on', () => {
    const r = evaluateAtSlot(
      feature('no_parking', {
        v: 1,
        status: 'partial',
        source: 'x',
        windows: MON_FRI_8_6.windows,
      }).properties,
      TUE_3PM,
      true,
    )
    expect(r.visible).toBe(true)
    expect(r.polarity).toBe('unknown')
    expect(r.unparsed).toBe(true)
  })
})

import type { ParkingFeature, ParkingProperties } from '../types/parking'

export type Schedule = {
  v: 1
  status: 'anytime' | 'ok' | 'partial' | 'failed'
  source: string
  windows?: Array<{
    days: number[]
    startMinute: number
    endMinute: number
    crossesMidnight?: boolean
  }>
  flags?: { exceptPublicHolidays?: boolean }
  unparsedClauses?: string[]
}

export type Slot = {
  dayOfWeek: number
  minuteOfDay: number
  month: number
  dayOfMonth: number
}

export type FilterPolarity =
  | 'restricted'
  | 'permitted'
  | 'not_permitted'
  | 'unknown'
  | 'inactive'

export type SlotEvaluation = {
  visible: boolean
  polarity: FilterPolarity
  unparsed: boolean
}

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

function isRestrictedCategory(
  category: ParkingProperties['schedule_category'],
): boolean {
  return (
    category === 'no_parking' ||
    category === 'no_stopping' ||
    category === 'no_standing'
  )
}

function minuteInWindow(
  minute: number,
  start: number,
  end: number,
  crossesMidnight?: boolean,
): boolean {
  if (crossesMidnight || end <= start) {
    return minute >= start || minute < end
  }
  return minute >= start && minute < end
}

export function overlapsMembership(
  schedule: Schedule | undefined | null,
  slot: Slot,
): boolean {
  if (!schedule || schedule.status === 'failed') return false
  if (schedule.status === 'anytime') return true
  const windows = schedule.windows
  if (!windows?.length) return false

  for (const w of windows) {
    if (!w.days.includes(slot.dayOfWeek)) continue
    if (
      minuteInWindow(
        slot.minuteOfDay,
        w.startMinute,
        w.endMinute,
        w.crossesMidnight,
      )
    ) {
      return true
    }
  }
  return false
}

export function ruleMatchesFilter(
  feature: ParkingFeature,
  slot: Slot,
  includeUnknown: boolean,
): boolean {
  const sched = feature.properties.schedule
  const cat = feature.properties.schedule_category
  const overlaps = overlapsMembership(sched, slot)
  if (sched?.status === 'failed' || sched?.status === 'partial') {
    if (!includeUnknown) return false
    return true
  }
  if (cat === 'restricted_periods') {
    return overlaps
  }
  return overlaps
}

export function evaluateAtSlot(
  props: ParkingProperties,
  slot: Slot,
  includeUnknown: boolean,
): SlotEvaluation {
  const sched = props.schedule
  const cat = props.schedule_category

  if (!sched) {
    if (!includeUnknown) {
      return { visible: false, polarity: 'unknown', unparsed: true }
    }
    return { visible: true, polarity: 'unknown', unparsed: true }
  }

  if (sched.status === 'failed' || sched.status === 'partial') {
    if (!includeUnknown) {
      return { visible: false, polarity: 'unknown', unparsed: true }
    }
    return { visible: true, polarity: 'unknown', unparsed: true }
  }

  const overlaps = overlapsMembership(sched, slot)

  if (sched.status === 'anytime') {
    if (cat === 'restricted_periods') {
      return { visible: true, polarity: 'permitted', unparsed: false }
    }
    return { visible: true, polarity: 'restricted', unparsed: false }
  }

  if (cat === 'restricted_periods') {
    return {
      visible: true,
      polarity: overlaps ? 'permitted' : 'not_permitted',
      unparsed: false,
    }
  }

  if (isRestrictedCategory(cat)) {
    if (overlaps) {
      return { visible: true, polarity: 'restricted', unparsed: false }
    }
    return { visible: true, polarity: 'inactive', unparsed: false }
  }

  return { visible: true, polarity: 'inactive', unparsed: false }
}

export function slotFromDate(d: Date): Slot {
  return {
    dayOfWeek: d.getDay(),
    minuteOfDay: d.getHours() * 60 + d.getMinutes(),
    month: d.getMonth() + 1,
    dayOfMonth: d.getDate(),
  }
}

export function formatSlotLabel(slot: Slot): string {
  const h = Math.floor(slot.minuteOfDay / 60)
  const m = slot.minuteOfDay % 60
  const time = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
  return `${DAY_NAMES[slot.dayOfWeek]} ${time}`
}

export function polarityLabel(
  polarity: FilterPolarity,
  category: ParkingProperties['schedule_category'],
): string {
  switch (polarity) {
    case 'restricted':
      if (category === 'no_stopping') return 'No stopping (active)'
      if (category === 'no_standing') return 'No standing (active)'
      return 'No parking (active)'
    case 'permitted':
      return 'Parking allowed'
    case 'not_permitted':
      return 'Not in permitted window'
    case 'inactive':
      return 'Restriction not active at this time'
    case 'unknown':
      return 'Schedule not parsed'
  }
}

export function enrichFeatureCollection(
  data: import('../types/parking').ParkingFeatureCollection,
  slot: Slot,
  includeUnknown: boolean,
): import('../types/parking').ParkingFeatureCollection {
  const features = data.features
    .map((feature) => {
      const evaluation = evaluateAtSlot(
        feature.properties,
        slot,
        includeUnknown,
      )
      return {
        ...feature,
        properties: {
          ...feature.properties,
          _polarity: evaluation.polarity,
          _visible: evaluation.visible,
          _unparsed: evaluation.unparsed,
        },
      }
    })
    .filter((f) => f.properties._visible !== false)

  return { type: 'FeatureCollection', features }
}

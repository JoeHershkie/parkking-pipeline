import { slotInCalendar } from './calendar'
import { isPublicHoliday } from './publicHolidays'
import type { Schedule, Slot, TimeWindow } from './types'

export function minuteInWindow(
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

/** Half-open [aStart, aEnd) overlaps [bStart, bEnd) on the same day (no wrap). */
function halfOpenRangesOverlap(
  aStart: number,
  aEnd: number,
  bStart: number,
  bEnd: number,
): boolean {
  return aStart < bEnd && bStart < aEnd
}

export function windowOverlapsQueryRange(
  qStart: number,
  qEnd: number,
  wStart: number,
  wEnd: number,
  crossesMidnight?: boolean,
): boolean {
  if (crossesMidnight || wEnd <= wStart) {
    return (
      halfOpenRangesOverlap(qStart, qEnd, wStart, 1440) ||
      halfOpenRangesOverlap(qStart, qEnd, 0, wEnd)
    )
  }
  return halfOpenRangesOverlap(qStart, qEnd, wStart, wEnd)
}

function rangeFullyInsideWindow(
  qStart: number,
  qEnd: number,
  wStart: number,
  wEnd: number,
  crossesMidnight?: boolean,
): boolean {
  if (crossesMidnight || wEnd <= wStart) {
    const inEvening =
      qStart >= wStart &&
      qEnd <= 1440 &&
      halfOpenRangesOverlap(qStart, qEnd, wStart, 1440)
    const inMorning =
      qStart >= 0 && qEnd <= wEnd && halfOpenRangesOverlap(qStart, qEnd, 0, wEnd)
    return inEvening || inMorning
  }
  return qStart >= wStart && qEnd <= wEnd
}

function effectiveCalendar(
  window: TimeWindow,
  schedule: Schedule,
): Schedule['calendar'] {
  return window.calendar ?? schedule.calendar
}

function holidayExcludesWindow(schedule: Schedule, slot: Slot): boolean {
  return (
    schedule.flags?.exceptPublicHolidays === true && isPublicHoliday(slot)
  )
}

/** Normal mode: window is an active prohibition period at slot. */
function windowMatchesSlot(
  window: TimeWindow,
  schedule: Schedule,
  slot: Slot,
): boolean {
  if (!slotInCalendar(effectiveCalendar(window, schedule), slot)) return false
  if (!window.days.includes(slot.dayOfWeek)) return false
  if (
    !minuteInWindow(
      slot.minuteOfDay,
      window.startMinute,
      window.endMinute,
      window.crossesMidnight,
    )
  ) {
    return false
  }
  if (holidayExcludesWindow(schedule, slot)) return false
  return true
}

/** Inverted mode: window is an except period (prohibition off). */
function exceptWindowMatchesSlot(
  window: TimeWindow,
  schedule: Schedule,
  slot: Slot,
): boolean {
  if (holidayExcludesWindow(schedule, slot)) return true
  if (!slotInCalendar(effectiveCalendar(window, schedule), slot)) return false
  if (!window.days.includes(slot.dayOfWeek)) return false
  return minuteInWindow(
    slot.minuteOfDay,
    window.startMinute,
    window.endMinute,
    window.crossesMidnight,
  )
}

function windowMatchesSlotInRange(
  window: TimeWindow,
  schedule: Schedule,
  slot: Slot,
  endMinute: number,
): boolean {
  if (!slotInCalendar(effectiveCalendar(window, schedule), slot)) return false
  if (!window.days.includes(slot.dayOfWeek)) return false
  if (
    !windowOverlapsQueryRange(
      slot.minuteOfDay,
      endMinute,
      window.startMinute,
      window.endMinute,
      window.crossesMidnight,
    )
  ) {
    return false
  }
  if (holidayExcludesWindow(schedule, slot)) return false
  return true
}

function exceptWindowMatchesRange(
  window: TimeWindow,
  schedule: Schedule,
  slot: Slot,
  endMinute: number,
): boolean {
  if (holidayExcludesWindow(schedule, slot)) return true
  if (!slotInCalendar(effectiveCalendar(window, schedule), slot)) return false
  if (!window.days.includes(slot.dayOfWeek)) return false
  return windowOverlapsQueryRange(
    slot.minuteOfDay,
    endMinute,
    window.startMinute,
    window.endMinute,
    window.crossesMidnight,
  )
}

function matchesWithWindows(
  schedule: Schedule,
  slot: Slot,
  inverted: boolean,
): boolean {
  const windows = schedule.windows ?? []
  if (inverted) {
    if (!slotInCalendar(schedule.calendar, slot)) return false
    for (const w of windows) {
      if (exceptWindowMatchesSlot(w, schedule, slot)) return false
    }
    return true
  }
  for (const w of windows) {
    if (windowMatchesSlot(w, schedule, slot)) return true
  }
  return false
}

function matchesWithWindowsInRange(
  schedule: Schedule,
  slot: Slot,
  endMinute: number,
  inverted: boolean,
): boolean {
  const windows = schedule.windows ?? []
  if (inverted) {
    if (!slotInCalendar(schedule.calendar, slot)) return false
    for (const w of windows) {
      if (exceptWindowMatchesRange(w, schedule, slot, endMinute)) return false
    }
    return true
  }
  for (const w of windows) {
    if (windowMatchesSlotInRange(w, schedule, slot, endMinute)) return true
  }
  return false
}

export function overlapsMembership(
  schedule: Schedule | undefined | null,
  slot: Slot,
): boolean {
  if (!schedule || schedule.status === 'failed') return false

  if (schedule.status === 'anytime') {
    return slotInCalendar(schedule.calendar, slot)
  }

  if (schedule.status === 'ok' || schedule.status === 'partial') {
    const windows = schedule.windows ?? []
    if (windows.length === 0) return false
    return matchesWithWindows(schedule, slot, schedule.inverted === true)
  }

  return false
}

export function overlapsMembershipInRange(
  schedule: Schedule | undefined | null,
  slot: Slot,
  endMinute: number,
): boolean {
  if (!schedule || schedule.status === 'failed') return false

  if (schedule.status === 'anytime') {
    return slotInCalendar(schedule.calendar, slot)
  }

  if (schedule.status === 'ok' || schedule.status === 'partial') {
    const windows = schedule.windows ?? []
    if (windows.length === 0) return false
    return matchesWithWindowsInRange(
      schedule,
      slot,
      endMinute,
      schedule.inverted === true,
    )
  }

  return false
}

export function membershipFullyCoversRange(
  schedule: Schedule,
  slot: Slot,
  endMinute: number,
): boolean {
  if (schedule.status === 'failed') return false
  if (schedule.status === 'anytime') {
    return slotInCalendar(schedule.calendar, slot)
  }

  const windows = schedule.windows ?? []
  if (windows.length === 0) return false

  if (schedule.inverted) {
    if (!slotInCalendar(schedule.calendar, slot)) return false
    for (const w of windows) {
      if (exceptWindowMatchesRange(w, schedule, slot, endMinute)) return false
    }
    return true
  }

  const qStart = slot.minuteOfDay
  const qEnd = endMinute
  const day = slot.dayOfWeek

  for (const w of windows) {
    if (!slotInCalendar(effectiveCalendar(w, schedule), slot)) continue
    if (!w.days.includes(day)) continue
    if (holidayExcludesWindow(schedule, slot)) continue
    if (
      rangeFullyInsideWindow(
        qStart,
        qEnd,
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

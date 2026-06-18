import type { Calendar, Slot } from './types'

function monthDayValue(month: number, day: number): number {
  return month * 32 + day
}

function inMonthRange(
  slot: Slot,
  range: NonNullable<Calendar['monthRanges']>[number],
): boolean {
  const slotMd = monthDayValue(slot.month, slot.dayOfMonth)
  const startMd = monthDayValue(range.startMonth, range.startDay)
  const endMd = monthDayValue(range.endMonth, range.endDay)
  if (startMd <= endMd) {
    return slotMd >= startMd && slotMd <= endMd
  }
  return slotMd >= startMd || slotMd <= endMd
}

function lastDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

function resolveYear(slot: Slot): number {
  return slot.year ?? new Date().getFullYear()
}

function inDayOfMonthRange(
  slot: Slot,
  range: NonNullable<Calendar['dayOfMonthRanges']>[number],
): boolean {
  const end =
    range.end === 'last'
      ? lastDayOfMonth(resolveYear(slot), slot.month)
      : range.end
  return slot.dayOfMonth >= range.start && slot.dayOfMonth <= end
}

/** True when calendar is absent/empty or every present predicate passes. */
export function slotInCalendar(
  calendar: Calendar | undefined,
  slot: Slot,
): boolean {
  if (!calendar) return true

  const { monthRanges, dayOfMonthRanges, months } = calendar
  const hasMonthRanges = monthRanges != null && monthRanges.length > 0
  const hasDayRanges =
    dayOfMonthRanges != null && dayOfMonthRanges.length > 0
  const hasMonths = months != null && months.length > 0

  if (!hasMonthRanges && !hasDayRanges && !hasMonths) return true

  if (hasMonthRanges && !monthRanges!.some((r) => inMonthRange(slot, r))) {
    return false
  }

  if (
    hasDayRanges &&
    !dayOfMonthRanges!.some((r) => inDayOfMonthRange(slot, r))
  ) {
    return false
  }

  if (hasMonths && !months!.includes(slot.month)) {
    return false
  }

  return true
}

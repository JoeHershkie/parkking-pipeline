import type { Slot } from './types'

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

export function slotFromDate(d: Date): Slot {
  return {
    dayOfWeek: d.getDay(),
    minuteOfDay: d.getHours() * 60 + d.getMinutes(),
    month: d.getMonth() + 1,
    dayOfMonth: d.getDate(),
    year: d.getFullYear(),
  }
}

export function slotToDateString(slot: Slot): string {
  const y = slot.year ?? new Date().getFullYear()
  const m = String(slot.month).padStart(2, '0')
  const d = String(slot.dayOfMonth).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function slotFromDateString(
  value: string,
  minuteOfDay: number,
): Slot {
  const [y, m, d] = value.split('-').map(Number)
  const date = new Date(y, m - 1, d, 0, 0, 0, 0)
  return {
    year: y,
    month: m,
    dayOfMonth: d,
    dayOfWeek: date.getDay(),
    minuteOfDay,
  }
}

export function formatSlotLabel(
  slot: Slot,
  endMinuteOfDay?: number | null,
): string {
  const h = Math.floor(slot.minuteOfDay / 60)
  const m = slot.minuteOfDay % 60
  const startTime = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
  const day = DAY_NAMES[slot.dayOfWeek]
  const datePart = slotToDateString(slot)

  if (endMinuteOfDay == null || endMinuteOfDay <= slot.minuteOfDay) {
    return `${day} ${datePart} ${startTime}`
  }

  const eh = Math.floor(endMinuteOfDay / 60)
  const em = endMinuteOfDay % 60
  const endTime = `${String(eh).padStart(2, '0')}:${String(em).padStart(2, '0')}`
  return `${day} ${datePart} ${startTime}–${endTime}`
}

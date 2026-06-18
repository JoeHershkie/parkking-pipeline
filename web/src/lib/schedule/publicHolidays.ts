import Holidays from 'date-holidays'
import type { Slot } from './types'

const cache = new Map<number, Holidays>()

function holidaysForYear(year: number): Holidays {
  let hd = cache.get(year)
  if (!hd) {
    hd = new Holidays('CA', 'ON')
    cache.set(year, hd)
  }
  return hd
}

/** Ontario statutory public holidays (observed when applicable). */
export function isPublicHoliday(slot: Slot): boolean {
  if (slot.year == null) return false
  const result = holidaysForYear(slot.year).isHoliday(
    new Date(slot.year, slot.month - 1, slot.dayOfMonth),
  )
  if (!result) return false
  const list = Array.isArray(result) ? result : [result]
  return list.some((h) => h.type === 'public')
}

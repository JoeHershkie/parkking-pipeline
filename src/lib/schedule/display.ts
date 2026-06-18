import type { Calendar, Schedule, TimeWindow } from './types'

const MONTH_SHORT = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

function formatMonthDay(month: number, day: number): string {
  return `${MONTH_SHORT[month - 1]} ${day}`
}

export function formatCalendarSummary(calendar: Calendar | undefined): string | null {
  if (!calendar) return null
  const parts: string[] = []

  if (calendar.monthRanges?.length) {
    for (const r of calendar.monthRanges) {
      parts.push(
        `${formatMonthDay(r.startMonth, r.startDay)}–${formatMonthDay(r.endMonth, r.endDay)}`,
      )
    }
  }

  if (calendar.dayOfMonthRanges?.length) {
    for (const r of calendar.dayOfMonthRanges) {
      const end = r.end === 'last' ? 'last' : String(r.end)
      parts.push(
        r.start === r.end || end === String(r.start)
          ? `${ordinal(r.start)} of month`
          : `${ordinal(r.start)}–${end === 'last' ? 'last' : ordinal(Number(end))} of month`,
      )
    }
  }

  if (calendar.months?.length) {
    parts.push(
      calendar.months.map((m) => MONTH_SHORT[m - 1]).join(', '),
    )
  }

  return parts.length > 0 ? parts.join('; ') : null
}

function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`
}

function formatMinute(minute: number): string {
  const h = Math.floor(minute / 60)
  const m = minute % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function formatDays(days: number[]): string {
  if (days.length === 7) return 'daily'
  if (
    days.length === 5 &&
    [1, 2, 3, 4, 5].every((d) => days.includes(d))
  ) {
    return 'Mon–Fri'
  }
  return days.map((d) => DAY_NAMES[d]).join(', ')
}

function formatWindowBrief(w: TimeWindow): string {
  const time =
    w.startMinute === 0 && w.endMinute >= 1439
      ? 'all day'
      : `${formatMinute(w.startMinute)}–${formatMinute(w.endMinute)}`
  return `${formatDays(w.days)} ${time}`
}

export function formatExceptWindowsSummary(schedule: Schedule): string | null {
  const windows = schedule.windows
  if (!windows?.length) return null
  return windows.map(formatWindowBrief).join('; ')
}

export type ScheduleHint = {
  kind: 'failed' | 'partial' | 'inverted' | 'calendar'
  text: string
  title?: string
}

export function scheduleStatusHints(schedule: Schedule | undefined): ScheduleHint[] {
  if (!schedule) return []
  const hints: ScheduleHint[] = []

  if (schedule.status === 'failed') {
    hints.push({
      kind: 'failed',
      text: 'Schedule not parsed — times may be incomplete',
    })
    return hints
  }

  if (schedule.status === 'partial') {
    const title =
      schedule.unparsedClauses?.length ?
        schedule.unparsedClauses.join('; ')
      : undefined
    hints.push({
      kind: 'partial',
      text: 'Partially parsed',
      title,
    })
  }

  if (schedule.inverted) {
    const except = formatExceptWindowsSummary(schedule)
    hints.push({
      kind: 'inverted',
      text: except ? `Applies except during ${except}` : 'Applies except during listed periods',
    })
  }

  const cal =
    formatCalendarSummary(schedule.calendar) ??
    (schedule.windows ?? [])
      .map((w) => formatCalendarSummary(w.calendar))
      .find((s) => s != null)

  if (cal) {
    hints.push({ kind: 'calendar', text: cal })
  }

  return hints
}

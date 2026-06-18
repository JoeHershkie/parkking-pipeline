export type Calendar = {
  monthRanges?: Array<{
    startMonth: number
    startDay: number
    endMonth: number
    endDay: number
  }>
  dayOfMonthRanges?: Array<{
    start: number
    end: number | 'last'
  }>
  months?: number[]
}

export type TimeWindow = {
  days: number[]
  startMinute: number
  endMinute: number
  crossesMidnight?: boolean
  calendar?: Calendar
}

export type Schedule = {
  v: 1
  status: 'anytime' | 'ok' | 'partial' | 'failed'
  source: string
  windows: TimeWindow[]
  calendar?: Calendar
  flags?: { exceptPublicHolidays?: boolean }
  inverted?: boolean
  unparsedClauses?: string[]
}

export type Slot = {
  dayOfWeek: number
  minuteOfDay: number
  month: number
  dayOfMonth: number
  year?: number
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
  partial?: boolean
  failed?: boolean
}

import { useId, useState } from 'react'
import {
  formatSlotLabel,
  slotFromDate,
  slotFromDateString,
  slotToDateString,
  type Slot,
} from '../lib/schedule'
import './TimeFilterBar.css'

function minuteToTimeValue(minute: number): string {
  const h = Math.floor(minute / 60)
  const m = minute % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function timeValueToMinute(value: string): number {
  const [h, m] = value.split(':').map(Number)
  return h * 60 + m
}

export interface TimeFilterState {
  slot: Slot
  endMinuteOfDay: number | null
  includeUnknown: boolean
}

interface TimeFilterBarProps {
  slot: Slot
  endMinuteOfDay: number | null
  includeUnknown: boolean
  onChange: (state: TimeFilterState) => void
  disabled?: boolean
}

export function TimeFilterBar({
  slot,
  endMinuteOfDay,
  includeUnknown,
  onChange,
  disabled,
}: TimeFilterBarProps) {
  const dateId = useId()
  const startTimeId = useId()
  const endTimeId = useId()
  const unknownId = useId()
  const [rangeWarning, setRangeWarning] = useState<string | null>(null)

  const effectiveEnd =
    endMinuteOfDay != null && endMinuteOfDay > slot.minuteOfDay
      ? endMinuteOfDay
      : null

  function emit(
    patch: Partial<TimeFilterState>,
    warning: string | null = null,
  ) {
    setRangeWarning(warning)
    onChange({
      slot,
      endMinuteOfDay,
      includeUnknown,
      ...patch,
    })
  }

  function updateSlot(patch: Partial<Slot>) {
    const nextSlot = { ...slot, ...patch }
    let nextEnd = endMinuteOfDay
    let warning: string | null = null
    if (nextEnd != null && nextEnd <= nextSlot.minuteOfDay) {
      warning = 'End time must be after start time; using start time only.'
      nextEnd = null
    }
    emit({ slot: nextSlot, endMinuteOfDay: nextEnd }, warning)
  }

  function handleDateChange(value: string) {
    if (!value) return
    updateSlot(slotFromDateString(value, slot.minuteOfDay))
  }

  function handleEndTimeChange(value: string) {
    if (!value) {
      emit({ endMinuteOfDay: null })
      return
    }
    const minute = timeValueToMinute(value)
    if (minute <= slot.minuteOfDay) {
      emit(
        { endMinuteOfDay: null },
        'End time must be after start time; using start time only.',
      )
      return
    }
    emit({ endMinuteOfDay: minute }, null)
  }

  function handleUseNow() {
    const now = slotFromDate(new Date())
    let nextEnd = endMinuteOfDay
    let warning: string | null = null
    if (nextEnd != null && nextEnd <= now.minuteOfDay) {
      warning = 'End time must be after start time; using start time only.'
      nextEnd = null
    }
    emit({ slot: now, endMinuteOfDay: nextEnd }, warning)
  }

  return (
    <div className="time-filter-bar" aria-label="Filter by date and time">
      <div className="time-filter-controls">
        <label htmlFor={dateId}>
          Date
          <input
            id={dateId}
            type="date"
            value={slotToDateString(slot)}
            disabled={disabled}
            onChange={(e) => handleDateChange(e.target.value)}
          />
        </label>

        <label htmlFor={startTimeId}>
          Start time
          <input
            id={startTimeId}
            type="time"
            value={minuteToTimeValue(slot.minuteOfDay)}
            disabled={disabled}
            onChange={(e) =>
              updateSlot({ minuteOfDay: timeValueToMinute(e.target.value) })
            }
          />
        </label>

        <label htmlFor={endTimeId}>
          End time (optional)
          <input
            id={endTimeId}
            type="time"
            value={
              endMinuteOfDay != null
                ? minuteToTimeValue(endMinuteOfDay)
                : ''
            }
            disabled={disabled}
            onChange={(e) => handleEndTimeChange(e.target.value)}
          />
        </label>

        <button
          type="button"
          className="time-filter-now"
          disabled={disabled}
          onClick={handleUseNow}
        >
          Use now
        </button>

        <label htmlFor={unknownId} className="time-filter-unknown">
          <input
            id={unknownId}
            type="checkbox"
            checked={includeUnknown}
            disabled={disabled}
            onChange={(e) =>
              emit({ includeUnknown: e.target.checked })
            }
          />
          Show rules without schedule data
        </label>
      </div>
      <p className="time-filter-status" role="status">
        Showing rules for{' '}
        <strong>{formatSlotLabel(slot, effectiveEnd)}</strong>
        {rangeWarning && (
          <span className="time-filter-warning"> ({rangeWarning})</span>
        )}
      </p>
    </div>
  )
}

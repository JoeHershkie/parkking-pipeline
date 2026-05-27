import { useId } from 'react'
import { formatSlotLabel, slotFromDate, type Slot } from '../lib/schedule'
import './TimeFilterBar.css'

const DAY_OPTIONS = [
  { value: 0, label: 'Sunday' },
  { value: 1, label: 'Monday' },
  { value: 2, label: 'Tuesday' },
  { value: 3, label: 'Wednesday' },
  { value: 4, label: 'Thursday' },
  { value: 5, label: 'Friday' },
  { value: 6, label: 'Saturday' },
] as const

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
  includeUnknown: boolean
}

interface TimeFilterBarProps {
  slot: Slot
  includeUnknown: boolean
  onChange: (state: TimeFilterState) => void
  disabled?: boolean
}

export function TimeFilterBar({
  slot,
  includeUnknown,
  onChange,
  disabled,
}: TimeFilterBarProps) {
  const dayId = useId()
  const timeId = useId()
  const unknownId = useId()

  function updateSlot(patch: Partial<Slot>) {
    onChange({
      slot: { ...slot, ...patch },
      includeUnknown,
    })
  }

  function handleUseNow() {
    onChange({
      slot: slotFromDate(new Date()),
      includeUnknown,
    })
  }

  return (
    <div className="time-filter-bar" aria-label="Filter by day and time">
      <div className="time-filter-controls">
        <label htmlFor={dayId}>
          Day
          <select
            id={dayId}
            value={slot.dayOfWeek}
            disabled={disabled}
            onChange={(e) =>
              updateSlot({ dayOfWeek: Number(e.target.value) })
            }
          >
            {DAY_OPTIONS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>

        <label htmlFor={timeId}>
          Time
          <input
            id={timeId}
            type="time"
            value={minuteToTimeValue(slot.minuteOfDay)}
            disabled={disabled}
            onChange={(e) =>
              updateSlot({ minuteOfDay: timeValueToMinute(e.target.value) })
            }
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
              onChange({ slot, includeUnknown: e.target.checked })
            }
          />
          Show unparsed schedules
        </label>
      </div>
      <p className="time-filter-status" role="status">
        Showing rules for <strong>{formatSlotLabel(slot)}</strong>
      </p>
    </div>
  )
}

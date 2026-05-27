import type { ParkingProperties, ScheduleCategory } from '../types/parking'

const CATEGORY_LABELS: Record<ScheduleCategory, string> = {
  no_parking: 'No parking',
  no_stopping: 'No stopping',
  no_standing: 'No standing',
  restricted_periods: 'Restricted periods',
}

export function scheduleCategoryLabel(
  category: ParkingProperties['schedule_category'],
): string {
  if (category in CATEGORY_LABELS) {
    return CATEGORY_LABELS[category as ScheduleCategory]
  }
  return String(category).replace(/_/g, ' ')
}

export function formatMax(max: ParkingProperties['max']): string | null {
  if (max == null || max === '') return null
  return String(max)
}

export function formatMaxStay(
  max: ParkingProperties['max'],
  maxMinutes: ParkingProperties['maxMinutes'],
): string | null {
  const text = formatMax(max)
  if (text) return text
  if (maxMinutes != null && maxMinutes > 0) {
    if (maxMinutes % 60 === 0 && maxMinutes >= 60) {
      const hours = maxMinutes / 60
      return hours === 1 ? '1 hour' : `${hours} hours`
    }
    return `${maxMinutes} min`
  }
  return null
}

export function ruleFeatureKey(props: ParkingProperties): string {
  return [
    props.Highway,
    props.Rule,
    props.Side,
    props.schedule_category,
    props.max ?? '',
  ].join('|')
}

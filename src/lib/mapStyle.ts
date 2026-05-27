import type { ExpressionSpecification } from 'maplibre-gl'
import type { ScheduleCategory } from '../types/parking'

export const CATEGORY_COLORS: Record<ScheduleCategory, string> = {
  no_parking: '#dc2626',
  no_stopping: '#ea580c',
  no_standing: '#ca8a04',
  restricted_periods: '#7c3aed',
}

export const DEFAULT_LINE_COLOR = '#6b7280'

export const POLARITY_COLORS = {
  permitted: '#16a34a',
  not_permitted: '#9ca3af',
  inactive: '#9ca3af',
  unknown: '#d97706',
} as const

const categoryColorMatch: ExpressionSpecification = [
  'match',
  ['get', 'schedule_category'],
  'no_parking',
  CATEGORY_COLORS.no_parking,
  'no_stopping',
  CATEGORY_COLORS.no_stopping,
  'no_standing',
  CATEGORY_COLORS.no_standing,
  'restricted_periods',
  CATEGORY_COLORS.restricted_periods,
  DEFAULT_LINE_COLOR,
]

export const lineColorExpression: ExpressionSpecification = [
  'case',
  ['has', '_polarity'],
  [
    'match',
    ['get', '_polarity'],
    'permitted',
    POLARITY_COLORS.permitted,
    'not_permitted',
    POLARITY_COLORS.not_permitted,
    'inactive',
    POLARITY_COLORS.inactive,
    'unknown',
    POLARITY_COLORS.unknown,
    'restricted',
    categoryColorMatch,
    categoryColorMatch,
  ],
  categoryColorMatch,
]

export const lineOpacityExpression: ExpressionSpecification = [
  'case',
  ['==', ['get', '_polarity'], 'inactive'],
  0.25,
  0.85,
]

export const lineWidthExpression: ExpressionSpecification = [
  'case',
  ['boolean', ['feature-state', 'hover'], false],
  4,
  2,
]

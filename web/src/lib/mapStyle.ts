import type { ExpressionSpecification } from 'maplibre-gl'

/** Map line colors: allowed, restricted, or ambiguous schedule. */
export const LINE_COLORS = {
  allowed: '#16a34a',
  restricted: '#dc2626',
  ambiguous: '#d97706',
} as const

export const DEFAULT_LINE_COLOR = '#6b7280'

/** @deprecated Use LINE_COLORS — kept for components that key off polarity names. */
export const POLARITY_COLORS = {
  permitted: LINE_COLORS.allowed,
  not_permitted: LINE_COLORS.restricted,
  inactive: LINE_COLORS.allowed,
  restricted: LINE_COLORS.restricted,
  unknown: LINE_COLORS.ambiguous,
} as const

const polarityColorMatch: ExpressionSpecification = [
  'match',
  ['get', '_polarity'],
  'permitted',
  LINE_COLORS.allowed,
  'inactive',
  LINE_COLORS.allowed,
  'not_permitted',
  LINE_COLORS.restricted,
  'restricted',
  LINE_COLORS.restricted,
  DEFAULT_LINE_COLOR,
]

export const lineColorExpression: ExpressionSpecification = [
  'case',
  [
    'any',
    ['==', ['get', '_polarity'], 'unknown'],
    ['==', ['get', '_unparsed'], true],
  ],
  LINE_COLORS.ambiguous,
  ['has', '_polarity'],
  polarityColorMatch,
  DEFAULT_LINE_COLOR,
]

export const lineOpacityExpression: ExpressionSpecification = ['literal', 0.85]

export const lineWidthExpression: ExpressionSpecification = [
  'case',
  ['boolean', ['feature-state', 'hover'], false],
  4,
  2,
]

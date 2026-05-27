import {
  CATEGORY_COLORS,
  POLARITY_COLORS,
} from '../lib/mapStyle'
import { formatSlotLabel, type Slot } from '../lib/schedule'
import { scheduleCategoryLabel } from '../lib/labels'
import type { ScheduleCategory } from '../types/parking'
import './Legend.css'

const LEGEND_CATEGORIES: ScheduleCategory[] = [
  'no_parking',
  'no_stopping',
  'no_standing',
  'restricted_periods',
]

const POLARITY_LEGEND = [
  { key: 'permitted', label: 'Parking allowed', color: POLARITY_COLORS.permitted },
  {
    key: 'restricted',
    label: 'Restriction active',
    color: CATEGORY_COLORS.no_parking,
  },
  {
    key: 'not_permitted',
    label: 'Not in permitted window',
    color: POLARITY_COLORS.not_permitted,
  },
  {
    key: 'inactive',
    label: 'Restriction not active',
    color: POLARITY_COLORS.inactive,
  },
  {
    key: 'unknown',
    label: 'Schedule not parsed',
    color: POLARITY_COLORS.unknown,
  },
] as const

interface LegendProps {
  featureCount: number | null
  visibleCount: number | null
  slotLabel: Slot
}

export function Legend({
  featureCount,
  visibleCount,
  slotLabel,
}: LegendProps) {
  return (
    <div className="map-legend" aria-label="Map legend">
      <h3>At {formatSlotLabel(slotLabel)}</h3>
      <ul>
        {POLARITY_LEGEND.map((item) => (
          <li key={item.key}>
            <span
              className="legend-swatch"
              style={{ background: item.color }}
            />
            {item.label}
          </li>
        ))}
      </ul>

      <h3 className="legend-section">Restriction type (active)</h3>
      <ul>
        {LEGEND_CATEGORIES.map((cat) => (
          <li key={cat}>
            <span
              className="legend-swatch"
              style={{ background: CATEGORY_COLORS[cat] }}
            />
            {scheduleCategoryLabel(cat)}
          </li>
        ))}
      </ul>

      {featureCount != null && (
        <p className="legend-coverage">
          Showing{' '}
          <strong>
            {(visibleCount ?? featureCount).toLocaleString()}
          </strong>{' '}
          of {featureCount.toLocaleString()} geocoded curb segments at the
          selected time.
        </p>
      )}
    </div>
  )
}

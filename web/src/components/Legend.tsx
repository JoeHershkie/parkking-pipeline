import { LINE_COLORS } from '../lib/mapStyle'
import { formatSlotLabel, type Slot } from '../lib/schedule'
import './Legend.css'

const LEGEND_ITEMS = [
  { label: 'Parking allowed', color: LINE_COLORS.allowed },
  {
    label: 'No parking, stopping, or standing',
    color: LINE_COLORS.restricted,
  },
  {
    label: 'Schedule unclear or not parsed',
    color: LINE_COLORS.ambiguous,
  },
] as const

function formatDataUpdated(iso: string): string | null {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

interface LegendProps {
  featureCount: number | null
  visibleCount: number | null
  slot: Slot
  endMinuteOfDay: number | null
  dataGeneratedAt?: string | null
  pipelineVersion?: string | null
}

export function Legend({
  featureCount,
  visibleCount,
  slot,
  endMinuteOfDay,
  dataGeneratedAt,
  pipelineVersion,
}: LegendProps) {
  const periodLabel = formatSlotLabel(slot, endMinuteOfDay)
  const timePhrase = endMinuteOfDay != null ? 'period' : 'time'

  return (
    <div className="map-legend" aria-label="Map legend">
      <h3>At {periodLabel}</h3>
      <ul>
        {LEGEND_ITEMS.map((item) => (
          <li key={item.label}>
            <span
              className="legend-swatch"
              style={{ background: item.color }}
            />
            {item.label}
          </li>
        ))}
      </ul>

      {featureCount != null && (
        <p className="legend-coverage">
          Showing{' '}
          <strong>
            {(visibleCount ?? featureCount).toLocaleString()}
          </strong>{' '}
          of {featureCount.toLocaleString()} geocoded curb segments in the
          selected {timePhrase}.
        </p>
      )}

      {dataGeneratedAt && formatDataUpdated(dataGeneratedAt) && (
        <p className="legend-meta">
          Map data generated {formatDataUpdated(dataGeneratedAt)}
          {pipelineVersion ? ` (pipeline ${pipelineVersion})` : ''}.
        </p>
      )}
    </div>
  )
}

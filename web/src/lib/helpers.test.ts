import { describe, expect, it } from 'vitest'
import { boundsFromFeatures } from './bbox'
import { dedupeParkingFeatures } from './dedupeFeatures'
import { popupHtml } from './popupHtml'
import type { ParkingFeature } from '../types/parking'

function feature(
  overrides: Partial<ParkingFeature['properties']> & { Highway: string },
): ParkingFeature {
  return {
    type: 'Feature',
    geometry: {
      type: 'LineString',
      coordinates: [
        [-79.4, 43.65],
        [-79.39, 43.66],
      ],
    },
    properties: {
      Rule: 'Mon–Fri 8am–6pm',
      schedule_category: 'no_parking',
      Side: 'North',
      max: null,
      ...overrides,
    },
  }
}

describe('dedupeParkingFeatures', () => {
  it('removes duplicate rules by identity key', () => {
    const a = feature({ Highway: 'Spadina Rd' })
    const b = feature({ Highway: 'Spadina Rd' })
    const c = feature({ Highway: 'Bloor St W' })
    expect(dedupeParkingFeatures([a, b, c])).toHaveLength(2)
  })

  it('keeps rules that differ on Side', () => {
    const north = feature({ Highway: 'Spadina Rd', Side: 'North' })
    const south = feature({ Highway: 'Spadina Rd', Side: 'South' })
    expect(dedupeParkingFeatures([north, south])).toHaveLength(2)
  })
})

describe('boundsFromFeatures', () => {
  it('returns min/max lng/lat envelope', () => {
    const f1 = feature({ Highway: 'A' })
    f1.geometry.coordinates = [
      [-79.5, 43.6],
      [-79.4, 43.7],
    ]
    const f2 = feature({ Highway: 'B' })
    f2.geometry.coordinates = [
      [-79.45, 43.65],
      [-79.35, 43.75],
    ]
    expect(boundsFromFeatures([f1, f2])).toEqual([
      [-79.5, 43.6],
      [-79.35, 43.75],
    ])
  })

  it('returns null for empty input', () => {
    expect(boundsFromFeatures([])).toBeNull()
  })
})

describe('popupHtml', () => {
  it('escapes HTML in highway and rule text', () => {
    const html = popupHtml({
      Highway: 'A & B <script>',
      Rule: 'Mon "special" days',
      schedule_category: 'no_parking',
      Side: 'North',
      max: null,
    })
    expect(html).toContain('A &amp; B &lt;script&gt;')
    expect(html).toContain('Mon &quot;special&quot; days')
    expect(html).not.toContain('<script>')
  })

  it('includes polarity row when provided', () => {
    const html = popupHtml(
      {
        Highway: 'Queen St W',
        Rule: 'anytime',
        schedule_category: 'no_stopping',
        Side: 'Both',
        max: null,
      },
      'restricted',
    )
    expect(html).toContain('At selected time')
    expect(html).toContain('No stopping')
  })

  it('shows max stay when maxMinutes is set', () => {
    const html = popupHtml({
      Highway: 'College St',
      Rule: 'Mon–Fri',
      schedule_category: 'restricted_periods',
      Side: 'North',
      max: null,
      maxMinutes: 120,
    })
    expect(html).toContain('Max stay')
    expect(html).toContain('2 hours')
  })
})

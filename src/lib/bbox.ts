import type { ParkingFeature } from '../types/parking'

export type LngLatBounds = [[number, number], [number, number]]

export function boundsFromFeatures(
  features: ParkingFeature[],
): LngLatBounds | null {
  let minLng = Infinity
  let minLat = Infinity
  let maxLng = -Infinity
  let maxLat = -Infinity

  for (const feature of features) {
    const coords = feature.geometry.coordinates
    for (const [lng, lat] of coords) {
      if (lng < minLng) minLng = lng
      if (lat < minLat) minLat = lat
      if (lng > maxLng) maxLng = lng
      if (lat > maxLat) maxLat = lat
    }
  }

  if (!Number.isFinite(minLng)) return null
  return [
    [minLng, minLat],
    [maxLng, maxLat],
  ]
}


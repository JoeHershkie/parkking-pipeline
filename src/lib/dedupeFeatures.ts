import { ruleFeatureKey } from './labels'
import type { ParkingFeature } from '../types/parking'

export function dedupeParkingFeatures(
  features: ParkingFeature[],
): ParkingFeature[] {
  const seen = new Set<string>()
  const result: ParkingFeature[] = []
  for (const f of features) {
    const key = ruleFeatureKey(f.properties)
    if (seen.has(key)) continue
    seen.add(key)
    result.push(f)
  }
  return result
}

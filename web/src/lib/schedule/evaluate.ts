import type { ParkingFeature, ParkingProperties } from '../../types/parking'
import {
  membershipFullyCoversRange,
  overlapsMembership,
  overlapsMembershipInRange,
} from './membership'
import type {
  FilterPolarity,
  Schedule,
  Slot,
  SlotEvaluation,
} from './types'

function isRestrictedCategory(
  category: ParkingProperties['schedule_category'],
): boolean {
  return (
    category === 'no_parking' ||
    category === 'no_stopping' ||
    category === 'no_standing'
  )
}

function polarityFromOverlap(
  cat: ParkingProperties['schedule_category'],
  overlaps: boolean,
  sched: Schedule,
): FilterPolarity {
  if (sched.status === 'anytime') {
    if (cat === 'restricted_periods') return 'permitted'
    return 'restricted'
  }
  if (cat === 'restricted_periods') {
    return overlaps ? 'permitted' : 'not_permitted'
  }
  if (isRestrictedCategory(cat)) {
    return overlaps ? 'restricted' : 'inactive'
  }
  return 'inactive'
}

function polarityFromRangeOverlap(
  cat: ParkingProperties['schedule_category'],
  overlaps: boolean,
  fullyCovered: boolean,
  sched: Schedule,
): FilterPolarity {
  if (sched.status === 'anytime') {
    if (cat === 'restricted_periods') return 'permitted'
    return 'restricted'
  }
  if (cat === 'restricted_periods') {
    return fullyCovered ? 'permitted' : 'not_permitted'
  }
  if (isRestrictedCategory(cat)) {
    return overlaps ? 'restricted' : 'inactive'
  }
  return 'inactive'
}

export function ruleMatchesFilter(
  feature: ParkingFeature,
  slot: Slot,
  includeUnknown: boolean,
): boolean {
  const sched = feature.properties.schedule
  if (!sched) return includeUnknown
  if (sched.status === 'failed') return true
  const overlaps = overlapsMembership(sched, slot)
  return overlaps
}

export function evaluateAtSlot(
  props: ParkingProperties,
  slot: Slot,
  includeUnknown: boolean,
): SlotEvaluation {
  const sched = props.schedule
  const cat = props.schedule_category

  if (!sched) {
    if (!includeUnknown) {
      return { visible: false, polarity: 'unknown', unparsed: true }
    }
    return { visible: true, polarity: 'unknown', unparsed: true }
  }

  if (sched.status === 'failed') {
    return {
      visible: true,
      polarity: 'unknown',
      unparsed: true,
      failed: true,
    }
  }

  if (sched.status === 'partial') {
    const overlaps = overlapsMembership(sched, slot)
    return {
      visible: true,
      polarity: polarityFromOverlap(cat, overlaps, sched),
      unparsed: true,
      partial: true,
    }
  }

  const overlaps = overlapsMembership(sched, slot)
  return {
    visible: true,
    polarity: polarityFromOverlap(cat, overlaps, sched),
    unparsed: false,
  }
}

export function evaluateInRange(
  props: ParkingProperties,
  slot: Slot,
  endMinuteOfDay: number | null,
  includeUnknown: boolean,
): SlotEvaluation {
  if (endMinuteOfDay == null || endMinuteOfDay <= slot.minuteOfDay) {
    return evaluateAtSlot(props, slot, includeUnknown)
  }

  const sched = props.schedule
  const cat = props.schedule_category
  const endMinute = endMinuteOfDay

  if (!sched) {
    if (!includeUnknown) {
      return { visible: false, polarity: 'unknown', unparsed: true }
    }
    return { visible: true, polarity: 'unknown', unparsed: true }
  }

  if (sched.status === 'failed') {
    return {
      visible: true,
      polarity: 'unknown',
      unparsed: true,
      failed: true,
    }
  }

  if (sched.status === 'partial') {
    const overlaps = overlapsMembershipInRange(sched, slot, endMinute)
    const fullyCovered =
      cat === 'restricted_periods'
        ? membershipFullyCoversRange(sched, slot, endMinute)
        : false
    return {
      visible: true,
      polarity: polarityFromRangeOverlap(
        cat,
        overlaps,
        fullyCovered,
        sched,
      ),
      unparsed: true,
      partial: true,
    }
  }

  const overlaps = overlapsMembershipInRange(sched, slot, endMinute)
  const fullyCovered = membershipFullyCoversRange(sched, slot, endMinute)

  return {
    visible: true,
    polarity: polarityFromRangeOverlap(
      cat,
      overlaps,
      fullyCovered,
      sched,
    ),
    unparsed: false,
  }
}

export function enrichFeatureCollection(
  data: import('../../types/parking').ParkingFeatureCollection,
  slot: Slot,
  includeUnknown: boolean,
  endMinuteOfDay: number | null = null,
): import('../../types/parking').ParkingFeatureCollection {
  const features = data.features
    .map((feature) => {
      const evaluation = evaluateInRange(
        feature.properties,
        slot,
        endMinuteOfDay,
        includeUnknown,
      )
      return {
        ...feature,
        properties: {
          ...feature.properties,
          _polarity: evaluation.polarity,
          _visible: evaluation.visible,
          _unparsed: evaluation.unparsed,
          _partial: evaluation.partial,
          _failed: evaluation.failed,
        },
      }
    })
    .filter((f) => f.properties._visible !== false)

  return { type: 'FeatureCollection', features }
}

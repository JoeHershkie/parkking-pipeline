import type { Feature, FeatureCollection, LineString } from 'geojson'
import type { FilterPolarity, Schedule } from '../lib/schedule'

export type { Schedule } from '../lib/schedule'
export type ScheduleCategory =
  | 'no_parking'
  | 'no_stopping'
  | 'no_standing'
  | 'restricted_periods'

export interface ParkingProperties {
  Highway: string
  Rule: string
  schedule_category: ScheduleCategory | string
  Side: string
  max: string | null
  schedule?: Schedule
  maxMinutes?: number | null
  /** Client-only fields set by schedule filter enrichment */
  _polarity?: FilterPolarity
  _visible?: boolean
  _unparsed?: boolean
}

export type ParkingFeature = Feature<LineString, ParkingProperties>
export type ParkingFeatureCollection = FeatureCollection<
  LineString,
  ParkingProperties
>

export const PARKING_SOURCE_ID = 'parking'
export const PARKING_LAYER_ID = 'parking-lines'
export const PARKING_HIGHLIGHT_LAYER_ID = 'parking-lines-highlight'

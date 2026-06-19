import { useCallback, useEffect, useRef, useState } from 'react'
import { Legend } from './components/Legend'
import {
  ParkingMap,
  type ParkingMapHandle,
} from './components/ParkingMap'
import { RulePanel } from './components/RulePanel'
import { SearchBar, type SelectedPlace } from './components/SearchBar'
import { TimeFilterBar, type TimeFilterState } from './components/TimeFilterBar'
import { slotFromDate } from './lib/schedule'
import type { ParkingFeature, ParkingFeatureCollection, ParkingMapMetadata } from './types/parking'
import './App.css'

function formatClickLabel(lngLat: [number, number]): string {
  return `${lngLat[1].toFixed(5)}°N, ${Math.abs(lngLat[0]).toFixed(5)}°W`
}

function App() {
  const mapHandleRef = useRef<ParkingMapHandle | null>(null)
  const [featureCount, setFeatureCount] = useState<number | null>(null)
  const [visibleCount, setVisibleCount] = useState<number | null>(null)
  const [rulesAtPoint, setRulesAtPoint] = useState<ParkingFeature[]>([])
  const [clickLabel, setClickLabel] = useState<string | null>(null)
  const [searchStatus, setSearchStatus] = useState<string | null>(null)
  const [dataReady, setDataReady] = useState(false)
  const [mapMetadata, setMapMetadata] = useState<ParkingMapMetadata | null>(null)
  const [timeFilter, setTimeFilter] = useState<TimeFilterState>(() => ({
    slot: slotFromDate(new Date()),
    endMinuteOfDay: null,
    includeUnknown: true,
  }))

  const applyMapFilter = useCallback((state: TimeFilterState) => {
    const effectiveEnd =
      state.endMinuteOfDay != null &&
      state.endMinuteOfDay > state.slot.minuteOfDay
        ? state.endMinuteOfDay
        : null
    const count = mapHandleRef.current?.applyScheduleFilter(
      state.slot,
      effectiveEnd,
      state.includeUnknown,
    )
    if (count != null) setVisibleCount(count)
  }, [])

  const handleMapReady = useCallback((handle: ParkingMapHandle) => {
    mapHandleRef.current = handle
  }, [])

  const handleDataLoaded = useCallback((data: ParkingFeatureCollection) => {
    setFeatureCount(data.features.length)
    setMapMetadata(data.metadata ?? null)
    setDataReady(true)
  }, [])

  useEffect(() => {
    if (!dataReady) return
    applyMapFilter(timeFilter)
  }, [dataReady, timeFilter, applyMapFilter])

  const handleTimeFilterChange = useCallback(
    (state: TimeFilterState) => {
      setTimeFilter(state)
      applyMapFilter(state)
    },
    [applyMapFilter],
  )

  const handleRulesAtPoint = useCallback(
    (rules: ParkingFeature[], lngLat: [number, number]) => {
      setRulesAtPoint(rules)
      setClickLabel(formatClickLabel(lngLat))
    },
    [],
  )

  const handlePlaceSelected = useCallback(async (place: SelectedPlace) => {
    const handle = mapHandleRef.current
    if (!handle) return

    try {
      const count = await handle.flyToAndHighlight(place.lng, place.lat)
      const segmentLabel =
        count === 1 ? '1 curb segment' : `${count} curb segments`
      setSearchStatus(`Zoomed to ${place.label} — ${segmentLabel} nearby.`)
    } catch {
      setSearchStatus(`Could not zoom to ${place.label}.`)
    }
  }, [])

  const handleSearchClear = useCallback(() => {
    mapHandleRef.current?.clearSearchHighlight()
    setSearchStatus(null)
  }, [])

  const effectiveEnd =
    timeFilter.endMinuteOfDay != null &&
    timeFilter.endMinuteOfDay > timeFilter.slot.minuteOfDay
      ? timeFilter.endMinuteOfDay
      : null

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-text">
          <h1>Toronto Parking Bylaws</h1>
          <p>Explore geocoded no-parking and related curb rules on the map.</p>
        </div>
      </header>

      <SearchBar
        onPlaceSelected={handlePlaceSelected}
        onClear={handleSearchClear}
        statusMessage={searchStatus}
        disabled={!dataReady}
      />

      <TimeFilterBar
        slot={timeFilter.slot}
        endMinuteOfDay={timeFilter.endMinuteOfDay}
        includeUnknown={timeFilter.includeUnknown}
        onChange={handleTimeFilterChange}
        disabled={!dataReady}
      />

      <div className="app-main">
        <div className="map-column">
          <ParkingMap
            onMapReady={handleMapReady}
            onDataLoaded={handleDataLoaded}
            onRulesAtPoint={handleRulesAtPoint}
          />
          <Legend
            featureCount={featureCount}
            visibleCount={visibleCount}
            slot={timeFilter.slot}
            endMinuteOfDay={effectiveEnd}
            dataGeneratedAt={mapMetadata?.generated_at}
            pipelineVersion={mapMetadata?.pipeline_version}
          />
        </div>
        <RulePanel rules={rulesAtPoint} clickLabel={clickLabel} />
      </div>
    </div>
  )
}

export default App

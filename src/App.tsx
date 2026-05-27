import { useCallback, useEffect, useRef, useState } from 'react'
import { Attribution } from './components/Attribution'
import { Legend } from './components/Legend'
import {
  ParkingMap,
  type ParkingMapHandle,
} from './components/ParkingMap'
import { RulePanel } from './components/RulePanel'
import { SearchBar } from './components/SearchBar'
import { TimeFilterBar, type TimeFilterState } from './components/TimeFilterBar'
import { slotFromDate } from './lib/schedule'
import { boundsFromFeatures, filterFeaturesByHighway } from './lib/bbox'
import type { ParkingFeature, ParkingFeatureCollection } from './types/parking'
import './App.css'

function formatClickLabel(lngLat: [number, number]): string {
  return `${lngLat[1].toFixed(5)}°N, ${Math.abs(lngLat[0]).toFixed(5)}°W`
}

function App() {
  const mapHandleRef = useRef<ParkingMapHandle | null>(null)
  const dataRef = useRef<ParkingFeatureCollection | null>(null)
  const [featureCount, setFeatureCount] = useState<number | null>(null)
  const [visibleCount, setVisibleCount] = useState<number | null>(null)
  const [rulesAtPoint, setRulesAtPoint] = useState<ParkingFeature[]>([])
  const [clickLabel, setClickLabel] = useState<string | null>(null)
  const [searchStatus, setSearchStatus] = useState<string | null>(null)
  const [dataReady, setDataReady] = useState(false)
  const [timeFilter, setTimeFilter] = useState<TimeFilterState>(() => ({
    slot: slotFromDate(new Date()),
    includeUnknown: true,
  }))

  const applyMapFilter = useCallback((state: TimeFilterState) => {
    const count = mapHandleRef.current?.applyScheduleFilter(
      state.slot,
      state.includeUnknown,
    )
    if (count != null) setVisibleCount(count)
  }, [])

  const handleMapReady = useCallback((handle: ParkingMapHandle) => {
    mapHandleRef.current = handle
  }, [])

  const handleDataLoaded = useCallback((data: ParkingFeatureCollection) => {
    dataRef.current = data
    setFeatureCount(data.features.length)
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

  const handleSearch = useCallback((query: string) => {
    const data = dataRef.current
    const handle = mapHandleRef.current
    if (!data || !handle) return

    const matches = filterFeaturesByHighway(data, query)
    if (matches.length === 0) {
      setSearchStatus(`No segments matching "${query}".`)
      handle.setSearchHighlight(null)
      return
    }

    const highwayNames = [...new Set(matches.map((f) => f.properties.Highway))]
    handle.setSearchHighlight(highwayNames)

    const bounds = boundsFromFeatures(matches)
    if (bounds) {
      handle.fitBounds(bounds)
    }

    const highwayLabel =
      highwayNames.length === 1
        ? highwayNames[0]
        : `${highwayNames.length} streets`
    setSearchStatus(
      `${matches.length} segment${matches.length === 1 ? '' : 's'} on ${highwayLabel}.`,
    )
  }, [])

  const handleSearchClear = useCallback(() => {
    mapHandleRef.current?.setSearchHighlight(null)
    setSearchStatus(null)
  }, [])

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-text">
          <h1>Toronto Parking Bylaws</h1>
          <p>Explore geocoded no-parking and related curb rules on the map.</p>
        </div>
      </header>

      <SearchBar
        onSearch={handleSearch}
        onClear={handleSearchClear}
        statusMessage={searchStatus}
        disabled={!dataReady}
      />

      <TimeFilterBar
        slot={timeFilter.slot}
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
            slotLabel={timeFilter.slot}
          />
        </div>
        <RulePanel rules={rulesAtPoint} clickLabel={clickLabel} />
      </div>

      <Attribution />
    </div>
  )
}

export default App

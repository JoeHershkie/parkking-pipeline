import { useEffect, useRef, useState } from 'react'
import maplibregl, {
  type MapGeoJSONFeature,
  type MapLayerMouseEvent,
} from 'maplibre-gl'
import { BASE_MAP_STYLE_URL } from '../lib/basemap'
import { dedupeParkingFeatures } from '../lib/dedupeFeatures'
import { popupHtml } from '../lib/popupHtml'
import { enrichFeatureCollection } from '../lib/schedule'
import type { Slot } from '../lib/schedule'
import {
  lineColorExpression,
  lineOpacityExpression,
  lineWidthExpression,
} from '../lib/mapStyle'
import type { ParkingFeature, ParkingFeatureCollection } from '../types/parking'
import {
  PARKING_HIGHLIGHT_LAYER_ID,
  PARKING_LAYER_ID,
  PARKING_SOURCE_ID,
} from '../types/parking'
import './ParkingMap.css'

const GEOJSON_URL = '/data/final_parking_map.geojson'
const TORONTO_CENTER: [number, number] = [-79.38, 43.65]
const QUERY_BUFFER_PX = 10
const HIDDEN_FILTER: maplibregl.FilterSpecification = ['==', 1, 0]

export interface ParkingMapHandle {
  getMap: () => maplibregl.Map | null
  fitBounds: (bounds: [[number, number], [number, number]]) => void
  clearSearchHighlight: () => void
  flyToAndHighlight: (lng: number, lat: number) => Promise<number>
  applyScheduleFilter: (
    slot: Slot,
    endMinuteOfDay: number | null,
    includeUnknown: boolean,
  ) => number | null
}

interface ParkingMapProps {
  onMapReady: (handle: ParkingMapHandle) => void
  onDataLoaded: (data: ParkingFeatureCollection) => void
  onRulesAtPoint: (rules: ParkingFeature[], lngLat: [number, number]) => void
}

function toParkingFeature(f: MapGeoJSONFeature): ParkingFeature | null {
  if (f.geometry.type !== 'LineString') return null
  return f as unknown as ParkingFeature
}

function queryBBox(
  point: maplibregl.Point,
  buffer: number,
): [maplibregl.PointLike, maplibregl.PointLike] {
  return [
    [point.x - buffer, point.y - buffer],
    [point.x + buffer, point.y + buffer],
  ]
}

function addParkingLayers(map: maplibregl.Map, data: ParkingFeatureCollection) {
  map.addSource(PARKING_SOURCE_ID, {
    type: 'geojson',
    data,
    generateId: true,
  })

  map.addLayer({
    id: PARKING_LAYER_ID,
    type: 'line',
    source: PARKING_SOURCE_ID,
    paint: {
      'line-color': lineColorExpression,
      'line-width': lineWidthExpression,
      'line-opacity': lineOpacityExpression,
    },
  })

  map.addLayer({
    id: PARKING_HIGHLIGHT_LAYER_ID,
    type: 'line',
    source: PARKING_SOURCE_ID,
    filter: HIDDEN_FILTER,
    paint: {
      'line-color': '#2563eb',
      'line-width': 5,
      'line-opacity': 1,
    },
  })
}

export function ParkingMap({
  onMapReady,
  onDataLoaded,
  onRulesAtPoint,
}: ParkingMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const rawDataRef = useRef<ParkingFeatureCollection | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const hoveredIdRef = useRef<string | number | null>(null)
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>(
    'loading',
  )

  useEffect(() => {
    if (!containerRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASE_MAP_STYLE_URL,
      center: TORONTO_CENTER,
      zoom: 11,
      maxBounds: [
        [-80.2, 43.4],
        [-78.8, 44.2],
      ],
    })

    map.addControl(new maplibregl.NavigationControl(), 'top-left')
    map.addControl(
      new maplibregl.AttributionControl({ compact: true }),
      'bottom-left',
    )

    mapRef.current = map
    popupRef.current = new maplibregl.Popup({
      closeButton: true,
      closeOnClick: false,
      maxWidth: '320px',
    })

    const applyScheduleFilter = (
      slot: Slot,
      endMinuteOfDay: number | null,
      includeUnknown: boolean,
    ): number | null => {
      const raw = rawDataRef.current
      const source = map.getSource(PARKING_SOURCE_ID) as
        | maplibregl.GeoJSONSource
        | undefined
      if (!raw || !source) return null
      const enriched = enrichFeatureCollection(
        raw,
        slot,
        includeUnknown,
        endMinuteOfDay,
      )
      source.setData(enriched)
      return enriched.features.length
    }

    const setHighlightByFeatureIds = (
      featureIds: (string | number)[] | null,
    ) => {
      if (!map.getLayer(PARKING_HIGHLIGHT_LAYER_ID)) return
      if (!featureIds?.length) {
        map.setFilter(PARKING_HIGHLIGHT_LAYER_ID, HIDDEN_FILTER)
        return
      }
      map.setFilter(PARKING_HIGHLIGHT_LAYER_ID, [
        'in',
        ['id'],
        ['literal', featureIds],
      ] as maplibregl.FilterSpecification)
    }

    const queryFeaturesNear = (lng: number, lat: number): ParkingFeature[] => {
      const point = map.project([lng, lat])
      const bbox = queryBBox(point, QUERY_BUFFER_PX)
      const raw = map.queryRenderedFeatures(bbox, {
        layers: [PARKING_LAYER_ID],
      })
      return dedupeParkingFeatures(
        raw
          .map(toParkingFeature)
          .filter((f): f is ParkingFeature => f != null),
      )
    }

    const handle: ParkingMapHandle = {
      getMap: () => mapRef.current,
      fitBounds: (bounds) => {
        map.fitBounds(bounds, { padding: 48, maxZoom: 16, duration: 800 })
      },
      clearSearchHighlight: () => setHighlightByFeatureIds(null),
      flyToAndHighlight: (lng, lat) =>
        new Promise((resolve) => {
          const onMoveEnd = () => {
            map.off('moveend', onMoveEnd)
            const features = queryFeaturesNear(lng, lat)
            const ids = features
              .map((f) => f.id)
              .filter((id): id is string | number => id != null)
            if (ids.length > 0) {
              setHighlightByFeatureIds(ids)
            } else {
              const highways = [
                ...new Set(features.map((f) => f.properties.Highway)),
              ]
              if (highways.length > 0 && map.getLayer(PARKING_HIGHLIGHT_LAYER_ID)) {
                map.setFilter(PARKING_HIGHLIGHT_LAYER_ID, [
                  'in',
                  ['get', 'Highway'],
                  ['literal', highways],
                ] as maplibregl.FilterSpecification)
              } else {
                setHighlightByFeatureIds(null)
              }
            }
            resolve(features.length)
          }
          map.once('moveend', onMoveEnd)
          map.flyTo({
            center: [lng, lat],
            zoom: 17,
            duration: 800,
          })
        }),
      applyScheduleFilter,
    }

    map.on('load', async () => {
      try {
        const res = await fetch(GEOJSON_URL)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = (await res.json()) as ParkingFeatureCollection
        rawDataRef.current = data

        addParkingLayers(map, data)

        setLoadState('ready')
        onDataLoaded(data)
        onMapReady(handle)
      } catch {
        setLoadState('error')
      }
    })

    map.on('mouseenter', PARKING_LAYER_ID, () => {
      map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', PARKING_LAYER_ID, () => {
      map.getCanvas().style.cursor = ''
      if (hoveredIdRef.current != null) {
        map.setFeatureState(
          { source: PARKING_SOURCE_ID, id: hoveredIdRef.current },
          { hover: false },
        )
        hoveredIdRef.current = null
      }
    })

    map.on('mousemove', PARKING_LAYER_ID, (e: MapLayerMouseEvent) => {
      if (!e.features?.length) return
      const id = e.features[0].id
      if (id == null) return
      if (hoveredIdRef.current != null && hoveredIdRef.current !== id) {
        map.setFeatureState(
          { source: PARKING_SOURCE_ID, id: hoveredIdRef.current },
          { hover: false },
        )
      }
      hoveredIdRef.current = id
      map.setFeatureState(
        { source: PARKING_SOURCE_ID, id },
        { hover: true },
      )
    })

    map.on('click', (e) => {
      const bbox = queryBBox(e.point, QUERY_BUFFER_PX)
      const raw = map.queryRenderedFeatures(bbox, {
        layers: [PARKING_LAYER_ID],
      })
      const features = dedupeParkingFeatures(
        raw
          .map(toParkingFeature)
          .filter((f): f is ParkingFeature => f != null),
      )

      const lngLat: [number, number] = [e.lngLat.lng, e.lngLat.lat]
      onRulesAtPoint(features, lngLat)

      if (features.length === 1 && popupRef.current) {
        popupRef.current
          .setLngLat(e.lngLat)
          .setHTML(popupHtml(features[0].properties, features[0].properties._polarity))
          .addTo(map)
      } else if (popupRef.current) {
        popupRef.current.remove()
      }
    })

    return () => {
      popupRef.current?.remove()
      map.remove()
      mapRef.current = null
    }
  }, [onMapReady, onDataLoaded, onRulesAtPoint])

  return (
    <div className="parking-map-wrap">
      <div ref={containerRef} className="parking-map" />
      {loadState === 'loading' && (
        <div className="map-overlay map-overlay-loading">
          Loading bylaws…
        </div>
      )}
      {loadState === 'error' && (
        <div className="map-overlay map-overlay-error">
          Could not load map data. Ensure{' '}
          <code>public/data/final_parking_map.geojson</code> exists.
        </div>
      )}
    </div>
  )
}

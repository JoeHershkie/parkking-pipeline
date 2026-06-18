import { useCallback, useEffect, useRef, useState } from 'react'
import {
  autocompleteSuggestions,
  createSessionToken,
  getPlacesApiKey,
  placeDetails,
  type PlaceSuggestion,
} from '../lib/places'
import './SearchBar.css'

export type SelectedPlace = {
  lat: number
  lng: number
  label: string
}

interface SearchBarProps {
  onPlaceSelected: (place: SelectedPlace) => void
  onClear: () => void
  statusMessage: string | null
  disabled?: boolean
}

const DEBOUNCE_MS = 300

export function SearchBar({
  onPlaceSelected,
  onClear,
  statusMessage,
  disabled,
}: SearchBarProps) {
  const apiKey = getPlacesApiKey()
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState<PlaceSuggestion[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeIndex, setActiveIndex] = useState(-1)
  const sessionRef = useRef(createSessionToken())
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const listId = 'address-suggestions'

  const inputDisabled = disabled || !apiKey

  const resetSession = useCallback(() => {
    sessionRef.current = createSessionToken()
  }, [])

  const fetchSuggestions = useCallback(
    async (value: string) => {
      if (!apiKey || !value.trim()) {
        setSuggestions([])
        setOpen(false)
        return
      }
      setLoading(true)
      setError(null)
      try {
        const results = await autocompleteSuggestions(
          value,
          sessionRef.current,
        )
        setSuggestions(results)
        setOpen(results.length > 0)
        setActiveIndex(-1)
      } catch (e) {
        setSuggestions([])
        setOpen(false)
        setError(e instanceof Error ? e.message : 'Autocomplete failed')
      } finally {
        setLoading(false)
      }
    },
    [apiKey],
  )

  useEffect(() => {
    if (!query.trim()) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      void fetchSuggestions(query)
    }, DEBOUNCE_MS)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, fetchSuggestions])

  function handleQueryChange(value: string) {
    setQuery(value)
    if (!value.trim()) {
      setSuggestions([])
      setOpen(false)
      setError(null)
      resetSession()
    }
  }

  const selectSuggestion = useCallback(
    async (suggestion: PlaceSuggestion) => {
      setLoading(true)
      setError(null)
      setOpen(false)
      try {
        const place = await placeDetails(
          suggestion.placeId,
          sessionRef.current,
        )
        setQuery(place.formattedAddress)
        onPlaceSelected({
          lat: place.lat,
          lng: place.lng,
          label: place.formattedAddress,
        })
        resetSession()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load place')
      } finally {
        setLoading(false)
      }
    },
    [onPlaceSelected, resetSession],
  )

  function handleClear() {
    setQuery('')
    setSuggestions([])
    setOpen(false)
    setError(null)
    resetSession()
    onClear()
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (!open || suggestions.length === 0) {
      if (e.key === 'Enter') e.preventDefault()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (i + 1) % suggestions.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) =>
        i <= 0 ? suggestions.length - 1 : i - 1,
      )
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const pick =
        activeIndex >= 0 ? suggestions[activeIndex] : suggestions[0]
      if (pick) void selectSuggestion(pick)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const setupHint = !apiKey
    ? 'Add VITE_GOOGLE_MAPS_API_KEY to .env (see .env.example) to enable address search.'
    : null

  return (
    <div className="search-bar">
      <label htmlFor="address-search" className="visually-hidden">
        Search address
      </label>
      <div className="search-bar-input-wrap">
        <input
          id="address-search"
          type="search"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          placeholder="Search address…"
          value={query}
          onChange={(e) => handleQueryChange(e.target.value)}
          onFocus={() => {
            if (suggestions.length > 0) setOpen(true)
          }}
          onBlur={() => {
            setTimeout(() => setOpen(false), 150)
          }}
          onKeyDown={handleKeyDown}
          disabled={inputDisabled}
          autoComplete="off"
        />
        {open && suggestions.length > 0 && (
          <ul id={listId} className="search-suggestions" role="listbox">
            {suggestions.map((s, i) => (
              <li
                key={s.placeId}
                role="option"
                aria-selected={i === activeIndex}
                className={
                  i === activeIndex ? 'search-suggestion-active' : undefined
                }
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => void selectSuggestion(s)}
              >
                {s.label}
              </li>
            ))}
          </ul>
        )}
      </div>
      {statusMessage && (
        <button type="button" className="search-clear" onClick={handleClear}>
          Clear
        </button>
      )}
      {(statusMessage || setupHint || error || loading) && (
        <p className="search-status" role="status">
          {loading && 'Searching… '}
          {error && <span className="search-error">{error} </span>}
          {setupHint ?? statusMessage}
        </p>
      )}
    </div>
  )
}

import { useState } from 'react'
import './SearchBar.css'

interface SearchBarProps {
  onSearch: (query: string) => void
  onClear: () => void
  statusMessage: string | null
  disabled?: boolean
}

export function SearchBar({
  onSearch,
  onClear,
  statusMessage,
  disabled,
}: SearchBarProps) {
  const [query, setQuery] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) return
    onSearch(trimmed)
  }

  function handleClear() {
    setQuery('')
    onClear()
  }

  return (
    <form className="search-bar" onSubmit={handleSubmit}>
      <label htmlFor="highway-search" className="visually-hidden">
        Search by street name
      </label>
      <input
        id="highway-search"
        type="search"
        placeholder="Search street name…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !query.trim()}>
        Search
      </button>
      {statusMessage && (
        <button type="button" className="search-clear" onClick={handleClear}>
          Clear
        </button>
      )}
      {statusMessage && (
        <p className="search-status" role="status">
          {statusMessage}
        </p>
      )}
    </form>
  )
}

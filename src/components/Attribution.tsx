import './Attribution.css'

export function Attribution() {
  return (
    <footer className="attribution">
      <p>
        Map tiles ©{' '}
        <a
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noopener noreferrer"
        >
          OpenStreetMap
        </a>{' '}
        contributors (basemap via{' '}
        <a
          href="https://openfreemap.org/"
          target="_blank"
          rel="noopener noreferrer"
        >
          OpenFreeMap
        </a>
        ). Bylaw data from{' '}
        <a
          href="https://open.toronto.ca/"
          target="_blank"
          rel="noopener noreferrer"
        >
          City of Toronto Open Data
        </a>
        . Segment geometry derived from Toronto Centreline (TCL).
      </p>
      <p className="attribution-disclaimer">
        For information only — not legal advice. Data may be incomplete or
        inaccurate.
      </p>
    </footer>
  )
}

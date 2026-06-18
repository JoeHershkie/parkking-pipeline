# Toronto Parking Bylaws (parking-web)

Interactive map of geocoded Toronto curb parking bylaws. Data is produced by the separate `parking-pipeline` project and loaded as static GeoJSON in this app.

## Prerequisites

- Node.js 20+
- Map data: copy `final_parking_map.geojson` from the pipeline repo into this project:

```bash
cp ../parking-pipeline/data/final_parking_map.geojson public/data/
```

GeoJSON files under `public/data/` are gitignored; refresh the copy whenever you re-run the pipeline fullrun so features include structured `schedule` objects (`v: 1`).

### Address search (Google Places)

1. Create a [Google Cloud](https://console.cloud.google.com/) project with billing enabled.
2. Enable **Places API (New)**.
3. Create a browser API key restricted to HTTP referrers (e.g. `http://localhost:5173/*`) and Places APIs only.
4. Copy `.env.example` to `.env` and set `VITE_GOOGLE_MAPS_API_KEY`.

The app uses session tokens and Place Details (Essentials, location fields only) so typical usage stays within the free monthly Essentials caps (10,000 autocomplete + 10,000 details requests). Set a billing budget alert in Cloud Console if desired.

## Development

```bash
npm install
npm run dev
```

Open the URL shown in the terminal (usually http://localhost:5173).

## Build

```bash
npm run build
npm run preview
```

## Map features

- Colored line segments by restriction type (`no_parking`, `no_stopping`, `no_standing`, `restricted_periods`)
- **Date/time filter** — calendar date (defaults to today), start time, and optional end time on the same day; map colors reflect whether each rule’s structured `schedule` applies in that period
- Ontario statutory public holidays (`exceptPublicHolidays`) and seasonal/month/day calendars from the pipeline schedule object
- Rules with `status: failed` always appear on the map with a warning (never treated as active); toggle **Show rules without schedule data** only affects features missing a `schedule` property
- `partial` schedules match on parsed windows only, with a “Partially parsed” note when unparsed clauses remain
- Click the map to list bylaws near that point; click a single segment for a quick popup
- Search by address (Google Places autocomplete) to fly to a location and highlight nearby curb segments
- Legend with segment counts for the selected time period

Schedule logic lives under [`src/lib/schedule/`](src/lib/schedule/) and mirrors the parking-pipeline contract. Display text for bylaws still comes from the `Rule` property; filtering never regex-parses `Rule`.

## Tests

```bash
npm test
```

# Toronto Parking Bylaws (parking-web)

Interactive map of geocoded Toronto curb parking bylaws. Data is produced by the separate `parking-pipeline` project and loaded as static GeoJSON in this app.

## Prerequisites

- Node.js 20+
- Map data: copy `final_parking_map.geojson` from the pipeline repo into this project:

```bash
cp ../parking-pipeline/data/final_parking_map.geojson public/data/
```

GeoJSON files under `public/data/` are gitignored; refresh the copy whenever you re-run the pipeline geometry step.

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
- **Day/time filter** — evaluate structured `schedule` windows at a selected slot; map colors reflect permitted vs restricted vs inactive vs unparsed
- Toggle **Show unparsed schedules** to include or hide rows where the pipeline could not parse times (`failed` / `partial`)
- Click the map to list bylaws near that point; click a single segment for a quick popup
- Search by street name to zoom and highlight matching segments
- Legend and footer attribution

Holiday enforcement and seasonal/monthly windows are not evaluated yet (`exceptPublicHolidays` is display-only; unparsed seasonal text remains `failed`).

# Toronto Parking Map

[![Pipeline CI](https://github.com/JoeHershkie/toronto-parking-map/actions/workflows/pipeline-ci.yml/badge.svg)](https://github.com/JoeHershkie/toronto-parking-map/actions/workflows/pipeline-ci.yml)
[![Web CI](https://github.com/JoeHershkie/toronto-parking-map/actions/workflows/web-ci.yml/badge.svg)](https://github.com/JoeHershkie/toronto-parking-map/actions/workflows/web-ci.yml)

Turn Toronto parking bylaw open data into an interactive map of curb restrictions — no parking, no stopping, no standing, and time-limited zones.

## Why this is hard

City open data lists *what* is restricted on each curb segment, but the geographic description is legal text — not coordinates:

> *"A point 59 metres north of Elm Avenue and Spadina Road"*

This project parses that text, resolves street names against the [Toronto Centreline (TCL)](https://open.toronto.ca/dataset/toronto-centreline-tcl/), walks centreline graphs to find block geometry, and exports map-ready GeoJSON for a React + MapLibre frontend.

**Scale (full local runs, 2026-06-18):** 76,849 raw bylaw rows → 21,433 map features. Failure-ledger triage drove `INTERSECTION_NOT_FOUND` from ~6,600 (early runs) → 1,174 on the current codebase.

> **Throughput vs accuracy:** These figures count how many rows were parsed, resolved, and emitted as map features — not whether each segment is placed on the correct stretch of curb. There is no automated ground-truth accuracy metric yet; see [`pipeline/tests/test_geometry_golden.py`](pipeline/tests/test_geometry_golden.py) for regression coverage on the sample cohort.
>
> **Reproducibility:** Full-run numbers depend on locally maintained, gitignored alias tables (`highway_aliases.csv`, `street_aliases.csv`) and the vintage of your Toronto open-data downloads. They are not third-party reproducible without those inputs.

## Repository layout

| Path | Description |
|------|-------------|
| [`pipeline/`](pipeline/) | Python ETL: parse bylaws, resolve streets, emit GeoJSON |
| [`web/`](web/) | React + TypeScript map UI |
| [`pipeline/docs/README.md`](pipeline/docs/README.md) | Pipeline deep-dive (schemas, failure codes, geometry model) |

```mermaid
flowchart LR
  raw[City open data CSV]
  pipeline[Python pipeline]
  geojson[final_parking_map.geojson]
  web[React map app]

  raw --> pipeline --> geojson --> web
```

## Quick start

### Prerequisites

- Python 3.12+ (3.14 tested locally; CI runs 3.12–3.14)
- Node.js 22+ (for the web app)
- Toronto open-data files (see [Data acquisition](#data-acquisition))

### Pipeline

```bash
./scripts/setup.sh          # once per clone / after pull (creates .venv, pip install -e)
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Run stages (or use: parking-run)
parking-clean
parking-parse-schedule
parking-parse-between
parking-resolve             # auto-refreshes tcl_street_names.csv when stale
parking-geo
```

Use `-v` / `--verbose` on any stage for debug logging, or set `PARKING_VERBOSE=1`.

### Web app

```bash
cd web
cp .env.example .env    # optional: Google Places API key for address search
npm ci
npm run dev             # runs sync-data first when pipeline output exists
```

`npm run sync-data` copies `pipeline/data/final_parking_map.geojson` into `web/public/data/` when present (also runs automatically before `dev` and `build`).

### Tests

```bash
cd pipeline
pytest                    # uses data/samples/ when full TCL is absent
ruff check src tests scripts
```

```bash
cd web
npm test
npm run build
```

CI runs on push to `main` (see badges above).

## Data acquisition

Download these datasets manually from Toronto Open Data and place them under `pipeline/data/`:

| File | Source |
|------|--------|
| `toronto_raw_parking_dump.csv` | [Traffic and Parking By-law Schedules](https://open.toronto.ca/dataset/traffic-and-parking-by-law-schedules/) |
| `tcl_streets.geojson` | [Toronto Centreline (TCL)](https://open.toronto.ca/dataset/toronto-centreline-tcl/) |
| `tcl_intersections.geojson` | [Intersection file](https://open.toronto.ca/dataset/intersection-file-city-of-toronto/) |

`parking-resolve` regenerates `tcl_street_names.csv` when it is missing or older than `tcl_streets.geojson`. To force a refresh: `python scripts/export_tcl_street_names.py --force` (from `pipeline/` with the venv active).

Committed [`pipeline/data/samples/`](pipeline/data/samples/) fixtures let tests and CI run without the full ~120 MB TCL download.

Regenerate sample fixtures after changing fixture streets:

```bash
cd pipeline
python scripts/build_sample_fixtures.py
```

## License

MIT — see [LICENSE](LICENSE).

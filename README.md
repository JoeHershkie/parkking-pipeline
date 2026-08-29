# Parkking Pipeline

[![Pipeline CI](https://github.com/JoeHershkie/parkking-pipeline/actions/workflows/pipeline-ci.yml/badge.svg)](https://github.com/JoeHershkie/parkking-pipeline/actions/workflows/pipeline-ci.yml)

High-performance Python ETL pipeline that transforms City of Toronto parking bylaw open data into geocoded curb restrictions with structured schedules and spatial indices for [parkking-ios](https://github.com/JoeHershkie/parkking-ios) and [parkking-web](https://github.com/JoeHershkie/parkking-web).

---

## Technical Overview

City open data lists *what* is restricted on each curb segment, but the geographic description is legal text rather than coordinates:

> *"A point 59 metres north of Elm Avenue and Spadina Road"*

This pipeline:
1. **Parses** freeform legal text for temporal rules (days, times, durations, public holiday exceptions) and geographic spans (between/from/to, offsets, compass directions).
2. **Resolves** street names against the [Toronto Centreline (TCL)](https://open.toronto.ca/dataset/toronto-centreline-tcl/) and intersection nodes using graph search and normalization heuristics.
3. **Projects** legal spans onto physical curbs using Toronto Road Edge polygons (with calibrated parallel offsets as fallback).
4. **Exports** validated, schema-compliant `final_parking_map.geojson` and metadata manifests.

For a technical deep-dive on schemas, failure codes, and geometry algorithms, see [docs/README.md](docs/README.md).

---

## Repository Layout

```
├── src/
│   └── parking_pipeline/     # Core library: parser, resolver, geometry engine
├── tests/                    # Unit tests, integration tests, golden regression suites
├── scripts/                  # Data fetching, failure ledger triage, QA audits
├── data/
│   └── samples/              # Committed offline sample fixtures for tests & CI
├── docs/                     # Comprehensive architecture and schema documentation
├── pyproject.toml            # Package configuration, dependencies, and CLI entry points
└── README.md
```

---

## Quick Start

### Prerequisites
- Python 3.12+ (tested with 3.12–3.14)

### Installation & Setup

```bash
./scripts/setup.sh          # One-time setup: creates .venv and installs in editable mode
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### Running Pipeline Stages

Execute stages sequentially or use `parking-run` for the end-to-end flow:

```bash
parking-clean               # Fetch/refresh toronto_raw_parking_dump.csv from Open Data
parking-parse-schedule      # Parse temporal bylaws into structured schedules
parking-parse-between       # Extract geographic boundaries from legal descriptions
parking-resolve             # Resolve spans against the Toronto Centreline graph
parking-geo                 # Place spans on physical curb geometries (or: parking-geo --require-road-edges)
```

Use `-v` / `--verbose` or set `PARKING_VERBOSE=1` for detailed debug logging.

---

## Testing & Quality Gates

Run the test suite and quality gates:

```bash
pytest                      # Runs suite using offline sample fixtures
ruff check src tests scripts # Linting
python scripts/check_module_coverage.py # Enforce test coverage floors
```

---

## Data Acquisition

| File | Source | Description |
|------|--------|-------------|
| `toronto_raw_parking_dump.csv` | Auto-fetched from [Toronto Open Data CKAN API](https://open.toronto.ca/dataset/traffic-and-parking-by-law-schedules/) | Raw traffic and parking bylaw tables |
| `tcl_streets.geojson` | [Toronto Centreline (TCL)](https://open.toronto.ca/dataset/toronto-centreline-tcl/) | Official road network centreline geometry |
| `tcl_intersections.geojson` | [Intersection file](https://open.toronto.ca/dataset/intersection-file-city-of-toronto/) | Official intersection points |
| `topographic_road_edges.gpkg` | `python scripts/fetch_topographic_road_edges.py` | Physical road edges and curb polygons |

---

## License

MIT — see [LICENSE](LICENSE).

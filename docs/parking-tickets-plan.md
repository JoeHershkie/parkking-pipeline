# Final Plan (v6): Toronto Parking Tickets Pipeline, Phase 1

## Scope

Build a standalone `parking-tickets` command producing:

- Per-row Parquet containing all source records
- Resolved-only Point GeoJSON
- QA JSON and provenance metadata

Do not modify `fullrun.py`, `web/`, or `sync-data.mjs`. Web integration remains Phase 2 and will be designed after observing geocode coverage and artifact sizes.

## Orchestration

`ticket_snapshot.py` owns `main()` and runs:

1. Ticket ZIP acquisition
2. Address-point snapshot load or refresh
3. Lightweight index construction
4. Streaming geocode pass
5. Atomic output-set publication

## CLI and Environment Variables

| Flag | Effect | Environment variable |
|---|---|---|
| `--year N` | Select annual ZIP; default latest | None |
| `--skip-refresh` | Reuse local ticket ZIP only | `PARKING_TICKETS_SKIP_REFRESH` |
| `--force-refresh` | Always redownload ticket ZIP | `PARKING_TICKETS_FORCE_REFRESH` |
| `--refresh-address-points` | Check CKAN metadata and refresh if stale | `PARKING_ADDRESS_POINTS_REFRESH` |
| `--force-address-points` | Always redownload address points | `PARKING_ADDRESS_POINTS_FORCE` |
| `-v/--verbose` | Enable verbose logging | Existing `PARKING_VERBOSE` |

Address points are reused by default. A missing local snapshot is an error unless refresh is requested. Snapshot age and source fingerprint are included in QA.

## `ckan.py`

Extract and parameterize reusable logic from `opendata.py`:

- Retrying HTTP open with exponential backoff
- JSON GET
- Streaming download through `.partial`
- SHA-256 calculation
- Atomic download replacement
- Manifest loading, writing, fingerprinting, and staleness checks
- Parameters for package ID, resource, source URL, target path, and catalogue URL

Use an injectable HTTP seam. Existing `test_opendata.py` currently monkeypatches `opendata.urlopen` and `opendata.time.sleep`; update that test in the same change and preserve behavior coverage.

Use stdlib `urllib` only. Do not add an HTTP dependency.

Ticket resource selection must use resource name, year, active state, and format. Do not reuse `select_dump_resource()`, which assumes `datastore_active`.

## `ticket_source.py`

Implement:

- CKAN discovery for package `parking-tickets`
- Annual ZIP selection by year
- Latest-year selection when `--year` is omitted
- Atomic ZIP download
- Manifest containing CKAN metadata, resource ID, resource URL, local SHA-256, and fetch time
- ZIP integrity validation
- Multi-member CSV discovery
- Required-header validation for every member
- Cross-year header normalization and encoding fallback
- Streaming row iteration without materializing the full dataset

Use strict UTF-8/BOM handling first, then a controlled `cp1252` or Latin-1 fallback. Header validation must still reject silently corrupted or incompatible files.

Preserve `source_member` and `source_row_number` for every input row.

## `address_points.py`

Use Toronto’s current 4326 Address Points CSV resource.

Implement:

- CKAN resource selection by name, format, and required schema
- Local snapshot and manifest
- Explicit metadata refresh and force-refresh modes
- Loud schema-drift failures
- Parsing of the source geometry field into finite WGS84 coordinates
- Lookup key:

```text
normalized street,
civic_number_text,
normalized suffix
```

Preserve civic numbers as text so values such as `54` and `54A` remain distinct. Include address suffix fields from the API schema where available.

Duplicate handling:

- Select the lowest point ID when duplicate coordinates agree within tolerance.
- Mark the key ambiguous when coordinates disagree.
- Allow ambiguous address-point matches to fall through to TCL interpolation.
- Record the ambiguity in `geocode_attempts` and warnings.

Build this index using `tcl_highway_key()` only. Do not initialize bylaw resolver or geometry-engine global state.

## `ticket_geocode.py`

### Location Grammar

Interpret location fields as positional pairs:

```text
location1 qualifies location2
location3 qualifies location4
```

Never treat all four fields as one flat token stream.

Always preserve:

- `location1_raw`
- `location3_raw`
- `qualifier_interpreted`

Examples:

| Input | Interpretation |
|---|---|
| `AT | YONGE ST | blank | BLOOR ST` | Resolve the Yonge/Bloor intersection |
| `NR | 266 DOVERCOURT RD` | Use address coordinate with reduced confidence |
| `OPP | 100 QUEEN ST W` | Preserve coordinate, record opposite-side warning |
| `S/S | PRYOR AVE | E/O | CLOVERDALE RD` | Preserve south-side and east-of metadata; resolve intersection context |
| Unknown or blank | Preserve raw value and mark uninterpreted |

Phase 1 records side and direction metadata but does not apply geometric offsets.

### Street Resolution

Resolve in this order:

1. Direct `tcl_highway_key()` normalization
2. Ticket-specific aliases
3. Explicitly initialized `resolve_tcl_highway()` fallback for difficult strings

Keep ticket aliases separate from bylaw `highway_aliases.csv`.

### Lightweight Indices

Construct only:

- Address-point dictionary
- TCL address-range index using required columns
- Intersection postings through `intersection_index.configure()`
- Local intersection-ID-to-Point mapping

Do not call:

- `geo_indices.init_geo()`
- `tcl_graph`
- Full street graph construction

### Resolution Ladder

Record every attempt in ordered `geocode_attempts`.

1. `address_point`
   - Exact normalized address match
   - Coordinates represent parcel/building position
   - Attach an `address_point` approximation warning

2. `tcl_interpolation`
   - Match street and address range
   - Use `LO_NUM_*` / `HI_NUM_*` for membership
   - Use `PARITY_L` / `PARITY_R`
   - Use `BEGIN_ADDR_*` / `END_ADDR_*` to orient geometry correctly
   - Fall back to `LO_NUM_*` / `HI_NUM_*` interpolation when endpoint address fields are absent, with a warning
   - Attach an interpolation approximation warning

3. `intersection`
   - Resolve with `resolve_pair_ids()`
   - Use `intersection_pair_resolve` fallbacks
   - Map the resolved ID through the local intersection point index
   - Treat the result as an intersection approximation, not the exact vehicle location

No live geocoders and no fabricated coordinates. Unresolved rows remain unresolved.

Confidence measures match certainty, not positional exactness. Every method carries an approximation warning.

## Outputs and Atomic Publication

Output root:

```text
pipeline/data/parking_tickets/<year>/
  runs/<run_id>/
    parking_tickets_<year>.parquet
    parking_tickets_<year>_points.geojson
    parking_tickets_<year>_qa.json
  current.json
```

The run process will:

1. Create a temporary run directory under the same filesystem.
2. Write all artifacts there.
3. Close and validate every artifact.
4. Rename the completed directory to `runs/<run_id>`.
5. Atomically replace `current.json`.
6. Prune old runs only after the pointer swap succeeds.

A crash before the pointer swap leaves the previous complete generation active. `current.json` contains the run ID, relative artifact paths, generation time, and artifact SHA-256 values.

## Parquet

`parking_tickets_<year>.parquet` is the canonical complete artifact.

It contains:

- All original source fields
- Nullable `lon` and `lat`
- `geocode_method`
- `geocode_status`
- `geocode_confidence`
- Ordered `geocode_attempts`
- `matched_address`
- `matched_tcl_id`
- Warnings
- `source_member`
- `source_row_number`
- `ticket_uid`
- `row_hash`
- `row_occurrence`

Identity semantics:

- `ticket_uid = <year>:<member>:<row>` is source-layout-specific.
- `row_hash` is content-based and stable across ZIP re-layouts, but not unique.
- `(row_hash, row_occurrence)` is the content identity when duplicate counts remain unchanged.

Use a fixed schema and `pyarrow.parquet.ParquetWriter` for incremental writes. Add an explicit pinned `pyarrow` dependency; pandas does not provide it as a normal dependency.

## GeoJSON

`parking_tickets_<year>_points.geojson` contains only successfully resolved Point features.

Each feature must have:

- Valid finite WGS84 Point geometry
- Original ticket properties
- Geocoding method/status/confidence
- Match metadata
- Approximation warnings

Write GeoJSON incrementally and validate the completed output before publication.

No all-rows GeoJSON is produced by default. Complete unresolved-inclusive data is available in Parquet.

## QA and Provenance

`parking_tickets_<year>_qa.json` contains:

- Input row count
- Parquet output row count
- Resolved Point count
- Unresolved count
- Match rates by method
- Attempt-trail distributions
- Ambiguous, missing, malformed, and out-of-bounds counts
- Duplicate-row count
- Coordinate validation results
- Address snapshot age and fingerprint
- Runtime and artifact sizes
- Run ID

Parquet key-value metadata and QA must include:

- `geocoder_version`
- `schema_version`
- `run_id`
- Ticket resource ID and SHA-256
- Address-point fingerprint
- TCL file mtimes
- `crs: EPSG:4326`
- `generated_at`

## Repository Plumbing

Update `pipeline/pyproject.toml`:

- Add the `parking-tickets` console script
- Add pinned `pyarrow`

Update ignore rules before generating artifacts:

- `pipeline/data/parking_tickets/`
- Ticket ZIPs
- Address-point snapshots and manifests
- Temporary and `.partial` paths
- Generated Parquet, GeoJSON, and QA files

Add self-contained fixtures:

- Synthetic ticket CSV in an in-memory ZIP
- Synthetic address points with suffix and duplicate cases
- Synthetic TCL segments with address ranges
- `BEGIN_ADDR_*` and `END_ADDR_*`
- Reversed-geometry interpolation case
- Anonymized qualifier examples based on real source patterns

## Tests

Add:

- `test_ticket_source.py`
- `test_ticket_geocode.py`
- `test_ticket_snapshot.py`

Cover:

- CKAN resource selection
- ZIP integrity and header validation
- Encoding fallback
- Manifest and refresh behavior
- Refresh-flag matrix
- Location and qualifier grammar
- Exact text civic-number matching
- Address suffixes such as `54A`
- Duplicate address-point handling
- BEGIN/END-directed interpolation
- Reversed geometry
- Intersection resolution
- Malformed and unresolved rows
- Attempt-trail recording
- Row-count invariant
- Ticket identity and row-hash semantics
- Atomic interrupted-run behavior
- Pointer never referencing incomplete output

All tests must be offline and use the established fake HTTP response pattern. Preserve the existing coverage floor.

## Verification

Run the full existing suite after the CKAN refactor.

Run one complete real-year integration job and report:

- Match-method counts
- Resolution percentage
- Ambiguous and unresolved counts
- Runtime
- Parquet size
- GeoJSON size
- QA output

Spot-check downtown, suburban, intersection, suffix, ambiguous, and unresolved locations.

Review licensing and redistribution terms before publishing any generated snapshot. CKAN currently reports the relevant licenses as unspecified.

## Phase 2

Deferred until Phase 1 results are available:

- Web artifact choice, including aggregation versus PMTiles
- `sync-data.mjs` and `current.json` consumption
- `web/src/types/tickets.ts`
- `fullrun.py` integration
- Side-of-street and direction-based geometric offsets
- Hosting and scheduled automation

## Implementation Order

1. Persist this plan to `pipeline/docs/parking-tickets-plan.md`.
2. Update `pyproject.toml` and ignore rules.
3. Extract `ckan.py` and update `test_opendata.py`.
4. Implement `ticket_source.py` and tests.
5. Implement `address_points.py` and tests.
6. Implement `ticket_geocode.py` and tests.
7. Implement `ticket_snapshot.py`, streaming writers, QA, run directories, and pointer swapping.
8. Run the full suite.
9. Run one real-year integration and report results.

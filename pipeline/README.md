# parking-pipeline

Python ETL for Toronto parking bylaws. **Start at the [monorepo README](../README.md)** for setup, data downloads, and tests.

Technical deep-dive (schemas, failure codes, curb geometry): [docs/README.md](docs/README.md)

## Local spatial sources

`parking-geo` reads TCL / Road Edge files under `data/` only — it never hits the network. `parking-clean` / `parking-run` do contact Toronto Open Data to refresh `data/toronto_raw_parking_dump.csv` when the CKAN datastore copy is newer (use `--skip-refresh` to stay offline).

| File | Role |
|------|------|
| `data/tcl_streets.geojson` | Toronto Centreline (legal span) |
| `data/tcl_intersections.geojson` | Intersection points |
| `data/topographic_road_edges.gpkg` | Road Edge + Intersection polygons (physical curb) |

Download Road Edge **once** (the Open Data catalogue page is retired; the FeatureServer is still live):

```bash
python scripts/fetch_topographic_road_edges.py
```

Keep `data/topographic_road_edges.gpkg` (gitignored, like TCL). Refresh with the same script when the city source changes.

Tests and CI copy committed [`data/samples/`](data/samples/) fixtures when the full downloads are absent (`ensure_sample_data_copies()`). `parking-geo` also copies the Road Edge sample if the GeoPackage is missing. For a full-city run that must not silently use that fixture:

```bash
parking-geo --require-road-edges
```

(`parking-run` does not pass that flag.)

## Curb geometry

After the centreline slice, each mapped row is placed on a curb. The three `curb_geometry_method` values are **not** equivalent quality:

- `road_edge` — measured tracks from Road Edge polygons
- `offset_fallback` — calibrated parallel offset (estimate)
- `centerline_unresolved` — legal centreline retained; not a curb

Output is `LineString` / `MultiLineString` only, with provenance (`centreline_ids`, Road Edge object IDs, manifest in GeoJSON metadata) and QA files `data/curb_geometry_qa.csv` / `data/curb_geometry_qa_summary.json`. Rare cases: `data/curb_geometry_overrides.csv`.

The web app still assumes `LineString`; `MultiLineString` features may need a later client update. The pipeline does not flatten or duplicate geometry for that.

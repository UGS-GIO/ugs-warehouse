# featureserv — OGC API Features for ArcGIS Pro (duckdb_featureserv over GeoParquet)

The **access tier** (per the GeoParquet-vs-OGC-API split: storage=GeoParquet, mgmt=DuckLake,
access=OGC API). Off-the-shelf [`tobilg/duckdb_featureserv`](https://github.com/tobilg/duckdb_featureserv)
(Go, OGC API Features Core) over the warehouse GeoParquet. **No database, no Postgres** —
collections are DuckDB **views over the parquet on the public CDN** (live, lake-native). Scale-to-zero
on Cloud Run: it spins up on demand.

This exists **only for OGC-API standard clients — ArcGIS Pro, QGIS, federation.** Your web maps + the
viewer use PMTiles/static and don't touch this.

## How it works

- `gen_db.py` reads the STAC catalog → builds a tiny DuckDB file with one `VIEW` per serving-topic
  (`SELECT * FROM read_parquet('<cdn url>')`). The **data** each view returns is live (read at query
  time via httpfs); the db holds only pointers, never a copy.
- It runs **on every container start** (`entrypoint.sh`), not just at image build, so a topic ingested
  standalone is queryable on the next cold start instead of waiting for an unrelated push to rebuild
  the image (#89). The db is a *cache* of the STAC catalog — STAC stays the source of truth.
- Only changed layers are rebound: `gen_db` reads the existing db's own view definitions and skips
  anything already pointing at the same parquet. Binding costs a remote footer read (~0.3s/layer), so
  an unchanged catalog is a 0.3s no-op instead of a ~7s full rebind.
- **Failure is bounded, never fatal.** Nothing listens on `:9000` until `gen_db` returns, so one
  deadline (`--deadline`, 60s) covers both the catalog scan and the binding. Catalog unreachable →
  the image-baked db still serves. Deadline hit mid-rebind → partial progress is kept when there was
  an existing db to add to, and discarded when there wasn't (a half-bound db must never replace a
  good one). Swaps are atomic.
- `Dockerfile` (multi-stage): stage 1 bakes the cold-floor db (`--strict`, so a broken catalog reddens
  the build); stage 2 lifts the featureserv binary + its Go templates onto a Python base. The upstream
  image is distroless, so it can't run `gen_db` at start — see the tradeoff note below.
- `duckdb_featureserv.toml` is baked in at `/config/`. It overrides only the upstream defaults that
  are wrong for us — the 1000-feature response cap, the 10-feature page size, the generic service
  title, and the `transform=` whitelist (empty by default, so the parameter is off). Everything else
  keeps upstream's value.

Endpoints (Features Core): `/`, `/conformance`, `/collections`, `/collections/{id}`,
`/collections/{id}/items?bbox=&limit=`. Conformance: core + oas3 + geojson + html.

Query parameters that work today and are worth knowing about: `filter=` (CQL — comparison,
`BETWEEN`, `IN`, `IS NULL`, boolean, and spatial `INTERSECTS`/`ENVELOPE`, all pushed down to
DuckDB), `?<column>=<value>` attribute shorthand, `properties=` column projection, `sortby=`
(`-col` for descending), `bbox=`, `limit=`/`offset=`, and `transform=`. Not wired up: `orderby=`
and `crs=` are accepted and silently ignored, `precision=` and `groupby=` 500 — all upstream.

## Local

```bash
docker build -t ugs-featureserv .
docker run --rm -p 9000:9000 ugs-featureserv
# http://localhost:9000/collections
# http://localhost:9000/collections/enmin_ucrc_wells/items?limit=5
```

Validated locally end-to-end: 27 collections, items + bbox queries 200, conformance complete.
Container ready in 2.4s steady-state (0.3s of that is the catalog scan), 3.5s with the catalog
unreachable (serves the baked db).

```bash
docker run --rm ugs-featureserv python3 /app/gen_db.py --out /data/database.duckdb --rebuild
```

## Deploy

`cloudbuild.yaml` builds `featureserv/` → `ugs-features` image → Cloud Run `ugs-warehouse-features`,
`--allow-unauthenticated`, `--port=9000`, scale-to-zero, no Cloud SQL / secrets. Then in **ArcGIS Pro**:
*Insert → Connections → Server → New OGC API Server* → the service URL. That's the acceptance test.

## Notes / upgrade path

- **The db is a mirror of the catalog, not a live query against it.** Boot regen shrinks the staleness
  window from "the next push to `main`" to "the next cold start" — it doesn't eliminate the class.
  That needs the server to resolve collections per request, which off-the-shelf featureserv can't do.
  **Exit condition:** if upstream gains a catalog refresh or glob/directory-based collection source,
  `gen_db.py` and `entrypoint.sh` both delete themselves.
- **Tradeoff taken to get boot regen: the runtime image is no longer distroless.** Running `gen_db` at
  start needs an interpreter, so the base moved to `python:3.12-slim` — a shell and a package manager
  now exist on a public unauthenticated service, and the image grew 91MB → ~359MB. Cloud Run streams
  images lazily and caches per region, so the pull cost lands mostly on the first cold start after a
  deploy; worth measuring against the ~3-8.6s baseline before optimizing. A duckdb-CLI-plus-shell
  build would land near 140MB but still isn't distroless; only a static helper binary would be.
- `MODE=table` (env) materializes layers into the db instead of views — a full snapshot (bigger image,
  and it duplicates the data), fallback if a future featureserv drops view support. Incremental
  rebinding is skipped in this mode: an unchanged URL doesn't mean the materialized rows are current.
- **Paging is a floor, not a fix.** `LimitMax = 100000` clears every topic we serve today
  (`enmin_plss_sections` is the largest at 84,756), but featureserv emits no `numberMatched` and no
  `rel="next"` link — neither exists anywhere in its source — so truncation stays *undetectable*. A
  layer that grows past the cap fails the same silent way. The real fix is an upstream patch that
  sets `NumberMatched` and emits next links (ALL-5402).
- Other upstream gaps worth knowing before wiring a client: GeoJSON features carry no `id` member
  and `/collections/{id}/items/{fid}` always 500s (`sqlTables` hardcodes `'' AS id_column`, so
  the single-feature query renders `WHERE "" = $1`); `/collections` reports `bbox [0,0,0,0]` for
  every collection while `/collections/{id}` has the real extent; `geometrytype` is always the
  literal `GEOMETRY`. The missing feature `id` is what blocks ArcGIS **Online** specifically —
  its `OGCFeatureLayer` needs one to derive `OBJECTID`.
- Pin `tobilg/duckdb_featureserv:latest` to a version tag once a known-good one is chosen.
- Replaces the earlier bespoke FastAPI (`ogcapi/`, removed) — off-the-shelf, less to maintain.

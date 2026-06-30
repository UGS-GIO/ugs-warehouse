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
  (`SELECT * FROM read_parquet('<cdn url>')`). The **collection list** is a build-time snapshot of the
  catalog; the **data** each view returns is live (read at query time via httpfs). Rebuild/redeploy to
  pick up newly-piped layers.
- `Dockerfile` (multi-stage): stage 1 builds that db; stage 2 = the `duckdb_featureserv` image with the
  db baked in. featureserv loads `spatial` + `httpfs` at query time (verified) so the views resolve
  against the remote parquet.

Endpoints (Features Core): `/`, `/conformance`, `/collections`, `/collections/{id}`,
`/collections/{id}/items?bbox=&limit=`. Conformance: core + oas3 + geojson + html.

## Local

```bash
docker build -t ugs-featureserv .
docker run --rm -p 9000:9000 ugs-featureserv
# http://localhost:9000/collections
# http://localhost:9000/collections/enmin_ucrc_wells/items?limit=5
```

Validated locally end-to-end: 19 collections, items + bbox queries 200, conformance complete.

## Deploy

`cloudbuild.yaml` builds `featureserv/` → `ugs-features` image → Cloud Run `ugs-warehouse-features`,
`--allow-unauthenticated`, `--port=9000`, scale-to-zero, no Cloud SQL / secrets. Then in **ArcGIS Pro**:
*Insert → Connections → Server → New OGC API Server* → the service URL. That's the acceptance test.

## Notes / upgrade path

- **Collection list is build-time**; data is live. For runtime-derived collections, regenerate the db
  on a schedule or at container start (would need duckdb in the runtime image).
- `MODE=table` (env) materializes layers into the db instead of views — a full snapshot (bigger image),
  fallback if a future featureserv drops view support.
- Pin `tobilg/duckdb_featureserv:latest` to a version tag once a known-good one is chosen.
- Replaces the earlier bespoke FastAPI (`ogcapi/`, removed) — off-the-shelf, less to maintain.

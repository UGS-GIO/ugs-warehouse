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
- The server binary is **built from pinned upstream source with `patches/` applied**, not lifted
  from the published image. See "The patch" below for why.

## The patch

Four OGC API - Features gaps are compiled into upstream, not configurable. Each one is a few lines
of `patches/0001-ogc-conformance.patch`, written against upstream `7609f02` and pinned by SHA in
the Dockerfile — `git apply` fails the build if upstream drifts, which is the signal to re-base.

| Gap | Upstream | Effect |
|---|---|---|
| No feature `id` | `sqlTables` hardcodes `'' AS id_column` | GeoJSON features carry no `id`, and `/collections/{id}/items/{fid}` renders `WHERE "" = $1` → **500 for every id** |
| No `numberMatched` | hardcoded `0` behind `json:",omitempty"` | never serialized, so a truncated response is indistinguishable from a complete one |
| No `next` link | no `next` rel anywhere in the source | a client cannot page past the first response |
| `/collections` extents | `Tables()` never calls `TableReload` | every collection reports `bbox [0,0,0,0]`; layer pickers and zoom-to-layer get null island |

The id column is resolved by convention — first match of `feature_id`, `fid`, `ogc_fid`, `objectid`,
`gid`, `id`. DuckDB views have no primary key, so there is nothing authoritative to read; every
warehouse topic carries `feature_id` (minted in `vector/transform.py`, stable across ingests and
already the MVT feature id in PMTiles), so the OGC `id` and the PMTiles `id` are the same value.

**Known cost:** the first `/collections` on a cold container resolves an extent per collection
against remote parquet — ~18s, then cached for the life of the process (~0.03s). Collections are
fixed once `gen_db` swaps the database in, so the cache cannot go stale.

The extent query reads the GeoParquet `bbox_xmin/ymin/xmax/ymax` covering columns (GeoParquet 1.1
calls these "covering" columns; every warehouse topic has them, written by `vector/sink_archive.py`)
rather than aggregating geometry. Aggregating was measured at over 30s, which **exceeded upstream's
default `Server.WriteTimeoutSec` and returned a 200 with an empty body** — the server abandons the
response mid-write and says nothing. Hence both the cheaper query here and `WriteTimeoutSec = 120`
in the config. If a table lacks the covering columns the query errors and upstream's existing
fallback recomputes it from geometry, so this is an optimisation, not a requirement.

Precomputing extents in `gen_db` is the way to remove that first-call cost rather than raise the
ceiling again.

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

`_FEATURES_MEMORY` in `cloudbuild.yaml` sets the deploy's `--memory` **and** is passed into the image
build, where `check_limits.py` refuses to build if it isn't a measured pairing with `Paging.LimitMax`.
See "Response cap and memory" below.

## Connecting clients

Always give a client the **service root**, never a `/collections/{id}` URL:

```
https://ugs-warehouse-features-xedvkyurga-uc.a.run.app
```

Esri resolves `/conformance` relative to whatever URL you hand it, so a collection URL fails with
`ogc-feature-layer:missing-conformance-page` ("Missing conformance url"). Its client takes the root
plus a `collectionId` as separate values.

**ArcGIS Pro** — *Insert → Connections → Server → New OGC API Server* → the root URL.

**ArcGIS Online** works too, but the Map Viewer flow has two steps that look like failure. Both were
hit on the first real attempt, so they are worth writing down:

1. *Add → Add layer from URL*, paste the root URL, then **set Type manually to "OGC feature layer."**
   Autodetect does not pick it, and the option is labelled *"OGC feature layer"* — not "OGC API -
   Features" as Esri's own docs describe it — and sits below KML in a list you have to scroll.
2. *Next* shows **"This data set has more than 1000 layers. Enter a search term to find a specific
   layer"** over an **empty list**. That message is AGOL's own heuristic, not something this service
   reports: `/collections` returns exactly 28 entries with no pagination and no `numberMatched`.
   Click into the search box and the list populates. (The box does not really filter — interacting
   with it is what loads the list.)

Then pick a collection → *Add to map*. Start with a small one — `enmin_ut_counties` (29 features) or
`enmin_ccus_cbcounty` (29) — so a mistake shows up in seconds; `enmin_plss_sections` (84,756) is the
stress case, not the smoke test.

A loaded layer should show `objectIdField: OBJECTID`, derived from `feature_id`. That is the same
identifier the feature carries in PMTiles (`vector/transform.py` mints it, `sink_pmtiles.py`
promotes it), so a feature clicked in the viewer and the same feature fetched over OGC agree.

To check conformance without an Esri client at all, load Esri's SDK and construct the layer directly:

```js
require(['esri/layers/OGCFeatureLayer'], async (OGC) => {
  const l = new OGC({ url: 'https://ugs-warehouse-features-xedvkyurga-uc.a.run.app',
                      collectionId: 'enmin_ut_counties' });
  await l.load();
  console.log(l.geometryType, l.objectIdField, l.fields.length);   // polygon OBJECTID 25
});
```

## Response cap and memory

featureserv buffers the entire FeatureCollection before writing it, so the largest response a client
can request has to fit in the container. That makes `Paging.LimitMax` (in `duckdb_featureserv.toml`)
and `--memory` (in `cloudbuild.yaml`) **one decision recorded in two files**, and an unfitting pair
does not degrade gracefully — Cloud Run SIGKILLs the container mid-response and returns 503, taking
any concurrent request on that instance with it.

`check_limits.py` runs at image build and refuses an unmeasured pairing. `_FEATURES_MEMORY` feeds
both the build arg and the deploy flag, so the two cannot drift apart silently.

Measured against the deployed image, full 84,756-feature pull of `enmin_plss_sections` (122MB
response) and unpaced 5×20k paging over the same layer:

| memory | full pull | unpaced paging |
|---|---|---|
| 512Mi | OOM | OOM on page 2–4 |
| 1Gi | OOM | survives |
| 2Gi | survives, repeated | survives |

It deliberately does not *model* memory from feature counts. Those numbers do not fit one
multiplier: paged 29MB requests kill 512Mi while a single 122MB request is fine on 2Gi, because
consecutive requests outrun GC. A model fitted to that would be wrong in one direction, and a wrong
model in a build gate is worse than no gate. So the rule is measure-and-record, with monotonicity the
only inference (more memory at the same cap is safe; a smaller cap at the same memory is safe).

To change either value: build, run the two probes at the new memory limit, confirm
`docker inspect <c> --format '{{.State.OOMKilled}}'` stays `false`, then add the pair to `VALIDATED`
in `check_limits.py`. The failure message spells out the commands.

Streaming the response instead of buffering would make memory roughly constant and retire this whole
coupling. That is an upstream change.

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
- Still upstream, still unfixed: `geometrytype` is always the literal `GEOMETRY` and `srid` the
  literal `4326` (harmless for us — every topic is 4326 and homogeneous, verified across all 27);
  `orderby=` and `crs=` are accepted and silently ignored; `precision=` and `groupby=` 500;
  `/functions/{id}/items` 500s. None of these block a standards client.
- **ArcGIS Online**: every documented blocker is now cleared — feature `id`, `numberMatched`, `next`
  links, working `/items/{fid}`, real collection extents, GeoJSON conformance, CORS. That is a
  requirements check against Esri's `OGCFeatureLayer` docs, **not** a confirmed AGOL connection;
  nobody has added the layer in AGOL yet. When someone does, give it the **service root** URL and
  pick the collection as a sublayer — a `/collections/{id}` URL is not what it expects. Treat AGOL
  as unverified until that acceptance test is run, the same way Arc Pro is the one for this service.
- Pin `tobilg/duckdb_featureserv:latest` to a version tag once a known-good one is chosen.
- Replaces the earlier bespoke FastAPI (`ogcapi/`, removed) — off-the-shelf, less to maintain.

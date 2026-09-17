# ugs-warehouse — PR review guide
Cloud-native STAC catalog producer (Python 3.11, DuckDB/DuckLake, obstore→GCS/CDN). Two producers (vector, pubs) on a shared `core/` emit ONE strict-STAC catalog + DuckLake/GeoParquet/PMTiles/COG to a private CDN-fronted bucket. Review ONLY the changed lines against these repo-specific rules (general bug/security/perf/quality assumed). Cite file:line, use repo context, skip unrelated pre-existing issues, group nits.

## STAC catalog — one builder, derive from truth
- ONE item builder / ONE catalog: items come from `core/stac.py` (`build_item`), and `refresh_catalog()` rewrites root `catalog.json` + every `collection.json` from GCS truth. Never hand-edit or hand-write catalog/collection JSON, and keep the pure builders (`build_item`, `_root_doc`, `_collection_doc`, `_group_items`) side-effect-free so tests hold.
- Collections are fixed (`ugs-serving-topics`, `ugs-publications`, `ugs-rasters`); new per-item data goes in properties/assets, not an ad-hoc collection. Prefer standard STAC constructs; `ugs:*` only when nothing standard fits.

## Styling & ISO ride the catalog
- Style binds as a `ugs:renders` block + `roles:["style"]` asset via `attach_renders`, rebound by the `restyle` job with no reingest — don't bake GL style into items, and styling must stay best-effort (never blocks ingest).
- ISO 19139 sidecars are generated from the STAC item (`core/iso.py::stac_to_iso19139`) — never hand-author the XML.

## CRS & geometry contract (vector)
- Target is EPSG:4326 (`transform.TARGET_SRS`). `transform` reprojects from `target_epsg` (the CRS the `geom_wkb` bytes are in) → 4326 via `ST_Transform(..., always_xy:=true)`, NOT from `source_epsg` (provenance only). Reprojecting off source_epsg, or defaulting an unstamped `target_epsg=0` to 4326, silently mislabels output.
- Keep the fail-loud guard: `target_epsg=0` must ERROR (CRS gap upstream), never assume 4326. Keep the geometry guard: 0 non-null-geom rows → SKIP (rc=1), never reach sinks (no empty/null-geom parquet or PMTiles).
- source.read()→transform contract: a new source backend MUST emit the same pyarrow shape (`geom_wkb` BLOB + `target_epsg` + `source_epsg`).

## GCS IO & cheap-path
- Upload via `core/gcs.py` obstore (ADC auth) with an explicit `Cache-Control` + CDN URLs. Do NOT re-add DuckDB `httpfs`/HMAC/gcsfuse/GCS-extension for GCS — DuckLake GCS IO routes through fsspec.
- DuckDB embedded + DuckLake only. No Spark/Dataproc/PyIceberg, no app server in the read path — flag any such dependency or proposal. Base ingest streams in DuckDB; don't add pyarrow/shapely to base deps (they live in the `pubs` extra).

## Correctness & failure handling
- FAIL LOUD — a swallowed error silently corrupts the catalog/warehouse. Sinks run in isolated try/except: one fails → log to stderr + set rc=1, never crash siblings; the Pub/Sub handler acks on rc!=0 to avoid retry storms. Validate DB rows / manifests / trigger payloads at boundaries.

## Cloud Run / deploy & security
- Vector = scale-to-zero service, pubs = sharded Jobs; admin console behind IAP. Deploy footgun: `--no-traffic --tag` pins traffic to the old revision — shift with `--to-latest` after. No secrets in code/logs (DSNs/creds via env/Secret Manager). `_current` (and the `geom` column) is an upstream contract — coordinate rather than edit here. GCP/Vertex only.

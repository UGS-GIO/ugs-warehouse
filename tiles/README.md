# tiles — XYZ vector tiles + ready-to-use MapLibre styles

`/{z}/{x}/{y}.mvt` for clients that can't read PMTiles directly (Leaflet, OpenLayers, plain
MapLibre setups, anything expecting an XYZ URL). **No data copy and no new artifact:** upstream
[`go-pmtiles`](https://github.com/protomaps/go-pmtiles) range-reads the *same* PMTiles the viewer
already uses on the CDN. Scale-to-zero on Cloud Run.

The viewer doesn't need this — it reads PMTiles directly. This exists for consumers that can't.

## What it serves

| Route | Gives you |
|---|---|
| `/` | every topic, with its tile/style/tilejson URLs |
| `/tiles/{topic}/{version}/{z}/{x}/{y}.mvt` | vector tiles (MVT, gzipped), cached `immutable` |
| `/tiles/{topic}/{z}/{x}/{y}.mvt` | same, unversioned — 5 min cache, for hand-written clients |
| `/styles/{topic}.json` | a **complete** MapLibre style — point MapLibre at it and it renders |
| `/styles/{topic}.json?render=NAME` | one specific render (`by-purpose`, `likelihood-24`, …) |
| `/tilejson/{topic}.json` | TileJSON 3.0 (zoom range, bounds, vector_layers) |

## Why the style endpoint exists

ugs-styles publishes a **fragment** — a `layers` array of paint/filter rules with no `sources`,
because a fragment is portable across PMTiles and XYZ. A client pointed at a fragment renders
nothing. `/styles/{topic}.json` binds it: injects `sources`, `source-layer`, zoom range and bounds,
and passes through `sprite`/`glyphs` when the render has them (icon renders need the sprite sheet
or MapLibre draws geometry and silently omits every icon).

Two things it reads rather than assumes, both of which fail silently if guessed:

- **`source-layer`** comes from the archive's own tippecanoe metadata, not from the topic id. A
  mismatch renders an empty map with no error anywhere.
- **The binding comes from `ugs:renders`, not the `style` asset.** That asset only exists for a
  render literally named `default`, and topics styled by attribute (`enmin_ucrc_wells` →
  `by-boxtype`/`by-purpose`, `hazards_debrisflow_babylon_basins` → eight `likelihood-*`) have none,
  so keying off it drops them — 19 topics instead of 24. `ugs:renders` is carried in the items
  index, so this still costs one GET for the whole catalog. The fragment URL inside it is keyed by
  *layer* name (`..._current`), not topic id, so it can't be derived by convention either.

## Freshness — nothing to run

Everything is read from the STAC catalog on demand and cached for `CACHE_TTL` (default 300s), so
updates land within ~5 minutes with **no redeploy, no restart, no rebuild**:

| Change | Picked up because |
|---|---|
| New or edited **style** | ugs-styles CI → `ugs-warehouse-restyle` rewrites `ugs:renders` → read from the item |
| **New topic** | ingest calls `refresh_catalog()` → appears in `items.json` |
| **Re-ingested data** | the item's `ugs:content_hash` changes → new tile URLs → cache miss + child restart |

That last row is the one that needed work. `go-pmtiles` caches each archive's directory in memory
and **never revalidates over the HTTP backend** — verified by swapping a different archive in at
the same URL: it kept serving the old one, with a single header fetch in its log. There is no
invalidation API (the admin port exposes only `/metrics`) and `--cache-size=0` hangs.

So the tile URL carries a version token derived from `ugs:content_hash`. A re-ingest changes it,
which does two things: the CDN and browsers miss instead of serving the previous archive's tiles,
and a request under an unseen version tells this process its child is stale, so it restarts it
(~1s, stateless). Old URLs keep resolving, so already-cached edge entries stay valid.

Because the URL is content-keyed, tiles are served `max-age=31536000, immutable` — aggressive edge
caching and correctness at the same time, rather than trading one for the other.

Same rule as featureserv's `gen_db`: derive from truth, cache cheaply. Nothing is stored here.

## ArcGIS Pro and ArcGIS Online

Esri won't read PMTiles or a bare `/{z}/{x}/{y}` endpoint — but that's paperwork, not data. Esri
vector tiles are plain MVT, and Esri vector tile styles are GL styles. Both already exist here;
what was missing is the descriptor Esri reads first.

| Route | Esri expects |
|---|---|
| `/esri/{topic}/VectorTileServer` | descriptor — tile template, LODs, extent, SRS |
| `/esri/{topic}/VectorTileServer/tile/{z}/{y}/{x}.pbf` | tiles, **y before x** |
| `/esri/{topic}/VectorTileServer/resources/styles/root.json` | the GL style |

Paste the `VectorTileServer` URL into **Pro** (Add Data → Data From Path) or **AGOL** (Add Item →
From a URL → Vector Tile Service). Symbology rides along, so no hand-built `.lyrx` per layer.

LODs are Esri's own numbers, read off a live basemap service: 512px tiles, level 0 at 78271.516964
m/px, Web Mercator top-left origin — which describes the same XYZ grid our tiles use. Deriving them
instead would render off by a zoom level.

**Untested against real Pro/AGOL.** Verified only that the documents are well-formed and the tiles
render through the Esri route in MapLibre. Expect a round of fixes on first contact.

Tiles are for drawing — in Pro a vector tile layer is display-only. For query and analysis Pro wants
the OGC API Features service (`featureserv/`), live over the same GeoParquet.

## Behind the CDN

`maps-assets.geology.utah.gov` already fronts the bucket. Adding this service as a serverless NEG
backend on the same URL map means it only ever sees **cache misses** — a tile fetched once is
served from the edge afterwards and never reaches the container. Cold start is then paid by the
first viewer of a cold tile, not by normal traffic.

## Local

```bash
docker build -t ugs-tiles .
docker run --rm -p 8080:8080 ugs-tiles
curl localhost:8080/ | jq '.collections[0]'
curl localhost:8080/styles/hazards_qfaults.json | jq '.sources'
```

Verified locally: 28 topics, 24 with at least one published render, container ready in ~1.0s,
warm tile ~1ms, zero spurious child restarts against the live CDN. Rendered headlessly in MapLibre
against the generated styles — `hazards_qfaults` 3468 features and `enmin_ucrc_wells` (sprite
render) 4004 features, zero style errors, colours matching the ugs-styles legends.

Re-ingest handling was tested against a local fixture (swap the archive, bump `ugs:content_hash`):
the version token changed, the child restarted, and the tile served came from the new archive
rather than the cached old one.

## Notes

- **Empty tiles return `204`**, not 404 — absent is not an error, and 404 makes some clients retry.
- **Two processes, one container.** `go-pmtiles` on loopback :8081, this app on :8080. They're one
  feature: tiles are useless without a style, and the style has to name the tile URL. The app spawns
  and supervises the child, because it has to be able to restart it (see Freshness); a refused
  loopback connection restarts it and retries once, so a crashed child costs one request rather than
  every tile until the instance recycles. `go-pmtiles` stays upstream and unpatched — everything we
  add is in `app.py`.
- **The flattened route is the point of the proxy.** Our PMTiles live one directory per topic, so
  `go-pmtiles` addresses them as `{topic}/{topic}`; consumers shouldn't have to say it twice.
- **4 topics have no published render** (`enmin_geophysics_heatflow`, `geolmap_geolunits_500k`,
  `geolmap_geolunits_gems`, `hazards_studyareas`). They serve tiles; `/styles/…` returns 404 until
  a style is published in ugs-styles. That's the correct signal, not a bug to paper over.
- **ArcGIS Pro can't consume raw XYZ MVT.** Pro wants an Esri vector tile service or WMTS — for Pro,
  the answer stays the OGC API Features service (`featureserv/`).
- **Raster XYZ is a different build.** PNG tiles mean server-side rendering (tileserver-gl or
  maplibre-native + the GL style), which doesn't idle cheaply. For the geologic-map COGs
  specifically, `titiler` is the standard answer and needs no pre-rendering.

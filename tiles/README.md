# tiles — an Esri compatibility layer (plus an XYZ fallback)

**This service exists because Esri will not read PMTiles.** Everything else already can —
MapLibre, Leaflet, OpenLayers, deck.gl and recent QGIS read the `.pmtiles` archive straight off the
CDN with range requests, no service in the path. That is the warehouse's design and it works today.

So this is not a peer of the PMTiles path. It is a compatibility layer with two consumers:

- **ArcGIS Pro / AGOL** — needs a `VectorTileServer` descriptor before it will accept a layer at
  all. This is the reason the service exists.
- **The narrow middle** — clients that want a plain tile URL but have no PMTiles support. Served by
  `/tiles/...` as a fallback, not as the recommended path.

Point anything else at the PMTiles file.

**No data copy and no new artifact:** upstream [`go-pmtiles`](https://github.com/protomaps/go-pmtiles)
range-reads the *same* archive the viewer uses. Scale-to-zero on Cloud Run.

## Exit condition

**If Esri ships native PMTiles support, this whole service is deleted** — not just the
`/rest/services/` routes. There is an open "Introduce a PMTileLayer" idea on the Esri community site (JavaScript Maps
SDK Ideas) that UGS has commented on; that landing is the trigger. Nothing here is core
infrastructure. It is an accommodation with a defined end, and it should be removed the day it
stops being necessary rather than outliving its reason.

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
or MapLibre draws geometry and silently omits every icon; label renders need the glyphs or no text
draws at all — #116).

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
| `/rest/services/{topic}/VectorTileServer` | descriptor — tile template, LODs, extent, SRS |
| `/rest/services/{topic}/VectorTileServer/tile/{z}/{y}/{x}.pbf` | tiles, **y before x** |
| `/rest/services/{topic}/VectorTileServer/resources/styles/root.json` | the GL style |
| `/rest/services/{topic}/VectorTileServer/resources/fonts/{fontstack}/{range}.pbf` | glyphs — proxied from the CDN set ugs-styles publishes, so Pro never leaves the service for fonts |
| `/rest/services/{topic}/{render}/VectorTileServer` | one service per published symbology (…/tile/…, …/resources/styles/… under it too) |

Paste the `VectorTileServer` URL into **Pro** (Add Data → Data From Path) or **AGOL** (Add layer
from URL, type *ArcGIS Server web service*). Symbology rides along, so no hand-built `.lyrx` per
layer.

**The `/rest/services` prefix is required.** AGOL decides whether a URL is a vector tile
service by matching the path against ArcGIS Server's REST layout, and it does that *before it
makes any request* — so a perfectly correct descriptor at the wrong path is never fetched at all.
Measured in Map Viewer on 2026-07-31:

| URL | Requests reaching this service | AGOL says |
|---|---|---|
| `/esri/{topic}/VectorTileServer` | **zero** | "This service type is not supported." |
| `/rest/services/{topic}/VectorTileServer` | `checkurl` + `?f=json` | adds and renders |

Zero requests is the tell: nothing about the payload was ever in question. The same six routes are
still mounted at `/esri/…` so URLs copied out of the viewer before this keep resolving, but that
form **cannot be added in AGOL** — hand out the `/rest/services` one.

**Confirmed end to end in AGOL, not just past the filter.** A throwaway Cloud Run deployment of
this code was added to Map Viewer twice — `hazards_qfaults` (root-level service) and
`enmin_ucrc_wells/by-purpose` (inside a folder). Both render their published symbology. The
observed sequence, which is the whole contract:

```
checkurl (AGOL server-side)                    200
/rest/services/…/VectorTileServer?f=json       200
…/resources/styles/root.json                   200
…/tile/5/12/6.pbf                              200
…/tile/5/12/7.pbf                              204   empty tile — AGOL handles it
```

Two things that run settled. The layer came in titled **"Hazards qfaults"**, confirming Esri takes
the title from the URL and not the descriptor's `name` (hence `_esri_service_url` dropping the
`/default/` segment). And `by-purpose` — whose paint uses `["get", <computed key>, ["literal",
{…}]]`, the obscurest corner of the expression grammar — drew in full colour rather than falling
through to its grey default, so AGOL's renderer handles it.

**AGOL never requested `/rest/info` or the catalog routes** on this path. They are kept as
standard-shape insurance for Pro and Portal federation, and because the catalog is what makes the
folder-vs-service split explicit — but only the prefix move is *proven* necessary.

LODs are Esri's own numbers, read off a live basemap service: 512px tiles, level 0 at 78271.516964
m/px, Web Mercator top-left origin — which describes the same XYZ grid our tiles use. Deriving them
instead would render off by a zoom level.

A topic with several renders needs one service each: Pro fetches `resources/styles/root.json` with
no query string, so `?render=` is unreachable from it and a bare topic URL only ever exposes
whichever render `_pick_render` defaults to. `enmin_ucrc_wells` has two, babylon basins has eight.
Esri derives the layer *title* from the URL path rather than the descriptor's `name`, so a
per-render service shows up as e.g. "By-purpose" — rename it in Pro if that matters.

**Verified with Esri's own client, not just the spec.** Loaded through the ArcGIS Maps SDK for
JavaScript `VectorTileLayer` — the same `VectorTileServer` contract Pro consumes: `hazards_qfaults`
and `enmin_ucrc_wells/by-purpose` both load with zero errors and render their published symbology.

**That verification is necessary but not sufficient, and this is the lesson.** `new
VectorTileLayer({url})` takes the URL you hand it and fetches. AGOL's "Add layer from URL" first
decides *whether the URL is a vector tile service at all*, by its path — a step the SDK never
performs. So the SDK passed on `/esri/…` while AGOL rejected the identical service outright. When
a client has an add-a-layer UI, the UI is part of the contract; test the UI, not just the loader.

**AGOL is now measured** (see above). **Pro is still untested** — its Add Data → From Path parser
is the same family as AGOL's, so the `/rest/services` path is expected to be what it needs too,
but that is inference. The open questions there are whether it accepts a service URL with no
instance segment, whether it honours a MapLibre-style `tiles` array in the style's source where
Esri's own root.json uses a relative `"url"`, and whether its renderer supports the expression-
driven `icon-image` in `enmin_ucrc_wells/by-boxtype` (a failure there drops every point silently).

One thing that verification did settle: **an Esri vector tile layer requires a style.** A topic with
no published render fails to load outright (`Failed to fetch` on the 404ing style resource, or
`Cannot read properties of null (reading 'sprite')` if `defaultStyles` is omitted) — it does not
draw unstyled. That is why the viewer only offers an ArcGIS link for topics that have a render.

Tiles are for drawing — in Pro a vector tile layer is display-only. For query and analysis Pro wants
the OGC API Features service (`featureserv/`), live over the same GeoParquet.

**Drift risk, unmitigated.** The descriptor is hand-written against a contract Esri controls and
does not version for us; those LOD numbers were read off a live Esri service. If Esri changes the
shape, nothing here fails loudly — a user finds out. The analogue already in this repo is
`featureserv/patches/`, pinned to an upstream SHA so `git apply` breaks the build on drift. The
equivalent here would be a periodic diff of our descriptor's field set against a known-good public
`VectorTileServer`. Not built.

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

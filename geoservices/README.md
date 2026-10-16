# geoservices: an ArcGIS FeatureServer for Map Viewer, Experience Builder and StoryMaps

ArcGIS Online draws OGC API Features (`featureserv/`) in one default symbol. A FeatureServer
carries its own renderer, so this service gives AGOL the same symbology the web viewer draws, with
popups and attribute tables. It is [Koop](https://koopjs.github.io) over the warehouse GeoParquet
on the CDN. There is no data copy, and it scales to zero.

## Routes

| Route | Gives you |
|---|---|
| `/` | every topic and render, with the URL to add |
| `/ugs/rest/services/{topic}/{render}/FeatureServer/0` | one layer per ugs-styles render (`default`, `by-purpose`, …) |

In Map Viewer: **Add layer → Add layer from URL**, paste the `FeatureServer/0` URL. A topic with
two renders (UCRC wells: `by-purpose`, `by-boxtype`) is two layers to add.

## How it works

- **Layers** come from the serving-topics `items.json` (`STAC_COLLECTION_URL`), cached for
  `CACHE_TTL` seconds. A new topic or a re-ingest shows up within that time, with no redeploy.
- **Symbology** is the Esri renderer ugs-styles builds next to each GL style (`build:esri` there).
  The warehouse passes its URL through as `ugs:renders[render].esri_url`. A render without one is
  served with Koop's default symbol.
- **Queries** read the GeoParquet over https with [hyparquet](https://github.com/hyparam/hyparquet)
  (`src/parquet.js`). Row groups are pruned on the bbox covering columns; inside one, the small
  bbox columns pick the matching rows and the page index fetches only the pages holding them. The
  warehouse writes ~100-row pages for this (`vector/sink_archive.py`). Geometry is simplified to the
  request's `maxAllowableOffset`, and the service pages when `where` is `1=1`. Koop does the rest
  (exact geometry tests, `where`, statistics, projection, PBF). Fetched pages stay in a
  `PAGE_CACHE_MB` (default 512) memory cache.
- **`where` is evaluated by Koop**, in memory, on the rows the bbox and ids select.

## Performance

A zoomed-in tile of the heaviest layer (`wetlands_nonriverine`, 279k polygons) fetches 1.5–20 MB
and answers in under a second cold, except where the geometry itself is that large (around the
Great Salt Lake, 3,400 polygons hold 170 MB of WKB). A statewide view of a heavy layer still reads
most of the file. Cold start is about 0.6 s to listen and 0.9 s for the first layer description.
Archives written before the page index read whole column chunks until their next ingest.

## Local

```bash
cd geoservices && npm ci && npm test
PORT=8090 node src/server.js
# Map Viewer refuses http URLs in "Add layer", but takes one in the page URL:
# https://www.arcgis.com/apps/mapviewer/index.html?url=http://localhost:8090/ugs/rest/services/hazards_qfaults/default/FeatureServer/0
```

`STAC_COLLECTION_URL` can be a local file, to test renderers before ugs-styles publishes them.

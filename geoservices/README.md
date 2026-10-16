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
- **Queries** run in DuckDB against the GeoParquet over https (`src/plan.js`). It reads only the
  columns a request needs, prunes row groups by the bbox covering columns, simplifies to the
  request's `maxAllowableOffset`, and pages when `where` is `1=1`. Koop does the rest (exact
  geometry tests, `where`, statistics, projection, PBF).
- **`where` never reaches DuckDB.** It is client SQL, and DuckDB can read files and URLs. Koop
  evaluates it in memory instead.

## Performance

A layer's first view fetches the row groups it touches, then DuckDB serves later requests for
those row groups from memory. Cold start is about 0.7 s to listen and 1.4 s for the first layer
description. Large polygon layers are slow cold (8–15 s for a tile of `wetlands_nonriverine`,
whose 32 MB row groups each span several degrees) and under 1 s warm.

## Local

```bash
cd geoservices && npm ci && npm test
PORT=8090 node src/server.js
# Map Viewer refuses http URLs in "Add layer", but takes one in the page URL:
# https://www.arcgis.com/apps/mapviewer/index.html?url=http://localhost:8090/ugs/rest/services/hazards_qfaults/default/FeatureServer/0
```

`STAC_COLLECTION_URL` can be a local file, to test renderers before ugs-styles publishes them.

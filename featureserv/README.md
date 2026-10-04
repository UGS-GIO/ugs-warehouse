# featureserv: OGC API Features for ArcGIS Pro and QGIS

[pygeoapi](https://pygeoapi.io) over the warehouse GeoParquet, on Cloud Run with scale-to-zero. It
exists only for OGC API clients (ArcGIS Pro, QGIS, ArcGIS Online). The viewers use PMTiles and do
not touch it.

## How it works

- The catalog refresh writes `warehouse/featureserv/collections.json` (`core/feature_service.py`):
  one entry per served STAC item, with its GeoParquet as a `gs://` path and `feature_id` as the id.
- `start.py` reads that file at startup, writes the pygeoapi config (`pygeoapi.base.yml` plus one
  resource per layer) and the OpenAPI document, then starts gunicorn. A new layer is served on the
  next cold start after a refresh. The image build reads nothing from the catalog.
- The service reads the GeoParquet from the bucket as the runtime service account. pyarrow reads
  only the columns and row groups a query needs.

## The plugin

`ugs_parquet.py` subclasses pygeoapi's Parquet provider to fix two gaps in 0.24:

| Gap | Effect without the plugin |
|---|---|
| Geometry is read only from a column named `geometry` | our column is `geom` (the GeoParquet `primary_column`), so every feature has a null geometry |
| `bbox` keeps features inside the box | OGC API Features wants every feature that intersects it, so features crossing the edge of a client's view go missing |

It also filters the GeoParquet 1.0 archives (the `hazards_*` layers), which have no bbox covering,
on their flat `bbox_*` columns.

pygeoapi is pinned exactly in `requirements.txt`. `test_ugs_parquet.py` runs in the image build,
so an upgrade that breaks the plugin fails the build. Delete the plugin when upstream fixes both.

The id is `feature_id`, minted in `vector/transform.py` and also the feature id in PMTiles, so a
feature clicked in the viewer and the same feature fetched over OGC agree. It is a row number, not
a durable key: a client must not store it across ingests.

## Local

```bash
# Mirror the objects: <dir>/<bucket>/warehouse/featureserv/collections.json and each GeoParquet
docker build -t ugs-features featureserv/
docker run -p 9000:9000 -v <dir>:/data:ro -e GCS_MIRROR=/data -e SERVER_URL=http://localhost:9000 ugs-features
# http://localhost:9000/collections
```

## Deploy

`cloudbuild.yaml` builds `featureserv/` into the `ugs-features` image and deploys Cloud Run
`ugs-warehouse-features`: public, port 9000, 1 CPU, 2Gi, `--concurrency=4` to match the 4
gunicorn threads. A local load test peaked at 1.4GB with 12 parallel 10,000-feature pages.

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
   reports: `/collections` returns every collection on one page.
   Click into the search box and the list populates. (The box does not really filter — interacting
   with it is what loads the list.)

Then pick a collection → *Add to map*. Start with a small one — `enmin_ut_counties` (29 features) or
`enmin_ccus_cbcounty` (29) — so a mistake shows up in seconds; `enmin_plss_sections` (84,756) is the
stress case, not the smoke test.

To check conformance without an Esri client at all, load Esri's SDK and construct the layer directly:

```js
require(['esri/layers/OGCFeatureLayer'], async (OGC) => {
  const l = new OGC({ url: 'https://ugs-warehouse-features-xedvkyurga-uc.a.run.app',
                      collectionId: 'enmin_ut_counties' });
  await l.load();
  console.log(l.geometryType, l.objectIdField, l.fields.length);
});
```

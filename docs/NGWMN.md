# NGWMN REST XML Services — Static-Serving Architecture

This document specifies the design, data structures, and pipeline implementation details for serving compliant USGS National Ground-Water Monitoring Network (NGWMN) REST XML services statically directly via the CDN.

## 1. Architectural Philosophy ("The Lake Serves Itself")

In accordance with `docs/SERVING.md`, the warehouse adheres to a pure serverless, CDN-driven design: **"the lake serves itself."** We avoid running active web services or runtime database query layers wherever possible.

Instead of running a runtime FastAPI server that intercepts requests, queries a live PostgreSQL database, and dynamically serializes XML, we **pre-generate all federally compliant XML documents during the database ingest/refresh stage** and upload them as statically cached assets directly to Cloud Storage.

### Payoffs:
1. **Infinite Scale & 100% Uptime:** Served directly via GCS/CDN edge nodes; completely immune to traffic spikes or container cold boots.
2. **Zero Maintenance & Cost:** Eliminates dedicated runtime servers, API connection pooling, and live database compute resources.
3. **Instant Latency:** REST queries are resolved as direct file fetches in microseconds.

---

## 2. Global Cloud Storage Mapping

All static XML files are uploaded to the public warehouse bucket under the `warehouse/ngwmn/` prefix with the standard `Content-Type: application/xml` and `Cache-Control: no-cache` (to ensure the harvester always reads fresh data after pipeline refreshes).

| Endpoint Purpose | Target CDN / GCS Object Path | XML Format |
|---|---|---|
| **Monitoring Locations** (Site Registry) | `warehouse/ngwmn/locations.xml` | NGWMN wellRegistry XML |
| **Water Levels** (Timeseries) | `warehouse/ngwmn/water_levels/{site_no}.xml` | WaterML 2.0 |
| **Lithology Logs** | `warehouse/ngwmn/lithology/{site_no}.xml` | NGWMN lithologyLog XML |
| **Construction Logs** | `warehouse/ngwmn/construction/{site_no}.xml` | NGWMN constructionLog XML |

---

## 3. Upstream Database Mappings

The source data resides in the dedicated `groundwater` schema on the same PostgreSQL instance as the main dbt vector marts. These tables/views are queryable read-only via our existing DuckDB Postgres ATTACH credentials:

*   **Locations:** `groundwater.ugs_ngwmn_monitoring_locations`
*   **Daily Levels:** `groundwater.ugs_ngwmn_daily_levels_view` (or `ugs_ngwmn_waterlevel_spatial`)
*   **Lithology Logs:** `groundwater.ugs_ngwmn_lithology`
*   **Casing Intervals:** `groundwater.ugs_ngwmn_casing`
*   **Screen Intervals:** `groundwater.ugs_ngwmn_screen`

---

## 4. Compliant XML Schema Layouts

### A. Monitoring Locations (Site Registry — `locations.xml`)
Exposes all well locations and basic well characteristics.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<ngwmn:wellRegistry xmlns:ngwmn="http://www.usgs.gov/ngwmn">
  <well>
    <usgs_id>USGS-40001</usgs_id>
    <locationid>UT-DNR-G101</locationid>
    <locationname>Cedar Valley Monitoring Well</locationname>
    <latitude>40.12345</latitude>
    <longitude>-111.98765</longitude>
    <horizontalcoordrefsystem>EPSG:4326</horizontalcoordrefsystem>
    <verticalmeasure>4850.5</verticalmeasure>
    <verticalunit>ft</verticalunit>
    <welldepth>350</welldepth>
  </well>
</ngwmn:wellRegistry>
```

### B. Daily Water Levels (WaterML 2.0 — `water_levels/{site_no}.xml`)
Timeseries format queryable by the site number.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<wml2:Collection xmlns:wml2="http://www.opengis.net/waterml/2.0"
                 xmlns:gml="http://www.opengis.net/gml/3.2"
                 xmlns:om="http://www.opengis.net/om/2.0">
  <wml2:observationMember>
    <om:OM_Observation>
      <om:phenomenonTime>
        <gml:TimePeriod gml:id="tp_1">
          <gml:beginPosition>1995-01-01</gml:beginPosition>
          <gml:endPosition>2026-06-24</gml:endPosition>
        </gml:TimePeriod>
      </om:phenomenonTime>
      <om:result>
        <wml2:MeasurementTimeseries gml:id="ts_1">
          <wml2:point>
            <wml2:MeasurementTVP>
              <wml2:time>2026-06-20</wml2:time>
              <wml2:value>45.2</wml2:value>
            </wml2:MeasurementTVP>
          </wml2:point>
        </wml2:MeasurementTimeseries>
      </om:result>
    </om:OM_Observation>
  </wml2:observationMember>
</wml2:Collection>
```

### C. Lithology Logs (`lithology/{site_no}.xml`)
Stratigraphic intervals per well.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<ngwmn:lithologyLog xmlns:ngwmn="http://www.usgs.gov/ngwmn">
  <lithologyInterval>
    <top>0</top>
    <bottom>25</bottom>
    <description>Alluvium, sand and gravelly clay</description>
  </lithologyInterval>
  <lithologyInterval>
    <top>25</top>
    <bottom>120</bottom>
    <description>Limestone, fractured water-bearing</description>
  </lithologyInterval>
</ngwmn:lithologyLog>
```

### D. Construction Logs (`construction/{site_no}.xml`)
Casing and screen segments detailing well construction.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<ngwmn:constructionLog xmlns:ngwmn="http://www.usgs.gov/ngwmn">
  <casing>
    <top>0</top>
    <bottom>100</bottom>
    <diameter>12.0</diameter>
    <material>Steel</material>
  </casing>
  <screen>
    <top>100</top>
    <bottom>120</bottom>
    <diameter>10.0</diameter>
    <material>Stainless Steel</material>
  </screen>
</ngwmn:constructionLog>
```

---

## 5. Build Pipeline Implementation Script

We will implement a clean, lightweight generator under `src/ugs_warehouse/groundwater/ngwmn.py` queryable via standard python or a dedicated `just groundwater` recipe.

### Implementation Checklist:
- [ ] Create `src/ugs_warehouse/groundwater/__init__.py` (initialize package).
- [ ] Implement `src/ugs_warehouse/groundwater/ngwmn.py` with:
  *   DuckDB-Postgres database readers queryed in-memory.
  *   Dynamic element constructors using Python's standard `xml.etree.ElementTree` or structured string formatting.
  *   Parallelized GCS writer uploading files via `gcs.put_bytes()`.
- [ ] Add `groundwater` recipe to the top-level `justfile` for on-demand execution.

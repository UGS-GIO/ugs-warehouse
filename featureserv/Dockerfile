# OGC API Features for ArcGIS Pro — duckdb_featureserv over the warehouse GeoParquet.
# Scale-to-zero on Cloud Run; spins up on demand. No database, no Postgres: collections are
# DuckDB VIEWS over the GeoParquet on the public CDN (live, lake-native). Everything else uses
# PMTiles/static — this exists only for OGC-API standard clients (Arc Pro / QGIS / federation).

# Stage 1 — build the tiny DuckDB file: one VIEW per serving-topic over its CDN parquet. The
# collection list is a build-time snapshot of the STAC catalog; the data each view returns stays
# live (read at query time via httpfs). Rebuild (redeploy) to pick up newly-piped layers.
FROM python:3.12-slim AS build
WORKDIR /build
RUN pip install --no-cache-dir duckdb==1.1.3
COPY gen_db.py ./
ARG STAC_CATALOG=https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json
ENV STAC_CATALOG=${STAC_CATALOG} MODE=view DB_PATH=/build/database.duckdb
RUN python gen_db.py

# Stage 2 — the off-the-shelf server. featureserv loads spatial + httpfs at query time
# (verified), so the views resolve against the remote parquet. db copied world-writable
# (DuckDB opens read-write).
FROM tobilg/duckdb_featureserv:latest
USER root
COPY --from=build /build/database.duckdb /data/database.duckdb
RUN chmod 0777 /data/database.duckdb
ENV DUCKDBFS_DATABASE_PATH=/data/database.duckdb
# Cloud Run targets this port (--port=9000 in the deploy).
EXPOSE 9000

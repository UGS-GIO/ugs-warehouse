# OGC API Features for ArcGIS Pro — duckdb_featureserv over the warehouse GeoParquet.
# Scale-to-zero on Cloud Run; spins up on demand. No database, no Postgres: collections are
# DuckDB VIEWS over the GeoParquet on the public CDN (live, lake-native). Everything else uses
# PMTiles/static — this exists only for OGC-API standard clients (Arc Pro / QGIS / federation).
#
# The collection list is re-derived from the live STAC catalog on every container start (see
# entrypoint.sh), not baked at build time — a topic ingested standalone is queryable on the next
# cold start instead of waiting for an unrelated push to rebuild this image (#89).

# Stage 1 — the cold floor: a DuckDB file with one VIEW per serving-topic, baked into the image so
# the container has something to serve if the catalog is unreachable at boot. --strict so a broken
# catalog reddens the build here rather than silently shipping an empty db as the fallback.
FROM python:3.12-slim AS build
WORKDIR /build
# Pinned to match the warehouse (single duckdb version repo-wide). The runtime server
# (tobilg/duckdb_featureserv:latest) links libduckdb 1.4.3; it reads this 1.5.3-written
# file fine — duckdb storage is backward-compatible. (The warehouse no longer uses the h3
# community ext, so its old <1.5.4 cap is gone; keep this pin in step with the warehouse duckdb.)
RUN pip install --no-cache-dir duckdb==1.5.3
COPY gen_db.py ./
ARG STAC_CATALOG=https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json
ENV STAC_CATALOG=${STAC_CATALOG} MODE=view
RUN python gen_db.py --out /build/database.duckdb --strict && chmod 0666 /build/database.duckdb

# Stage 2 — the off-the-shelf server binary. Its own image is distroless (no shell, no Python), so
# it can't run gen_db at start; lift the binary + its Go templates onto a Python base instead. It's
# dynamically linked against stock glibc/libstdc++, so it runs unchanged here.
FROM tobilg/duckdb_featureserv:latest AS server

FROM python:3.12-slim
COPY --from=server /duckdb_featureserv /duckdb_featureserv
COPY --from=server /assets /assets
# featureserv loads spatial + httpfs at query time (verified) so the views resolve against the
# remote parquet; it resolves ./assets from the working dir.
WORKDIR /
RUN pip install --no-cache-dir duckdb==1.5.3 \
    && useradd --uid 65532 --create-home --home-dir /home/nonroot nonroot \
    && mkdir -p /data && chown nonroot:nonroot /data
COPY gen_db.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
COPY --from=build --chown=nonroot:nonroot /build/database.duckdb /data/database.duckdb

# Match the upstream image: nonroot, and the Go binary reads the system CA bundle from here.
USER nonroot
ARG STAC_CATALOG=https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    DUCKDBFS_DATABASE_PATH=/data/database.duckdb \
    STAC_CATALOG=${STAC_CATALOG} \
    MODE=view
# Pre-install the DuckDB extensions gen_db needs into this user's extension dir, so a cold start
# LOADs them locally instead of fetching from the extension servers (latency + an external SPOF).
RUN python3 -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL spatial'); c.execute('INSTALL httpfs')"

# Cloud Run targets this port (--port=9000 in the deploy).
EXPOSE 9000
ENTRYPOINT ["/app/entrypoint.sh"]

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

# Refuse to build a response cap the container cannot serve. LimitMax lives in the toml and
# --memory in cloudbuild.yaml; MEMORY_LIMIT is passed from the same substitution the deploy uses, so
# the two cannot drift silently. An unfitting pair does not degrade gracefully -- it OOM-kills the
# container mid-response and 503s (ALL-5410). Also reports the largest collection's feature count,
# which is the number that moves as the catalog grows.
ARG MEMORY_LIMIT=2Gi
COPY check_limits.py duckdb_featureserv.toml ./
RUN python check_limits.py --memory "${MEMORY_LIMIT}" \
      --config duckdb_featureserv.toml --db /build/database.duckdb

# Stage 2 — the server binary, built from pinned upstream source with our patch applied.
#
# We build rather than lift the published image because the gaps that made this service unusable
# for standards clients are compiled in, not configurable: upstream's catalog query hardcodes
# `'' AS id_column`, which drops the GeoJSON `id` from every feature and renders the single-feature
# query as `WHERE "" = $1`; `NumberMatched` is hardcoded to 0 behind `omitempty`; there is no `next`
# rel anywhere in the source; and `/collections` never loads extents. See patches/ for the diff and
# README for what each one broke.
#
# Pinned by SHA, not a tag — `latest` is a moving target and this patch is written against one tree.
# `git apply` fails the build if upstream drifts, which is the intended signal to re-base the patch.
FROM golang:1.24.6-bookworm AS server
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential pkg-config git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Upstream main as of 2025-12-10; unchanged since.
ARG FEATURESERV_REF=7609f027b5f0a062e5f05b96b74fba477e224421
WORKDIR /src
RUN git clone --quiet https://github.com/tobilg/duckdb_featureserv.git . \
    && git checkout --quiet ${FEATURESERV_REF}
COPY patches/ /patches/
RUN git apply --verbose /patches/*.patch
RUN CGO_ENABLED=1 go build -ldflags "-s -w" -o /duckdb_featureserv .

FROM python:3.12-slim
COPY --from=server /duckdb_featureserv /duckdb_featureserv
COPY --from=server /src/assets /assets
# featureserv loads spatial + httpfs at query time (verified) so the views resolve against the
# remote parquet; it resolves ./assets from the working dir.
WORKDIR /
RUN pip install --no-cache-dir duckdb==1.5.3 \
    && useradd --uid 65532 --create-home --home-dir /home/nonroot nonroot \
    && mkdir -p /data && chown nonroot:nonroot /data
COPY gen_db.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
COPY --from=build --chown=nonroot:nonroot /build/database.duckdb /data/database.duckdb
# Server config. Without it featureserv runs on upstream's viper defaults, which cap every /items
# response at 1000 features with no numberMatched and no next link to say so (see the file for the
# per-topic numbers). Root-owned 0644 — nonroot only needs to read it.
COPY duckdb_featureserv.toml /config/duckdb_featureserv.toml

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

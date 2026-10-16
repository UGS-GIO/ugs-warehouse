# ArcGIS FeatureServer for Map Viewer, Experience Builder and StoryMaps: Koop over the warehouse
# GeoParquet, symbolized by the Esri renderers ugs-styles publishes. Scale-to-zero (README.md).
FROM node:22-slim
# DuckDB's httpfs verifies TLS against the system CA store, which the slim image leaves out.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* && useradd --uid 65532 --create-home nonroot
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
COPY test ./test
RUN node --test && rm -r test
USER nonroot
# Install DuckDB's extensions at build time, into the runtime user's home, so a cold start never
# downloads them. httpfs reads the public CDN over https: no GCS credentials or HMAC keys.
RUN node -e "require('@duckdb/node-api').DuckDBInstance.create(':memory:').then((db) => db.connect()).then((c) => c.run('INSTALL spatial; INSTALL httpfs;'))"
ENV STAC_COLLECTION_URL=https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json \
    PORT=8080
EXPOSE 8080
CMD ["node", "src/server.js"]

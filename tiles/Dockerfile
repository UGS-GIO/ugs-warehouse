# XYZ vector tiles for the warehouse serving topics — go-pmtiles over the PMTiles already on the
# CDN, plus a thin app that serves ready-to-use MapLibre styles (see app.py).
#
# No data copy and no new artifact: tiles are range reads against the same PMTiles the viewer uses.
# Scale-to-zero on Cloud Run; behind the CDN it only ever sees cache misses.
FROM ghcr.io/protomaps/go-pmtiles:latest AS pmtiles

FROM python:3.12-slim
# The upstream binary is statically linked, so it runs as-is on this base — no runtime deps to
# match and no reason to build it ourselves.
COPY --from=pmtiles /go-pmtiles /go-pmtiles
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn[standard]==0.34.0" \
    && useradd --uid 65532 --create-home --home-dir /home/nonroot nonroot
COPY app.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

USER nonroot
ENV PMTILES_BUCKET=https://maps-assets.geology.utah.gov/warehouse/pmtiles \
    STAC_COLLECTION_URL=https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json \
    TILES_UPSTREAM=http://127.0.0.1:8081 \
    PORT=8080
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]

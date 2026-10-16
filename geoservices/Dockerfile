# ArcGIS FeatureServer for Map Viewer, Experience Builder and StoryMaps: Koop over the warehouse
# GeoParquet, symbolized by the Esri renderers ugs-styles publishes. Scale-to-zero (README.md).
FROM node:22-slim
RUN useradd --uid 65532 --create-home nonroot
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
COPY test ./test
RUN node --test && rm -r test
USER nonroot
ENV STAC_COLLECTION_URL=https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json \
    PORT=8080
EXPOSE 8080
CMD ["node", "src/server.js"]

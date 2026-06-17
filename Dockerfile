# tippecanoe comes prebuilt from the ugs-tippecanoe base image (compiled once via
# cloudbuild.tippecanoe.yaml) — no per-build compile. Override with --build-arg TIPPECANOE_IMAGE=.
# Cloud Run runtime: uvicorn serves the FastAPI Pub/Sub push handler on :8080.
ARG TIPPECANOE_IMAGE=us-central1-docker.pkg.dev/ut-dnr-ugs-backend-tools/ugs-warehouse/ugs-tippecanoe:latest
FROM ${TIPPECANOE_IMAGE} AS tippecanoe

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps for tippecanoe + duckdb.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libsqlite3-0 zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Bring tippecanoe over from the prebuilt base image.
COPY --from=tippecanoe /usr/local/bin/tippecanoe /usr/local/bin/tile-join /usr/local/bin/

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY service ./service
COPY scripts ./scripts
RUN chmod +x scripts/*.sh

RUN pip install --upgrade pip && pip install .

EXPOSE 8080
CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]

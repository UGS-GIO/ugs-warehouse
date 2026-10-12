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
# Deps layer from uv.lock, cached until the lock changes (NOT on every code edit).
COPY --from=ghcr.io/astral-sh/uv:0.12.12@sha256:73d2665b478d8fa2de1cf105c6841f8e9cb6b09e568fc7700440c09f8fcd7ac4 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-emit-project -q -o /tmp/requirements.txt \
    && pip install --upgrade pip && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Pre-bake the DuckDB spatial extension into the image so the runtime LOADs it locally — no fetch
# from the extension servers on each cold start (removes an external single-point-of-failure + saves
# download latency). Version matches the pinned duckdb; platform matches this base image. Cached with
# the deps layer (re-bakes only on a duckdb bump).
RUN python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL spatial')"

# App layer — only this rebuilds on a source change (deps already satisfied above).
COPY src ./src
COPY service ./service
COPY scripts ./scripts
RUN chmod +x scripts/*.sh && pip install --no-deps --force-reinstall .

EXPOSE 8080
CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]

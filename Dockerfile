# Multi-stage: tippecanoe build, then a slim python runtime.
# Cloud Run runtime: uvicorn serves the FastAPI Pub/Sub push handler on :8080.

FROM debian:bookworm-slim AS tippecanoe-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git ca-certificates libsqlite3-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth=1 https://github.com/felt/tippecanoe.git /src \
    && cd /src \
    && make -j"$(nproc)" \
    && make install PREFIX=/usr/local

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps for tippecanoe + duckdb.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libsqlite3-0 zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Bring tippecanoe over from the builder stage.
COPY --from=tippecanoe-builder /usr/local/bin/tippecanoe /usr/local/bin/
COPY --from=tippecanoe-builder /usr/local/bin/tile-join /usr/local/bin/

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY service ./service
COPY scripts ./scripts

RUN pip install --upgrade pip && pip install .

EXPOSE 8080
CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8080"]

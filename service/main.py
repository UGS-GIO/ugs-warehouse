"""Cloud Run service: Pub/Sub push handler that triggers per-topic ingest.

The handler does NOT ingest inline. It starts the `ugs-warehouse-ingest` Cloud Run job for the
topic and acks immediately. Ingesting inside the request meant a DuckDB/geometry workload ran in a
512Mi HTTP container: it needed a 3600s request timeout, an OOM killed the container mid-request,
and a slow topic produced 429s that Pub/Sub retried into a redelivery storm (~$2.2k of GCS ops in
2026-08). The job is built for it (2Gi, its own timeout) and the handler returns in milliseconds.

Set INGEST_INLINE=1 to ingest in-process instead — local dev and tests.

Pub/Sub push delivers an HTTP POST with envelope:
    { "message": { "data": "<base64-encoded-JSON>", ... }, "subscription": "..." }

The encoded JSON payload (emitted by dataELT `publish.sh`) carries:
    { "schema": "hazards", "topic": "hazards_qfaults_current" }

A 2xx response acks the message; non-2xx triggers Pub/Sub retry (and DLQ if
configured). Per-sink failures are logged but DO NOT fail the ack — they're
recovered by the next publish for that topic.
"""
from __future__ import annotations

import base64
import json
import logging
import os

from fastapi import FastAPI, HTTPException, Request

from ugs_warehouse.raster.consume import consume as consume_raster
from ugs_warehouse.vector.ingest import ingest_topic
from ugs_warehouse.vector.topics import MART_SCHEMAS, from_pubsub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ugs-warehouse.service")

app = FastAPI(title="ugs-warehouse")

INGEST_JOB = os.environ.get("INGEST_JOB", "ugs-warehouse-ingest")
INGEST_JOB_REGION = os.environ.get("INGEST_JOB_REGION", "us-central1")
INGEST_JOB_PROJECT = os.environ.get("INGEST_JOB_PROJECT", "")
INGEST_INLINE = os.environ.get("INGEST_INLINE", "0") == "1"


def _start_ingest_job(topic_fqn: str) -> str:
    """Start the ingest job for one topic. Returns the execution name."""
    from google.cloud import run_v2

    if not INGEST_JOB_PROJECT:
        raise RuntimeError("INGEST_JOB_PROJECT is unset — cannot address the ingest job")
    client = run_v2.JobsClient()
    # Args REPLACE the job's configured `--all`; the entrypoint execs `python -m <module> "$@"`.
    override = run_v2.RunJobRequest.Overrides.ContainerOverride(args=["--topic", topic_fqn])
    op = client.run_job(request=run_v2.RunJobRequest(
        name=f"projects/{INGEST_JOB_PROJECT}/locations/{INGEST_JOB_REGION}/jobs/{INGEST_JOB}",
        overrides=run_v2.RunJobRequest.Overrides(container_overrides=[override], task_count=1),
    ))
    return (op.metadata.name if op.metadata else "") or "(started)"


def _decode(envelope: dict) -> dict:
    """The decoded JSON payload from a Pub/Sub push envelope. 400 on a missing/undecodable body."""
    data_b64 = (envelope.get("message") or {}).get("data")
    if not data_b64:
        raise HTTPException(status_code=400, detail="missing message.data")
    try:
        return json.loads(base64.b64decode(data_b64).decode())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid payload: {e}") from e


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
async def pubsub_push(req: Request) -> dict[str, str]:
    payload = _decode(await req.json())

    # A payload the Topic rules reject is malformed, not transient — retrying redelivers the same
    # bytes. The subscription has no dead-letter policy and a 1-day retention, so a non-2xx here
    # buys 24h of redelivery for a message that can never succeed. Ack + skip, same as the
    # unsupported-schema gate below.
    try:
        topic = from_pubsub(payload)
    except ValueError as e:
        log.warning("skip malformed payload %s: %s", payload, e)
        return {"status": "skipped", "reason": str(e)}

    # dataELT (#418) notifies for every public domain schema, including ones the
    # warehouse can't reach (e.g. gwportal lives in a separate DB). Ack + skip so
    # those don't log a doomed ingest or trigger Pub/Sub retries.
    if topic.schema not in MART_SCHEMAS:
        log.info("skip unsupported schema: %s", topic.fqn)
        return {"status": "skipped", "topic": topic.fqn,
                "reason": f"{topic.schema} not in MART_SCHEMAS"}

    if INGEST_INLINE:
        log.info("ingest start (inline): %s", topic.fqn)
        rc = ingest_topic(topic)
        log.info("ingest done : %s rc=%s", topic.fqn, rc)
        # rc != 0 = at least one sink failed — already logged. Ack anyway so
        # Pub/Sub does not retry-storm; recovery happens on the next publish.
        return {"status": "ok", "topic": topic.fqn, "rc": str(rc)}

    try:
        execution = _start_ingest_job(topic.fqn)
    except Exception as e:  # noqa: BLE001 — a 5xx here is what Pub/Sub retries into a storm
        log.exception("could not start ingest job for %s", topic.fqn)
        return {"status": "error", "topic": topic.fqn, "reason": f"{type(e).__name__}: {e}"}
    log.info("ingest queued: %s -> %s", topic.fqn, execution)
    return {"status": "queued", "topic": topic.fqn, "execution": execution}


@app.post("/raster")
async def pubsub_push_raster(req: Request) -> dict[str, str]:
    """Raster promote handler — a SEPARATE push subscription from `/` (ugs-ingest #183 emits the raster
    promote signal). Payload: `{ "item_id": "...", "layer": "..." }` (item_id required, layer optional).
    Fetches the edition from raw.raster_catalog, promotes its COG + emits STAC, refreshes the catalog."""
    payload = _decode(await req.json())
    item_id = payload.get("item_id")
    if not item_id:
        raise HTTPException(status_code=400, detail="missing item_id")

    try:
        path = consume_raster(item_id)
    except ValueError as e:  # malformed item_id
        raise HTTPException(status_code=400, detail=str(e)) from e

    # No such edition (e.g. not flipped to prod yet) — ack + skip so Pub/Sub doesn't retry-storm; the
    # next promote for this item recovers it.
    if path is None:
        log.info("raster skip: no raw.raster_catalog row for %s", item_id)
        return {"status": "skipped", "item_id": item_id, "reason": "not found"}

    log.info("raster promoted: %s -> %s", item_id, path)
    return {"status": "ok", "item_id": item_id, "item": path}

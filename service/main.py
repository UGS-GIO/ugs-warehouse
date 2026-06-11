"""Cloud Run service: Pub/Sub push handler that triggers per-topic ingest.

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

from fastapi import FastAPI, HTTPException, Request

from ugs_warehouse.vector.ingest import ingest_topic
from ugs_warehouse.vector.topics import from_pubsub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ugs-warehouse.service")

app = FastAPI(title="ugs-warehouse")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
async def pubsub_push(req: Request) -> dict[str, str]:
    envelope = await req.json()
    msg = envelope.get("message") or {}
    data_b64 = msg.get("data")
    if not data_b64:
        raise HTTPException(status_code=400, detail="missing message.data")
    try:
        payload = json.loads(base64.b64decode(data_b64).decode())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid payload: {e}") from e

    try:
        topic = from_pubsub(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    log.info("ingest start: %s", topic.fqn)
    rc = ingest_topic(topic)
    log.info("ingest done : %s rc=%s", topic.fqn, rc)

    # rc != 0 = at least one sink failed — already logged. Ack anyway so
    # Pub/Sub does not retry-storm; recovery happens on the next publish.
    return {"status": "ok", "topic": topic.fqn, "rc": str(rc)}

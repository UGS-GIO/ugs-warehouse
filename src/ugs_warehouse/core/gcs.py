"""Shared GCS IO via obstore — upload, list. ADC auth, no HMAC, no gcsfuse.

Cache-Control belongs here so updates show through the CDN without manual invalidation:
mutable objects (catalog.json, "latest" pointers) get `no-cache` so the CDN revalidates;
immutable/dated artifacts (a dated archive, a content-addressed COG) cache long.
"""
from __future__ import annotations

from pathlib import Path

import obstore as obs
from obstore.store import GCSStore

from . import config

# Sensible Cache-Control presets — pass explicitly at call sites.
CACHE_MUTABLE = "no-cache"               # revalidate every time (catalog.json, latest pointers)
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"   # dated/content-addressed artifacts


def _store() -> GCSStore:
    return GCSStore(bucket=config.BUCKET)


def _attrs(content_type: str, cache_control: str | None) -> dict[str, str]:
    attrs = {"Content-Type": content_type}
    if cache_control:
        attrs["Cache-Control"] = cache_control
    return attrs


def upload(local_path: str, object_path: str, *, content_type: str,
           cache_control: str | None = None) -> None:
    """Upload a local file to `gs://{BUCKET}/{object_path}`."""
    obs.put(_store(), object_path, Path(local_path),
            attributes=_attrs(content_type, cache_control))


def put_bytes(data: bytes, object_path: str, *, content_type: str,
              cache_control: str | None = None) -> None:
    """Write bytes to `gs://{BUCKET}/{object_path}`."""
    obs.put(_store(), object_path, data, attributes=_attrs(content_type, cache_control))


def list_paths(prefix: str) -> list[str]:
    """All object paths under `prefix` (obstore yields batches of metadata dicts)."""
    out: list[str] = []
    for batch in obs.list(_store(), prefix=prefix):
        out.extend(m["path"] for m in batch)
    return out

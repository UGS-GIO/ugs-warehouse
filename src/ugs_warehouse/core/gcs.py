"""Shared GCS IO via obstore — upload, list. ADC auth, no HMAC, no gcsfuse.

Cache-Control belongs here so updates show through the CDN without manual invalidation:
mutable objects (catalog.json, "latest" pointers) get `no-cache` so the CDN revalidates;
immutable/dated artifacts (a dated archive, a content-addressed COG) cache long.
"""
from __future__ import annotations

from pathlib import Path

import obstore as obs
from google.cloud import storage as gcloud_storage
from obstore.store import GCSStore

from . import config

# Sensible Cache-Control presets — pass explicitly at call sites.
CACHE_MUTABLE = "no-cache"               # revalidate every time ("latest" data pointers)
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"   # dated/content-addressed artifacts
# STAC JSON (catalog/collection/items/item): a brief edge cache to spare the CDN a revalidation
# round-trip on every crawl, with stale-while-revalidate so a stale copy serves instantly while it
# refreshes in the background. Edits still propagate within ~max-age; operators reading the bucket
# directly (ops console) see changes immediately regardless.
CACHE_CATALOG = "public, max-age=60, stale-while-revalidate=600"


_cached_store: GCSStore | None = None
_cached_gcs_client: gcloud_storage.Client | None = None


def _store() -> GCSStore:
    global _cached_store
    if _cached_store is None:
        _cached_store = GCSStore(bucket=config.BUCKET)
    return _cached_store


def _gcs_client() -> gcloud_storage.Client:
    # Only used by copy_from_uri's server-side rewrite — obstore has no cross-bucket copy
    # primitive (its copy() takes a single store, i.e. one bucket). google-cloud-storage is
    # already an installed dependency (transitive via firebase-admin), ADC auth, no new footprint.
    global _cached_gcs_client
    if _cached_gcs_client is None:
        _cached_gcs_client = gcloud_storage.Client()
    return _cached_gcs_client


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


def get_bytes(object_path: str) -> bytes:
    """Download an object's bytes from `gs://{BUCKET}/{object_path}`."""
    return bytes(obs.get(_store(), object_path).bytes())


def copy_from_uri(src_uri: str, dest_path: str, *, content_type: str,
                  cache_control: str | None = None) -> None:
    """Copy an object from another bucket (`gs://<bucket>/<key>`) into
    `gs://{BUCKET}/{dest_path}`. Used to promote a staged COG from the ingest bucket
    (`gs://stagedrasters/...`) to the public bucket. Needs read on the source bucket.

    Server-side GCS rewrite — no bytes pass through this process, so COG size doesn't touch
    the container's memory budget. `Blob.rewrite()` (not the simpler `copy_blob`) because it
    loops on a continuation token, which is what makes a single large/cross-location object
    copy reliably instead of risking a timeout on one big call.

    CHANGED 2026-07-27: previously buffered the whole object in memory (obstore get + put).
    That OOM-killed ugs-warehouse-service on its first live raster promote, taking down
    co-tenant tabular-ingest traffic on the same instance with it — see ugs-ingest#183.
    """
    if not src_uri.startswith("gs://"):
        raise ValueError(f"expected a gs:// URI, got {src_uri!r}")
    src_bucket_name, _, key = src_uri[len("gs://"):].partition("/")
    if not src_bucket_name or not key:
        raise ValueError(f"malformed gs:// URI: {src_uri!r}")

    client = _gcs_client()
    src_blob = client.bucket(src_bucket_name).blob(key)
    dest_blob = client.bucket(config.BUCKET).blob(dest_path)
    dest_blob.content_type = content_type
    if cache_control:
        dest_blob.cache_control = cache_control

    token = None
    while True:
        token, _, _ = dest_blob.rewrite(src_blob, token=token)
        if token is None:
            break


def exists(object_path: str) -> bool:
    """True if the object exists (HEAD). Used for skip-if-already-harvested."""
    try:
        obs.head(_store(), object_path)
        return True
    except Exception:
        return False


def delete(object_path: str) -> None:
    """Delete an object. Best-effort — a missing object is not an error (used to clean up strays)."""
    try:
        obs.delete(_store(), object_path)
    except Exception:  # noqa: BLE001 — already gone / race → nothing to clean
        pass


def list_paths(prefix: str) -> list[str]:
    """All object paths under `prefix` (obstore yields batches of metadata dicts)."""
    out: list[str] = []
    for batch in obs.list(_store(), prefix=prefix):
        out.extend(m["path"] for m in batch)
    return out

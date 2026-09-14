"""Shared GCS IO via obstore — upload, list. ADC auth, no HMAC, no gcsfuse.

Cache-Control belongs here so updates show through the CDN without manual invalidation:
mutable objects (catalog.json, "latest" pointers) get `no-cache` so the CDN revalidates;
immutable/dated artifacts (a dated archive, a content-addressed COG) cache long.

Every write returns a `FileMeta` — the size and sha256 the caller needs for the STAC `file`
extension (`file:size` / `file:checksum`). Computed from the bytes as they are written, so no
object is read back to describe it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

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


# multihash prefix for a sha2-256 digest: 0x12 names the function, 0x20 its 32-byte length.
# STAC's file extension requires multihash encoding, not a bare hex digest.
_MULTIHASH_SHA2_256 = "1220"
_HASH_CHUNK = 1 << 20  # stream the file past the hasher — a COG never lands in memory


class FileMeta(NamedTuple):
    """What a write knows about the bytes it wrote. `checksum` is a multihash-encoded sha256,
    or None when the bytes never passed through this process (a server-side copy)."""

    size: int
    checksum: str | None = None


def multihash_sha256(digest: bytes) -> str:
    return _MULTIHASH_SHA2_256 + digest.hex()


def _file_meta(local_path: str) -> FileMeta:
    h = hashlib.sha256()
    size = 0
    with open(local_path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
            size += len(chunk)
    return FileMeta(size, multihash_sha256(h.digest()))


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
           cache_control: str | None = None) -> FileMeta:
    """Upload a local file to `gs://{BUCKET}/{object_path}`."""
    obs.put(_store(), object_path, Path(local_path),
            attributes=_attrs(content_type, cache_control))
    return _file_meta(local_path)


def put_bytes(data: bytes, object_path: str, *, content_type: str,
              cache_control: str | None = None) -> FileMeta:
    """Write bytes to `gs://{BUCKET}/{object_path}`."""
    obs.put(_store(), object_path, data, attributes=_attrs(content_type, cache_control))
    return FileMeta(len(data), multihash_sha256(hashlib.sha256(data).digest()))


def get_bytes(object_path: str) -> bytes:
    """Download an object's bytes from `gs://{BUCKET}/{object_path}`."""
    return bytes(obs.get(_store(), object_path).bytes())


def copy_from_uri(src_uri: str, dest_path: str, *, content_type: str,
                  cache_control: str | None = None) -> FileMeta:
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

    Returns size only. The rewrite already counts the bytes, so the size is free; a sha256
    would cost a download of the whole COG, which is the memory the rewrite exists to avoid.
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

    # Check the byte counts rather than trusting the loop exit. A truncated destination is the
    # worst failure available here — `promote()` would report success and the STAC item would
    # point at a COG that is short. Logging the size also means the next incident starts with
    # the number this one did not have.
    token, done, total = None, 0, 0
    while True:
        token, done, total = dest_blob.rewrite(src_blob, token=token)
        if token is None:
            break
    if total and done != total:
        raise OSError(f"incomplete rewrite {src_uri} -> {dest_path}: {done}/{total} bytes")
    print(f"[gcs] rewrote {src_uri} -> gs://{config.BUCKET}/{dest_path} ({total} bytes)")
    return FileMeta(total or done)


def exists(object_path: str) -> bool:
    """True if the object exists (HEAD). Used for skip-if-already-harvested."""
    try:
        obs.head(_store(), object_path)
        return True
    except Exception:
        return False


class WriteOnceViolation(Exception):
    """Attempt to overwrite an existing write-once (authoritative published) object."""


def upload_write_once(local_path: str, object_path: str, *, content_type: str,
                      cache_control: str | None = None) -> FileMeta:
    """Upload only if `object_path` does not already exist. A published object is never
    overwritten — a revision must be published as a new edition (new object path)."""
    if exists(object_path):
        raise WriteOnceViolation(object_path)
    return upload(local_path, object_path, content_type=content_type, cache_control=cache_control)


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

"""Shared GCS IO via obstore — upload, list. ADC auth, no HMAC, no gcsfuse.

Cache-Control belongs here so updates show through the CDN without manual invalidation:
mutable objects (catalog.json, "latest" pointers) get `no-cache` so the CDN revalidates;
immutable/dated artifacts (a dated archive, a content-addressed COG) cache long.

Every write returns a `FileMeta` — the size and sha256 the caller needs for the STAC `file`
extension (`file:size` / `file:checksum`). Computed from the bytes as they are written, so no
object is read back to describe it. The write also stores both as object metadata, so a builder
that only links to the object (a pub cover, a COG) reads them from a listing (`list_file_meta`).
"""
from __future__ import annotations

import gzip
import hashlib
import sys
from pathlib import Path
from typing import NamedTuple

import obstore as obs
from google.api_core.exceptions import NotFound
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
# Object metadata keys (x-goog-meta-*). The size is stored too: a gzipped object's stored size is
# not the size a consumer receives.
META_SIZE = "file-size"
META_CHECKSUM = "file-checksum"


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


_cached_stores: dict[str, GCSStore] = {}
_cached_gcs_client: gcloud_storage.Client | None = None


def _store(bucket: str | None = None) -> GCSStore:
    name = bucket or config.BUCKET
    if name not in _cached_stores:
        _cached_stores[name] = GCSStore(bucket=name)
    return _cached_stores[name]


def _gcs_client() -> gcloud_storage.Client:
    # A secondary google-cloud-storage client (ADC auth). Two uses: copy_from_uri's server-side
    # cross-bucket rewrite (obstore has no cross-bucket copy primitive), and the get_bytes/exists
    # fallback for gzipped objects obstore can't read (GCS strips Content-Length under decompressive
    # transcoding).
    global _cached_gcs_client
    if _cached_gcs_client is None:
        _cached_gcs_client = gcloud_storage.Client()
    return _cached_gcs_client


def _attrs(content_type: str, cache_control: str | None,
           content_encoding: str | None = None, meta: FileMeta | None = None) -> dict[str, str]:
    attrs = {"Content-Type": content_type}
    if cache_control:
        attrs["Cache-Control"] = cache_control
    if content_encoding:
        attrs["Content-Encoding"] = content_encoding
    if meta is not None:  # any other key becomes user metadata (x-goog-meta-*)
        attrs[META_SIZE] = str(meta.size)
        if meta.checksum:
            attrs[META_CHECKSUM] = meta.checksum
    return attrs


def upload(local_path: str, object_path: str, *, content_type: str,
           cache_control: str | None = None) -> FileMeta:
    """Upload a local file to `gs://{BUCKET}/{object_path}`."""
    meta = _file_meta(local_path)
    obs.put(_store(), object_path, Path(local_path),
            attributes=_attrs(content_type, cache_control, meta=meta))
    return meta


def put_bytes(data: bytes, object_path: str, *, content_type: str,
              cache_control: str | None = None, compress: bool = False) -> FileMeta:
    """Write bytes to `gs://{BUCKET}/{object_path}`.

    `compress` stores the object gzipped with `Content-Encoding: gzip`. GCS then serves it
    compressed to clients that send `Accept-Encoding: gzip` (every browser) and decompresses it
    for those that don't, so the bytes a consumer sees are unchanged. Catalog JSON compresses
    ~25:1 — the 4,212-item mining-district index is 6.3 MB stored plain, 250 KB gzipped.

    The returned FileMeta describes the UNCOMPRESSED bytes, because `file:size` / `file:checksum`
    describe the document a consumer receives, not how it happens to be stored.
    """
    meta = FileMeta(len(data), multihash_sha256(hashlib.sha256(data).digest()))
    body = gzip.compress(data, 6) if compress else data
    obs.put(_store(), object_path, body,
            attributes=_attrs(content_type, cache_control, "gzip" if compress else None, meta))
    return meta


def _gunzip(raw: bytes) -> bytes:
    """Gunzip when the body carries the gzip magic, else return it unchanged.

    The magic is a guess, not a guarantee: arbitrary bytes can start 1f 8b, so a body that merely
    looks gzipped falls through rather than raising out of a plain download.
    """
    if raw[:2] != b"\x1f\x8b":
        return raw
    try:
        return gzip.decompress(raw)
    except OSError:
        return raw


def get_bytes(object_path: str, *, bucket: str | None = None) -> bytes:
    """Download an object's bytes from `gs://{bucket or BUCKET}/{object_path}`.

    Gunzips when the body still carries the gzip magic. GCS decompresses a `Content-Encoding: gzip`
    object for clients that don't ask for it, but whether obstore asks is a detail of its HTTP
    stack, so a caller would otherwise get plain bytes or compressed ones depending on the build.

    Fallback: when GCS serves a `Content-Encoding: gzip` object with decompressive transcoding it
    strips `Content-Length`, and obstore (Rust) raises rather than return the body. Retry through
    google-cloud-storage with `raw_download=True` — the stored bytes, checksum-validated against the
    stored md5 — and let `_gunzip` decompress them. Keeps the gzipped catalog/rollup indexes, and any
    items still stored gzipped by a prior build, readable instead of silently dropping out of
    refresh_catalog / prior_property / overrides. (#341)
    """
    try:
        raw = bytes(obs.get(_store(bucket), object_path).bytes())
    except FileNotFoundError:
        raise  # genuine 404 — preserve the type callers catch; the fallback would only 404 again
    except Exception as e:  # noqa: BLE001 — obstore chokes on the stripped Content-Length; fall back
        # For the permanently-gzipped indexes this IS the read path, so word it as info (not "failed")
        # and keep it to one line — the GenericError repr is a multi-line debug block. Still loud
        # enough that a genuine auth/permission failure (which re-raises from the fallback) is visible.
        print(f"[gcs] {object_path}: obstore cannot read a gzipped object "
              f"({type(e).__name__}: {(str(e).splitlines() or [''])[0]}); reading via google-cloud-storage",
              file=sys.stderr)
        try:
            raw = (_gcs_client().bucket(bucket or config.BUCKET).blob(object_path)
                   .download_as_bytes(raw_download=True))
        except NotFound as nf:
            # google-cloud-storage raises NotFound, not FileNotFoundError; translate it so the fallback
            # keeps get_bytes' one 404 contract — serve/refresh_catalog treat an absent object as a 404,
            # not a 500. (#341)
            raise FileNotFoundError(object_path) from nf
    return _gunzip(raw)


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
    """True if the object exists (HEAD). Used for skip-if-already-harvested + ops-console override
    placement."""
    try:
        obs.head(_store(), object_path)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:  # noqa: BLE001 — obstore.head fails the same way as get on a gzipped object
        # (GCS strips Content-Length), so fall back rather than report a gzipped STAC item as absent —
        # which made ops-console overrides silently not apply to the gzipped items (#341). A genuine
        # auth/transport error surfaces from the fallback instead of the old fail-closed `return False`.
        print(f"[gcs] {object_path}: obstore.head cannot read a gzipped object "
              f"({type(e).__name__}: {(str(e).splitlines() or [''])[0]}); checking via google-cloud-storage",
              file=sys.stderr)
        return _gcs_client().bucket(config.BUCKET).blob(object_path).exists()


def get_tail(object_path: str, length: int) -> bytes:
    """The last `length` bytes of an object, such as a Parquet footer, without the rest of it."""
    size = obs.head(_store(), object_path)["size"]
    return bytes(obs.get_range(_store(), object_path, start=max(0, size - length), end=size))


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


def list_etags(prefix: str) -> dict[str, str]:
    """{path: etag} for every object under `prefix`. An etag changes whenever the object is
    rewritten, so this fingerprints a prefix without reading any object."""
    out: dict[str, str] = {}
    for batch in obs.list(_store(), prefix=prefix):
        out.update((m["path"], m.get("e_tag") or "") for m in batch)
    return out


def _meta_of(blob) -> FileMeta | None:
    """FileMeta from a listed blob: stored metadata first, else the stored size (not for a gzipped
    object, whose stored size is not what a consumer receives)."""
    md = blob.metadata or {}
    if md.get(META_SIZE, "").isdigit():
        return FileMeta(int(md[META_SIZE]), md.get(META_CHECKSUM) or None)
    if blob.content_encoding == "gzip" or not blob.size:
        return None
    return FileMeta(blob.size)


def list_file_meta(prefix: str) -> dict[str, FileMeta]:
    """{path: FileMeta} for every object under `prefix`, from one listing (no object is read).

    google-cloud-storage, not obstore: only the JSON API listing returns custom metadata.
    """
    blobs = _gcs_client().list_blobs(
        config.BUCKET, prefix=prefix,
        fields="items(name,size,contentEncoding,metadata),nextPageToken")
    return {b.name: m for b in blobs if (m := _meta_of(b)) is not None}


def get_file_meta(object_path: str) -> FileMeta | None:
    """FileMeta for one object, or None when it does not exist (a metadata GET, not a listing)."""
    blob = _gcs_client().bucket(config.BUCKET).get_blob(object_path)
    return _meta_of(blob) if blob is not None else None


def hash_object(object_path: str) -> FileMeta:
    """Size + sha256 of an existing object, streamed (for objects written before the metadata)."""
    h = hashlib.sha256()
    size = 0
    for chunk in obs.get(_store(), object_path).stream(min_chunk_size=_HASH_CHUNK):
        h.update(chunk)
        size += len(chunk)
    return FileMeta(size, multihash_sha256(h.digest()))


def set_file_meta(object_path: str, meta: FileMeta) -> None:
    """Store `meta` on an existing object (metadata-only PATCH; the bytes are untouched)."""
    blob = _gcs_client().bucket(config.BUCKET).blob(object_path)
    blob.metadata = {META_SIZE: str(meta.size),
                     **({META_CHECKSUM: meta.checksum} if meta.checksum else {})}
    blob.patch()


def list_paths(prefix: str, *, bucket: str | None = None) -> list[str]:
    """All object paths under `prefix` in `bucket` (default BUCKET); obstore yields batches of
    metadata dicts."""
    out: list[str] = []
    for batch in obs.list(_store(bucket), prefix=prefix):
        out.extend(m["path"] for m in batch)
    return out

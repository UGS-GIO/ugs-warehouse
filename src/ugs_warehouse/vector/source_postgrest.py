"""HTTP source — reads `_current` via the public PostgREST instance.

Bandaid: lets the warehouse run end-to-end without a Postgres login on
mapping-db. Switches off via `SOURCE_BACKEND=postgres` (default) once direct
DB creds arrive. See followup issue.

Coverage notes (as of writing): web_anon has `SELECT` on emp + wetlands +
mapping schemas; hazards + gengis return 401. Topics in those schemas fail
fast here — surface that to the operator instead of silently skipping.

Output shape matches `source.read()`:
  - all business columns from the row
  - `geom_wkb`     : BLOB, re-encoded from PostgREST's GeoJSON
  - `target_epsg`  : INT, extracted from the geom GeoJSON's CRS extension

Caveats:
  - PostgREST default pagination cap is small (~1000 rows); we loop on Range.
  - GeoJSON is much larger over the wire than WKB; per-topic this is fine,
    a full --all backfill is hours.
  - Server-side filtering is not used; we pull the whole `_current` table.

Env:
  POSTGREST_URL   default the public Cloud Run URL
  POSTGREST_PAGE  default 1000 (server cap)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import pyarrow as pa
import shapely

from .topics import MART_SCHEMAS, Topic

POSTGREST_URL = os.environ.get(
    "POSTGREST_URL",
    "https://postgrest-seamlessgeolmap-734948684426.us-central1.run.app",
).rstrip("/")
PAGE = int(os.environ.get("POSTGREST_PAGE", "1000"))
GEOM_COLUMN = "geom"  # UGS dbt convention
# GeoJSON (RFC 7946): absence of a `crs` member means WGS84 / EPSG:4326.
DEFAULT_EPSG = 4326
_EPSG_RE = re.compile(r"EPSG:(\d+)", re.IGNORECASE)


def _http(
    url: str,
    profile: str,
    range_header: str | None = None,
    accept: str = "application/json",
) -> tuple[bytes, dict]:
    req = urllib.request.Request(url)
    req.add_header("Accept-Profile", profile)
    req.add_header("Accept", accept)
    if range_header is not None:
        req.add_header("Range-Unit", "items")
        req.add_header("Range", range_header)
        req.add_header("Prefer", "count=exact")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read(), dict(resp.headers)


def _fetch_page(topic: Topic, start: int, end: int) -> tuple[list[dict], int]:
    url = f"{POSTGREST_URL}/{topic.layer}?select=*"
    body, headers = _http(url, topic.schema, f"{start}-{end}")
    rows = json.loads(body.decode())
    cr = headers.get("Content-Range", "")
    tail = cr.split("/")[-1] if "/" in cr else ""
    total = int(tail) if tail.isdigit() else len(rows)
    return rows, total


def _extract_epsg(geom_geojson) -> int | None:
    """Pull EPSG int from the GeoJSON CRS extension; None if absent."""
    if not isinstance(geom_geojson, dict):
        return None
    crs = geom_geojson.get("crs") or {}
    name = (crs.get("properties") or {}).get("name") or ""
    m = _EPSG_RE.search(name)
    return int(m.group(1)) if m else None


def _to_wkb(geom_geojson) -> bytes | None:
    if geom_geojson is None:
        return None
    if isinstance(geom_geojson, str):
        try:
            geom_geojson = json.loads(geom_geojson)
        except json.JSONDecodeError:
            return None
    # shapely doesn't grok GeoJSON's CRS extension — drop before passing through.
    g = {k: v for k, v in geom_geojson.items() if k != "crs"}
    shp = shapely.from_geojson(json.dumps(g))
    return shapely.to_wkb(shp)


def read(topic: Topic) -> pa.Table:
    """Pull `{schema}.{layer}` via PostgREST into a pyarrow Table.

    Adds `geom_wkb` (BLOB) and `target_epsg` (INT, extracted from geom CRS).
    """
    rows: list[dict] = []
    start = 0
    while True:
        page, total = _fetch_page(topic, start, start + PAGE - 1)
        if not page:
            break
        rows.extend(page)
        if len(rows) >= total:
            break
        start += len(page)

    if not rows:
        return pa.table({})

    for r in rows:
        g = r.pop(GEOM_COLUMN, None)
        r["geom_wkb"] = _to_wkb(g)
        # Pre-cutover rows carry an explicit CRS extension (e.g. 3857); already-
        # 4326 rows have no crs member. Default to 4326 rather than dropping the
        # column, which would break transform's reproject CASE on target_epsg.
        epsg = _extract_epsg(g)
        r["target_epsg"] = epsg if epsg is not None else DEFAULT_EPSG

    return pa.Table.from_pylist(rows)


def read_metadata(topic: Topic) -> dict:  # noqa: ARG001
    """PostgREST exposes no schema_registry — descriptive metadata is Postgres-only."""
    return {}


def discover() -> list[Topic]:
    """Enumerate `_current` tables PostgREST advertises per mart schema.

    Note: existence in the OpenAPI does NOT imply web_anon has SELECT — some
    rows here will 401 on `read()`. The ingest loop's per-topic isolation
    surfaces those instead of crashing the run.
    """
    found: list[Topic] = []
    for schema in MART_SCHEMAS:
        url = f"{POSTGREST_URL}/"
        try:
            body, _ = _http(url, schema, accept="application/openapi+json")
        except urllib.error.HTTPError as e:
            if e.code == 406:
                continue  # schema not exposed via this PostgREST profile
            raise
        spec = json.loads(body.decode())
        for path in spec.get("paths", {}):
            name = path.lstrip("/")
            if name.endswith("_current"):
                found.append(Topic(layer=name, schema=schema))
    return found

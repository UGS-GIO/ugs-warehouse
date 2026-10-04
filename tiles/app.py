"""XYZ vector tiles + ready-to-use MapLibre styles for the warehouse serving topics.

`go-pmtiles serve` (subprocess, loopback) does the PMTiles directory→offset translation and the
range reads against the CDN. This app sits in front of it for the two things it cannot do:

  * flattens its `/{name}/{name}/{z}/{x}/{y}.mvt` (our PMTiles are nested one dir per topic) down
    to `/tiles/{topic}/{z}/{x}/{y}.mvt`;
  * assembles a COMPLETE MapLibre style per topic. ugs-styles publishes a style *fragment* — a
    `layers` array with paint/filter rules and no `sources`, because a fragment is portable across
    PMTiles and XYZ. A client pointed at a fragment renders nothing, so `/styles/{topic}.json`
    binds it to this service's tile URL and injects the `source-layer`.

Nothing is stored here. The topic list and the style binding are read from the STAC catalog on
demand (derive-from-truth) and cached for CACHE_TTL seconds.

Env: TILES_UPSTREAM, STAC_COLLECTION_URL, PUBLIC_URL, CACHE_TTL, HTTP_TIMEOUT.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urljoin

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

UPSTREAM = os.environ.get("TILES_UPSTREAM", "http://127.0.0.1:8081")
PMTILES_BUCKET = os.environ.get("PMTILES_BUCKET", "")
PMTILES_PORT = os.environ.get("PMTILES_PORT", "8081")
PMTILES_CACHE_MB = os.environ.get("PMTILES_CACHE_MB", "64")
COLLECTION_URL = os.environ.get(
    "STAC_COLLECTION_URL",
    "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json",
)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
CACHE_TTL = float(os.environ.get("CACHE_TTL", "300"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "10"))
# Fallback for a render that names no glyphs of its own — ugs-styles publishes these (#116).
GLYPHS_URL = os.environ.get(
    "GLYPHS_URL", "https://maps-assets.geology.utah.gov/styles/fonts/{fontstack}/{range}.pbf")

_cache: dict[str, tuple[float, object]] = {}
_served: dict[str, str] = {}          # topic -> the version this process has served tiles for
_child: subprocess.Popen | None = None
_child_lock = threading.Lock()
_last_restart = 0.0
RESTART_DEBOUNCE = 5.0  # seconds; a restart already picks up every topic's current archive
_rechecked: dict[str, float] = {}     # topic -> when an unrecognised version last forced a refetch
UNVERSIONED = "0"  # a topic whose version is unknown: served, but never forever-cached or restarted for
VERSION_RECHECK = 30.0  # seconds; bounds the CDN GETs a stream of made-up versions can cause


def _spawn() -> subprocess.Popen:
    return subprocess.Popen([  # noqa: S603 (fixed argv, no shell)
        "/go-pmtiles", "serve", "/",
        f"--bucket={PMTILES_BUCKET}",
        f"--port={PMTILES_PORT}",
        "--interface=127.0.0.1",
        f"--cache-size={PMTILES_CACHE_MB}",
    ])


def _wait_ready(timeout: float = 20.0) -> None:
    """Block until the child answers on loopback.

    Ready means *listening*, not 2xx — go-pmtiles has no health route and answers `/` with a 404,
    so an HTTPError is proof it is up. Only a refused connection means keep waiting; treating any
    error as not-ready would spin here for the whole timeout on every start.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{UPSTREAM}/", timeout=1)  # noqa: S310 (loopback)
        except urllib.error.HTTPError:
            return
        except Exception:  # noqa: BLE001 — connection refused / not up yet
            time.sleep(0.05)
            continue
        return
    print("[tiles] go-pmtiles did not come up within "
          f"{timeout:.0f}s — serving anyway, requests will retry", flush=True)


def _restart_child(reason: str) -> None:
    """Replace the go-pmtiles process.

    It caches each archive's directory in memory and never revalidates over the HTTP backend —
    verified: swap a different archive in at the same URL and it keeps serving the old one, with a
    single header fetch in its log. There is no invalidation API (the admin port exposes only
    /metrics), and `--cache-size=0` hangs. So a re-ingest means a new process; it is stateless and
    comes back in about a second.
    """
    global _child, _last_restart
    with _child_lock:
        # Concurrent tile requests all notice a new version at once; one restart covers them all.
        if time.monotonic() - _last_restart < RESTART_DEBOUNCE:
            return
        _last_restart = time.monotonic()
        print(f"[tiles] restarting go-pmtiles: {reason}", flush=True)
        if _child and _child.poll() is None:
            _child.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                _child.wait(timeout=10)
        _child = _spawn()
    _wait_ready()


def _is_current(topic: str, version: str) -> bool:
    """Is `version` the topic's published version? Another instance can mint a re-ingest's new URL
    before our TTL'd copy moves, so a mismatch refetches once, but at most every VERSION_RECHECK
    per topic: anyone can put any string in the URL."""
    if version == UNVERSIONED:
        return False
    if version == _version(topic):
        return True
    now = time.monotonic()
    if now - _rechecked.get(topic, -math.inf) < VERSION_RECHECK:
        return False
    _rechecked[topic] = now
    key = f"version:{topic}"
    if key in _cache:
        _cache[key] = (-math.inf, _cache[key][1])  # expire, but keep it for _cached's stale-on-error
    return version == _version(topic)


def _note_version(topic: str, version: str) -> None:
    """A tile asked for under a version we have not served means the archive was re-ingested since
    this process cached its directory. Restart before serving, or the answer is silently stale.
    Only call it with a published version (_is_current), or a made-up one restarts the server."""
    if _served.setdefault(topic, version) != version:
        _served[topic] = version
        _restart_child(f"{topic} changed version")


@contextlib.asynccontextmanager
async def _lifespan(_app):
    global _child
    _child = _spawn()
    _wait_ready()
    yield
    with _child_lock:
        if _child and _child.poll() is None:
            _child.terminate()


app = FastAPI(title="ugs-warehouse-tiles", lifespan=_lifespan)
# Every route here is cross-origin by design — the viewer, the ArcGIS JS SDK, Pro/AGOL and arbitrary
# MapLibre clients all fetch from another origin. Setting the header per-response meant each new
# route was a fresh chance to forget it, which is exactly how the empty-tile 204s shipped without
# one (#109). Middleware covers /healthz, /, /tilejson and anything added later by construction.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_credentials=False)


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:  # noqa: S310 (trusted https CDN)
        return r.read()


def _cached(key: str, produce):
    """TTL memo. Stale-on-error: a CDN blip serves the last good value rather than a 502, since
    the catalog changes on ingest (minutes) and not per request."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        value = produce()
    except Exception:
        if hit:
            # Serve it again for VERSION_RECHECK before retrying, not a blocking GET per request.
            _cache[key] = (time.monotonic() - CACHE_TTL + VERSION_RECHECK, hit[1])
            return hit[1]
        raise
    _cache[key] = (time.monotonic(), value)
    return value


def _topics() -> dict[str, dict]:
    """{topic id: index entry} for every topic with PMTiles. One GET for the whole catalog."""
    def load():
        doc = json.loads(_get(COLLECTION_URL))
        return {item["id"]: item for item in doc.get("items", [])
                if "pmtiles" in (item.get("assets") or {})}
    return _cached("topics", load)


def _renders(topic: str) -> dict[str, dict]:
    """`ugs:renders` for a topic — {render name: render}, each with a `style_url`.

    Read `ugs:renders`, NOT the `style` asset: that asset only exists for a render literally named
    `default`, and topics styled by attribute (`enmin_ucrc_wells` → `by-boxtype`/`by-purpose`,
    `hazards_debrisflow_babylon_basins` → eight `likelihood-*`) have none, so keying off the asset
    silently drops them. The fragment URL is keyed by *layer* name (`..._current`), not topic id,
    so it cannot be derived by convention either.
    """
    renders = (_topics().get(topic, {}).get("properties") or {}).get("ugs:renders") or {}
    return {k: v for k, v in renders.items() if v.get("style_url")}


def _version(topic: str) -> str:
    """Short, URL-safe token that changes when the topic's data changes.

    Derived from the item's `ugs:content_hash`, which the ingest already stamps per topic. It is
    only on the per-item doc (the index omits it), so this costs one extra GET per topic per TTL.
    Falls back to the item's datetime, which also moves on re-ingest, if the hash is ever absent.
    """
    def load():
        # The index knows where each item lives; the path nests by mart schema, so don't guess it.
        links = (_topics().get(topic) or {}).get("links") or []
        href = next((ln.get("href") for ln in links if ln.get("rel") == "self"), None)
        if not href:
            print(f"[tiles] index entry for {topic} has no self link; serving it unversioned",
                  flush=True)
            return UNVERSIONED
        url = urljoin(COLLECTION_URL, href)
        try:
            props = json.loads(_get(url)).get("properties") or {}
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise  # a CDN blip: _cached keeps serving the last good version
            # A topic listed in a cached index but since removed.
            print(f"[tiles] no item doc for {topic} at {url} ({e}); serving it unversioned",
                  flush=True)
            return UNVERSIONED
        raw = props.get("ugs:content_hash") or props.get("datetime") or "0"
        # The raw hash carries a ':' and is long; a digest keeps it opaque and path-safe.
        return hashlib.sha1(str(raw).encode()).hexdigest()[:12]  # noqa: S324 (cache key, not crypto)
    try:
        return _cached(f"version:{topic}", load)
    except Exception as e:  # noqa: BLE001 (no version ever fetched and the CDN is failing)
        print(f"[tiles] no version for {topic} yet ({e}); serving it unversioned", flush=True)
        return UNVERSIONED  # not cached, so the next request tries again


def _pick_render(topic: str, name: str | None) -> tuple[str, dict]:
    renders = _renders(topic)
    if not renders:
        raise HTTPException(404, f"no published style for {topic}")
    if name:
        if name not in renders:
            raise HTTPException(404, f"unknown render '{name}' for {topic} "
                                     f"(have: {', '.join(sorted(renders))})")
        return name, renders[name]
    # No render is privileged upstream, so fall back deterministically rather than arbitrarily.
    key = "default" if "default" in renders else sorted(renders)[0]
    return key, renders[key]


def _upstream(path: str) -> tuple[bytes, int, dict]:
    """GET from the go-pmtiles child, restarting it once if the loopback is refused.

    Every route needs this, not just tiles: a dead child means /styles and /tilejson cannot read
    the archive metadata either. Restarting on the first refused connection costs one request
    instead of failing everything until the instance recycles.
    """
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(f"{UPSTREAM}{path}", timeout=HTTP_TIMEOUT) as r:  # noqa: S310
                return r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            raise HTTPException(e.code, f"upstream {e.code} for {path}") from e
        except urllib.error.URLError as e:
            if attempt == 2:
                raise HTTPException(503, "tile backend unavailable") from e
            _restart_child("child not answering")
    raise HTTPException(503, "tile backend unavailable")  # unreachable


def _pmtiles_metadata(topic: str) -> dict:
    """tippecanoe metadata as go-pmtiles reports it — the source of the `source-layer` name, the
    zoom range, and the bounds. Read from the archive itself rather than assumed, because a topic
    whose tiling options change would otherwise get a style that lies about its zooms."""
    return _cached(f"meta:{topic}", lambda: json.loads(_upstream(f"/{topic}/{topic}/metadata")[0]))


def _base_url(request: Request) -> str:
    """This service's public origin. PUBLIC_URL wins; otherwise honour the proxy's forwarded
    scheme, since Cloud Run terminates TLS and the request arrives as plain http."""
    if PUBLIC_URL:
        return PUBLIC_URL
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


def _tile_url(base: str, topic: str) -> str:
    """Version-keyed so a re-ingest changes every tile URL — the edge misses instead of serving
    the previous archive's tiles, and it tells this process the archive moved."""
    return f"{base}/tiles/{topic}/{_version(topic)}/{{z}}/{{x}}/{{y}}.mvt"


def _is_folder(topic: str) -> bool:
    """Does this topic's Esri surface need a folder, i.e. more than the one `default` service?

    Under Esri's layout `/rest/services/{a}/VectorTileServer` is a root-level SERVICE and
    `/rest/services/{a}/{b}/VectorTileServer` is service `b` inside FOLDER `a`. A name is one or
    the other, never both — real ArcGIS catalogs never list the same name in `folders` and
    `services`.
    """
    return sorted(_renders(topic)) not in ([], ["default"])


def _esri_service_url(base: str, topic: str, render: str) -> str:
    """The addable URL for one symbology.

    A topic whose only render is `default` is a root-level service, so it must NOT carry the
    render segment. Two reasons, and the second is the one users feel: the catalog at
    `/rest/services` lists it as a bare service, so the two would disagree and AGOL would end up
    with two portal items for one layer — and Esri derives the layer TITLE from the URL path, so
    the `/default/` form imports as a layer literally named "Default" (measured: `/hazards_qfaults
    /VectorTileServer` titles "Hazards qfaults", `/hazards_qfaults/default/VectorTileServer`
    titles "Default").
    """
    seg = "" if render == "default" and not _is_folder(topic) else f"/{quote(render, safe='')}"
    return f"{base}{ESRI_PREFIX}/{quote(topic, safe='')}{seg}/VectorTileServer"


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/")
def index(request: Request) -> dict:
    base = _base_url(request)
    # One item GET per topic for the version token. Serial, that is ~5s on a cold cache for 28
    # topics; they are independent network calls, so warm them in parallel first.
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(_version, _topics()))
    return {
        "title": "UGS warehouse — XYZ vector tiles",
        "collections": [
            {"id": t,
             "tiles": _tile_url(base, t),
             "tilejson": f"{base}/tilejson/{t}.json",
             # One style per published render. A topic with no render serves tiles but has no
             # style to offer — say so with an empty list rather than a link that 404s.
             "styles": {r: f"{base}/styles/{t}.json?render={quote(r, safe='')}"
                        for r in sorted(_renders(t))},
             # Empty when nothing styles it — Esri can't add a layer with no style.
             "arcgis": {r: _esri_service_url(base, t, r) for r in sorted(_renders(t))}}
            for t in sorted(_topics())
        ],
    }


@app.get("/tilejson/{topic}.json")
def tilejson(topic: str, request: Request) -> dict:
    if topic not in _topics():
        raise HTTPException(404, f"unknown topic: {topic}")
    meta = _pmtiles_metadata(topic)
    layer = (meta.get("vector_layers") or [{}])[0]
    doc = {
        "tilejson": "3.0.0",
        "name": topic,
        "scheme": "xyz",
        "tiles": [_tile_url(_base_url(request), topic)],
        "minzoom": layer.get("minzoom", 0),
        "maxzoom": layer.get("maxzoom", 14),
        "vector_layers": meta.get("vector_layers") or [],
    }
    if bounds := meta.get("antimeridian_adjusted_bounds"):
        doc["bounds"] = [float(v) for v in bounds.split(",")]
    return doc


@app.get("/styles/{topic}.json")
def style(topic: str, request: Request, render: str | None = None) -> Response:
    """A complete MapLibre GL style for one topic — the ugs-styles fragment bound to this
    service's tiles. Point MapLibre at this URL and it renders; the fragment alone cannot.

    `?render=` selects among a topic's renders (`by-purpose`, `by-boxtype`, …).
    """
    if topic not in _topics():
        raise HTTPException(404, f"unknown topic: {topic}")
    name, chosen = _pick_render(topic, render)
    fragment = _cached(f"style:{topic}:{name}", lambda: json.loads(_get(chosen["style_url"])))
    meta = _pmtiles_metadata(topic)
    layer = (meta.get("vector_layers") or [{}])[0]
    # source-layer is the MVT layer name, which tippecanoe took from -l. Read it back rather than
    # assuming it equals the topic id — a mismatch renders an empty map with no error anywhere.
    source_layer = layer.get("id", topic)
    source = {
        "type": "vector",
        "tiles": [_tile_url(_base_url(request), topic)],
        "minzoom": layer.get("minzoom", 0),
        "maxzoom": layer.get("maxzoom", 14),
    }
    if bounds := meta.get("antimeridian_adjusted_bounds"):
        source["bounds"] = [float(v) for v in bounds.split(",")]
    doc = {
        "version": 8,
        "name": f"{topic} ({name})",
        "sources": {topic: source},
        # The fragment owns paint/filter/layout; we own only the binding to a source. Anything the
        # fragment already declares wins, so a hand-set source in ugs-styles is not overwritten.
        "layers": [{"source": topic, "source-layer": source_layer, **lyr}
                   for lyr in fragment.get("layers", [])],
    }
    # Icon renders point at a sprite sheet published alongside the fragment; without it MapLibre
    # draws the geometry and silently omits every icon.
    for key in ("sprite", "glyphs"):
        if value := (fragment.get(key) or chosen.get(key)):
            doc[key] = value
    # Text layers with no `glyphs` render nothing; covers a fragment bound before ugs-styles had them.
    if "glyphs" not in doc and any("text-field" in (lyr.get("layout") or {})
                                   for lyr in doc["layers"]):
        doc["glyphs"] = GLYPHS_URL
    return Response(json.dumps(doc), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=300"})


def _proxy_tile(topic: str, z: int, x: int, y: int, cache: str) -> Response:
    """Flattened tile route. go-pmtiles addresses our archives as `{topic}/{topic}` because they
    live one directory per topic on the CDN; consumers should not have to say it twice."""
    try:
        body, status, hdrs = _upstream(f"/{topic}/{topic}/{z}/{x}/{y}.mvt")
    except HTTPException as exc:
        # Past maxLOD — or off the archive's grid — go-pmtiles 404s. For a topic we serve that is
        # an ABSENT tile, not an error: our descriptor advertises maxzoom 22 over maxLOD 14, so a
        # client that overzooms by REQUESTING deeper tiles (rather than rescaling the last cached
        # level) is inside the range we published. Esri's own services answer those with an empty
        # tile; we answered with `404 application/json` where protobuf was asked for (#117).
        # An unknown topic still 404s — that one is a real error.
        if exc.status_code == 404 and topic in _topics():
            return Response(status_code=204)
        raise
    ctype = hdrs.get("Content-Type", "")
    encoding = hdrs.get("Content-Encoding")
    if status == 204 or not body:
        # A browser enforces CORS on 204s too, and most of a sparse point layer's low-zoom grid
        # is empty tiles — so this response needs the header as much as any other (#109).
        return Response(status_code=204)  # empty tile: absent, not an error
    headers = {"Cache-Control": cache}
    if encoding:
        headers["Content-Encoding"] = encoding  # pass gzip through; do not re-compress
    return Response(body, media_type=ctype or "application/x-protobuf", headers=headers)


@app.get("/tiles/{topic}/{version}/{z}/{x}/{y}.mvt")
def tile(topic: str, version: str, z: int, x: int, y: int) -> Response:
    """The URL the generated styles hand out. `version` is a content token, so these bytes can be
    cached forever — a re-ingest mints new URLs rather than invalidating old ones. Any other
    version (a stale style, or a made-up string) gets today's bytes, briefly cached."""
    if topic not in _topics():
        raise HTTPException(status_code=404, detail=f"unknown topic {topic}")
    if not _is_current(topic, version):
        # Short-lived: if this is a re-ingest's new URL we haven't recognised yet, the bytes may be
        # the old archive's, and they must not outlive the next recheck.
        return _proxy_tile(topic, z, x, y, f"public, max-age={VERSION_RECHECK:.0f}")
    _note_version(topic, version)
    return _proxy_tile(topic, z, x, y, "public, max-age=31536000, immutable")


@app.get("/tiles/{topic}/{z}/{x}/{y}.mvt")
def tile_unversioned(topic: str, z: int, x: int, y: int) -> Response:
    """Convenience for hand-written clients. Carries no version, so it cannot be cached hard and
    cannot signal a re-ingest — prefer the versioned URL from /styles or /tilejson."""
    return _proxy_tile(topic, z, x, y, "public, max-age=300")


# --- ArcGIS Pro / AGOL -------------------------------------------------------------------------
# Pro and ArcGIS Online will not read PMTiles, and they will not take a bare /{z}/{x}/{y} endpoint
# either — not because the tiles are wrong (Esri vector tiles ARE plain MVT, the same bytes we
# already serve) but because nothing tells them what the tiles are. An Esri vector tile service is
# three documents: a descriptor, tiles at /tile/{z}/{y}/{x}.pbf (y before x), and a GL style at
# resources/styles. We already have the last two in all but name.
#
# The LOD table below is Esri's own, read off a live basemap service rather than derived: 512px
# tiles, level 0 at 78271.516964 m/px, Web Mercator origin at the top-left corner of the world.
# That pairing describes exactly the standard XYZ grid our tiles use (512 x 78271 = one world-wide
# tile at z0), so the addressing lines up; using Esri's numbers verbatim avoids a subtle mismatch
# that would render as a map offset by a zoom level.
#
# The PATH these live under is itself part of the contract, and it is not free-form. AGOL's "Add
# layer from URL" decides whether a URL is a vector tile service by pattern-matching the path
# against ArcGIS Server's REST layout BEFORE it makes any request — so a correct descriptor at the
# wrong path is never even fetched. Measured in Map Viewer on 2026-07-31:
#
#   /esri/{topic}/VectorTileServer           -> "This service type is not supported."
#                                               ZERO requests reached this service.
#   /rest/services/{topic}/VectorTileServer  -> got past the check; AGOL called its own
#                                               sharing/rest/portals/checkurl and then fetched
#                                               `?f=json` here (404 at the time — the path did
#                                               not exist yet).
#
# Hence ESRI_PREFIX below. `/esri` stays mounted as an alias so URLs already copied out of the
# viewer keep resolving; everything we hand out now uses the REST-shaped path.
#
# Note this is specifically Map Viewer's URL parser. `new VectorTileLayer({url})` in the ArcGIS
# Maps SDK bypasses it entirely and loads the `/esri/...` form happily, which is why the SDK-based
# verification in README.md passed while AGOL itself never worked.
ESRI_PREFIX = "/rest/services"
_ESRI_R0, _ESRI_S0 = 78271.516964, 295828763.7957775
_WEB_MERCATOR_ORIGIN = 20037508.342787


_ESRI_MAX_LEVEL = 22  # the standard scheme's depth, independent of how deep our tiles go


def _esri_lods() -> list[dict]:
    """The full LOD scheme, NOT one truncated at our data's maxzoom.

    Esri's own services list every level and separately report `maxLOD` as the deepest cached one
    (OpenStreetMap_v2: 23 lods, maxLOD 16, maxzoom 22). Clients overzoom past maxLOD by scaling the
    last cached level. Truncating the list instead pins the client at our maxzoom, which on a
    statewide layer means it simply refuses to zoom in past z14.
    """
    return [{"level": z, "resolution": _ESRI_R0 / (2 ** z), "scale": _ESRI_S0 / (2 ** z)}
            for z in range(_ESRI_MAX_LEVEL + 1)]


def _to_3857(lon: float, lat: float) -> tuple[float, float]:
    x = lon * _WEB_MERCATOR_ORIGIN / 180.0
    y = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    return x, y * _WEB_MERCATOR_ORIGIN / 180.0


def _esri_extent(meta: dict) -> dict:
    bounds = meta.get("antimeridian_adjusted_bounds")
    w, s, e, n = ([float(v) for v in bounds.split(",")] if bounds
                  else [-114.05, 36.99, -109.04, 42.01])
    xmin, ymin = _to_3857(w, s)
    xmax, ymax = _to_3857(e, n)
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
            "spatialReference": {"wkid": 102100, "latestWkid": 3857}}


def _esri_descriptor(topic: str, request: Request, render: str | None) -> Response:
    """The document Pro and AGOL read before they will accept the layer."""
    if topic not in _topics():
        raise HTTPException(404, f"unknown topic: {topic}")
    # An Esri vector tile layer REQUIRES a style: with none, the client fails on the 404ing style
    # resource rather than drawing unstyled. Serving a descriptor here would hand out a URL that
    # cannot work, and would contradict `/rest/services`, which omits these topics for this reason.
    if not _renders(topic):
        raise HTTPException(404, f"no published style for {topic}; nothing Esri can add")
    name, _ = _pick_render(topic, render)
    meta = _pmtiles_metadata(topic)
    layer = (meta.get("vector_layers") or [{}])[0]
    maxzoom = int(layer.get("maxzoom", 14))
    extent = _esri_extent(meta)
    doc = {
        "currentVersion": 11.2,
        # NOT what the client shows in the layer list — measured through the ArcGIS Maps SDK, the
        # title comes from the URL's last segment before `VectorTileServer` (`.../hazards_qfaults/
        # VectorTileServer` titles "Hazards qfaults"; `.../hazards_qfaults/default/...` titles
        # "Default"). That is why a single-`default` topic is addressed WITHOUT the render segment
        # (see `_esri_service_url`). This field is still worth setting correctly — it is what a
        # human sees when they open the descriptor — but do not rely on it reaching the UI.
        "name": f"{topic} ({name})" if render and name else topic,
        "copyrightText": "Utah Geological Survey",
        "capabilities": "TilesOnly",
        "type": "indexedVector",
        "tiles": ["tile/{z}/{y}/{x}.pbf"],
        "defaultStyles": "resources/styles",
        "exportTilesAllowed": False,
        "initialExtent": extent,
        "fullExtent": extent,
        "minScale": 0,
        "maxScale": 0,
        "maxzoom": _ESRI_MAX_LEVEL,   # how far a client may zoom; past maxLOD it overzooms
        "minLOD": int(layer.get("minzoom", 0)),
        "maxLOD": maxzoom,            # deepest level we actually have tiles for
        "resourceInfo": {"styleVersion": 8, "tileCompression": "gzip",
                         "cacheInfo": {"storageInfo": {"packetSize": 128, "storageFormat": "compactV2"}}},
        "tileInfo": {
            "rows": 512, "cols": 512, "dpi": 96, "format": "pbf",
            "origin": {"x": -_WEB_MERCATOR_ORIGIN, "y": _WEB_MERCATOR_ORIGIN},
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
            "lods": _esri_lods(),
        },
    }
    return Response(json.dumps(doc), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=300"})


esri = APIRouter()


@esri.get("/{topic}/VectorTileServer")
def esri_service(topic: str, request: Request) -> Response:
    """Default render. Kept so a bare topic URL still works."""
    return _esri_descriptor(topic, request, None)


@esri.get("/{topic}/{render}/VectorTileServer")
def esri_service_render(topic: str, render: str, request: Request) -> Response:
    """One service per published render.

    Pro fetches `resources/styles/root.json` with no query string, so `?render=` is unreachable
    from it — a topic with several renders (`enmin_ucrc_wells` → by-boxtype/by-purpose, babylon
    basins → eight `likelihood-*`) would only ever expose whichever one `_pick_render` defaults to.
    Putting the render in the path makes each one its own addable layer.
    """
    return _esri_descriptor(topic, request, render)


_STYLE_SUFFIX = "/resources/styles/root.json"


def _service_base_path(request: Request) -> str:
    """The service root as the CLIENT addressed it, ready to embed in an absolute URL.

    The style names its own tile URL absolutely, so it has to know which prefix this request
    arrived on — reconstructing `/esri/{topic}/VectorTileServer` would send a client that came in
    via `/rest/services` back out to the alias. Stripping the known suffix off the real path keeps
    the two mounts self-consistent without either of them naming the other.

    `re-quote` is the subtle half. ASGI percent-DECODES into `scope["path"]` before routing, so a
    render named `a?b` arrives here as a literal `a?b`; interpolating that straight into a URL
    produces `.../a?b/VectorTileServer/tile/...`, where everything from the `?` is a query string.
    That is a 200 carrying a tile URL pointing nowhere — an empty map, nothing in any log. Every
    render name today is URL-safe, so this is latent rather than live, but it costs one call to
    fix and the failure mode is invisible.
    """
    path = request.scope["path"]
    # Routing matched on the literal suffix, so this holds by construction — asserted, not relied on.
    if not path.endswith(_STYLE_SUFFIX):
        raise HTTPException(500, f"style route path {path!r} does not end in {_STYLE_SUFFIX}")
    return quote(path[: -len(_STYLE_SUFFIX)], safe="/")


def _esri_style_doc(topic: str, request: Request, render: str | None) -> Response:
    """Esri vector tile styles ARE MapLibre GL styles, so this is the same document `/styles`
    serves — pointed at the Esri-ordered tile route. The symbology therefore comes from ugs-styles
    like everywhere else, instead of each Pro user rebuilding it by hand as a .lyrx."""
    doc = json.loads(style(topic, request, render).body)
    base_path = _service_base_path(request)
    base = _base_url(request)
    for src in doc.get("sources", {}).values():
        src["tiles"] = [f"{base}{base_path}/tile/{{z}}/{{y}}/{{x}}.pbf"]
    # Esri serves glyphs from the service itself, so Pro never leaves it for fonts.
    if "glyphs" in doc:
        doc["glyphs"] = f"{base}{base_path}/resources/fonts/{{fontstack}}/{{range}}.pbf"
    return Response(json.dumps(doc), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=300"})


@esri.get("/{topic}/VectorTileServer" + _STYLE_SUFFIX)
def esri_style(topic: str, request: Request, render: str | None = None) -> Response:
    return _esri_style_doc(topic, request, render)


@esri.get("/{topic}/{render}/VectorTileServer" + _STYLE_SUFFIX)
def esri_style_render(topic: str, render: str, request: Request) -> Response:
    return _esri_style_doc(topic, request, render)


@esri.get("/{topic}/VectorTileServer/tile/{z}/{y}/{x}.pbf")
def esri_tile(topic: str, z: int, y: int, x: int) -> Response:
    """Same tiles, Esri's argument order. The y/x swap is the whole difference — get it backwards
    and the map renders mirrored about the diagonal rather than erroring."""
    return _proxy_tile(topic, z, x, y, "public, max-age=300")


@esri.get("/{topic}/{render}/VectorTileServer/tile/{z}/{y}/{x}.pbf")
def esri_tile_render(topic: str, render: str, z: int, y: int, x: int) -> Response:
    """Renders differ in symbology only — same archive, so the render is not part of the lookup."""
    return _proxy_tile(topic, z, x, y, "public, max-age=300")


def _proxy_glyphs(topic: str, render: str | None, fontstack: str, glyph_range: str) -> Response:
    """Esri reads fonts from under the service; the bytes live on the CDN with the styles."""
    if topic not in _topics():
        raise HTTPException(404, f"unknown topic: {topic}")
    template = (_pick_render(topic, render)[1].get("glyphs")) or GLYPHS_URL
    url = (template.replace("{fontstack}", quote(fontstack, safe=""))
                   .replace("{range}", quote(glyph_range, safe="")))
    try:
        body = _get(url)
    except urllib.error.HTTPError as exc:
        raise HTTPException(exc.code, f"no glyphs for {fontstack} {glyph_range}") from exc
    return Response(body, media_type="application/x-protobuf",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@esri.get("/{topic}/VectorTileServer/resources/fonts/{fontstack}/{glyph_range}.pbf")
def esri_font(topic: str, fontstack: str, glyph_range: str) -> Response:
    return _proxy_glyphs(topic, None, fontstack, glyph_range)


@esri.get("/{topic}/{render}/VectorTileServer/resources/fonts/{fontstack}/{glyph_range}.pbf")
def esri_font_render(topic: str, render: str, fontstack: str, glyph_range: str) -> Response:
    return _proxy_glyphs(topic, render, fontstack, glyph_range)


# The REST-shaped path is the one AGOL will parse; `/esri` is the alias that keeps already-copied
# URLs alive. The alias is out of the schema so the OpenAPI doc describes one service, not two.
app.include_router(esri, prefix=ESRI_PREFIX)
app.include_router(esri, prefix="/esri", include_in_schema=False)


# --- ArcGIS Server discovery ------------------------------------------------------------------
# Claiming the REST path shape has a second half: clients that recognise it then probe the server
# ROOT, which they never did while we lived at `/esri/...`. `/rest/info` is the handshake — Portal,
# IdentityManager and Pro read `authInfo` from it to decide whether a URL needs a token before they
# will load anything from it. Absent, that read 404s, and a client is free to treat "cannot
# determine the security model" as "do not proceed". Cheap to answer honestly; expensive to debug.
#
# The catalog routes below exist for the same reason. Our two service forms map onto Esri's layout
# exactly: a single-render topic is a root-level SERVICE (`/rest/services/{topic}/VectorTileServer`)
# and a multi-render topic reads as a FOLDER of one service per symbology
# (`/rest/services/{topic}/{render}/VectorTileServer`). Listing them makes the facade browsable
# instead of only addressable, which is what a client walking down from the root expects to find.
@app.get("/rest/info")
def rest_info() -> dict:
    """Anonymous, no token. Stated explicitly so a client stops looking for an auth endpoint."""
    return {"currentVersion": 11.2, "fullVersion": "11.2.0",
            "authInfo": {"isTokenBasedSecurity": False}}


@app.get("/rest/services")
def rest_catalog() -> dict:
    """Root catalog: every topic that has a style, as a service; multi-render topics also as
    folders. Topics with no published render appear in neither — Esri cannot add a styleless
    vector tile layer, so listing one would advertise something that fails on click."""
    topics = [t for t in sorted(_topics()) if _renders(t)]
    return {
        "currentVersion": 11.2,
        "folders": [t for t in topics if _is_folder(t)],
        "services": [{"name": t, "type": "VectorTileServer"}
                     for t in topics if not _is_folder(t)],
    }


@app.get("/rest/services/{topic}")
def rest_folder(topic: str) -> dict:
    """Folder listing: one service per published symbology, named `{topic}/{render}` as Esri's
    catalog does for services inside a folder.

    Only a real folder answers here. A single-`default` topic is a root-level SERVICE, and a name
    that is both a folder and a service is a shape real ArcGIS never produces — answering would
    invite a client to address the same layer two ways."""
    if topic not in _topics():
        raise HTTPException(404, f"unknown topic: {topic}")
    if not _is_folder(topic):
        raise HTTPException(404, f"{topic} is a service, not a folder")
    renders = sorted(_renders(topic))
    return {
        "currentVersion": 11.2,
        "folders": [],
        "services": [{"name": f"{topic}/{r}", "type": "VectorTileServer"} for r in renders],
    }

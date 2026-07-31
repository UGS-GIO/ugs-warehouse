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
demand (derive-from-truth, same rule as featureserv's gen_db) and cached for CACHE_TTL seconds.

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

from fastapi import FastAPI, HTTPException, Request, Response
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
# Fonts for label layers. ugs-styles publishes sprites but not glyphs, and this is the source
# vector/thumbs.py already uses. Override once ugs-styles hosts its own.
GLYPHS_URL = os.environ.get(
    "GLYPHS_URL", "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf")

_cache: dict[str, tuple[float, object]] = {}
_served: dict[str, str] = {}          # topic -> the version this process has served tiles for
_child: subprocess.Popen | None = None
_child_lock = threading.Lock()
_last_restart = 0.0
RESTART_DEBOUNCE = 5.0  # seconds; a restart already picks up every topic's current archive


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


def _note_version(topic: str, version: str) -> None:
    """A tile asked for under a version we have not served means the archive was re-ingested since
    this process cached its directory. Restart before serving, or the answer is silently stale."""
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
        base = COLLECTION_URL.rsplit("/", 1)[0]
        try:
            props = json.loads(_get(f"{base}/{topic}/{topic}.json")).get("properties") or {}
        except Exception as e:  # noqa: BLE001 — a topic listed in a cached index but since removed
            print(f"[tiles] no item doc for {topic} ({e}); serving it unversioned", flush=True)
            return "0"
        raw = props.get("ugs:content_hash") or props.get("datetime") or "0"
        # The raw hash carries a ':' and is long; a digest keeps it opaque and path-safe.
        return hashlib.sha1(str(raw).encode()).hexdigest()[:12]  # noqa: S324 (cache key, not crypto)
    return _cached(f"version:{topic}", load)


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
             "styles": {r: f"{base}/styles/{t}.json?render={r}" for r in sorted(_renders(t))},
             # Empty when nothing styles it — Esri can't add a layer with no style.
             "arcgis": {r: f"{base}/esri/{t}/{r}/VectorTileServer" for r in sorted(_renders(t))}}
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
    # A style with text layers and no `glyphs` renders no labels at all — 7 of the published styles
    # have them. ugs-styles does not host fonts, so this points at the same public glyph source
    # `vector/thumbs.py` already falls back to rather than inventing a second convention.
    if "glyphs" not in doc and any("text-field" in (lyr.get("layout") or {})
                                   for lyr in doc["layers"]):
        doc["glyphs"] = GLYPHS_URL
    return Response(json.dumps(doc), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=300"})


def _proxy_tile(topic: str, z: int, x: int, y: int, cache: str) -> Response:
    """Flattened tile route. go-pmtiles addresses our archives as `{topic}/{topic}` because they
    live one directory per topic on the CDN; consumers should not have to say it twice."""
    body, status, hdrs = _upstream(f"/{topic}/{topic}/{z}/{x}/{y}.mvt")
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
    cached forever — a re-ingest mints new URLs rather than invalidating old ones."""
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
    name, _ = _pick_render(topic, render) if _renders(topic) else (None, None)
    meta = _pmtiles_metadata(topic)
    layer = (meta.get("vector_layers") or [{}])[0]
    maxzoom = int(layer.get("maxzoom", 14))
    extent = _esri_extent(meta)
    doc = {
        "currentVersion": 11.2,
        # Esri shows this in the layer list, so a per-render service says which symbology it is.
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


@app.get("/esri/{topic}/VectorTileServer")
def esri_service(topic: str, request: Request) -> Response:
    """Default render. Kept so a bare topic URL still works."""
    return _esri_descriptor(topic, request, None)


@app.get("/esri/{topic}/{render}/VectorTileServer")
def esri_service_render(topic: str, render: str, request: Request) -> Response:
    """One service per published render.

    Pro fetches `resources/styles/root.json` with no query string, so `?render=` is unreachable
    from it — a topic with several renders (`enmin_ucrc_wells` → by-boxtype/by-purpose, babylon
    basins → eight `likelihood-*`) would only ever expose whichever one `_pick_render` defaults to.
    Putting the render in the path makes each one its own addable layer.
    """
    return _esri_descriptor(topic, request, render)


def _esri_style_doc(topic: str, request: Request, render: str | None, base_path: str) -> Response:
    """Esri vector tile styles ARE MapLibre GL styles, so this is the same document `/styles`
    serves — pointed at the Esri-ordered tile route. The symbology therefore comes from ugs-styles
    like everywhere else, instead of each Pro user rebuilding it by hand as a .lyrx."""
    doc = json.loads(style(topic, request, render).body)
    for src in doc.get("sources", {}).values():
        src["tiles"] = [f"{_base_url(request)}{base_path}/tile/{{z}}/{{y}}/{{x}}.pbf"]
    return Response(json.dumps(doc), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/esri/{topic}/VectorTileServer/resources/styles/root.json")
def esri_style(topic: str, request: Request, render: str | None = None) -> Response:
    return _esri_style_doc(topic, request, render, f"/esri/{topic}/VectorTileServer")


@app.get("/esri/{topic}/{render}/VectorTileServer/resources/styles/root.json")
def esri_style_render(topic: str, render: str, request: Request) -> Response:
    return _esri_style_doc(topic, request, render, f"/esri/{topic}/{render}/VectorTileServer")


@app.get("/esri/{topic}/VectorTileServer/tile/{z}/{y}/{x}.pbf")
def esri_tile(topic: str, z: int, y: int, x: int) -> Response:
    """Same tiles, Esri's argument order. The y/x swap is the whole difference — get it backwards
    and the map renders mirrored about the diagonal rather than erroring."""
    return _proxy_tile(topic, z, x, y, "public, max-age=300")


@app.get("/esri/{topic}/{render}/VectorTileServer/tile/{z}/{y}/{x}.pbf")
def esri_tile_render(topic: str, render: str, z: int, y: int, x: int) -> Response:
    """Renders differ in symbology only — same archive, so the render is not part of the lookup."""
    return _proxy_tile(topic, z, x, y, "public, max-age=300")

"""Explode one topic's PMTiles into a static Esri VectorTileServer tree — spike for #175.

The question this answers: AGOL makes four plain GETs at a vector tile service (descriptor,
root.json, tiles, and a server-side checkurl). If static objects satisfy them, a topic can be added
to AGOL with no Cloud Run in the path — today both AGOL routes (ugs-warehouse-tiles,
ugs-warehouse-features) are services.

    python tiles/export_static.py enmin_ccus_cbcounty --out /tmp/spike \\
        --base-url https://storage.googleapis.com/BUCKET

Writes `upload.sh` alongside the tree. Run that to push it, then add
`{base-url}/rest/services/{topic}/VectorTileServer` in Map Viewer.

NOT production code. The Esri descriptor numbers are duplicated from `tiles/app.py` rather than
imported (that module needs fastapi, which this script deliberately does not) — if the spike wins,
the exporter should import them instead of keeping a second copy.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shlex
import urllib.request

from pmtiles.reader import all_tiles, deserialize_header

CATALOG = "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json"
GLYPHS = "https://maps-assets.geology.utah.gov/styles/fonts/{fontstack}/{range}.pbf"

# Esri's own LOD numbers, read off a live basemap service — see tiles/app.py `_esri_lods`.
_R0, _S0, _ORIGIN, _MAX_LEVEL = 78271.516964, 295828763.7957775, 20037508.342787, 22


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 — fixed https CDN
        return r.read()


def _to_3857(lon: float, lat: float) -> tuple[float, float]:
    x = lon * _ORIGIN / 180.0
    y = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    return x, y * _ORIGIN / 180.0


def _extent(bounds: list[float]) -> dict:
    xmin, ymin = _to_3857(bounds[0], bounds[1])
    xmax, ymax = _to_3857(bounds[2], bounds[3])
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
            "spatialReference": {"wkid": 102100, "latestWkid": 3857}}


def _descriptor(topic: str, layer: dict, bounds: list[float]) -> dict:
    """Byte-for-byte the shape tiles/app.py `_esri_descriptor` serves, with relative tile
    templates — which is what makes a static tree viable at all."""
    extent = _extent(bounds)
    return {
        "currentVersion": 11.2,
        "name": topic,
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
        "maxzoom": _MAX_LEVEL,
        "minLOD": int(layer.get("minzoom", 0)),
        "maxLOD": int(layer.get("maxzoom", 14)),
        "resourceInfo": {"styleVersion": 8, "tileCompression": "gzip",
                         "cacheInfo": {"storageInfo": {"packetSize": 128,
                                                       "storageFormat": "compactV2"}}},
        "tileInfo": {
            "rows": 512, "cols": 512, "dpi": 96, "format": "pbf",
            "origin": {"x": -_ORIGIN, "y": _ORIGIN},
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
            "lods": [{"level": z, "resolution": _R0 / (2 ** z), "scale": _S0 / (2 ** z)}
                     for z in range(_MAX_LEVEL + 1)],
        },
    }


def _root_style(topic: str, name: str, fragment: dict, layer: dict,
                bounds: list[float], service_url: str) -> dict:
    """The ugs-styles fragment bound to the static tile path — mirrors tiles/app.py `style`."""
    source_layer = layer.get("id", topic)
    source = {"type": "vector", "tiles": [f"{service_url}/tile/{{z}}/{{y}}/{{x}}.pbf"],
              "minzoom": layer.get("minzoom", 0), "maxzoom": layer.get("maxzoom", 14),
              "bounds": bounds}
    doc = {
        "version": 8,
        "name": f"{topic} ({name})",
        "sources": {topic: source},
        "layers": [{"source": topic, "source-layer": source_layer, **lyr}
                   for lyr in fragment.get("layers", [])],
    }
    for key in ("sprite", "glyphs"):
        if value := fragment.get(key):
            doc[key] = value
    # Glyphs stay on the ugs-styles CDN rather than being proxied — the service does that so Pro
    # never leaves it for fonts; a static tree has nothing to proxy with.
    if "glyphs" not in doc and any("text-field" in (lyr.get("layout") or {})
                                   for lyr in doc["layers"]):
        doc["glyphs"] = GLYPHS
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base-url", required=True, help="origin the tree will be served from")
    ap.add_argument("--render", default=None)
    ap.add_argument("--bucket", default="BUCKET", help="gs:// bucket name for upload.sh")
    args = ap.parse_args()

    items = json.loads(_get(CATALOG))
    entry = next((i for i in items.get("items", []) if i["id"] == args.topic), None)
    if entry is None:
        raise SystemExit(f"{args.topic}: not in the catalog")
    renders = {k: v for k, v in ((entry.get("properties") or {}).get("ugs:renders") or {}).items()
               if v.get("style_url")}
    if not renders:
        raise SystemExit(f"{args.topic}: no published style — Esri cannot add it (see #119)")
    name = args.render or ("default" if "default" in renders else next(iter(renders)))
    fragment = json.loads(_get(renders[name]["style_url"]))

    raw = _get(entry["assets"]["pmtiles"]["href"])
    header = deserialize_header(raw[:127])
    blob = raw[header["metadata_offset"]:header["metadata_offset"] + header["metadata_length"]]
    if blob[:2] == b"\x1f\x8b":       # sniffed, not read off the header — archives vary
        blob = gzip.decompress(blob)
    meta = json.loads(blob.decode())
    layer = (meta.get("vector_layers") or [{}])[0]
    bounds = [header["min_lon_e7"] / 1e7, header["min_lat_e7"] / 1e7,
              header["max_lon_e7"] / 1e7, header["max_lat_e7"] / 1e7]

    base = args.base_url.rstrip("/")
    svc_rel = f"rest/services/{args.topic}/VectorTileServer"
    service_url = f"{base}/{svc_rel}"
    root = os.path.join(args.out, svc_rel)
    os.makedirs(os.path.join(root, "resources", "styles"), exist_ok=True)

    # `VectorTileServer` must be BOTH an object and a path prefix. Object stores allow that; a
    # filesystem does not, so locally it gets a .json suffix and upload.sh drops it on the way up.
    with open(os.path.join(args.out, svc_rel + ".json"), "w") as f:
        json.dump(_descriptor(args.topic, layer, bounds), f)
    with open(os.path.join(root, "resources", "styles", "root.json"), "w") as f:
        json.dump(_root_style(args.topic, name, fragment, layer, bounds, service_url), f)

    n = 0
    for (z, x, y), data in all_tiles(lambda off, ln: raw[off:off + ln]):
        d = os.path.join(root, "tile", str(z), str(y))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{x}.pbf"), "wb") as f:
            f.write(data)
        n += 1

    b = shlex.quote(args.bucket)
    with open(os.path.join(args.out, "upload.sh"), "w") as f:
        f.write(f"""#!/usr/bin/env bash
# Spike #175 — push the static Esri tree. Content types and gzip encoding are load-bearing:
# a .pbf served without `Content-Encoding: gzip` reaches Esri as garbage, and the descriptor
# must be application/json at an EXTENSIONLESS object name.
set -euo pipefail
BUCKET={b}

gsutil -h 'Content-Type:application/json' -h 'Cache-Control:public,max-age=300' \\
  cp {shlex.quote(svc_rel + '.json')} "gs://$BUCKET/{svc_rel}"

gsutil -h 'Content-Type:application/json' -h 'Cache-Control:public,max-age=300' \\
  cp {shlex.quote(svc_rel + '/resources/styles/root.json')} \\
     "gs://$BUCKET/{svc_rel}/resources/styles/root.json"

gsutil -m -h 'Content-Type:application/vnd.mapbox-vector-tile' -h 'Content-Encoding:gzip' \\
  -h 'Cache-Control:public,max-age=300' \\
  cp -r {shlex.quote(svc_rel + '/tile')} "gs://$BUCKET/{svc_rel}/"

gsutil iam ch allUsers:objectViewer "gs://$BUCKET"
echo
echo "Add this URL in AGOL (Add layer from URL → ArcGIS Server web service):"
echo "  {service_url}"
""")
    os.chmod(os.path.join(args.out, "upload.sh"), 0o755)

    print(f"topic     {args.topic}  (render: {name})")
    print(f"layer     {layer.get('id')}  z{layer.get('minzoom')}–{layer.get('maxzoom')}")
    print(f"tiles     {n}")
    print(f"out       {args.out}")
    print(f"service   {service_url}")


if __name__ == "__main__":
    main()

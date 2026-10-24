"""Check the deployed FeatureServer and ImageServer end to end, through the load balancer and CDN.

    python -m scripts.smoke_geoservices [--base https://api.geology.utah.gov]

Each check reads real layers and compares with the published STAC catalog, so a change in the
server, the catalog, the load balancer or the CDN that breaks a client fails here. Exits 1 when a
check fails. Standard library only, so it runs in any image.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ITEMS_URL = "https://maps-assets.geology.utah.gov/warehouse/stac/items.json"
# One layer per geometry type, and one with 64-bit integer ids (ugs_key).
LAYERS = {
    "enmin_powerplants": "esriGeometryPoint",
    "hazards_qfaults": "esriGeometryPolyline",
    "enmin_ut_counties": "esriGeometryPolygon",
    "enmin_ccs_co2_sources": None,
}
BIG_KEY_LAYER = "enmin_ccs_co2_sources"
# A publication COG, and a tile inside it.
IMAGE_ID = "M-103"
IMAGE_TILE = "12/1580/768"


def fetch(url: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, head, body = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, head, body = e.code, dict(e.headers), e.read()
    head = {k.lower(): v for k, v in head.items()}
    if head.get("content-encoding") == "gzip":
        body = gzip.decompress(body)
    return status, head, body


def get_json(url: str) -> dict:
    status, _, body = fetch(url)
    if status != 200:
        raise AssertionError(f"{url}: status {status}")
    doc = json.loads(body)
    if "error" in doc:
        raise AssertionError(f"{url}: {doc['error']}")
    return doc


def layer_url(base: str, topic: str) -> str:
    return f"{base}/ugs/rest/services/{topic}/default/FeatureServer/0"


def query(base: str, topic: str, **params) -> str:
    return f"{layer_url(base, topic)}/query?" + urllib.parse.urlencode({"f": "json", **params})


def row_counts() -> dict[str, int]:
    doc = get_json(ITEMS_URL)
    items = doc.get("items") or doc.get("features") or []
    return {i["id"]: i["properties"].get("ugs:row_count") for i in items if i["id"] in LAYERS}


def check_layer(base: str, topic: str, geometry: str | None, rows: int | None) -> None:
    meta = get_json(f"{layer_url(base, topic)}?f=json")
    if geometry:
        assert meta["geometryType"] == geometry, meta["geometryType"]
    assert meta["objectIdField"] == "feature_id", meta["objectIdField"]
    assert any(f["type"] == "esriFieldTypeOID" for f in meta["fields"])
    e = meta["extent"]

    count = get_json(query(base, topic, where="1=1", returnCountOnly="true"))["count"]
    if rows is not None:
        assert count == rows, f"count {count}, catalog says {rows}"

    page = get_json(query(base, topic, where="1=1", outFields="*", resultRecordCount=5,
                          orderByFields="feature_id"))
    assert len(page["features"]) == min(5, count), len(page["features"])
    assert all(f.get("geometry") for f in page["features"]), "a feature without geometry"

    first = sorted(f["attributes"]["feature_id"] for f in page["features"])[:2]
    ids = get_json(query(base, topic, objectIds=",".join(map(str, first)), outFields="feature_id"))
    assert sorted(f["attributes"]["feature_id"] for f in ids["features"]) == first

    only = get_json(query(base, topic, where="1=1", returnIdsOnly="true"))
    assert len(only["objectIds"]) == count, len(only["objectIds"])

    stats = get_json(query(base, topic, outStatistics=json.dumps(
        [{"statisticType": "count", "onStatisticField": "feature_id", "outStatisticFieldName": "n"}])))
    assert stats["features"][0]["attributes"]["n"] == count

    status, head, body = fetch(query(base, topic, where="1=1", resultRecordCount=5, f="pbf"))
    assert status == 200 and body and "protobuf" in head.get("content-type", ""), (status, head)

    gj = get_json(query(base, topic, where="1=1", resultRecordCount=5, f="geojson"))
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == min(5, count)

    # A drawing tile, as Map Viewer asks for one.
    tile = get_json(query(base, topic, geometry=json.dumps({**{k: e[k] for k in
                          ("xmin", "ymin", "xmax", "ymax")}, "spatialReference": e["spatialReference"]}),
                          geometryType="esriGeometryEnvelope", spatialRel="esriSpatialRelIntersects",
                          resultType="tile", quantizationParameters=json.dumps(
                              {"mode": "view", "originPosition": "upperLeft", "tolerance": 1222.99,
                               "extent": {**{k: e[k] for k in ("xmin", "ymin", "xmax", "ymax")},
                                          "spatialReference": e["spatialReference"]}})))
    assert tile["features"], "an extent tile with no features"


def check_big_integer(base: str) -> None:
    """A 64-bit id comes back exact and filters exactly."""
    top = get_json(query(base, BIG_KEY_LAYER, where="1=1", outFields="ugs_key",
                         orderByFields="ugs_key DESC", resultRecordCount=1))
    key = top["features"][0]["attributes"]["ugs_key"]
    assert isinstance(key, int) and key > 2**31, key
    hit = get_json(query(base, BIG_KEY_LAYER, where=f"ugs_key={key}", returnCountOnly="true"))
    assert hit["count"] >= 1, hit


def check_errors(base: str) -> None:
    """A bad where is an Esri error, and errors are not cached."""
    status, head, body = fetch(query(base, "hazards_qfaults", where="nope ==="))
    assert "error" in json.loads(body), body[:200]
    assert "no-store" in head.get("cache-control", ""), head.get("cache-control")
    status, _, _ = fetch(f"{base}/ugs/rest/services/no_such_layer/default/FeatureServer/0?f=json")
    assert status in (200, 400, 404), status


def check_cache(base: str) -> None:
    """Responses carry an ETag that revalidates, and the CDN serves a repeat from cache."""
    url = query(base, "enmin_ut_counties", where="1=1", returnCountOnly="true", smoke=int(time.time()))
    status, head, _ = fetch(url)
    tag = head.get("etag")
    assert status == 200 and tag, head
    assert "public" in head.get("cache-control", ""), head.get("cache-control")
    status, _, _ = fetch(url, {"If-None-Match": tag})
    assert status == 304, status
    for _ in range(5):
        _, head, _ = fetch(url)
        if "age" in head:
            return
        time.sleep(1)
    raise AssertionError("the CDN never served the repeat from cache (no Age header)")


def check_image(base: str) -> None:
    """An ImageServer describes an 8-bit COG and draws a tile and an export."""
    url = f"{base}/ugs/rest/services/{IMAGE_ID}/ImageServer"
    meta = get_json(f"{url}?f=json")
    assert meta["pixelType"] == "U8" and meta["tileInfo"], meta.get("pixelType")
    status, head, body = fetch(f"{url}/tile/{IMAGE_TILE}")
    assert status == 200 and head.get("content-type", "").startswith("image/") and body, status
    bbox = ",".join(str(meta["extent"][k]) for k in ("xmin", "ymin", "xmax", "ymax"))
    status, head, _ = fetch(f"{url}/exportImage?" + urllib.parse.urlencode(
        {"f": "image", "format": "png", "bbox": bbox, "bboxSR": 3857, "size": "256,256"}))
    assert status == 200 and head.get("content-type") == "image/png", (status, head)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="https://api.geology.utah.gov")
    base = ap.parse_args().base.rstrip("/")
    rows = row_counts()
    checks = [(f"layer {t}", lambda t=t, g=g: check_layer(base, t, g, rows.get(t)))
              for t, g in LAYERS.items()]
    checks += [("big integer", lambda: check_big_integer(base)),
               ("errors", lambda: check_errors(base)), ("cache", lambda: check_cache(base)),
               ("image server", lambda: check_image(base))]
    failed = 0
    for name, check in checks:
        try:
            check()
            print(f"ok    {name}")
        except Exception as e:  # noqa: BLE001  report every failed check, not only the first
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"{len(checks) - failed}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

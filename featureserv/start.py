"""Start pygeoapi on the layer list the catalog refresh writes (core/feature_service.py).

Reads FEATURES_COLLECTIONS (a gs:// or local path), writes the pygeoapi config and OpenAPI
document, then execs gunicorn. Before the first refresh has written that file, it builds the list
from the serving-topics index (FEATURES_INDEX). A layer whose file does not open is left out and
logged, so one bad file never stops the service. For a local run, GCS_MIRROR=/data reads
gs://<bucket>/<object> from /data/<bucket>/<object> instead.
"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import yaml
from pyarrow import fs

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG, OPENAPI = "/tmp/pygeoapi.yml", "/tmp/pygeoapi-openapi.yml"
UTAH_BBOX = [-114.1, 36.9, -108.9, 42.1]
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
PUBLIC_BASE = "https://maps-assets.geology.utah.gov/"
PROVIDER = "ugs_parquet.GeoParquetProvider"


def _uri(path: str) -> str:
    mirror = os.environ.get("GCS_MIRROR")
    return f"{mirror}/{path.removeprefix('gs://')}" if mirror and path.startswith("gs://") else path


def _read_json(path: str) -> dict:
    filesystem, name = fs.FileSystem.from_uri(_uri(path))
    with filesystem.open_input_stream(name) as f:
        raw = f.read()
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def _from_index(index: str) -> list[dict]:
    """The layer list from the catalog index, with titles for descriptions."""
    bucket = index.removeprefix("gs://").split("/")[0]
    layers = []
    for item in _read_json(index)["items"]:
        href = ((item.get("assets") or {}).get("data") or {}).get("href") or ""
        if href.endswith(".parquet") and href.startswith(PUBLIC_BASE):
            title = (item.get("properties") or {}).get("title") or item["id"]
            layers.append({"id": item["id"], "title": title, "description": title,
                           "keywords": [], "bbox": item.get("bbox"), "id_field": "feature_id",
                           "source": f"gs://{bucket}/{href.removeprefix(PUBLIC_BASE)}"})
    return layers


def _layers() -> list[dict]:
    try:
        return _read_json(os.environ["FEATURES_COLLECTIONS"])["collections"]
    except FileNotFoundError:
        print("[featureserv] no layer list from the refresh yet; reading the catalog index",
              file=sys.stderr)
        return _from_index(os.environ["FEATURES_INDEX"])


def _opens(layer: dict) -> bool:
    from ugs_parquet import GeoParquetProvider
    try:
        GeoParquetProvider({"name": PROVIDER, "type": "feature", "id_field": layer["id_field"],
                            "data": {"source": _uri(layer["source"])}})
        return True
    except Exception as e:  # noqa: BLE001 — any failure means this layer cannot be served
        print(f"[featureserv] leaving out {layer['id']}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def resources(layers: list[dict]) -> dict:
    return {layer["id"]: {
        "type": "collection", "title": layer["title"], "description": layer["description"],
        "keywords": layer["keywords"] or [layer["title"]], "links": [],
        "extents": {"spatial": {"bbox": layer["bbox"] or UTAH_BBOX, "crs": CRS84}},
        "providers": [{"type": "feature", "name": PROVIDER,
                       "data": {"source": _uri(layer["source"])}, "id_field": layer["id_field"]}],
    } for layer in layers}


def main() -> None:
    with open(os.path.join(HERE, "pygeoapi.base.yml")) as f:
        config = yaml.safe_load(f)
    layers = _layers()
    with ThreadPoolExecutor(16) as ex:
        layers = [layer for layer, ok in zip(layers, ex.map(_opens, layers)) if ok]
    if not layers:
        # Exit so Cloud Run keeps the previous revision instead of serving an empty list.
        sys.exit("[featureserv] no layer opened; not starting")
    config["resources"] = resources(layers)
    with open(CONFIG, "w") as f:
        yaml.safe_dump(config, f)
    os.environ.update(PYGEOAPI_CONFIG=CONFIG, PYGEOAPI_OPENAPI=OPENAPI)
    subprocess.run(["pygeoapi", "openapi", "generate", CONFIG, "--output-file", OPENAPI],
                   check=True)
    print(f"[featureserv] serving {len(layers)} collections", flush=True)
    # One process, so the 4 threads match Cloud Run's --concurrency=4.
    os.execvp("gunicorn", ["gunicorn", "--workers=1", "--threads=4", "--timeout=120",
                           f"--bind=0.0.0.0:{os.environ.get('PORT', '9000')}",
                           "pygeoapi.flask_app:APP"])


if __name__ == "__main__":
    main()

"""Start pygeoapi on the layer list the catalog refresh writes (core/feature_service.py).

Reads FEATURES_COLLECTIONS (a gs:// or local path), writes the pygeoapi config and OpenAPI
document, then execs gunicorn. For a local run, GCS_MIRROR=/data reads gs://<bucket>/<object> from
/data/<bucket>/<object> instead.
"""
from __future__ import annotations

import json
import os
import subprocess

import yaml
from pyarrow import fs

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG, OPENAPI = "/tmp/pygeoapi.yml", "/tmp/pygeoapi-openapi.yml"
UTAH_BBOX = [-114.1, 36.9, -108.9, 42.1]
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"


def _uri(path: str) -> str:
    mirror = os.environ.get("GCS_MIRROR")
    return f"{mirror}/{path.removeprefix('gs://')}" if mirror and path.startswith("gs://") else path


def _read_json(path: str) -> dict:
    filesystem, name = fs.FileSystem.from_uri(_uri(path))
    with filesystem.open_input_stream(name) as f:
        return json.loads(f.read())


def resources(layers: list[dict]) -> dict:
    return {layer["id"]: {
        "type": "collection", "title": layer["title"], "description": layer["description"],
        "keywords": layer["keywords"] or [layer["title"]], "links": [],
        "extents": {"spatial": {"bbox": layer["bbox"] or UTAH_BBOX, "crs": CRS84}},
        "providers": [{"type": "feature", "name": "ugs_parquet.GeoParquetProvider",
                       "data": {"source": _uri(layer["source"])}, "id_field": layer["id_field"]}],
    } for layer in layers}


def main() -> None:
    with open(os.path.join(HERE, "pygeoapi.base.yml")) as f:
        config = yaml.safe_load(f)
    layers = _read_json(os.environ["FEATURES_COLLECTIONS"])["collections"]
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

"""Build the viewer's 7.5' quad gazetteer: every Utah quad name and its USGS Ohio code.

    python -m scripts.build_quad_gazetteer                 # writes viewer/public/utah-quads.json

Source: UGRC's SGID "Utah USGS 24K Quads" feature service. Each row is `[name, code]`. The code
names a cell of the basemap grid (ugs_warehouse.basemap), so the viewer derives the bbox from it;
the build fails if any UGRC outline is not the cell its code names.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from ugs_warehouse.basemap import quad_at

SOURCE = (
    "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/USGS24KQuads/FeatureServer/0"
)
OUT = Path(__file__).resolve().parents[1] / "viewer" / "public" / "utah-quads.json"
# UGRC's outlines are reprojected from Web Mercator, so corners drift ~100 m off the grid.
TOLERANCE = 0.005


def fetch(url: str = SOURCE) -> list[dict]:
    q = urllib.parse.urlencode({
        "where": "1=1", "outFields": "NAME,OHIO_CODE", "outSR": "4326", "geometryPrecision": "5",
        "resultRecordCount": "2000", "f": "json",
    })
    with urllib.request.urlopen(f"{url}/query?{q}", timeout=60) as r:
        body = json.load(r)
    if "error" in body or body.get("exceededTransferLimit"):
        raise RuntimeError(f"quad index query failed or was truncated: {body.get('error')}")
    return body["features"]


def tidy(name: str) -> str:
    """UGRC title-cases the directional suffix ("Camels Back Ridge Sw"); USGS writes it SW."""
    return re.sub(r"\b(Ne|Nw|Se|Sw)\b", lambda m: m[1].upper(), name.strip())


def rows(features: list[dict]) -> list[list[str]]:
    out = []
    for f in features:
        code = f["attributes"]["OHIO_CODE"].lower()
        xs, ys = zip(*(p for ring in f["geometry"]["rings"] for p in ring))
        q = quad_at((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        off = max(abs(a - b) for a, b in zip(q.bbox, (min(xs), min(ys), max(xs), max(ys))))
        if q.code != code or off > TOLERANCE:
            raise ValueError(f"{code}: outline is cell {q.code}, {off:.4f} deg off the grid")
        out.append([tidy(f["attributes"]["NAME"]), code])
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    data = rows(fetch())
    args.out.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(f"{len(data)} quads -> {args.out} ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

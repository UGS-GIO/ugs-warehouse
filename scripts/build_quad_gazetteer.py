"""Build the viewer's quadrangle gazetteer: Utah's 7.5' and 30'x60' map names and USGS codes.

    python -m scripts.build_quad_gazetteer                 # writes viewer/public/utah-quads.json

Source: UGRC's SGID "Utah USGS 24K Quads" and "Utah USGS 100K Quads" feature services. Output is
`{"24k": [[name, code], ...], "100k": [...]}`. A code is the Ohio code of the basemap grid cell
(ugs_warehouse.basemap) at the map's SE corner, so the viewer derives the bbox from it; the build
fails if any UGRC outline is not the area its code names.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from ugs_warehouse.basemap import CELL, NOTCH_SOUTH, NOTCH_WEST, UTAH, quad_at

UGRC = "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services"
SOURCE = f"{UGRC}/USGS24KQuads/FeatureServer/0"
SOURCE_100K = f"{UGRC}/USGS100KQuads/FeatureServer/0"
OUT = Path(__file__).resolve().parents[1] / "viewer" / "public" / "utah-quads.json"
# UGRC's outlines are reprojected from Web Mercator, so corners drift ~100 m off the grid.
TOLERANCE = 0.005
# The 100K index runs into the neighbouring states. A sheet must reach this far into Utah to
# count, which drops the Nevada sheets over the 0.05 deg sliver west of 114W.
MIN_OVERLAP = 0.1


def fetch(url: str = SOURCE, fields: str = "NAME,OHIO_CODE") -> list[dict]:
    q = urllib.parse.urlencode({
        "where": "1=1", "outFields": fields, "outSR": "4326", "geometryPrecision": "5",
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


def _bbox(f: dict) -> tuple[float, float, float, float]:
    xs, ys = zip(*(p for ring in f["geometry"]["rings"] for p in ring))
    return min(xs), min(ys), max(xs), max(ys)


def _off(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def rows(features: list[dict]) -> list[list[str]]:
    out = []
    for f in features:
        code = f["attributes"]["OHIO_CODE"].lower()
        w, s, e, n = _bbox(f)
        q = quad_at((w + e) / 2, (s + n) / 2)
        off = _off(q.bbox, (w, s, e, n))
        if q.code != code or off > TOLERANCE:
            raise ValueError(f"{code}: outline is cell {q.code}, {off:.4f} deg off the grid")
        out.append([tidy(f["attributes"]["NAME"]), code])
    return sorted(out)


def _in_utah(w: float, s: float, e: float, n: float) -> bool:
    uw, us, ue, un = UTAH
    wide = min(e, ue) - max(w, uw) >= MIN_OVERLAP and min(n, un) - max(s, us) >= MIN_OVERLAP
    return wide and not (s >= NOTCH_SOUTH - TOLERANCE and w >= NOTCH_WEST - TOLERANCE)


def rows_100k(features: list[dict]) -> list[list[str]]:
    """30'x60' sheets in Utah, coded by their SE 7.5' cell. They have no code at UGRC."""
    out = []
    for f in features:
        name = f["attributes"]["NAME"].strip()
        bbox = _bbox(f)
        east, south = round(bbox[2]), round(bbox[1] * 2) / 2
        if _off((east - 1, south, east, south + 0.5), bbox) > TOLERANCE:
            raise ValueError(f"{name}: outline is not a 30'x60' sheet: {bbox}")
        if _in_utah(*bbox):
            out.append([name, quad_at(east - CELL / 2, south + CELL / 2).code])
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    data = {"24k": rows(fetch()), "100k": rows_100k(fetch(SOURCE_100K, "NAME"))}
    args.out.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    n24, n100 = len(data["24k"]), len(data["100k"])
    print(f"{n24} + {n100} quads -> {args.out} ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

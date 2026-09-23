"""Build the offline Utah basemap: one statewide overview archive plus one archive per quad.

    python -m scripts.build_basemap                       # everything (~1,500 quads)
    python -m scripts.build_basemap --quads 40111g8,40111b6
    python -m scripts.build_basemap --reuse-tiles         # skip Planetiler, re-cut the archives

Source is OpenStreetMap through Planetiler's OpenMapTiles profile, run on Geofabrik's Utah extract.
That is the same schema OpenFreeMap serves (OpenFreeMap is built with Planetiler), so the viewer's
existing Streets and Light styles draw these tiles unchanged. We build our own rather than cache
OpenFreeMap's because its terms prohibit collecting from the service "in automated ways without
permission", which is what saving a region for offline use amounts to.

Output, ready to upload under the CDN's basemap/ prefix (that step needs GCP permissions):

    <out>/utah.pmtiles            z0-14, the whole state in one file — the main "save" option
    <out>/overview.pmtiles        z0-10, statewide, the low zooms behind a partial save
    <out>/quads/<code>.pmtiles    z11+, one per 7.5-minute quad, named by USGS Ohio code
    <out>/index.json              what exists and how big, for the download UI
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ugs_warehouse.basemap import OVERVIEW_MAXZOOM, QUAD_MINZOOM, Quad, utah_quads

PLANETILER_VERSION = "0.10.2"
PLANETILER_URL = (f"https://github.com/onthegomap/planetiler/releases/download/"
                  f"v{PLANETILER_VERSION}/planetiler.jar")
UTAH_PBF_URL = "https://download.geofabrik.de/north-america/us/utah-latest.osm.pbf"


def _fetch(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    print(f"downloading {url}", file=sys.stderr)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)          # a half-finished download never passes the exists() check above
    return dest


def build_tiles(work: Path) -> Path:
    """OSM → one statewide OpenMapTiles-schema archive, via Planetiler."""
    jar = _fetch(PLANETILER_URL, work / f"planetiler-{PLANETILER_VERSION}.jar")
    pbf = _fetch(UTAH_PBF_URL, work / "utah-latest.osm.pbf")
    out = work / "utah.pmtiles"
    subprocess.run(
        ["java", "-Xmx4g", "-jar", str(jar),
         f"--osm-path={pbf}", f"--output={out}",
         # Natural Earth, water polygons and lake centerlines; cached under data/ after the first run.
         "--download", f"--download-dir={work / 'sources'}", "--force"],
        check=True, cwd=work,
    )
    return out


def _extract(src: Path, dest: Path, *flags: str) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pmtiles", "extract", str(src), str(dest), *flags],
                   check=True, stdout=subprocess.DEVNULL)
    return dest.stat().st_size


def cut(src: Path, out: Path, quads: list[Quad], workers: int = 8) -> dict:
    """The statewide archive, plus overview + per-quad archives cut from it, and their index."""
    out.mkdir(parents=True, exist_ok=True)
    # The whole state is published as-is: at ~180 MB it is the download most people want before a
    # trip, and it is smaller than all the quads together, which repeat every tile on a quad edge.
    shutil.copyfile(src, out / "utah.pmtiles")
    overview = _extract(src, out / "overview.pmtiles", f"--maxzoom={OVERVIEW_MAXZOOM}")

    def one(q: Quad) -> tuple[str, dict]:
        # --bbox keeps every tile that TOUCHES the quad, so a tile on a quad edge is complete in
        # both neighbours and the map shows no seam whichever one it is read from.
        size = _extract(src, out / "quads" / f"{q.code}.pmtiles",
                        f"--bbox={q.west},{q.south},{q.east},{q.north}", f"--minzoom={QUAD_MINZOOM}")
        return q.code, {"bbox": list(q.bbox), "bytes": size}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        entries = dict(pool.map(one, quads))

    index = {
        "state": {"bytes": (out / "utah.pmtiles").stat().st_size},
        "overview": {"maxzoom": OVERVIEW_MAXZOOM, "bytes": overview},
        "quad_minzoom": QUAD_MINZOOM,
        "quads": entries,
    }
    (out / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", type=Path, default=Path("build/basemap-work"))
    ap.add_argument("--out", type=Path, default=Path("build/basemap"))
    ap.add_argument("--quads", help="comma-separated Ohio codes; default is every Utah quad")
    ap.add_argument("--reuse-tiles", action="store_true",
                    help="use the existing statewide archive instead of re-running Planetiler")
    args = ap.parse_args(argv)

    # Absolute: Planetiler runs with cwd=work, so a relative jar or PBF path would resolve twice.
    args.work = args.work.resolve()
    args.out = args.out.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    src = args.work / "utah.pmtiles"
    if not (args.reuse_tiles and src.exists()):
        src = build_tiles(args.work)

    quads = list(utah_quads())
    if args.quads:
        wanted = set(args.quads.split(","))
        quads = [q for q in quads if q.code in wanted]
        missing = wanted - {q.code for q in quads}
        if missing:
            print(f"not Utah quads, skipped: {', '.join(sorted(missing))}", file=sys.stderr)

    index = cut(src, args.out, quads)
    quads_total = index["overview"]["bytes"] + sum(q["bytes"] for q in index["quads"].values())
    print(f"state {index['state']['bytes'] / 1e6:.1f} MB; overview + {len(index['quads'])} quads "
          f"{quads_total / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

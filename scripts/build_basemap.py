"""Build the offline Utah basemap: one statewide overview archive plus one archive per quad.

    python -m scripts.build_basemap                       # everything (~1,500 quads)
    python -m scripts.build_basemap --quads 40111g8,40111b6
    python -m scripts.build_basemap --reuse-tiles         # skip the extract, re-cut the archives

Source: the newest Protomaps daily build, cut to Utah with `pmtiles extract` (~140 MB, seconds).
Protomaps schema; style it with @protomaps/basemaps.

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
from datetime import UTC, datetime
from pathlib import Path

from ugs_warehouse.basemap import OVERVIEW_MAXZOOM, QUAD_MINZOOM, Quad, utah_quads

BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
# MapLibre overzooms past 14; z15 would double the file.
MAXZOOM = 14


def latest_build() -> str:
    """The newest Protomaps daily build's file name, e.g. 20260924.pmtiles."""
    # The metadata host 403s Python's default User-Agent.
    req = urllib.request.Request(BUILDS_URL, headers={"User-Agent": "ugs-warehouse-basemap"})
    with urllib.request.urlopen(req, timeout=60) as r:
        builds = json.load(r)
    return max(b["key"] for b in builds)     # YYYYMMDD.pmtiles


def grid_bbox(quads: list[Quad]) -> str:
    """Extent of the quad grid, so edge quads are complete."""
    return ",".join(str(v) for v in (min(q.west for q in quads), min(q.south for q in quads),
                                     max(q.east for q in quads), max(q.north for q in quads)))


def build_tiles(work: Path, build: str) -> Path:
    """Extract the statewide archive from a Protomaps build."""
    out = work / "utah.pmtiles"
    (work / "build.txt").write_text(build)    # read back by --reuse-tiles
    _extract(BUILD_BASE + build, out, f"--bbox={grid_bbox(list(utah_quads()))}", f"--maxzoom={MAXZOOM}")
    return out


def _extract(src: Path | str, dest: Path, *flags: str) -> int:
    subprocess.run(["pmtiles", "extract", str(src), str(dest), *flags],
                   check=True, stdout=subprocess.DEVNULL)
    return dest.stat().st_size


def cut(src: Path, out: Path, quads: list[Quad], build: str, workers: int = 8) -> dict:
    """The statewide archive, plus overview + per-quad archives cut from it, and their index."""
    (out / "quads").mkdir(parents=True, exist_ok=True)
    # The whole state is published as-is: at ~140 MB it is the download most people want before a
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
        # When this build was made: the viewer offers an update for any basemap saved before it.
        "built": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": BUILD_BASE + build,
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
                    help="use the existing statewide archive instead of re-extracting it")
    args = ap.parse_args(argv)

    args.work = args.work.resolve()
    args.out = args.out.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    src = args.work / "utah.pmtiles"
    stamp = args.work / "build.txt"
    if args.reuse_tiles and src.exists() and stamp.exists():
        build = stamp.read_text().strip()
    else:
        build = latest_build()
        src = build_tiles(args.work, build)

    quads = list(utah_quads())
    if args.quads:
        wanted = set(args.quads.split(","))
        quads = [q for q in quads if q.code in wanted]
        missing = wanted - {q.code for q in quads}
        if missing:
            print(f"not Utah quads, skipped: {', '.join(sorted(missing))}", file=sys.stderr)
        if not quads:
            print("no Utah quads to build", file=sys.stderr)
            return 2

    index = cut(src, args.out, quads, build)
    quads_total = index["overview"]["bytes"] + sum(q["bytes"] for q in index["quads"].values())
    print(f"state {index['state']['bytes'] / 1e6:.1f} MB; overview + {len(index['quads'])} quads "
          f"{quads_total / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""3D geologic publication conversion — the repeatable ingest entry point.

Takes a GeMS geodatabase (with `CSA_3D_*` cross-section feature classes) + its ArcGIS scene (`.mapx`,
the authored symbology) and produces cloud-native, warehouse-standard outputs:

  - **3D GeoParquet** for the fence polygons + lines (WKB-Z, EPSG:4326) — served, read by the viewer
    via duckdb-wasm. Carries MapUnit / label / authored fill / line cartography attributes.
  - **classification:classes** (STAC Classification extension) — the warehouse's built-in per-unit
    colour mechanism (same as vector topics); one class per MapUnit, authored colour as `color_hint`.
  - (TODO phase 2) a **glTF** mesh for download/interop.

Authored colours come from the `.mapx` `CSA_3D_MapUnitPolys` `CIMUniqueValueRenderer`, keyed on the
`Symbol` field (`"24_Tk_…"` → `Tk`), handling RGB **and** CMYK (and Gray/HSV) CIM colour types — an
RGB-only walk silently drops CMYK units (Qay/TRt). `DescriptionOfMapUnits.AreaFillRGB` is empty, so
the `.mapx` is the colour source. The cross-section symbology is authoritative for the fence even where
it differs from the plan-view map plate.

Line cartography is data-driven from the GeMS `Type` + `Symbol` fields: contact / fault / section
boundary, and "approximately located" (→ dashed) vs "well located" (→ solid).

`convert()` is pure (GDB+mapx → local files + asset metadata); `ingest()` adds GCS upload + STAC write.
Runnable against an extracted GDB — see `convert()` / `main()`.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import re
from pathlib import Path

POLY_LAYER = "CSA_3D_MapUnitPolys"
LINE_LAYER = "CSA_3D_ContactsAndFaults"
DMU_LAYER = "DescriptionOfMapUnits"


# ---- authored colours from the .mapx symbology -------------------------------------------------

def _cim_hex(ctype: str, values: list) -> str | None:
    """A CIM colour node → "#rrggbb". Handles the colour models ArcGIS actually emits."""
    if ctype == "CIMRGBColor":
        r, g, b = values[0], values[1], values[2]
    elif ctype == "CIMCMYKColor":
        c, m, y, k = (v / 100 for v in values[:4])
        r, g, b = 255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k)
    elif ctype == "CIMGrayColor":
        r = g = b = values[0]
    elif ctype == "CIMHSVColor":
        rr, gg, bb = colorsys.hsv_to_rgb(values[0] / 360, values[1] / 100, values[2] / 100)
        r, g, b = rr * 255, gg * 255, bb * 255
    else:
        return None
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def _first_color(node) -> str | None:
    """First fill colour in a CIM symbol subtree (descends symbol/symbolLayers first)."""
    if isinstance(node, dict):
        t = node.get("type", "")
        if "Color" in t and "values" in node:
            h = _cim_hex(t, node["values"])
            if h:
                return h
        for key in ("symbol", "symbolLayers"):
            if key in node:
                h = _first_color(node[key])
                if h:
                    return h
        for v in node.values():
            h = _first_color(v)
            if h:
                return h
    elif isinstance(node, list):
        for v in node:
            h = _first_color(v)
            if h:
                return h
    return None


def _renderer_for(doc, layer_name: str) -> dict | None:
    if isinstance(doc, dict):
        if doc.get("type") == "CIMFeatureLayer" and doc.get("name") == layer_name:
            return doc.get("renderer")
        for v in doc.values():
            r = _renderer_for(v, layer_name)
            if r:
                return r
    elif isinstance(doc, list):
        for v in doc:
            r = _renderer_for(v, layer_name)
            if r:
                return r
    return None


def mapx_colors(mapx_path: str) -> dict[str, str]:
    """`MapUnit -> "#rrggbb"` from the CSA_3D_MapUnitPolys unique-value renderer (RGB + CMYK + …)."""
    doc = json.loads(Path(mapx_path).read_text(encoding="utf-8", errors="ignore"))
    rnd = _renderer_for(doc, POLY_LAYER)
    out: dict[str, str] = {}
    if not rnd:
        return out
    for grp in rnd.get("groups", []):
        for cls in grp.get("classes", []):
            vals = cls.get("values", [{}])
            sym = vals[0].get("fieldValues", ["?"])[0] if vals else None
            m = re.match(r"^\d+_([A-Za-z0-9]+)_", sym or "")
            mu = m.group(1) if m else sym
            hexv = _first_color(cls.get("symbol"))
            if mu and hexv:
                out[mu] = hexv
    return out


# ---- GDB read + conversion ---------------------------------------------------------------------

def _dmu(gdb_path: str) -> dict[str, dict]:
    """`MapUnit -> {name, hierarchy}` from DescriptionOfMapUnits (legend label + stratigraphic order)."""
    import geopandas as gpd
    df = gpd.read_file(gdb_path, layer=DMU_LAYER)
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        mu = str(r.get("MapUnit") or "").strip()
        if mu:
            out[mu] = {"name": str(r.get("Name") or mu), "hierarchy": str(r.get("HierarchyKey") or "")}
    return out


def _line_kind(t: str) -> str:
    t = t.lower()
    return "fault" if "fault" in t else "boundary" if "boundary" in t else "contact"


def _triangulate(ring: list) -> list[int]:
    """Triangle indices for one 3D ring (Newell normal → drop dominant axis → earcut). Fence panels
    are near-planar, so a planar projection triangulates them correctly."""
    import mapbox_earcut as earcut
    import numpy as np
    n = [0.0, 0.0, 0.0]
    m = len(ring)
    for i in range(m):
        a, b = ring[i], ring[(i + 1) % m]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    ax = max(range(3), key=lambda i: abs(n[i]))  # project away the dominant-normal axis
    keep = [i for i in range(3) if i != ax]
    verts2d = np.array([[p[keep[0]], p[keep[1]]] for p in ring], dtype=np.float32)
    return list(earcut.triangulate_float32(verts2d, np.array([m])))


def build_gltf(polys, colors: dict[str, str], center: tuple[float, float], out_path: Path) -> int:
    """Triangulate the fence polygons → a binary glTF (.glb) mesh for download/interop. Local metres,
    Y-up (glTF convention: X=east, Y=elevation, Z=−north). Per-vertex colour = authored unit fill.
    Returns the triangle count."""
    import numpy as np
    import pygltflib
    from shapely.geometry import MultiPolygon, Polygon

    cLon, cLat = center
    mLon = 111320 * np.cos(np.radians(cLat))
    pos: list[list[float]] = []
    col: list[list[float]] = []
    idx: list[int] = []
    for geom, unit in zip(polys.geometry, polys["MapUnit"], strict=False):
        rgb = colors.get(str(unit), "#808080").lstrip("#")
        c = [int(rgb[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]
        parts = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        for poly in parts:
            if not isinstance(poly, Polygon):
                continue
            ring = [[(x - cLon) * mLon, z, -(y - cLat) * 110574]  # X east, Y up (elev), Z −north
                    for x, y, z in poly.exterior.coords]
            base = len(pos)
            pos.extend(ring)
            col.extend([c] * len(ring))
            for t in _triangulate(ring):
                idx.append(base + t)
    if not idx:
        return 0

    positions = np.array(pos, dtype=np.float32)
    colors_arr = np.array(col, dtype=np.float32)
    indices = np.array(idx, dtype=np.uint32)
    blob = indices.tobytes() + positions.tobytes() + colors_arr.tobytes()
    # accessor 0 = indices, 1 = POSITION, 2 = COLOR_0
    g = pygltflib.GLTF2(
        scene=0, scenes=[pygltflib.Scene(nodes=[0])], nodes=[pygltflib.Node(mesh=0)],
        meshes=[pygltflib.Mesh(primitives=[pygltflib.Primitive(
            attributes=pygltflib.Attributes(POSITION=1, COLOR_0=2), indices=0, material=0)])],
        materials=[pygltflib.Material(
            pbrMetallicRoughness=pygltflib.PbrMetallicRoughness(metallicFactor=0, roughnessFactor=1),
            doubleSided=True)],
        accessors=[
            pygltflib.Accessor(bufferView=0, componentType=pygltflib.UNSIGNED_INT, count=len(indices),
                               type=pygltflib.SCALAR, max=[int(indices.max())], min=[0]),
            pygltflib.Accessor(bufferView=1, componentType=pygltflib.FLOAT, count=len(positions),
                               type=pygltflib.VEC3, max=positions.max(0).tolist(), min=positions.min(0).tolist()),
            pygltflib.Accessor(bufferView=2, componentType=pygltflib.FLOAT, count=len(colors_arr),
                               type=pygltflib.VEC4, max=[1, 1, 1, 1], min=[0, 0, 0, 1]),
        ],
        bufferViews=[
            pygltflib.BufferView(buffer=0, byteOffset=0, byteLength=indices.nbytes,
                                 target=pygltflib.ELEMENT_ARRAY_BUFFER),
            pygltflib.BufferView(buffer=0, byteOffset=indices.nbytes, byteLength=positions.nbytes,
                                 target=pygltflib.ARRAY_BUFFER),
            pygltflib.BufferView(buffer=0, byteOffset=indices.nbytes + positions.nbytes,
                                 byteLength=colors_arr.nbytes, target=pygltflib.ARRAY_BUFFER),
        ],
        buffers=[pygltflib.Buffer(byteLength=len(blob))],
    )
    g.set_binary_blob(blob)
    g.save_binary(str(out_path))
    return len(indices) // 3


def convert(gdb_path: str, mapx_path: str, series_id: str, out_dir: str) -> dict:
    """GDB + .mapx → GeoParquet-3D (polys, lines) + classification:classes. Returns asset metadata.
    Pure/local: writes files under out_dir, no GCS. Z is preserved (WKB-Z in the GeoParquet)."""
    import geopandas as gpd

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    colors = mapx_colors(mapx_path)
    dmu = _dmu(gdb_path)

    # Polygons: reproject to 4326 (keeps Z — only x/y transform), stamp MapUnit/label/fill.
    polys = gpd.read_file(gdb_path, layer=POLY_LAYER).to_crs(4326)
    polys["MapUnit"] = polys.get("MapUnit").astype("string")
    polys["label"] = polys["MapUnit"].map(lambda u: dmu.get(str(u), {}).get("name", u))
    polys["fill"] = polys["MapUnit"].map(lambda u: colors.get(str(u)))
    poly_cols = [c for c in ("MapUnit", "label", "fill", "geometry") if c in polys.columns]
    poly_path = out / f"{series_id}_3d_polygons.parquet"
    polys[poly_cols].to_parquet(poly_path, index=False)

    # glTF mesh (download/interop) — triangulated panels, authored per-vertex colour, local metres.
    b = polys.total_bounds
    gltf_path = out / f"{series_id}_3d.glb"
    tris = build_gltf(polys, colors, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), gltf_path)

    # Lines: contact/fault/boundary + dashed (approximately located) from Type/Symbol.
    lines = gpd.read_file(gdb_path, layer=LINE_LAYER).to_crs(4326)
    lines["kind"] = lines.get("Type").astype("string").map(lambda t: _line_kind(str(t or "")))
    lines["dashed"] = lines.get("Symbol").astype("string").map(lambda s: "approxim" in str(s or "").lower())
    line_cols = [c for c in ("kind", "dashed", "Type", "Symbol", "geometry") if c in lines.columns]
    line_path = out / f"{series_id}_3d_lines.parquet"
    lines[line_cols].to_parquet(line_path, index=False)

    # classification:classes (STAC standard): one per MapUnit, ordered by HierarchyKey, value=MapUnit
    # so the viewer joins a feature's MapUnit → colour. Same shape core/styles.classification_classes
    # emits for vector topics.
    units = sorted(
        {str(u) for u in polys["MapUnit"].dropna().unique()},
        key=lambda u: dmu.get(u, {}).get("hierarchy", "zzz"),
    )
    classes = []
    for u in units:
        cls = {"value": u, "name": re.sub(r"[^0-9A-Za-z_-]+", "_", u), "title": dmu.get(u, {}).get("name", u)}
        if colors.get(u):
            cls["color_hint"] = colors[u].lstrip("#")
        classes.append(cls)

    meta = {
        "series_id": series_id,
        "polygons_parquet": str(poly_path),
        "lines_parquet": str(line_path),
        "gltf": str(gltf_path),
        "gltf_triangles": tris,
        "classification:classes": classes,
        "units": len(units),
        "uncolored": [u for u in units if not colors.get(u)],
    }
    (out / f"{series_id}_3d_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


# ---- ingest: localize source → convert → upload → stamp the STAC item ---------------------------

from . import identity  # noqa: E402

THREED_PREFIX = identity.THREED_PREFIX  # CDN object prefix for the 3D artifacts
GLTF_MIME = "model/gltf-binary"


def threed_object(series_id: str, name: str) -> str:
    """Object path for a 3D artifact, keyed on the UPPER series id so presence detection + STAC
    stamping (which use sid.upper()) line up with what the convert step writes."""
    return f"{THREED_PREFIX}/{series_id.upper()}_{name}"


# The three STAC assets a converted pub carries, plus the classes sidecar — single source of truth so
# the convert step (writes them) and the pubs ingest (presence-stamps them) never drift.
POLY_NAME, LINE_NAME, MESH_NAME, CLASSES_NAME = "3d_polygons.parquet", "3d_lines.parquet", "3d.glb", "3d_classes.json"


def _localize(uri: str, dest: str) -> str:
    """Bring a gs:// or https:// file local; pass a local path through."""
    import subprocess
    import urllib.request
    if uri.startswith("gs://"):
        subprocess.run(["gcloud", "storage", "cp", uri, dest], check=True)
        return dest
    if uri.startswith("http://") or uri.startswith("https://"):
        urllib.request.urlretrieve(uri, dest)  # noqa: S310 — fetching our own pub assets
        return dest
    return uri


def _localize_gdb(uri: str, tmp: str) -> str:
    """Localize a GDB given as a .zip (downloaded + unzipped) or an existing .gdb directory."""
    import zipfile
    from pathlib import Path as _P
    if uri.endswith(".gdb"):
        return _localize(uri, uri)  # already a dir (local)
    z = _localize(uri, f"{tmp}/gdb.zip")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(f"{tmp}/gdb")
    gdb = next(_P(f"{tmp}/gdb").rglob("*.gdb"), None)
    if not gdb:
        raise FileNotFoundError(f"no .gdb inside {uri}")
    return str(gdb)


def _upload_outputs(meta: dict, series_id: str) -> None:
    """Upload the GeoParquet + glTF + a classes sidecar to the CDN (immutable). The STAC assets are NOT
    stamped here — the pubs ingest does that presence-driven (it detects these objects exist). So this
    is a pure harvest step: produce artifacts, let ingest bind them."""
    from ..core import config, gcs
    for local, name, mime in [
        (meta["polygons_parquet"], POLY_NAME, config.PARQUET_MIME),
        (meta["lines_parquet"], LINE_NAME, config.PARQUET_MIME),
        (meta["gltf"], MESH_NAME, GLTF_MIME),
    ]:
        gcs.upload(local, threed_object(series_id, name), content_type=mime, cache_control=gcs.CACHE_IMMUTABLE)
    # classification:classes can't be recomputed from the artifacts (it's the .mapx symbology), so
    # persist it next to them; the pubs ingest reads it back when stamping classification:classes.
    gcs.put_bytes(json.dumps(meta["classification:classes"]).encode(),
                  threed_object(series_id, CLASSES_NAME),
                  content_type="application/json", cache_control=gcs.CACHE_IMMUTABLE)


def ingest(series_id: str, gdb_uri: str, mapx_uri: str, out_dir: str | None = None) -> dict:
    """Convert one pub's GDB + .mapx → 3D artifacts → upload to the CDN. Does NOT stamp/refresh: the
    pubs ingest binds the assets (presence-driven). Needs GCS write perms. Returns the conversion meta."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        gdb = _localize_gdb(gdb_uri, tmp)
        mapx = _localize(mapx_uri, f"{tmp}/scene.mapx")
        meta = convert(gdb, mapx, series_id, out_dir or f"{tmp}/out")
        _upload_outputs(meta, series_id)
        print(f"3D convert complete for {series_id}: {meta['units']} units, {meta['gltf_triangles']} tris")
    return meta


# ---- discovery: which published pubs carry a CSA_3D source (gdb + .mapx) ------------------------

def _threed_sources() -> list[tuple[str, str, str]]:
    """(series_id, gdb_uri, mapx_uri) for every pub whose attachments include BOTH a GeMS gdb (.gdb.zip)
    and an ArcGIS scene (.mapx) — the CSA_3D source pair. This is the auto-detection that makes 3D
    "come from ingest": drop the gdb+mapx on a pub, and a convert run picks it up."""
    from . import sink_stac, source
    by_sid: dict[str, dict[str, str]] = {}
    for a in source.read_attachments():
        sid = (a.get("series_id") or "").strip()
        url = sink_stac.href(a.get("pub_url"))
        if not sid or not url:
            continue
        low = url.lower().split("?")[0]
        if low.endswith(".mapx"):
            by_sid.setdefault(sid.upper(), {})["mapx"] = url
        elif low.endswith(".gdb.zip") or low.endswith("_gdb.zip"):
            by_sid.setdefault(sid.upper(), {})["gdb"] = url
    return [(sid, v["gdb"], v["mapx"]) for sid, v in sorted(by_sid.items())
            if "gdb" in v and "mapx" in v]


def run_all(force: bool = False, limit: int | None = None) -> int:
    """Convert every 3D-eligible pub whose artifacts aren't already present (skip-existing). Sharded via
    CLOUD_RUN_TASK_INDEX/COUNT — the pubs "harvest" of 3D, run before pubs-ingest binds the assets."""
    import os

    from ..core import gcs
    srcs = _threed_sources()
    n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if n > 1:
        srcs = srcs[i::n]
    if limit:
        srcs = srcs[:limit]
    print(f"[3d] {len(srcs)} 3D-eligible pubs (shard {i + 1}/{n})")
    rc = 0
    for sid, gdb_uri, mapx_uri in srcs:
        if not force and gcs.exists(threed_object(sid, POLY_NAME)):
            print(f"[3d] {sid}: skip (exists)")
            continue
        try:
            ingest(sid, gdb_uri, mapx_uri)
        except Exception as e:  # noqa: BLE001 — one bad pub shouldn't sink the shard
            print(f"[3d] {sid}: FAIL {type(e).__name__}: {e}")
            rc |= 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert 3D GeMS pubs (GDB + .mapx) → GeoParquet-3D + glTF")
    # --all: the Cloud Run path — auto-discover every 3D-eligible pub + convert (skip-existing). The
    # pubs ingest binds the assets afterward. One-off path: --gdb/--mapx/--id (+ --upload to push).
    ap.add_argument("--all", action="store_true", help="convert every 3D-eligible pub (gdb+mapx), skip-existing")
    ap.add_argument("--force", action="store_true", help="with --all: re-convert even if artifacts exist")
    ap.add_argument("--limit", type=int, default=None, help="with --all: cap the number of pubs")
    ap.add_argument("--gdb", help="local .gdb dir (convert), or a .gdb.zip path/gs://-/https:// URI (--upload)")
    ap.add_argument("--mapx", help="the ArcGIS scene .mapx (path or gs://-/https:// URI)")
    ap.add_argument("--id", help="series id, e.g. OFR-778DM")
    ap.add_argument("--out", default="out/3d", help="output directory")
    ap.add_argument("--upload", action="store_true",
                    help="localize → convert → upload artifacts to CDN (pubs ingest binds them; needs GCS perms)")
    args = ap.parse_args()

    if args.all:
        return run_all(force=args.force, limit=args.limit)
    if not (args.gdb and args.mapx and args.id):
        ap.error("one-off mode needs --gdb, --mapx, and --id (or use --all)")
    m = ingest(args.id, args.gdb, args.mapx, args.out) if args.upload \
        else convert(args.gdb, args.mapx, args.id, args.out)
    print(json.dumps({k: v for k, v in m.items() if k != "classification:classes"}, indent=2))
    print(f"classification:classes: {len(m['classification:classes'])} units"
          f"{', uncolored ' + ','.join(m['uncolored']) if m['uncolored'] else ''}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

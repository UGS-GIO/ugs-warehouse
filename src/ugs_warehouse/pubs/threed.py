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

THREED_PREFIX = "geolmap/3d"  # CDN object prefix (matches the existing csa_3d GeoJSON assets)
GLTF_MIME = "model/gltf-binary"


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


def _upload_outputs(meta: dict, series_id: str) -> dict:
    """Upload the GeoParquet + glTF to the CDN; return the STAC asset dicts (immutable, content-addressed
    by the series id path)."""
    from ..core import config, gcs
    out = {}
    specs = [
        ("fence_polygons", meta["polygons_parquet"], f"{series_id}_3d_polygons.parquet",
         config.PARQUET_MIME, ["data", "3d-vector"], "3D fence polygons (GeoParquet)"),
        ("fence_lines", meta["lines_parquet"], f"{series_id}_3d_lines.parquet",
         config.PARQUET_MIME, ["data", "3d-vector"], "3D fence contacts & faults (GeoParquet)"),
        ("fence_mesh", meta["gltf"], f"{series_id}_3d.glb",
         GLTF_MIME, ["data", "visual"], "3D fence mesh (glTF)"),
    ]
    for key, local, name, mime, roles, title in specs:
        obj = f"{THREED_PREFIX}/{name}"
        gcs.upload(local, obj, content_type=mime, cache_control=gcs.CACHE_IMMUTABLE)
        out[key] = {"href": config.public_url(obj), "type": mime, "roles": roles, "title": title}
    return out


def _stamp_item(series_id: str, assets: dict, classes: list[dict]) -> None:
    """Patch the published pub STAC item: add the cloud-native 3D assets + classification:classes (the
    standard per-unit colour mechanism) + the extension. Leaves existing assets/links intact."""
    from ..core import config, gcs, stac
    series = re.match(r"[A-Za-z]+", series_id)
    coll = f"ugs-publications/{(series.group(0).upper() if series else 'OTHER')}"
    obj = stac.item_object_path(coll, series_id)
    item = json.loads(gcs.get_bytes(obj))
    item.setdefault("assets", {}).update(assets)
    item.setdefault("properties", {})["classification:classes"] = classes
    exts = item.setdefault("stac_extensions", [])
    if stac.CLASSIFICATION_EXT not in exts:
        exts.append(stac.CLASSIFICATION_EXT)
    gcs.put_bytes(json.dumps(item).encode(), obj, content_type="application/geo+json",
                  cache_control=gcs.CACHE_MUTABLE)
    print(f"  stamped {config.public_url(obj)}")


def ingest(series_id: str, gdb_uri: str, mapx_uri: str, out_dir: str | None = None) -> dict:
    """Full 3D ingest: localize the pub's GDB + .mapx → convert → upload outputs → stamp the STAC item
    → refresh the catalog. Needs GCS write perms. Returns the conversion meta."""
    import tempfile

    from ..core import stac
    with tempfile.TemporaryDirectory() as tmp:
        gdb = _localize_gdb(gdb_uri, tmp)
        mapx = _localize(mapx_uri, f"{tmp}/scene.mapx")
        meta = convert(gdb, mapx, series_id, out_dir or f"{tmp}/out")
        assets = _upload_outputs(meta, series_id)
        _stamp_item(series_id, assets, meta["classification:classes"])
        stac.refresh_catalog()
        print(f"3D ingest complete for {series_id}: {meta['units']} units, {meta['gltf_triangles']} tris")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert a 3D GeMS pub (GDB + .mapx) to GeoParquet-3D")
    ap.add_argument("--gdb", required=True,
                    help="local .gdb dir (convert), or a .gdb.zip path/gs://-/https:// URI (--upload)")
    ap.add_argument("--mapx", required=True, help="the ArcGIS scene .mapx (path or gs://-/https:// URI)")
    ap.add_argument("--id", required=True, help="series id, e.g. OFR-778DM")
    ap.add_argument("--out", default="out/3d", help="output directory")
    ap.add_argument("--upload", action="store_true",
                    help="localize → convert → upload to CDN → stamp the STAC item → refresh (needs GCS perms)")
    args = ap.parse_args()
    m = ingest(args.id, args.gdb, args.mapx, args.out) if args.upload \
        else convert(args.gdb, args.mapx, args.id, args.out)
    print(json.dumps({k: v for k, v in m.items() if k != "classification:classes"}, indent=2))
    print(f"classification:classes: {len(m['classification:classes'])} units"
          f"{', uncolored ' + ','.join(m['uncolored']) if m['uncolored'] else ''}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

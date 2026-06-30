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

Pure conversion (GDB+mapx → local files + asset metadata); GCS upload + STAC write is a thin wrapper
(work box). Runnable + verifiable locally against an extracted GDB — see `convert()` / `main()`.
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
        "classification:classes": classes,
        "units": len(units),
        "uncolored": [u for u in units if not colors.get(u)],
    }
    (out / f"{series_id}_3d_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert a 3D GeMS pub (GDB + .mapx) to GeoParquet-3D")
    ap.add_argument("--gdb", required=True, help="path to the .gdb directory")
    ap.add_argument("--mapx", required=True, help="path to the ArcGIS scene .mapx (authored symbology)")
    ap.add_argument("--id", required=True, help="series id, e.g. OFR-778DM")
    ap.add_argument("--out", default="out/3d", help="output directory")
    args = ap.parse_args()
    m = convert(args.gdb, args.mapx, args.id, args.out)
    print(json.dumps({k: v for k, v in m.items() if k != "classification:classes"}, indent=2))
    print(f"classification:classes: {len(m['classification:classes'])} units"
          f"{', uncolored ' + ','.join(m['uncolored']) if m['uncolored'] else ''}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

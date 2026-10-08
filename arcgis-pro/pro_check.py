"""Check the UGS Warehouse toolbox inside ArcGIS Pro and write a report anyone can read back.

Answers what the fake-arcpy tests cannot: which of the toolbox's ways in open a GeoParquet and a COG, which
GeoParquet shapes Pro opens (the published file with its nested `bbox`, a flat copy, a native
GEOMETRY copy), what Add Warehouse Layer actually puts on a map from each source, and whether a
geodatabase copy keeps a non-WGS84 layer in its own CRS.

A shell, no Pro window (any saved project; it is opened, never saved over):
    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\scripts\\propy.bat" pro_check.py --aprx <project.aprx>
Pro's Python window, with this file next to UGSWarehouse.pyt:
    import sys; sys.path.insert(0, r"<this folder>"); import pro_check; pro_check.run()
  The window run leaves its check maps and layout in the open project; close it without saving.
Add --big (or big=True) to also open the 1.9 GB wetlands outline online; it needs a sign-in Pro
opens GeoParquet with (a service account key), so with a personal sign-in it reports a failure.

Output goes to Documents\\UGS Warehouse check\\<timestamp>: report.json, a PNG per layer added, and,
from a shell, check.aprx. The shell run exits 1 when any check failed; report.json says which.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import platform
import sys
import time
import traceback
import types

import arcpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ugs_catalog as cat  # noqa: E402

SMALL = "hazards_qfaults"  # 4.4 MB, one row group, 3D lines, a nested bbox column
BIG = "wetlands_wetlandsoutline"  # 1.9 GB, 105 row groups
# Layers the tool runs on: lines, polygons, points, a `date` column, a style with an `in` filter,
# and the RASTERS below.
PICKS = ("hazards_qfaults", "hazards_alluvialfan", "enmin_powerplants", "enmin_oilgasfields_ogm",
         "enmin_ccus_geochemistry")
SLC = (-112.0, 40.6, -111.8, 40.8)  # a small Salt Lake City extent for the spatial-filter read
# Most publication COGs are WebP-compressed inside the TIFF, which Esri doesn't document reading;
# one of each kind shows whether Pro opens them.
RASTERS = {"FSM-18": "WebP", "UU-MS-601": "DEFLATE"}


class Messages:
    """Stands in for the geoprocessing messages object and keeps every line."""

    def __init__(self):
        self.lines: list[list[str]] = []

    def addMessage(self, text):  # noqa: N802 - arcpy's name
        self.lines.append(["info", str(text)])

    def addWarningMessage(self, text):  # noqa: N802
        self.lines.append(["warning", str(text)])

    def addErrorMessage(self, text):  # noqa: N802
        self.lines.append(["error", str(text)])


def _err() -> str:
    return traceback.format_exc().strip().splitlines()[-1]


def _timed(fn):
    t = time.time()
    return fn(), round(time.time() - t, 2)


def _load_toolbox():
    loader = importlib.machinery.SourceFileLoader("ugs_warehouse_toolbox",
                                                  os.path.join(HERE, "UGSWarehouse.pyt"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def _io():
    """Upper bounds on what came down the wire: all traffic on the machine, and all reads (disk
    and network) by this process. Run the big check on an otherwise quiet machine."""
    try:
        import psutil
    except ImportError:
        return None
    out = {"machine_bytes_recv_all_traffic": psutil.net_io_counters().bytes_recv}
    if hasattr(psutil.Process, "io_counters"):
        out["process_bytes_read_all_io"] = psutil.Process().io_counters().read_bytes
    return out


def _delta(before, after):
    return {k: after[k] - before[k] for k in before} if before and after else None


def _env() -> dict:
    out = {"pro": arcpy.GetInstallInfo().get("Version"), "license": arcpy.ProductInfo(),
           "python": sys.version.split()[0], "os": platform.platform(),
           "toolbox_branch": cat.toolbox_branch(HERE)}
    for mod in ("pyarrow", "duckdb", "psutil"):
        try:
            out[mod] = __import__(mod).__version__
        except ImportError:
            out[mod] = None
    return out


def _asset(layer_id: str) -> dict:
    layer = next(x for x in cat.layers() if x.id == layer_id)
    return cat.get_json(layer.item_url)["assets"]["data"]


def _open(path: str) -> dict:
    """What Pro sees in a dataset: row count, fields, geometry type, and how long that took."""
    try:
        n, secs = _timed(lambda: int(arcpy.management.GetCount(path)[0]))
        d = arcpy.Describe(path)
        return {"ok": True, "count": n, "seconds": secs,
                "shape": getattr(d, "shapeType", None), "has_z": getattr(d, "hasZ", None),
                "fields": [f"{f.name}:{f.type}" for f in arcpy.ListFields(path)]}
    except Exception:  # noqa: BLE001 - the failure is the finding
        return {"ok": False, "error": _err()}


def _open_raster(path: str) -> dict:
    try:
        bands, secs = _timed(lambda: int(arcpy.management.GetRasterProperties(path, "BANDCOUNT").getOutput(0)))
        return {"ok": bands > 0, "bands": bands, "seconds": secs}
    except Exception:  # noqa: BLE001 - the failure is the finding
        return {"ok": False, "error": _err()}


def _ways(href: str, work: str, raster: bool) -> dict:
    """Each way the toolbox tries for `href`, opened on its own, plus the ways it ruled out."""
    tries, reasons = _load_toolbox()._ways_in(href, work, raster)
    out = {"ruled_out": reasons, "ways": {}}
    for label, path in tries:
        try:
            where = path()
            out["ways"][label] = {"path": where, **(_open_raster if raster else _open)(where)}
        except Exception:  # noqa: BLE001 - creating the connection failed
            out["ways"][label] = {"ok": False, "error": _err()}
    out["opened_by"] = [label for label, r in out["ways"].items() if r.get("ok")]
    return out


def check_connections(work: str) -> dict:
    """The small GeoParquet and a COG through every way the toolbox tries."""
    out = {"parquet": _ways(_asset(SMALL)["href"], work, raster=False)}
    cog = next((r for r in cat.rasters() if r.id in RASTERS), None)
    out["cog"] = _ways(cog.href, work, raster=True) if cog else {"error": "no listed COG to try"}
    return out


def check_shapes(work: str) -> dict:
    """The published file, a copy without its nested columns, and a native-GEOMETRY copy, all
    opened from local disk: tells "Pro refuses a nested column" from "Pro skips it", and whether
    Parquet's own geometry type (bboxes in the footer, nothing nested) is the way out."""
    import pyarrow.parquet as pq

    path = cat.download(_asset(SMALL), work, f"{SMALL}.parquet")
    table = pq.read_table(path)
    nested = [f.name for f in table.schema if f.type.num_fields]
    geo = json.loads((table.schema.metadata or {}).get(b"geo", b"{}"))
    for col in geo.get("columns", {}).values():
        col.pop("covering", None)
    flat = table.select([n for n in table.column_names if n not in nested])
    flat_path = os.path.join(work, f"{SMALL}.flat.parquet")
    pq.write_table(flat.replace_schema_metadata({b"geo": json.dumps(geo).encode()}), flat_path,
                   compression="zstd")
    out = {"nested_columns": nested, "as_published": _open(path), "without_nested": _open(flat_path)}
    native = os.path.join(work, f"{SMALL}.native.parquet")
    try:
        import duckdb

        drop = f"EXCLUDE ({', '.join(nested)})" if nested else ""
        duckdb.connect().execute(f"COPY (SELECT * {drop} FROM '{path}') TO '{native}' "
                                 "(FORMAT PARQUET, COMPRESSION ZSTD, GEOPARQUET_VERSION 'BOTH')")
        out["native_geometry"] = _open(native)
    except Exception:  # noqa: BLE001 - an older duckdb cannot write it; the report says so
        out["native_geometry"] = {"ok": False, "error": f"copy not written: {_err()}"}
    return out


def check_big(work: str, label: str) -> dict:
    """Open the 1.9 GB file the way the small one opened, then read one small extent."""
    asset = _asset(BIG)
    tries, _ = _load_toolbox()._ways_in(asset["href"], work, False)
    path = dict(tries)[label]()
    out = {"way": label, "file_bytes": asset.get("file:size")}
    before = _io()
    out["open"] = _open(path)
    out["io_during_open"] = _delta(before, _io())
    x0, y0, x1, y1 = SLC
    box = arcpy.Polygon(arcpy.Array([arcpy.Point(x0, y0), arcpy.Point(x1, y0), arcpy.Point(x1, y1),
                                     arcpy.Point(x0, y1)]), arcpy.SpatialReference(4326))
    try:
        mid = _io()

        def count():
            with arcpy.da.SearchCursor(path, ["OID@"], spatial_filter=box) as cur:
                return sum(1 for _ in cur)

        n, secs = _timed(count)
        total = out["open"].get("count")
        out["slc_extent"] = {"ok": True, "count": n, "seconds": secs, "io": _delta(mid, _io()),
                             "fraction_of_rows": round(n / total, 4) if total else None}
    except Exception:  # noqa: BLE001
        out["slc_extent"] = {"ok": False, "error": _err()}
    return out


def check_copy_crs(work: str) -> dict:
    """Copy a publication's unit polygons to the geodatabase and confirm the feature class lands
    in the CRS its GeoParquet names rather than WGS84."""
    import pyarrow.parquet as pq

    root = cat.get_json(f"{cat.STAC}/items.json")
    entry = next((i for i in root.get("items") or [] if (i.get("assets") or {}).get("units")), None)
    if entry is None:
        return {"ok": False, "error": "no item in the root index has a units asset"}
    asset = entry["assets"]["units"]
    path = cat.download(asset, work, f"{cat.safe_filename(entry['id'])}.units.parquet")
    geo = json.loads(pq.read_schema(path).metadata[b"geo"])
    expected, problem = cat.crs_code(geo, geo.get("primary_column", "geom"))
    msgs = Messages()
    fc = _load_toolbox()._to_fgdb(cat.pro_ready(path), work, f"{entry['id']}_units", msgs)
    got = arcpy.Describe(fc).spatialReference.factoryCode
    return {"ok": got == expected, "item": entry["id"], "expected": expected, "got": got,
            "problem": problem, "rows": int(arcpy.management.GetCount(fc)[0]), "messages": msgs.lines}


def _symbology(lyr) -> dict:
    try:
        if lyr.isRasterLayer:
            return {"colorizer": lyr.symbology.colorizer.type}
        r = lyr.symbology.renderer
        out = {"type": r.type}
        if r.type == "UniqueValueRenderer":
            items = [i for g in r.groups for i in g.items]
            out.update(fields=list(r.fields), classes=len(items),
                       sample=[[str(i.values[0][0]) if i.values else "", i.label] for i in items[:5]])
        elif r.type == "SimpleRenderer":
            out["color"] = r.symbol.color
        return out
    except Exception:  # noqa: BLE001
        return {"error": _err()}


def run_tool(m, mode: str, work: str, picks: list[str]) -> dict:
    """Run Add Warehouse Layer as Pro would, onto its own map `m`, and record what landed there.
    `mode` is "stream" or "copy"; the option's label comes from the toolbox, so a rename holds."""
    tbx = _load_toolbox()
    source = {"stream": tbx.STREAM, "copy": tbx.COPY}[mode]
    tool = tbx.AddLayer()
    params = tool.getParameterInfo()
    by_id = {x.id: x for x in tbx._layers()}
    params[2].values = [by_id[i].choice for i in picks if i in by_id]
    params[3].value = source
    params[4].value = work
    params[5].value = True
    msgs = Messages()
    real = arcpy.mp.ArcGISProject

    def project(path):
        return types.SimpleNamespace(activeMap=m) if path == "CURRENT" else real(path)

    arcpy.mp.ArcGISProject = project  # the tool asks for CURRENT; hand it this check's map
    try:
        _, secs = _timed(lambda: tool.execute(params, msgs))
        error = None
    except Exception:  # noqa: BLE001
        secs, error = None, _err()
    finally:
        arcpy.mp.ArcGISProject = real
    layers = []
    for lyr in m.listLayers():
        if lyr.isBasemapLayer or lyr.isGroupLayer:
            continue
        src = getattr(lyr, "dataSource", "") or ""
        layers.append({"name": lyr.name, "broken": lyr.isBroken, "source": src,
                       "streamed": ".acs" in src.lower(), "symbology": _symbology(lyr)})
    return {"source": source, "seconds": secs, "error": error, "messages": msgs.lines,
            "missing_picks": [i for i in picks if i not in by_id], "layers": layers}


def export_pngs(aprx, maps: dict, out: str) -> list[dict]:
    """One PNG per layer, zoomed to it, so the styling can be looked at without Pro."""
    rows = []
    for label, m in maps.items():
        try:
            layout = aprx.createLayout(8, 6, "INCH", f"UGS check {label}")
            mf = layout.createMapFrame(arcpy.Extent(0, 0, 8, 6).polygon, m, "frame")
        except Exception:  # noqa: BLE001
            rows.append({"map": label, "ok": False, "error": f"no layout: {_err()}"})
            continue
        layers = [x for x in m.listLayers() if not (x.isBasemapLayer or x.isGroupLayer)]
        for lyr in layers:
            if lyr.isBroken or not (lyr.isFeatureLayer or lyr.isRasterLayer):
                rows.append({"map": label, "layer": lyr.name, "ok": False, "error": "broken or not drawable"})
                continue
            for other in layers:
                other.visible = other is lyr
            png = os.path.join(out, f"{label}_{len(rows):02d}_{cat.esri_name(lyr.name, set())}.png")
            try:
                mf.camera.setExtent(mf.getLayerExtent(lyr, False, True))
                layout.exportToPNG(png, resolution=96)
                rows.append({"map": label, "layer": lyr.name, "ok": True, "png": png})
            except Exception:  # noqa: BLE001
                rows.append({"map": label, "layer": lyr.name, "ok": False, "error": _err()})
    return rows


def failures(report: dict) -> list[str]:
    """Every failed check in the report, as `step: what`."""
    bad = []

    def walk(where, node):
        if isinstance(node, dict):
            if node.get("ok") is False or node.get("error") or node.get("broken"):
                bad.append(f"{where}: {node.get('error') or ('broken' if node.get('broken') else 'ok false')}")
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(f"{where}.{k}", v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(f"{where}[{i}]", v)

    for step, node in report.items():
        if step != "connections":  # one way in failing is expected while another opens
            walk(step, node)
    conns = report.get("connections") or {}
    if conns.get("error"):
        bad.append(f"connections: {conns['error']}")
    for kind in ("parquet", "cog"):
        node = conns.get(kind) or {}
        if node.get("error"):
            bad.append(f"connections.{kind}: {node['error']}")
        elif node.get("ways") and not node.get("opened_by"):
            bad.append(f"connections.{kind}: no way in opened it")
        elif kind == "cog" and "ways" in node and not node["ways"]:
            bad.append(f"connections.cog: no way in to try ({'; '.join(node.get('ruled_out') or [])})")
    for run in ("tool_stream", "tool_copy"):
        if isinstance(report.get(run), dict) and not report[run].get("layers"):
            bad.append(f"{run}: no layers added")
    return bad


def run(aprx_path: str | None = None, big: bool = False, out: str | None = None) -> dict:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out or os.path.join(os.path.expanduser("~"), "Documents", "UGS Warehouse check", stamp)
    work = os.path.join(out, "work")
    os.makedirs(work, exist_ok=True)
    aprx = arcpy.mp.ArcGISProject(aprx_path or "CURRENT")
    listed = {x.id for x in cat.rasters()}
    picks = [*PICKS, *(r for r in RASTERS if r in listed)]
    maps = {"stream": aprx.createMap(f"UGS check stream {stamp}"),
            "copy": aprx.createMap(f"UGS check copy {stamp}")}
    report: dict = {"started": stamp, "out": out, "picks": picks,
                    "raster_compression": {r: c for r, c in RASTERS.items() if r in listed}}

    def step(name, fn):
        print(f"[pro_check] {name} ...")
        try:
            report[name] = fn()
        except Exception:  # noqa: BLE001 - one failed step still leaves the others in the report
            report[name] = {"ok": False, "error": traceback.format_exc()}
        with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)

    step("env", _env)
    step("connections", lambda: check_connections(work))
    step("shapes", lambda: check_shapes(work))
    step("tool_stream", lambda: run_tool(maps["stream"], "stream", os.path.join(work, "stream"), picks))
    step("tool_copy", lambda: run_tool(maps["copy"], "copy", os.path.join(work, "copy"), picks))
    step("copy_crs", lambda: check_copy_crs(os.path.join(work, "crs")))
    if big:
        good = ((report.get("connections") or {}).get("parquet") or {}).get("opened_by") or []
        step("big", (lambda: check_big(work, good[0])) if good
             else (lambda: {"ok": False, "error": "no way in opened the small file"}))
    step("pngs", lambda: export_pngs(aprx, maps, out))
    if aprx_path:
        step("project_copy", lambda: aprx.saveACopy(os.path.join(out, "check.aprx")) or "check.aprx")
    report["failures"] = failures(report)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"[pro_check] {len(report['failures'])} failed checks; report: {os.path.join(out, 'report.json')}")
    for line in report["failures"]:
        print(f"  {line[:200]}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aprx", required=True, help="a saved project to open (never saved over)")
    ap.add_argument("--big", action="store_true", help="also open the 1.9 GB wetlands file online")
    ap.add_argument("--out", help="output folder (default: Documents\\UGS Warehouse check\\<timestamp>)")
    a = ap.parse_args()
    sys.exit(1 if run(a.aprx, a.big, a.out)["failures"] else 0)

"""UGS Warehouse toolbox for ArcGIS Pro: add warehouse vector layers, styled like the web viewer.

Keep this file and `ugs_catalog.py` in the same folder. See README.md.
"""
from __future__ import annotations

import importlib
import json
import os
import sys

import arcpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ugs_catalog as cat  # noqa: E402

importlib.reload(cat)  # Pro keeps imported modules; pick up an updated ugs_catalog.py

STREAM = "Open online (always current)"
COPY = "Download a copy (works offline)"
ALL = "All"
# Cloud storage connections tried in order: Pro's generic-HTTP provider, then S3 read anonymously,
# which the CDN answers because an unsigned path-style S3 GET is a plain https GET.
CONNECTIONS = (
    ("WEB", {}),
    ("AMAZON", {"region": "us-east-1",
                "config_options": [["AWS_NO_SIGN_REQUEST", "YES"], ["AWS_VIRTUAL_HOSTING", "FALSE"]]}),
)
_cache: dict = {}


def _layers() -> list[cat.Layer]:
    """The catalog's layers, or [] when it cannot be reached (the tool then says so)."""
    if "layers" not in _cache:  # one attempt per session: offline must not stall every refresh
        try:
            _cache["layers"] = cat.layers() + cat.rasters()
        except OSError as e:
            _cache["error"] = f"Can't reach the UGS catalog. Check your internet connection. ({e})"
            _cache["layers"] = []
    return _cache["layers"]


def _here() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _update_notice() -> str | None:
    """A note when GitHub has a newer toolbox than this folder, checked once per session."""
    if "stale" not in _cache:
        branch = cat.toolbox_branch(_here())
        stale = cat.stale_files(_here(), branch)
        _cache["stale"] = ("A newer version of this toolbox is available. Run Update Toolbox, then "
                           "right-click the toolbox and choose Refresh.") if stale else None
    return _cache["stale"]


def _default_folder() -> str:
    """`UGS Warehouse` in the project's folder, else Pro's scratch folder: both exist and are
    writable, unlike a Documents folder redirected to OneDrive or guarded by Windows."""
    try:
        home = arcpy.mp.ArcGISProject("CURRENT").homeFolder
        path = os.path.join(home, "UGS Warehouse")
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:  # noqa: BLE001 - no project, or a folder we may not write to
        return arcpy.env.scratchFolder


def _pro_version() -> tuple[int, ...]:
    v = arcpy.GetInstallInfo().get("Version", "0")
    return tuple(int(p) for p in v.split(".")[:2] if p.isdigit())


def _connection(folder: str, provider: str, options: dict) -> str:
    """The `.acs` for the CDN with `provider`, created once in `folder`."""
    name = f"ugs_cdn_{provider.lower()}"
    path = os.path.join(folder, name + ".acs")
    if not os.path.exists(path):
        arcpy.management.CreateCloudStorageConnectionFile(
            folder, name, provider, cat.CDN_BUCKET, end_point=cat.CDN_HOST, **options)
    return path


def _usable(m, lyr) -> bool:
    """True when Pro opened the layer; a broken one is removed so the fallback can replace it."""
    try:
        if not getattr(lyr, "isBroken", False) and int(arcpy.management.GetCount(lyr)[0]) >= 0:
            return True
    except Exception:  # noqa: BLE001 - any failure to read it means it is not usable
        pass
    m.removeLayer(lyr)
    return False


def _stream(m, asset: dict, folder: str, messages):
    """Add the GeoParquet from the CDN through a cloud storage connection, or None."""
    key = cat.cdn_key(asset["href"])
    if key is None or _pro_version() < (3, 5):
        return None
    tries = sorted(CONNECTIONS, key=lambda c: c[0] != _cache.get("stream"))  # last good one first
    reasons = []
    for provider, options in tries:
        try:
            path = os.path.join(_connection(folder, provider, options), *key.split("/"))
            lyr = m.addDataFromPath(path)
            if _usable(m, lyr):
                _cache["stream"] = provider
                messages.addMessage(f"  Opened online. ({provider})")
                return lyr
            reasons.append(f"{provider}: would not open")
        except Exception as e:  # noqa: BLE001 - try the next way in
            reasons.append(f"{provider}: {e}")
    messages.addMessage(f"  Couldn't open it online, so it was downloaded instead. "
                        f"({'; '.join(reasons)})")
    return None


_GEOMETRY = {"Point": "POINT", "MultiPoint": "MULTIPOINT", "LineString": "POLYLINE",
             "MultiLineString": "POLYLINE", "Polygon": "POLYGON", "MultiPolygon": "POLYGON"}


def _to_fgdb(parquet: str, folder: str, name: str) -> str:
    """A feature class in `<folder>/UGS Warehouse.gdb` with the GeoParquet's rows. Any Pro version."""
    import pyarrow.parquet as pq

    table = pq.read_table(parquet)
    geo = json.loads((table.schema.metadata or {}).get(b"geo", b"{}"))
    gcol = geo.get("primary_column", "geom")
    types = [t.replace(" Z", "") for t in geo.get("columns", {}).get(gcol, {}).get("geometry_types", [])]
    shape = "MULTIPOINT" if "MultiPoint" in types else _GEOMETRY.get(types[0] if types else "", "POLYGON")
    has_z = any(t.endswith(" Z") for t in geo.get("columns", {}).get(gcol, {}).get("geometry_types", []))

    gdb = os.path.join(folder, "UGS Warehouse.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(folder, "UGS Warehouse.gdb")
    fc = os.path.join(gdb, name)
    if arcpy.Exists(fc):
        try:
            arcpy.management.Delete(fc)
        except Exception:  # noqa: BLE001 - in use on a map: write alongside it
            fc = arcpy.CreateUniqueName(name, gdb)
    sr = arcpy.SpatialReference(4326)
    arcpy.management.CreateFeatureclass(gdb, os.path.basename(fc), shape, spatial_reference=sr,
                                        has_z="ENABLED" if has_z else "DISABLED")
    fields = [(f.name, spec) for f in table.schema if f.name != gcol
              and (spec := cat.esri_field(f.type, table.column(f.name), _pro_version()))]
    if fields:
        arcpy.management.AddFields(fc, [[n, s[0], n, s[1]] for n, s in fields])
    names = [n for n, _ in fields]
    with arcpy.da.InsertCursor(fc, ["SHAPE@", *names]) as cur:
        for batch in table.to_batches():
            cols = [batch.column(gcol).to_pylist(), *(batch.column(n).to_pylist() for n in names)]
            for row in zip(*cols):
                wkb = row[0]
                cur.insertRow([arcpy.FromWKB(bytearray(wkb), sr) if wkb else None,
                               *(cat.esri_value(v) for v in row[1:])])
    return fc


def _copy(m, asset: dict, folder: str, name: str, messages):
    mb = (asset.get("file:size") or 0) / 1e6
    messages.addMessage(f"  Downloading ({mb:.1f} MB)...")
    path = cat.download(asset, folder, f"{name}.parquet")
    return m.addDataFromPath(_to_fgdb(cat.pro_ready(path), folder, name))


def _apply_metadata(lyr, fields: dict[str, str], messages) -> None:
    """Title, description, tags, credits and use limits from the catalog, on the layer.

    A layer that shows its source's metadata is read-only; then it goes on a geodatabase copy's
    feature class instead. A streamed file keeps none, which the messages say.
    """
    md = lyr.metadata
    if getattr(md, "isReadOnly", False):
        source = getattr(lyr, "dataSource", "") or ""
        if not (source and ".gdb" in source.lower()):
            return
        md = arcpy.metadata.Metadata(source)
    for key, value in fields.items():
        setattr(md, key, value)
    md.save()


def _apply_style(lyr, item: dict, messages) -> None:
    """A renderer from the layer's GL style: same field, colors and legend labels."""
    renders = (item.get("properties") or {}).get("ugs:renders") or {}
    render = renders.get("default") or next(iter(renders.values()), None) or {}
    if not render.get("style_url"):
        return
    style = cat.get_json(render["style_url"])
    found = cat.classes(style, render.get("legend"))
    if not found:
        rgb = cat.single_color(style)
        if rgb and hasattr(lyr.symbology, "renderer"):
            sym = lyr.symbology
            sym.updateRenderer("SimpleRenderer")
            sym.renderer.symbol.color = {"RGB": [*rgb, 100]}
            lyr.symbology = sym
        else:
            messages.addMessage("  Shown with ArcGIS's default symbol (no UGS colors for this one).")
        return
    field, classes = found
    names = {f.name.lower(): f.name for f in arcpy.ListFields(lyr)}
    if field.lower() not in names or not hasattr(lyr.symbology, "renderer"):
        messages.addWarningMessage(f"  Couldn't apply UGS colors (no '{field}' field).")
        return
    lookup = {str(v).lower(): (label, rgb) for v, label, rgb in classes}
    sym = lyr.symbology
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [names[field.lower()]]
    if hasattr(sym.renderer, "addAllValues"):
        sym.renderer.addAllValues()
    for group in sym.renderer.groups:
        for entry in group.items:
            if not (entry.values and entry.values[0]):  # the "all other values" entry
                continue
            match = lookup.get(str(entry.values[0][0]).lower())
            if match:
                entry.label = match[0]
                entry.symbol.color = {"RGB": [*match[1], 100]}
    lyr.symbology = sym


class Toolbox:
    def __init__(self):
        self.label = "UGS Warehouse"
        self.alias = "ugswarehouse"
        self.tools = [AddLayer, UpdateToolbox]


class AddLayer:
    def __init__(self):
        self.label = "Add Warehouse Layer"
        self.description = ("Add Utah Geological Survey map layers and scanned geologic maps to the "
                            "current map, in the same colors as the UGS web map.")

    def getParameterInfo(self):
        theme = arcpy.Parameter(displayName="Theme", name="theme", datatype="GPString",
                                parameterType="Optional", direction="Input")
        theme.filter.type = "ValueList"
        theme.filter.list = [ALL, *sorted({cat.theme_name(lyr.theme) for lyr in _layers()})]
        theme.value = ALL

        search = arcpy.Parameter(displayName="Search", name="search", datatype="GPString",
                                 parameterType="Optional", direction="Input")

        picks = arcpy.Parameter(displayName="Layers", name="layers", datatype="GPString",
                                parameterType="Required", direction="Input", multiValue=True)
        picks.filter.type = "ValueList"
        picks.filter.list = [lyr.choice for lyr in _layers()]

        source = arcpy.Parameter(displayName="Source", name="source", datatype="GPString",
                                 parameterType="Required", direction="Input")
        source.filter.type = "ValueList"
        source.filter.list = [STREAM, COPY]
        source.value = STREAM

        folder = arcpy.Parameter(displayName="Save downloads to", name="folder", datatype="DEFolder",
                                 parameterType="Optional", direction="Input")
        folder.value = _default_folder()  # an input folder must exist to validate

        style = arcpy.Parameter(displayName="Use UGS map colors", name="style",
                                datatype="GPBoolean", parameterType="Optional", direction="Input")
        style.value = True
        return [theme, search, picks, source, folder, style]

    def updateParameters(self, parameters):
        theme, search, picks = parameters[:3]
        if theme.altered or search.altered:
            want, query = theme.valueAsText, search.valueAsText
            chosen = set(picks.values or [])  # keep what is already picked while the list narrows
            picks.filter.list = [lyr.choice for lyr in _layers()
                                 if (want in (None, ALL, cat.theme_name(lyr.theme))
                                     and lyr.matches(query))
                                 or lyr.choice in chosen]

    def updateMessages(self, parameters):
        if notice := _update_notice():
            parameters[0].setWarningMessage(notice)
        if "error" in _cache:
            parameters[2].setErrorMessage(_cache["error"])
        source = parameters[3]
        if source.valueAsText == STREAM and _pro_version() < (3, 5):
            source.setWarningMessage("This version of ArcGIS Pro can't open these layers online, "
                                     "so they'll be downloaded.")

    def execute(self, parameters, messages):
        picks, source, folder, style = parameters[2:6]
        m = arcpy.mp.ArcGISProject("CURRENT").activeMap
        if m is None:
            raise arcpy.ExecuteError("Open a map first.")
        work = folder.valueAsText or _default_folder()
        os.makedirs(work, exist_ok=True)
        by_id = {lyr.id: lyr for lyr in _layers()}
        for choice in picks.values:
            layer = by_id[cat.id_of(choice)]
            messages.addMessage(layer.title)
            if layer.is_raster:  # a COG: Pro reads it from its URL, range by range
                lyr = m.addDataFromPath(layer.href)
                fields = cat.metadata(dict(layer.properties), source=layer.href)
            else:
                item = cat.get_json(layer.item_url)
                asset = item["assets"]["data"]
                lyr = _stream(m, asset, work, messages) if source.valueAsText == STREAM else None
                if lyr is None:
                    lyr = _copy(m, asset, work, layer.id, messages)
                try:
                    collection = cat.get_json(layer.collection_url)
                except OSError:
                    collection = None
                fields = cat.metadata(item.get("properties") or {}, collection, layer.item_url)
            lyr.name = layer.title
            try:
                _apply_metadata(lyr, fields, messages)
            except Exception as e:  # noqa: BLE001 - the layer is on the map; metadata is extra
                messages.addWarningMessage(f"  Couldn't add the layer's description. ({e})")
            if style.value and not layer.is_raster:
                try:
                    _apply_style(lyr, item, messages)
                except Exception as e:  # noqa: BLE001 - the layer is on the map; styling is extra
                    messages.addWarningMessage(f"  Couldn't apply UGS colors. ({e})")


class UpdateToolbox:
    def __init__(self):
        self.label = "Update Toolbox"
        self.description = "Get the latest version of the UGS Warehouse toolbox."

    def getParameterInfo(self):
        branch = arcpy.Parameter(displayName="Version: main, or a pull request number to test",
                                 name="branch", datatype="GPString",
                                 parameterType="Required", direction="Input")
        branch.value = cat.toolbox_branch(_here())
        return [branch]

    def updateMessages(self, parameters):
        notice = _update_notice()
        if notice:
            parameters[0].setWarningMessage(notice)

    def execute(self, parameters, messages):
        folder = _here()
        branch = parameters[0].valueAsText.strip()
        try:
            changed = cat.update_toolbox(folder, branch)
        except cat.VersionNotFound:
            raise arcpy.ExecuteError(f"Couldn't find version '{branch}'. Enter main, or a pull "
                                     "request number like 535. Your toolbox wasn't changed.")
        except Exception as e:  # noqa: BLE001 - nothing was replaced; say why
            raise arcpy.ExecuteError(f"Couldn't update; your toolbox wasn't changed. ({e})")
        if not changed:
            messages.addMessage("Already up to date.")
            return
        _cache.pop("stale", None)
        messages.addMessage("Updated.")
        messages.addWarningMessage("To finish, right-click the UGS Warehouse toolbox and choose Refresh.")

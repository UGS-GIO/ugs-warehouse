"""UGS Warehouse toolbox for ArcGIS Pro: add warehouse vector layers, styled like the web viewer.

Keep this file and `ugs_catalog.py` in the same folder. See README.md.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import uuid

import arcpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ugs_catalog as cat  # noqa: E402

importlib.reload(cat)  # Pro keeps imported modules; pick up an updated ugs_catalog.py

STREAM = "Open online (always current)"
COPY = "Download a copy (works offline)"
ALL = "All"
# An https href's host as a cloud storage connection. Esri's generic HTTP provider opens rasters only.
CONNECTIONS = (("WEB", {}),)
# An s3:// or gs:// href goes to that provider's own endpoint, read anonymously.
NATIVE = {"s3": ("AMAZON", {"config_options": [["AWS_NO_SIGN_REQUEST", "YES"]]}),
          "gs": ("GOOGLE", {"config_options": [["GS_NO_SIGN_REQUEST", "YES"]]})}
_cache: dict = {}


def _layers() -> list[cat.Layer]:
    """The catalog's layers; what can't be read is left out and the tool says so."""
    if "layers" not in _cache:  # one attempt per session: offline must not stall every refresh
        found, failed = [], []
        for kind, read in (("map layers", cat.layers), ("scanned geologic maps", cat.rasters)):
            try:
                found += read()
            except OSError as e:
                failed.append(f"{kind}: can't connect ({e})")
            except (ValueError, KeyError, TypeError) as e:  # a cut-off, non-JSON or odd response
                failed.append(f"{kind}: unexpected response ({type(e).__name__}: {e})")
        if failed and not found:
            _cache["error"] = f"Can't read the UGS catalog. ({'; '.join(failed)})"
        elif failed:
            _cache["warning"] = f"Some of the UGS catalog couldn't be read, so it isn't listed. ({'; '.join(failed)})"
        _cache["layers"] = found
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


def _connection(folder: str, provider: str, options: dict, bucket: str, host: str = "") -> str:
    """The `.acs` for one bucket on one host with `provider`, created once in `folder`. No host
    means the provider's own endpoint (an s3:// or gs:// href)."""
    name = cat.connection_name(provider, host, bucket)
    path = os.path.join(folder, name + ".acs")
    if not os.path.exists(path):
        endpoint = {"end_point": host} if host else {}
        arcpy.management.CreateCloudStorageConnectionFile(
            folder, name, provider, bucket, **endpoint, **options)
    return path


def _short_path(path: str) -> str:
    """Windows' 8.3 form of `path` (no spaces) where the volume keeps one, else `path`."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf)):
            return buf.value
    except (AttributeError, OSError):  # not Windows
        pass
    return path


def _google(folder: str, href: str):
    """(label, path-maker) for reading `href` straight from the bucket with this machine's Google
    sign-in, or (label, None) with the reason it can't."""
    obj, cred = cat.bucket_object(href), cat.google_credentials()
    if obj is None:
        return "GOOGLE", None
    if cred is None:
        return "GOOGLE (not signed in)", None
    kind = cat.google_credential_type(cred)
    if kind is None:
        return f"GOOGLE (can't read the sign-in file {cred}; sign in again)", None
    cred = _short_path(cred).replace("\\", "/")  # Pro reads backslashes here as escapes
    if " " in cred:  # the options string splits on spaces
        return f"GOOGLE (the sign-in file's path has a space: {cred})", None
    # Esri's documented form, which opened GeoParquet in Pro: one string, True/False.
    options = f"GOOGLE_APPLICATION_CREDENTIALS {cred}; GS_NO_SIGN_REQUEST False"
    name = "google_ugs_" + hashlib.sha1(options.encode()).hexdigest()[:6]

    def path() -> str:
        acs = os.path.join(folder, name + ".acs")
        if not os.path.exists(acs):
            arcpy.management.CreateCloudStorageConnectionFile(
                folder, name, "GOOGLE", cat.GCS_BUCKET, config_options=options)
        return f"{acs}/{obj}".replace("\\", "/")
    return "GOOGLE", path


def _unusable(m, lyr, raster: bool = False) -> str | None:
    """Why Pro can't read the layer it just added, or None when it can. An unreadable layer is
    removed so the next way in can replace it."""
    try:
        if getattr(lyr, "isBroken", False):
            why = "the layer is broken"
        elif raster:
            bands = int(arcpy.management.GetRasterProperties(lyr, "BANDCOUNT").getOutput(0))
            why = None if bands > 0 else "no bands"
        else:
            int(arcpy.management.GetCount(lyr)[0])
            why = None
    except Exception as e:  # noqa: BLE001 - the reason goes back to the caller's messages
        why = str(e) or type(e).__name__
    if why:
        m.removeLayer(lyr)
    return why


def _ways_in(href: str, folder: str, raster: bool) -> tuple[list, list[str]]:
    """(label, path-maker) pairs for opening `href` through a cloud storage connection, plus the
    reasons any way was ruled out. The bucket itself with a Google sign-in comes first, then a
    connection to the href's own host or provider."""
    tries, reasons = [], []
    label, path = _google(folder, href)
    if path:
        tries.append((label, path))
    elif label != "GOOGLE":  # the UGS bucket, but no usable sign-in; elsewhere it doesn't apply
        reasons.append(label)
    parts = cat.href_parts(href)
    if parts:
        scheme, host, bucket, key = parts
        ways = [NATIVE[scheme]] if scheme in NATIVE else [
            c for c in CONNECTIONS if raster or c[0] != "WEB"]  # Esri: WEB opens rasters only
        for provider, options in ways:
            tries.append((provider, lambda p=provider, o=options:
                          os.path.join(_connection(folder, p, o, bucket, host), *key.split("/"))))
    if not tries and not reasons:
        reasons.append(f"no cloud storage connection reaches {href}")
    return tries, reasons


def _add_parquet(m, path: str):
    """A GeoParquet in cloud storage as a layer on `m`. Map.addDataFromPath fails on these
    ("Possible credentials issue") where MakeFeatureLayer opens the same path."""
    path = path.replace("\\", "/")
    made = arcpy.management.MakeFeatureLayer(path, f"ugs_{uuid.uuid4().hex[:8]}").getOutput(0)
    try:
        return m.addLayer(made)[0]
    finally:
        arcpy.management.Delete(made)  # the map holds its own copy


def _open_first(m, tries: list, reasons: list[str], cached: str, raster: bool, messages):
    """The first way in that gives a layer Pro can read (the last good way tried first), or None;
    every failure is added to `reasons`."""
    tries = sorted(tries, key=lambda t: t[0] != _cache.get(cached))
    for how, path in tries:
        try:
            lyr = m.addDataFromPath(path()) if raster else _add_parquet(m, path())
            if not (why := _unusable(m, lyr, raster=raster)):
                _cache[cached] = how
                messages.addMessage(f"  Opened online. ({how})")
                return lyr
            reasons.append(f"{how}: {why}")
        except Exception as e:  # noqa: BLE001 - try the next way in
            reasons.append(f"{how}: {e}")
    return None


def _open_raster(m, href: str, folder: str, messages):
    """A COG through a cloud storage connection, which is how Pro reads cloud rasters."""
    tries, reasons = _ways_in(href, folder, raster=True)
    lyr = _open_first(m, tries, reasons, "raster", True, messages)
    if lyr is None:
        raise RuntimeError(f"ArcGIS Pro couldn't open the map image. ({'; '.join(reasons)})")
    return lyr


def _stream(m, asset: dict, folder: str, messages):
    """Add the GeoParquet through a cloud storage connection, or None when it can't be opened."""
    if _pro_version() < (3, 5) or cat.asset_format(asset) != "parquet":  # Pro streams Parquet only
        return None
    tries, reasons = _ways_in(asset["href"], folder, raster=False)
    if not tries:
        messages.addMessage("  Can't open it online, so it was downloaded instead. "
                            f"({'; '.join(reasons)})")
        return None
    lyr = _open_first(m, tries, reasons, "stream", False, messages)
    if lyr is None:
        messages.addMessage(f"  Couldn't open it online, so it was downloaded instead. "
                            f"({'; '.join(reasons)})")
    return lyr


_GEOMETRY = {"Point": "POINT", "MultiPoint": "MULTIPOINT", "LineString": "POLYLINE",
             "MultiLineString": "POLYLINE", "Polygon": "POLYGON", "MultiPolygon": "POLYGON"}


def _to_fgdb(parquet: str, folder: str, name: str, messages) -> str:
    """A feature class in `<folder>/UGS Warehouse.gdb` with the GeoParquet's rows, in the CRS its
    metadata names, or a table for a Parquet with no geometry. Any Pro version.

    Field names go through the geodatabase's own validation; columns with no Esri field type
    (binary, nested) are left out and named in a warning."""
    import pyarrow.parquet as pq

    src = pq.ParquetFile(parquet)  # read a batch at a time: a layer can be several GB
    schema = src.schema_arrow
    geo = json.loads((schema.metadata or {}).get(b"geo", b"{}"))
    gcol = geo.get("primary_column", "geom") if geo else None  # no geo metadata: a plain table
    types = [t.replace(" Z", "") for t in geo.get("columns", {}).get(gcol, {}).get("geometry_types", [])]
    shape = "MULTIPOINT" if "MultiPoint" in types else _GEOMETRY.get(types[0] if types else "", "POLYGON")
    has_z = any(t.endswith(" Z") for t in geo.get("columns", {}).get(gcol, {}).get("geometry_types", []))

    gdb = _gdb(folder)
    fc = os.path.join(gdb, arcpy.ValidateTableName(name, gdb))  # an item id like M-180 isn't one
    if arcpy.Exists(fc):
        try:
            arcpy.management.Delete(fc)
        except Exception:  # noqa: BLE001 - in use on a map: write alongside it
            fc = arcpy.CreateUniqueName(os.path.basename(fc), gdb)
    sr = None
    if gcol:
        code, problem = cat.crs_code(geo, gcol)
        if problem:
            messages.addWarningMessage(f"  No coordinate system set: {problem}.")
        sr = arcpy.SpatialReference(code) if code else arcpy.SpatialReference()
        arcpy.management.CreateFeatureclass(gdb, os.path.basename(fc), shape, spatial_reference=sr,
                                            has_z="ENABLED" if has_z else "DISABLED")
    else:
        arcpy.management.CreateTable(gdb, os.path.basename(fc))
    fields, dropped, taken = [], [], set(cat._RESERVED)  # expects pro_ready's renamed columns
    longest = cat.text_lengths(parquet)
    for f in schema:
        if f.name == gcol:
            continue
        spec = cat.esri_field(f.type, longest.get(f.name, 1), _pro_version())
        if spec is None:
            dropped.append(f.name)
            continue
        valid = base = arcpy.ValidateFieldName(f.name, gdb)
        n = 1
        while valid.lower() in taken:  # validation can map two names to one
            n += 1
            valid = f"{base[:64 - len(str(n)) - 1]}_{n}"
        taken.add(valid.lower())
        fields.append((f.name, valid, spec))
    if dropped:
        messages.addWarningMessage(f"  Left out columns ArcGIS can't store: {', '.join(dropped)}")
    if fields:  # the alias keeps the column's name when the geodatabase's validation changed it
        arcpy.management.AddFields(fc, [[v, s[0], n, s[1]] for n, v, s in fields])
    names = [n for n, _, _ in fields]
    out = [v for _, v, _ in fields]
    with arcpy.da.InsertCursor(fc, ["SHAPE@", *out] if gcol else out) as cur:
        for batch in src.iter_batches(batch_size=20_000, columns=[gcol, *names] if gcol else names):
            cols = [batch.column(n).to_pylist() for n in names]
            if not gcol:
                for row in zip(*cols):
                    cur.insertRow([cat.esri_value(v) for v in row])
                continue
            for wkb, *row in zip(batch.column(gcol).to_pylist(), *cols):
                cur.insertRow([arcpy.FromWKB(bytearray(wkb), sr) if wkb else None,
                               *(cat.esri_value(v) for v in row)])
    return fc


def _copy(m, asset: dict, folder: str, name: str, messages):
    fmt = cat.asset_format(asset)
    if fmt is None:
        raise ValueError(f"the toolbox can't add {asset.get('type') or asset.get('href')}; "
                         "it adds GeoParquet and GeoJSON")
    size = asset.get("file:size")
    messages.addMessage(f"  Downloading ({size / 1e6:.1f} MB)..." if size else "  Downloading...")
    path = cat.download(asset, folder, f"{cat.safe_filename(name)}.{fmt}")
    if fmt == "geojson":
        return m.addDataFromPath(_geojson_to_fgdb(path, folder, name, messages))
    if nested := cat.nested_columns(path):
        messages.addWarningMessage(f"  Left out nested columns ArcGIS can't store: {', '.join(nested)}")
    return m.addDataFromPath(_to_fgdb(cat.pro_ready(path), folder, name, messages))


_ESRI_SHAPE = {"Point": "POINT", "MultiPoint": "MULTIPOINT", "LineString": "POLYLINE",
               "MultiLineString": "POLYLINE", "Polygon": "POLYGON", "MultiPolygon": "POLYGON"}


def _gdb(folder: str) -> str:
    gdb = os.path.join(folder, "UGS Warehouse.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(folder, "UGS Warehouse.gdb")
    return gdb


def _geojson_to_fgdb(path: str, folder: str, name: str, messages) -> str:
    """A feature class in `<folder>/UGS Warehouse.gdb` from a GeoJSON file, by Esri's own tool.

    JSONToFeatures writes one geometry type per feature class, so a mixed file takes its most
    common type and the warning counts the features left out."""
    counts, shapes = cat.geojson_types(path), {}
    for kind, n in counts.items():
        if kind in _ESRI_SHAPE:
            shapes[_ESRI_SHAPE[kind]] = shapes.get(_ESRI_SHAPE[kind], 0) + n
    if not shapes:
        raise ValueError("the GeoJSON has no point, line or polygon features")
    shape = max(shapes, key=shapes.get)
    if left := sum(counts.values()) - shapes[shape]:  # other types, null and mixed geometries
        messages.addWarningMessage(f"  Left out {left} features that aren't {shape.lower()}s; "
                                   "a geodatabase layer holds one geometry type.")
    gdb = _gdb(folder)
    fc = arcpy.CreateUniqueName(arcpy.ValidateTableName(name, gdb), gdb)
    arcpy.conversion.JSONToFeatures(path, fc, shape)
    return fc


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


def _collection(url: str, messages) -> dict | None:
    """A theme's collection.json (its license and providers), read once per session."""
    seen = _cache.setdefault("collections", {})
    if url not in seen:
        try:
            seen[url] = cat.get_json(url)
        except (OSError, ValueError) as e:  # not kept: the next layer tries again
            messages.addWarningMessage(f"  Couldn't read the theme's license and credits. ({e})")
            return None
    return seen[url]


def _add_layer(m, layer: cat.Layer, source: str, work: str, style: bool, messages) -> None:
    """Put one picked layer on `m`, with its metadata and, for a vector, the UGS colors."""
    if layer.is_raster:  # a COG, read range by range from the CDN
        lyr = _open_raster(m, layer.href, work, messages)
        props = dict(layer.properties)
        if layer.self_href:
            try:
                props = cat.get_json(layer.self_href).get("properties") or props
            except (OSError, ValueError) as e:
                messages.addWarningMessage(f"  Couldn't read the map's full description. ({e})")
        fields = cat.metadata(props, source=layer.self_href or layer.href)  # like a vector: its item
    else:
        item = cat.get_json(layer.item_url)
        asset = item["assets"]["data"]
        lyr = _stream(m, asset, work, messages) if source == STREAM else None
        if lyr is None:
            lyr = _copy(m, asset, work, layer.id, messages)
        collection = _collection(layer.collection_url, messages)
        fields = cat.metadata(item.get("properties") or {}, collection, layer.item_url)
    try:
        lyr.name = layer.title
        _apply_metadata(lyr, fields, messages)
    except Exception as e:  # noqa: BLE001 - the layer is on the map; metadata is extra
        messages.addWarningMessage(f"  Couldn't add the layer's description. ({e})")
    if style and not layer.is_raster:
        try:
            _apply_style(lyr, item, messages)
        except Exception as e:  # noqa: BLE001 - the layer is on the map; styling is extra
            messages.addWarningMessage(f"  Couldn't apply UGS colors. ({e})")


class Toolbox:
    def __init__(self):
        self.label = "UGS Warehouse"
        self.alias = "ugswarehouse"
        self.tools = [AddLayer, SignIn, UpdateToolbox]


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
        # Every time, not only when altered: a cleared Search box reads as unaltered, and the list
        # must widen again. Pro calls this when a box loses focus (Enter or Tab), not per keystroke.
        theme, search, picks = parameters[:3]
        want, query = theme.valueAsText, search.valueAsText
        chosen = set(picks.values or [])  # keep what is already picked while the list narrows
        picks.filter.list = [lyr.choice for lyr in _layers()
                             if (want in (None, ALL, cat.theme_name(lyr.theme)) and lyr.matches(query))
                             or lyr.choice in chosen]

    def updateMessages(self, parameters):
        if notice := _update_notice():
            parameters[0].setWarningMessage(notice)
        search, picks = parameters[1], parameters[2]
        if search.valueAsText and not picks.filter.list and "error" not in _cache:
            search.setWarningMessage(f"No layers match '{search.valueAsText}'.")
        if "error" in _cache:
            parameters[2].setErrorMessage(_cache["error"])
        elif "warning" in _cache:  # some layers listed; the rest could not be read
            parameters[2].setWarningMessage(_cache["warning"])
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
        failed = []
        for choice in picks.values:
            messages.addMessage(choice)
            try:
                layer = by_id.get(cat.id_of(choice))
                if layer is None:
                    raise LookupError("it is no longer in the UGS catalog")
                _add_layer(m, layer, source.valueAsText, work, style.value, messages)
            except Exception as e:  # noqa: BLE001 - finish the other picks, then report this one
                messages.addWarningMessage(f"  Couldn't add this layer. ({e})")
                failed.append(choice)
        if failed:
            raise arcpy.ExecuteError(f"Couldn't add {len(failed)} of {len(picks.values)} layers: "
                                     f"{'; '.join(failed)}. The messages above say why.")


class SignIn:
    def __init__(self):
        self.label = "Sign In to UGS Storage"
        self.description = ("Sign in with your Google account once, so Add Warehouse Layer can open "
                            "layers online. Your account needs read access to UGS storage.")

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        import shutil
        import subprocess

        gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
        if not gcloud:
            raise arcpy.ExecuteError(
                "Install the Google Cloud CLI first (https://cloud.google.com/sdk/docs/install), "
                "then run this again.")
        messages.addMessage("A browser window will open; sign in with your utah.gov account.")
        # gcloud runs its own Python, which fails to load if it inherits Pro's.
        pro = [os.path.normcase(os.path.abspath(p)) for p in
               (sys.prefix, sys.exec_prefix, arcpy.GetInstallInfo().get("InstallDir") or sys.prefix)]
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "CONDA"))}
        env["PATH"] = os.pathsep.join(
            p for p in os.environ.get("PATH", "").split(os.pathsep)
            if p and not any((os.path.normcase(os.path.abspath(p)) + os.sep).startswith(r + os.sep)
                             for r in pro))
        done = subprocess.run([gcloud, "auth", "application-default", "login"],
                              capture_output=True, text=True, env=env)
        if done.returncode != 0 or cat.google_credentials() is None:
            raise arcpy.ExecuteError(f"Sign-in didn't finish. ({(done.stderr or '').strip()[-300:]})")
        messages.addMessage("Signed in. Add Warehouse Layer will now open layers online.")


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

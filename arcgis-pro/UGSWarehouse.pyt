"""UGS Warehouse toolbox for ArcGIS Pro: add warehouse vector layers, styled like the web viewer.

Keep this file and `ugs_catalog.py` in the same folder. See README.md.
"""
from __future__ import annotations

import os
import sys

import arcpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ugs_catalog as cat  # noqa: E402

DOWNLOAD = "GeoParquet (download)"
LIVE = "Live (OGC API Features)"
ALL = "All themes"
_cache: dict[str, list[cat.Layer]] = {}


def _layers() -> list[cat.Layer]:
    if "layers" not in _cache:
        _cache["layers"] = cat.layers()
    return _cache["layers"]


def _pro_version() -> tuple[int, ...]:
    v = arcpy.GetInstallInfo().get("Version", "0")
    return tuple(int(p) for p in v.split(".")[:2] if p.isdigit())


def _apply_style(lyr, item: dict, messages) -> None:
    """A unique-values renderer from the layer's GL style: same field, colors and legend labels."""
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
            messages.addMessage(f"{lyr.name}: style has no simple equivalent; kept the default symbol")
        return
    field, classes = found
    names = {f.name.lower(): f.name for f in arcpy.ListFields(lyr)}
    if field.lower() not in names or not hasattr(lyr.symbology, "renderer"):
        messages.addWarningMessage(f"{lyr.name}: no field '{field}' to style by")
        return
    lookup = {str(v).lower(): (label, rgb) for v, label, rgb in classes}
    sym = lyr.symbology
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [names[field.lower()]]
    for group in sym.renderer.groups:
        for entry in group.items:
            match = lookup.get(str(entry.values[0][0]).lower())
            if match:
                entry.label = match[0]
                entry.symbol.color = {"RGB": [*match[1], 100]}
    lyr.symbology = sym


class Toolbox:
    def __init__(self):
        self.label = "UGS Warehouse"
        self.alias = "ugswarehouse"
        self.tools = [AddLayer]


class AddLayer:
    def __init__(self):
        self.label = "Add Warehouse Layer"
        self.description = ("Add UGS warehouse vector layers to the active map, styled with the "
                            "web viewer's colors and legend.")

    def getParameterInfo(self):
        theme = arcpy.Parameter(displayName="Theme", name="theme", datatype="GPString",
                                parameterType="Optional", direction="Input")
        theme.filter.type = "ValueList"
        theme.filter.list = [ALL, *sorted({lyr.theme for lyr in _layers()})]
        theme.value = ALL

        picks = arcpy.Parameter(displayName="Layers", name="layers", datatype="GPString",
                                parameterType="Required", direction="Input", multiValue=True)
        picks.filter.type = "ValueList"
        picks.filter.list = [lyr.choice for lyr in _layers()]

        source = arcpy.Parameter(displayName="Source", name="source", datatype="GPString",
                                 parameterType="Required", direction="Input")
        source.filter.type = "ValueList"
        source.filter.list = [DOWNLOAD, LIVE]
        source.value = DOWNLOAD

        folder = arcpy.Parameter(displayName="Download folder", name="folder", datatype="DEFolder",
                                 parameterType="Optional", direction="Input")
        folder.value = os.path.join(os.path.expanduser("~"), "Documents", "UGS Warehouse")

        style = arcpy.Parameter(displayName="Style like the web viewer", name="style",
                                datatype="GPBoolean", parameterType="Optional", direction="Input")
        style.value = True
        return [theme, picks, source, folder, style]

    def updateParameters(self, parameters):
        theme, picks, source, folder = parameters[:4]
        if theme.altered:
            want = theme.valueAsText
            picks.filter.list = [lyr.choice for lyr in _layers() if want in (None, ALL, lyr.theme)]
        folder.enabled = source.valueAsText == DOWNLOAD

    def updateMessages(self, parameters):
        source = parameters[2]
        if source.valueAsText == DOWNLOAD and _pro_version() < (3, 5):
            source.setErrorMessage("GeoParquet needs ArcGIS Pro 3.5 or later; use the live source.")

    def execute(self, parameters, messages):
        picks, source, folder, style = parameters[1:5]
        m = arcpy.mp.ArcGISProject("CURRENT").activeMap
        if m is None:
            raise arcpy.ExecuteError("Open a map first.")
        by_id = {lyr.id: lyr for lyr in _layers()}
        for choice in picks.values:
            layer = by_id[cat.id_of(choice)]
            item = cat.get_json(layer.item_url)
            if source.valueAsText == LIVE:
                lyr = m.addDataFromPath(layer.features_url)
            else:
                asset = item["assets"]["data"]
                mb = (asset.get("file:size") or 0) / 1e6
                messages.addMessage(f"Downloading {layer.id} ({mb:.1f} MB)")
                lyr = m.addDataFromPath(cat.download(asset, folder.valueAsText, f"{layer.id}.parquet"))
            lyr.name = layer.title
            if style.value:
                try:
                    _apply_style(lyr, item, messages)
                except Exception as e:  # noqa: BLE001 — the layer is on the map; styling is extra
                    messages.addWarningMessage(f"{layer.title}: not styled ({e})")
            messages.addMessage(f"Added {layer.title}")

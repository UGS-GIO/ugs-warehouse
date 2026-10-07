"""Read the UGS warehouse STAC catalog for the ArcGIS Pro toolbox.

Standard library only, so it runs in Pro's Python with nothing installed. No arcpy here: the
toolbox (`UGSWarehouse.pyt`) does the map work, and this module stays testable off Windows.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass

STAC = "https://maps-assets.geology.utah.gov/warehouse/stac"
TOPICS = f"{STAC}/ugs-serving-topics"
FEATURES = "https://ugs-warehouse-features-xedvkyurga-uc.a.run.app"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Layer:
    id: str
    title: str
    theme: str  # the dbt schema, which is the serving-topics collection

    @property
    def item_url(self) -> str:
        return f"{TOPICS}/{self.theme}/{self.id}/{self.id}.json"

    @property
    def features_url(self) -> str:
        return f"{FEATURES}/collections/{self.id}"

    @property
    def choice(self) -> str:
        """How the layer reads in the tool's pick list; `id_of` reverses it."""
        return f"{self.title} [{self.id}]"


def id_of(choice: str) -> str:
    return choice.rsplit("[", 1)[-1].rstrip("]")


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:  # urllib asks for no gzip, so bytes are plain
        return json.load(r)


def layers(index: dict | None = None) -> list[Layer]:
    """Every serving-topic layer, sorted by theme then title."""
    index = index if index is not None else get_json(f"{TOPICS}/items.json")
    out = [Layer(i["id"], (i.get("properties") or {}).get("title") or i["id"],
                 (i.get("properties") or {}).get("ugs:dbt_schema") or "")
           for i in index.get("items") or []]
    return sorted(out, key=lambda x: (x.theme, x.title.lower()))


def _rgb(color: str) -> tuple[int, int, int] | None:
    c = color.strip().lstrip("#") if isinstance(color, str) else ""
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) if len(c) == 6 else None
    except ValueError:
        return None


def _field(expr) -> str | None:
    """The field a GL value expression reads, through `downcase`, `to-string` and a `coalesce`
    default: `["downcase", ["coalesce", ["get", "f"], ""]]` -> "f"."""
    while isinstance(expr, list) and expr:
        if expr[0] == "get" and len(expr) == 2 and isinstance(expr[1], str):
            return expr[1]
        if expr[0] in ("downcase", "to-string", "to-number", "coalesce") and len(expr) > 1:
            expr = expr[1]
            continue
        return None
    return None


def _field_value(expr) -> tuple[str, object] | None:
    """(field, value) from a GL filter that keeps one value of one field, else None.

    Matches `["==", <get>, v]`, with the `get` optionally wrapped (`downcase`, `to-string`), and
    `["all", ["has", f], ["==", ...]]`.
    """
    if not isinstance(expr, list) or not expr:
        return None
    if expr[0] == "all":
        found = [fv for e in expr[1:] if (fv := _field_value(e))]
        return found[0] if len(found) == 1 else None
    if expr[0] != "==" or len(expr) != 3:
        return None
    field = _field(expr[1])
    return (field, expr[2]) if field else None


def _paint_color(layer: dict):
    paint = layer.get("paint") or {}
    for key in ("fill-color", "line-color", "circle-color"):
        if key in paint:
            return paint[key]
    return None


def classes(style: dict, legend: list[dict] | None = None):
    """(field, [(value, label, rgb)]) for a categorical GL style, or None for anything else.

    Two shapes cover the warehouse styles: one layer per value (a filter per layer), or one layer
    whose color is a `match` on a field. Labels come from the item's legend when it lines up.
    """
    gl = [lyr for lyr in style.get("layers") or [] if lyr.get("type") in ("fill", "line", "circle")]
    labels = [e.get("label") for e in legend or []]
    out: list[tuple[object, str, tuple[int, int, int]]] = []
    field = None
    per_layer = [(_field_value(lyr.get("filter")), _rgb(_paint_color(lyr) or "")) for lyr in gl]
    if per_layer and all(fv and rgb for fv, rgb in per_layer):
        fields = {fv[0] for fv, _ in per_layer}
        if len(fields) == 1:
            field = fields.pop()
            seen = set()
            for i, (fv, rgb) in enumerate(per_layer):
                if fv[1] in seen:  # a second layer for the same value (a casing, a halo)
                    continue
                seen.add(fv[1])
                label = labels[i] if len(labels) == len(per_layer) and labels[i] else str(fv[1])
                out.append((fv[1], label, rgb))
            return field, out
    for lyr in gl:
        found = _expression_classes(_paint_color(lyr))
        if found:
            field, pairs = found
            by_color: dict[tuple, list[str]] = {}
            for e in legend or []:
                if (rgb := _rgb(e.get("color") or "")) and e.get("label"):
                    by_color.setdefault(rgb, []).append(e["label"])
            used = [rgb for _, rgb in pairs]
            for v, rgb in pairs:  # a legend label pairs with a class only when its color is unique
                label = by_color.get(rgb, [None])[0] if len(by_color.get(rgb, [])) == 1 \
                    and used.count(rgb) == 1 else None
                out.append((v, label or str(v), rgb))
            return field, out
    return None


def _expression_classes(color) -> tuple[str, list[tuple[object, tuple]]] | None:
    """(field, [(value, rgb)]) from a `match` or a `coalesce`/`get`/`literal` lookup color."""
    if not isinstance(color, list) or not color:
        return None
    if color[0] == "match" and len(color) >= 5 and (field := _field(color[1])):
        pairs = []
        for i in range(2, len(color) - 2, 2):
            values = color[i] if isinstance(color[i], list) else [color[i]]
            if rgb := _rgb(color[i + 1]) if isinstance(color[i + 1], str) else None:
                pairs.extend((v, rgb) for v in values)
        return (field, pairs) if pairs else None
    # ["coalesce", ["get", <key>, ["literal", {value: color}]], default]
    if color[0] == "coalesce" and len(color) >= 2 and isinstance(color[1], list) \
            and color[1][:1] == ["get"] and len(color[1]) == 3:
        lookup = color[1][2]
        table = lookup[1] if isinstance(lookup, list) and lookup[:1] == ["literal"] else None
        if (field := _field(color[1][1])) and isinstance(table, dict):
            pairs = [(v, rgb) for v, c in table.items() if (rgb := _rgb(c) if isinstance(c, str) else None)]
            return (field, pairs) if pairs else None
    return None


def single_color(style: dict) -> tuple[int, int, int] | None:
    """The color of a one-color style (its first fill, line or circle layer), else None."""
    for lyr in style.get("layers") or []:
        if lyr.get("type") in ("fill", "line", "circle"):
            color = _paint_color(lyr)
            return _rgb(color) if isinstance(color, str) else None
    return None


def verify(path: str, size: int | None, checksum: str | None) -> bool:
    """True when the file matches the asset's `file:size` and sha256 `file:checksum`."""
    if size is not None and os.path.getsize(path) != size:
        return False
    if not checksum:
        return True
    if not checksum.startswith("1220"):  # not sha2-256: nothing to compare against
        return True
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest() == checksum[4:]


def download(asset: dict, folder: str, name: str) -> str:
    """Fetch an asset into `folder` (reused when an earlier copy still matches). Returns the path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    size, checksum = asset.get("file:size"), asset.get("file:checksum")
    if os.path.exists(path) and verify(path, size, checksum):
        return path
    tmp = path + ".part"
    with urllib.request.urlopen(asset["href"], timeout=300) as r, open(tmp, "wb") as fh:
        while chunk := r.read(_CHUNK):
            fh.write(chunk)
    if not verify(tmp, size, checksum):
        os.remove(tmp)
        raise OSError(f"{name}: download does not match the catalog's size or checksum")
    os.replace(tmp, path)
    return path

"""MapLibre style files for a publication's map layers, built from the map's own colours.

A GeMS map's unit polygons join its DescriptionOfMapUnits table (DMU), whose `AreaFillRGB` holds each
unit's colour as an "R;G;B" string. The style is a `match` on the polygons' unit-code column. A polygon
layer that carries a colour column itself (`AreaFillRGB`, `RGB`, `COLOR`) is coloured from that
column's values. Every other layer gets the viewer's default look (`defaultVectorLayers`, neutral
colour). The style holds only `layers`, like the ugs-styles files: the viewer supplies the PMTiles
source and the `source-layer`.

Pure: the caller reads columns from the GeoParquet through `read`, and writes the file. The style is
published beside the layer's PMTiles and bound to the STAC Item by the restyle job, never written into it.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# Bump when the generator changes what it writes; a manifest below it is styled again by the heal.
STYLE_VERSION = 1

# The viewer's neutral layer colour (NEUTRAL_VECTOR_COLOR in map-model.ts) and the fill for a unit
# code the DMU does not list.
NEUTRAL = "#374151"
FALLBACK_FILL = "#9ca3af"

# Lower-cased names of a column holding a colour, in the order they are tried.
COLOR_COLUMNS = ("areafillrgb", "rgb", "color", "colour")

# A colour column with more distinct values than this is not a palette; the layer gets the default.
MAX_COLORS = 2000

# Same geometry filters as the viewer's GEOM_FILTER, so a default style draws exactly as no style does.
_FILL = ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], True, False]
_LINE = ["match", ["geometry-type"], ["LineString", "MultiLineString", "Polygon", "MultiPolygon"], True, False]
_POINT = ["match", ["geometry-type"], ["Point", "MultiPoint"], True, False]

_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_SEPARATOR = re.compile(r"[;,\s]+")

Read = Callable[[str, list[str]], object]  # (label, columns) -> pyarrow Table of those columns


def parse_color(raw: object) -> str | None:
    """`#rrggbb` for a GeMS "R;G;B" string (spaces or commas also split it, 0-255 each) or a hex
    colour; None for a blank or malformed value."""
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return None
    if _HEX.fullmatch(text):
        return text.lower()
    parts = [p for p in _SEPARATOR.split(text) if p]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    r, g, b = (int(p) for p in parts)
    if max(r, g, b) > 255:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


def default_layers(label: str, color: str = NEUTRAL) -> list[dict]:
    """The viewer's unstyled look: a translucent fill with an outline for polygons, a line, a circle."""
    return [
        {"id": f"{label}-fill", "type": "fill", "filter": _FILL,
         "paint": {"fill-color": color, "fill-opacity": 0.15, "fill-outline-color": color}},
        {"id": f"{label}-line", "type": "line", "filter": _LINE,
         "paint": {"line-color": color, "line-width": 1.2}},
        {"id": f"{label}-circle", "type": "circle", "filter": _POINT,
         "paint": {"circle-color": color, "circle-radius": 3.5, "circle-opacity": 0.85,
                   "circle-stroke-color": "#ffffff", "circle-stroke-width": 0.5}},
    ]


def match_layers(label: str, key: str, colors: dict[str, str]) -> list[dict]:
    """Polygons filled by a `match` on the `key` column (a code not in `colors` gets FALLBACK_FILL),
    with a thin outline. The input is cast to a string so a numeric code matches its text label."""
    expr: list = ["match", ["to-string", ["get", key]]]
    for code in sorted(colors):
        expr += [code, colors[code]]
    expr.append(FALLBACK_FILL)
    return [
        {"id": f"{label}-fill", "type": "fill", "filter": _FILL,
         "paint": {"fill-color": expr, "fill-opacity": 0.7}},
        {"id": f"{label}-line", "type": "line", "filter": _FILL,
         "paint": {"line-color": NEUTRAL, "line-width": 0.4, "line-opacity": 0.6}},
    ]


@dataclass
class Styled:
    """A layer's style document and how it was made. `kind` is `units` (colours from the joined DMU),
    `column` (from the layer's own colour column) or `default`; `fallback_rows` counts the polygons of a
    `units` style whose code is not in the match (they draw in FALLBACK_FILL); `warnings` are problems worth a manifest entry."""
    doc: dict
    kind: str
    fallback_rows: int | None = None
    warnings: list[str] = field(default_factory=list)


def _is_polygon(entry: dict) -> bool:
    return any("Polygon" in str(t) for t in entry.get("geometry_types") or [])


def _column(entry: dict, names: tuple[str, ...], types: tuple[str, ...] = ("string",)) -> str | None:
    """The first of `names` (matched case-insensitively) among a layer's columns of one of `types`."""
    by_lower = {c["name"].lower(): c["name"] for c in entry.get("columns") or []
                if c.get("type") in types}
    return next((by_lower[n] for n in names if n in by_lower), None)


def _dmu_join(label: str, entries: dict[str, dict], relationships: list[dict]
              ) -> tuple[str, str, str, str] | None:
    """`(unit-code column, DMU label, DMU key column, colour column)` for the table that joins the
    layer on one column and holds a colour column, preferring declared then more confident joins."""
    ranked = sorted(relationships, key=lambda r: (r.get("detected_by") != "declared",
                                                  -(r.get("confidence") or 0)))
    for rel in ranked:
        if label == rel.get("origin"):
            own, other, theirs = rel.get("origin_cols"), rel.get("destination"), rel.get("destination_cols")
        elif label == rel.get("destination"):
            own, other, theirs = rel.get("destination_cols"), rel.get("origin"), rel.get("origin_cols")
        else:
            continue
        table = entries.get(other or "")
        if not table or table.get("spatial") or not (own and theirs and len(own) == len(theirs) == 1):
            continue
        # An all-null column is recorded as type `null`: the join is still found and yields no colours.
        color = _column(table, COLOR_COLUMNS[:1], ("string", "null"))
        if color:
            return own[0], other, theirs[0], color
    return None


def _distinct(values: list, limit: int = MAX_COLORS + 1) -> list:
    seen: dict = {}
    for v in values:
        if v is not None and str(v).strip() and v not in seen:
            seen[v] = None
            if len(seen) >= limit:
                break
    return list(seen)


def _palette(codes: list, raw_colors: list) -> tuple[dict[str, str], list[str]]:
    """{code: #rrggbb} from parallel code and colour values, and the problems met. A row with no
    code (a DMU heading) or a blank colour is skipped; a malformed colour, or a code listed with two
    different colours (the first stands), is a problem."""
    colors: dict[str, str] = {}
    bad: list[str] = []
    clash: list[str] = []
    for code, raw in zip(codes, raw_colors, strict=True):
        if code is None or not str(code).strip() or raw is None or not str(raw).strip():
            continue
        color = parse_color(raw)
        if color is None:
            bad.append(f"{code}={raw!r}")
        elif colors.setdefault(str(code), color) != color:
            clash.append(str(code))
    problems = []
    if bad:
        problems.append(f"{len(bad)} unit colours not read as R;G;B (e.g. {', '.join(bad[:3])})")
    if clash:
        problems.append(f"units listed with two colours, the first used: {', '.join(clash[:5])}")
    return colors, problems


def build(entry: dict, entries: dict[str, dict], relationships: list[dict], read: Read) -> Styled:
    """The style for the spatial layer `entry` (a manifest layer). `entries` maps label to every
    manifest layer, `relationships` is the manifest's, and `read(label, columns)` returns those
    columns of a layer's GeoParquet as a pyarrow Table."""
    label = entry["label"]
    if not _is_polygon(entry):
        return Styled({"layers": default_layers(label)}, "default")
    warnings: list[str] = []

    join = _dmu_join(label, entries, relationships)
    if join:
        own, dmu, dmu_key, color_col = join
        table = read(dmu, [dmu_key, color_col])
        colors, problems = _palette(table.column(dmu_key).to_pylist(), table.column(color_col).to_pylist())
        warnings += problems
        if colors:
            polys = [str(v) for v in read(label, [own]).column(own).to_pylist()]
            fallback = sum(1 for v in polys if v not in colors)
            return Styled({"layers": match_layers(label, own, colors)}, "units", fallback, warnings)
        # AreaFillRGB is optional and often left empty; only a populated column that reads as no
        # colour is a problem worth a warning.
        if any(v is not None and str(v).strip() for v in table.column(color_col).to_pylist()):
            warnings.append(f"no usable colours in {dmu}.{color_col}; the layer gets its own "
                            "colours or the default")

    own_col = _column(entry, COLOR_COLUMNS)
    if own_col:
        values = _distinct(read(label, [own_col]).column(own_col).to_pylist())
        if len(values) <= MAX_COLORS:
            colors, problems = _palette(values, values)
            if colors:  # a column of prose (a "Color" of rock descriptions) is not a palette, and silent
                warnings += problems
                return Styled({"layers": match_layers(label, own_col, colors)}, "column", None, warnings)
        else:
            warnings.append(f"{own_col} has more than {MAX_COLORS} distinct values; the default style is used")
    return Styled({"layers": default_layers(label)}, "default", None, warnings)

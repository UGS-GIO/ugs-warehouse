"""MapLibre style files for a publication's map layers.

Every layer gets the viewer's default look (`defaultVectorLayers` in map-model.ts): one layer per
geometry type, in the viewer's neutral colour. The style holds only `layers`, like the ugs-styles files:
the viewer supplies the PMTiles source and the `source-layer`. The file is published beside the layer's
PMTiles and bound to the STAC Item by the restyle job, never written into it.
"""
from __future__ import annotations

# Bump when the generator changes what it writes; a manifest below it is styled again by the heal.
STYLE_VERSION = 1

# The viewer's neutral layer colour (NEUTRAL_VECTOR_COLOR in map-model.ts).
NEUTRAL = "#374151"

# Same geometry filters as the viewer's GEOM_FILTER, so the style draws exactly as no style does.
_FILL = ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], True, False]
_LINE = ["match", ["geometry-type"], ["LineString", "MultiLineString", "Polygon", "MultiPolygon"], True, False]
_POINT = ["match", ["geometry-type"], ["Point", "MultiPoint"], True, False]


def default_style(label: str, color: str = NEUTRAL) -> dict:
    """The viewer's unstyled look: a translucent fill with an outline for polygons, a line, a circle."""
    return {"layers": [
        {"id": f"{label}-fill", "type": "fill", "filter": _FILL,
         "paint": {"fill-color": color, "fill-opacity": 0.15, "fill-outline-color": color}},
        {"id": f"{label}-line", "type": "line", "filter": _LINE,
         "paint": {"line-color": color, "line-width": 1.2}},
        {"id": f"{label}-circle", "type": "circle", "filter": _POINT,
         "paint": {"circle-color": color, "circle-radius": 3.5, "circle-opacity": 0.85,
                   "circle-stroke-color": "#ffffff", "circle-stroke-width": 0.5}},
    ]}

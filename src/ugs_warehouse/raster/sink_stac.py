"""STAC item for a raster COG — built through the shared `core.stac` so rasters land in
the same collections-layout catalog as vector topics + publications.

The COG asset gets the standard `data` role + a web-map-links `cog` link so STAC Browser
(and a future titiler) can render it; an optional thumbnail asset is added when present.
"""
from __future__ import annotations

from ..core import config, stac
from .identity import Raster

COG_MIME = "image/tiff; application=geotiff; profile=cloud-optimized"


def build_item(raster: Raster, *, bbox: list[float], geometry: dict | None,
               properties: dict | None = None, has_thumbnail: bool = False,
               proj_epsg: int | None = None) -> dict:
    cog_url = config.public_url(raster.cog_object_path)
    assets = {
        "cog": {"href": cog_url, "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF",
                "roles": ["data", "visual"]},
    }
    if has_thumbnail:
        assets["thumbnail"] = {"href": config.public_url(raster.thumb_object_path),
                               "type": "image/png", "roles": ["thumbnail"]}

    return stac.build_item(
        item_id=raster.item_id,
        collection=raster.collection,
        geometry=geometry if geometry else (stac.bbox_polygon(bbox) if bbox else None),
        bbox=bbox,
        datetime_iso=raster.datetime_iso,
        properties=properties or {},
        assets=assets,
        extra_links=[stac.cog_link(cog_url)],
        stac_extensions=[stac.WEB_MAP_LINKS_EXT],
        proj_epsg=proj_epsg,
    )


def write(raster: Raster, *, bbox: list[float], geometry: dict | None = None,
          properties: dict | None = None, has_thumbnail: bool = False,
          proj_epsg: int | None = None) -> str:
    """Build + upload the item JSON. Caller runs `core.stac.refresh_catalog()` after."""
    return stac.write_item(build_item(
        raster, bbox=bbox, geometry=geometry, properties=properties,
        has_thumbnail=has_thumbnail, proj_epsg=proj_epsg,
    ))

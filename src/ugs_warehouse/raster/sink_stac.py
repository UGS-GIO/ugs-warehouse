"""STAC item for a raster COG — built through the shared `core.stac` so rasters land in
the same collections-layout catalog as vector topics + publications.

The COG asset gets the standard `data`+`visual` roles (media type `…;profile=cloud-optimized`),
which STAC Browser and our viewer render natively — no web-map-links `cog` link, because that
extension defines no `cog` rel (its rels are xyz/wms/wmts/tilejson/pmtiles/3d-tiles) and declaring
it would force one of those. An optional thumbnail asset is added when present.
"""
from __future__ import annotations

from ..core import config, gcs, stac
from .identity import Raster

COG_MIME = config.COG_MIME


def build_item(raster: Raster, *, bbox: list[float], geometry: dict | None,
               properties: dict | None = None, has_thumbnail: bool = False,
               proj_epsg: int | None = None, has_webmercator: bool = False,
               file_meta: dict[str, gcs.FileMeta] | None = None) -> dict:
    """`file_meta` is what the promote's copies reported, keyed by asset. Both arrive through a
    server-side rewrite, so they carry a size and no checksum — see core.gcs.copy_from_uri."""
    meta = file_meta or {}
    cog_url = config.public_url(raster.cog_object_path)
    # `visual` belongs to the copy a web map can draw. The canonical COG is in the source CRS
    # (EPSG:26912 for the 24k series), and a client that takes `visual` at its word tries to paint
    # UTM over web mercator and fails (#84) — so the native COG claims `data` alone, and the role
    # moves to the reprojected derivative when the promote found one.
    assets = {
        "cog": {"href": cog_url, "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF",
                "roles": ["data"], **stac.file_fields(meta.get("cog"))},
    }
    if has_webmercator:
        assets["visual"] = {"href": config.public_url(raster.webmercator_cog_object_path),
                            "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF (Web Mercator)",
                            "roles": ["visual"], "proj:code": "EPSG:3857",
                            **stac.file_fields(meta.get("visual"))}
    if has_thumbnail:
        assets["thumbnail"] = {"href": config.public_url(raster.thumb_object_path),
                               "type": "image/png", "roles": ["thumbnail"],
                               **stac.file_fields(meta.get("thumbnail"))}

    return stac.build_item(
        item_id=raster.item_id,
        collection=raster.collection_id,          # STAC id = layer (last path segment)
        collection_path=raster.collection,         # nested layout path, e.g. ugs-rasters/slope
        geometry=geometry if geometry else (stac.bbox_polygon(bbox) if bbox else None),
        bbox=bbox,
        datetime_iso=raster.datetime_iso,
        properties=properties or {},
        assets=assets,
        proj_epsg=proj_epsg,
    )


def write(raster: Raster, *, bbox: list[float], geometry: dict | None = None,
          properties: dict | None = None, has_thumbnail: bool = False,
          proj_epsg: int | None = None, has_webmercator: bool = False,
          file_meta: dict[str, gcs.FileMeta] | None = None) -> str:
    """Build + upload the item JSON. Caller runs `core.stac.refresh_catalog()` after."""
    item = build_item(
        raster, bbox=bbox, geometry=geometry, properties=properties,
        has_thumbnail=has_thumbnail, proj_epsg=proj_epsg,
        has_webmercator=has_webmercator, file_meta=file_meta,
    )
    stac.attach_renders(item)  # ugs-styles colormap/rescale -> render extension (graceful if none)
    return stac.write_item(item)

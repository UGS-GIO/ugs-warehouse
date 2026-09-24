"""WebP previews for publications (#372): a harvested map's catalog thumbnail, the sheet the 3D viewer
drapes over terrain, and any pub's cover. Encoded with GDAL, which reads a COG through its overviews
and can scale 16-bit bands down to 8 bits.
"""
from __future__ import annotations

import json
import subprocess

from . import identity

# Catalog thumbnails fit in this on their long side: they load on every catalog page, including on a
# weak connection in the field.
CATALOG_PX = 512
# The 3D viewer drapes the sheet over terrain, so it keeps more detail than a card thumbnail.
SHEET_PX = 700
QUALITY = 80
# 16-bit values that stay under this hold 8-bit data (a lanczos warp can overshoot 255 a little);
# past it, they span the 16-bit range.
_EIGHT_BIT_CEILING = 1023
_GDAL_TIMEOUT_S = 300


def plate_previews(pub: identity.Pub) -> list[tuple[str, int | None]]:
    """(object, fit) for a harvested map's two previews: the 3D sheet at its source's width, and the
    catalog thumbnail."""
    return [(pub.sheet_object, None), (pub.thumb_object, CATALOG_PX)]


def _gdal(*args: str) -> str:
    # Raised with GDAL's own message: str(CalledProcessError) carries only the exit status.
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=_GDAL_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{args[0]}: timed out after {_GDAL_TIMEOUT_S}s") from e
    if r.returncode:
        raise RuntimeError(f"{args[0]}: {r.stderr.strip() or f'exit status {r.returncode}'}")
    return r.stdout


def _range(values: list[float]) -> int:
    """The fixed range 16-bit values come from: 0-255 when they hold 8-bit data, else 0-65535."""
    return 65535 if max(values) > _EIGHT_BIT_CEILING else 255


def encode(src: str, dst: str, *, fit: int | None = None, quality: int = QUALITY) -> None:
    """Write `src` (anything GDAL reads: a COG overview, a PNG) to `dst` as WebP. With `fit`, the long
    side shrinks to at most that many pixels; nothing is enlarged. Raises on a raster WebP can't
    carry faithfully: not RGB/RGBA, or neither 8- nor 16-bit."""
    info = json.loads(_gdal("gdalinfo", "-json", src))
    width, height = info["size"]
    bands = info["bands"]
    if len(bands) not in (3, 4):
        raise ValueError(f"{src}: WebP holds RGB or RGBA, not {len(bands)} band(s)")
    # QUALITY is the WEBP driver's option (the harvest's COG profile sets GTiff's WEBP_LEVEL instead).
    # Alpha survives the lossy encode.
    args = ["gdal_translate", "-q", "-of", "WEBP", "-co", f"QUALITY={quality}", "-r", "average"]
    if fit and max(width, height) > fit:
        args += ["-outsize", str(fit), "0"] if width >= height else ["-outsize", "0", str(fit)]
    types = {b["type"] for b in bands}
    if types == {"UInt16"}:
        # The harvest's lzw fallback leaves some plates 16-bit, and some of those hold 8-bit colors
        # with only the alpha in 0-65535. So the colors share one fixed range and the alpha gets its
        # own, each as gdal_translate `-scale_<band>`; never a band's own min/max, which would
        # stretch the colors and shift the hue.
        maxes = [b["computedMax"] for b in json.loads(_gdal("gdalinfo", "-json", "-mm", src))["bands"]]
        tops = [_range(maxes[:3])] * 3 + [_range(maxes[3:])] * (len(maxes) - 3)
        for i, top in enumerate(tops, start=1):
            args += [f"-scale_{i}", "0", str(top), "0", "255"]
        args += ["-ot", "Byte"]
    elif types != {"Byte"}:
        raise ValueError(f"{src}: no WebP for {'/'.join(sorted(types))} bands")
    _gdal(*args, src, dst)

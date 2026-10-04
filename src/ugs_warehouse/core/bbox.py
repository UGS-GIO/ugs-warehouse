"""STAC bbox helpers."""
from __future__ import annotations


def to_2d_bbox(bbox: list[float]) -> tuple[float, float, float, float]:
    """[xmin, ymin, xmax, ymax] from a 2D or 3D STAC bbox.

    A 3D bbox is [xmin, ymin, zmin, xmax, ymax, zmax], so the max corner starts halfway.
    """
    if len(bbox) not in (4, 6):
        raise ValueError(f"bbox must have 4 or 6 values, got {len(bbox)}")
    half = len(bbox) // 2
    return bbox[0], bbox[1], bbox[half], bbox[half + 1]

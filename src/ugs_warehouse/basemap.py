"""Utah basemap tiling grid: the 7.5-minute quads the offline basemap is cut into.

The offline basemap is one statewide overview archive for low zooms plus one archive per
7.5-minute quadrangle for high zooms, so a field user downloads the few quads they are working
in rather than the whole state. The quad is the unit because it is already UGS's mapping unit:
every 1:24,000 map is one, and staff know them by name.

Quads are named by the USGS "Ohio code", the standard index for 7.5-minute cells:

    40111a1  =  1-degree block whose SOUTHEAST corner is 40N 111W,
                row a (southernmost of a-h), column 1 (easternmost of 1-8)

Pure, no I/O: the build script and the viewer's tile resolver both depend on this arithmetic
agreeing exactly, so it is kept testable on its own.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

CELL = 0.125                     # 7.5 minutes, in degrees
ROWS = "abcdefgh"                # south → north within a 1-degree block

# Utah's extent, and the Wyoming notch cut out of its northeast corner (north of 41N, east of
# 111.047W). Cells wholly inside the notch cover no part of Utah and are not built.
UTAH = (-114.052, 37.0, -109.041, 42.0)            # west, south, east, north
NOTCH_SOUTH, NOTCH_WEST = 41.0, -111.047

# Zoom split between the two kinds of archive. The overview carries z0-10 statewide; a quad
# carries z11 and up. At z11 a tile is ~15 km across in Utah, about one quad, so this is the
# zoom where "only the quads I need" starts to save real bytes.
OVERVIEW_MAXZOOM = 10
QUAD_MINZOOM = OVERVIEW_MAXZOOM + 1


@dataclass(frozen=True)
class Quad:
    code: str
    west: float
    south: float
    east: float
    north: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)


def quad_at(lon: float, lat: float) -> Quad:
    """The 7.5-minute quad containing a point (western hemisphere, northern latitudes)."""
    block_lat = int(lat // 1)                       # SE corner latitude
    block_lon = int(-lon // 1)                      # SE corner longitude, degrees west
    row = min(int((lat - block_lat) / CELL), 7)
    col = min(int((-lon - block_lon) / CELL), 7)
    return _quad(block_lat, block_lon, row, col)


def _quad(block_lat: int, block_lon: int, row: int, col: int) -> Quad:
    south = block_lat + row * CELL
    east = -(block_lon + col * CELL)
    return Quad(
        code=f"{block_lat:02d}{block_lon:03d}{ROWS[row]}{col + 1}",
        west=east - CELL, south=south, east=east, north=south + CELL,
    )


def _in_utah(q: Quad) -> bool:
    west, south, east, north = UTAH
    overlaps = q.north > south and q.south < north and q.east > west and q.west < east
    in_notch = q.south >= NOTCH_SOUTH and q.west >= NOTCH_WEST
    return overlaps and not in_notch


def utah_quads() -> Iterator[Quad]:
    """Every 7.5-minute quad covering some part of Utah, in a stable order."""
    for block_lat in range(37, 42):
        for block_lon in range(109, 115):
            for row in range(8):
                for col in range(8):
                    q = _quad(block_lat, block_lon, row, col)
                    if _in_utah(q):
                        yield q

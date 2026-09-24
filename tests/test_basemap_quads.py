from __future__ import annotations

from ugs_warehouse.basemap import CELL, quad_at, utah_quads


def test_named_quads_match_the_usgs_ohio_codes():
    # Real 7.5-minute quads, by a point inside each. The Ohio code is the index staff already use,
    # so an off-by-one row or column here would name every archive after its neighbour.
    assert quad_at(-111.89, 40.76).code == "40111g8"     # Salt Lake City North
    assert quad_at(-111.66, 40.23).code == "40111b6"     # Provo


def test_a_quad_is_one_7_5_minute_cell_containing_its_point():
    q = quad_at(-111.89, 40.76)
    assert q.east - q.west == CELL and q.north - q.south == CELL
    assert q.west <= -111.89 <= q.east and q.south <= 40.76 <= q.north


def test_a_point_on_a_block_edge_lands_in_one_quad():
    # Exactly on the SE corner of block 40111: belongs to that block's a1 cell, not its neighbours.
    assert quad_at(-111.0, 40.0).code == "40111a1"


def test_utah_is_covered_without_the_wyoming_notch():
    quads = list(utah_quads())
    codes = {q.code for q in quads}
    assert len(codes) == len(quads), "duplicate quad codes"
    # 40 rows x 41 columns (the westmost column is the sliver past 114W), less the 8 x 16 notch.
    assert len(quads) == 40 * 41 - 8 * 16
    assert "40111g8" in codes                            # Salt Lake City North
    assert quad_at(-109.5, 41.5).code not in codes       # inside the notch: Wyoming only
    assert quad_at(-111.5, 41.5).code in codes           # west of the notch line: Utah


def test_every_utah_quad_resolves_back_to_itself():
    # The viewer finds a tile's archive with quad_at(tile centre); a cell whose centre named a
    # different code would ask for an archive that does not exist.
    for q in utah_quads():
        assert quad_at((q.west + q.east) / 2, (q.south + q.north) / 2).code == q.code

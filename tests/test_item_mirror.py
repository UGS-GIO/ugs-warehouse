"""items.parquet row order: nearby items share row groups."""
from __future__ import annotations

import json

import pytest

duckdb = pytest.importorskip("duckdb")
from ugs_warehouse.core import item_mirror  # noqa: E402


def _item(i: int, x: float, y: float) -> dict:
    return {"type": "Feature", "stac_version": "1.1.0", "id": f"i{i}", "collection": "c",
            "geometry": {"type": "Point", "coordinates": [x, y]}, "bbox": [x, y, x, y],
            "properties": {"datetime": "2026-01-01T00:00:00Z"}, "assets": {}, "links": []}


def test_nearby_items_share_row_groups(tmp_path):
    # A 40x40 grid around Salt Lake City. Unbounded, ST_Hilbert orders by float bits, and every
    # block of 100 rows spans most of the grid (about 85% of its area), so a bbox filter skips
    # nothing. Over the extent, each block covers a small patch.
    items = [_item(i, -112.2 + (i % 40) * 0.025, 40.4 + (i // 40) * 0.025) for i in range(1600)]
    src, out = tmp_path / "items.ndjson", tmp_path / "items.parquet"
    src.write_text("".join(json.dumps(it) + "\n" for it in items))

    con = item_mirror._connect()
    con.execute(item_mirror._copy_sql(str(src), str(out), item_mirror._extent(items)))
    xy = con.execute(f"SELECT bbox.xmin, bbox.ymin FROM read_parquet('{out}')").fetchall()
    full = (39 * 0.025) ** 2
    areas = []
    for k in range(0, len(xy), 100):
        xs, ys = zip(*xy[k:k + 100])
        areas.append((max(xs) - min(xs)) * (max(ys) - min(ys)) / full)
    assert sum(areas) / len(areas) < 0.2


def test_a_3d_bbox_keeps_its_max_corner(tmp_path):
    item = {**_item(0, -111.9, 40.7), "bbox": [-111.9, 40.7, 1200.0, -111.8, 40.8, 1500.0]}
    src, out = tmp_path / "items.ndjson", tmp_path / "items.parquet"
    src.write_text(json.dumps(item) + "\n")

    con = item_mirror._connect()
    con.execute(item_mirror._copy_sql(str(src), str(out), item_mirror._extent([item])))
    row = con.execute(f"SELECT bbox.xmax, bbox.ymax FROM read_parquet('{out}')").fetchone()
    assert row == (-111.8, 40.8)

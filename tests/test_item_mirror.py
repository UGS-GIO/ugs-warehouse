"""items.parquet row order: nearby items share row groups."""
from __future__ import annotations

import json

import pytest

duckdb = pytest.importorskip("duckdb")
from ugs_warehouse.core import item_mirror  # noqa: E402


def _item(i: int, x: float, y: float) -> dict:
    return {"type": "Feature", "stac_version": "1.1.0", "stac_extensions": [], "id": f"i{i}", "collection": "c",
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
    con.execute(item_mirror._copy_sql(str(src), str(out), item_mirror._extent(items),
                                      item_mirror._geo_metadata(con, str(src))))
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
    con.execute(item_mirror._copy_sql(str(src), str(out), item_mirror._extent([item]),
                                      item_mirror._geo_metadata(con, str(src))))
    row = con.execute(f"SELECT bbox.xmax, bbox.ymax FROM read_parquet('{out}')").fetchone()
    assert row == (-111.8, 40.8)


def test_mirror_is_geoparquet_1_1_with_the_bbox_covering(tmp_path):
    items = [{**_item(i, -111.9 + i * 0.01, 40.7), "stac_extensions": ["https://x/ext.json"]}
             for i in range(3)]
    src, out = tmp_path / "items.ndjson", tmp_path / "items.parquet"
    src.write_text("".join(json.dumps(it) + "\n" for it in items))

    con = item_mirror._connect()
    con.execute(item_mirror._copy_sql(str(src), str(out), item_mirror._extent(items),
                                      item_mirror._geo_metadata(con, str(src))))
    kv = dict(con.execute(f"SELECT decode(key), decode(value) FROM parquet_kv_metadata('{out}')").fetchall())
    geo = json.loads(kv["geo"])
    assert geo["version"] == "1.1.0"
    assert geo["columns"]["geometry"]["covering"]["bbox"]["xmin"] == ["bbox", "xmin"]
    assert geo["columns"]["geometry"]["geometry_types"] == ["Point"]
    ext = con.execute(f"SELECT stac_extensions FROM read_parquet('{out}') LIMIT 1").fetchone()[0]
    assert ext == ["https://x/ext.json"]

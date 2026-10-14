"""pubs/identify.py: the per-tier identify PMTiles, its state file, and the `units` assets."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import identify, vectors

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
box = pytest.importorskip("shapely.geometry").box

CHECKSUM = "1220" + "cd" * 32


def _units_file(tmp_path: Path, name: str, frame) -> Path:
    path = tmp_path / name
    vectors.geoparquet.write(frame, path)
    return path


def _own(tmp_path, sid, codes, x0):
    """Put a pub's `unit_polys` (lon/lat, the product's columns) on the in-memory bucket."""
    frame = gpd.GeoDataFrame(
        {"series_id": sid, "unit_code": codes, "unit_name": [f"name {c}" for c in codes],
         "age": "Jurassic", "description": [f"desc {c}" for c in codes], "source_label": "lyr"},
        geometry=[box(x0 + i * 0.01, 40, x0 + i * 0.01 + 0.005, 40.005) for i in range(len(codes))],
        crs="EPSG:4326").rename_geometry("geom")
    path = _units_file(tmp_path, f"{sid}.parquet", frame)
    gcs.upload(str(path), vectors.unit_polys_object(sid), content_type="application/vnd.apache.parquet")


def _seamless(tmp_path, rows):
    """The statewide seamless layer: (series_id, symbol, name, age, x0) rows, geometry column `geometry`."""
    frame = gpd.GeoDataFrame(
        {"unit_symbol": [r[1] for r in rows], "unit_name": [r[2] for r in rows],
         "age": [r[3] for r in rows], "scale": "intermediate", "series_id": [r[0] for r in rows]},
        geometry=[box(r[4], 39, r[4] + 0.005, 39.005) for r in rows], crs="EPSG:4326")
    path = _units_file(tmp_path, "seamless.parquet", frame)
    gcs.upload(str(path), identify.SEAMLESS_OBJECT, content_type="application/vnd.apache.parquet")


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Stub tippecanoe (recording its input), the mosaic membership and the manifest checksums.
    Returns a namespace to set `members`, `sums` and read `inputs`."""
    class Run:
        members: dict[str, list[str]] = {}
        sums: dict[str, str] = {}
        inputs: list[list[dict]] = []

    def tiler(geojsonl, pmtiles, layer, **kw):
        Run.inputs.append([json.loads(line) for line in Path(geojsonl).read_text().splitlines()])
        assert layer == "units"
        Path(pmtiles).write_bytes(b"tiles")

    Run.members, Run.sums, Run.inputs = {}, {}, []
    monkeypatch.setattr(identify.sink_pmtiles, "run_tippecanoe", tiler)
    monkeypatch.setattr(identify, "membership", lambda: Run.members)
    monkeypatch.setattr(identify, "unit_checksums", lambda: (dict(Run.sums), []))
    return Run


def _codes(features):
    return [(f["properties"]["series_id"], f["properties"]["unit_code"]) for f in features]


def test_a_tier_takes_own_polygons_then_seamless_ones_for_members_without(run, tmp_path):
    _own(tmp_path, "A-1", ["Qal", "Qaf"], -112.0)
    _own(tmp_path, "C-3", ["Jn"], -111.0)
    _seamless(tmp_path, [("B-2", "Tc", "Claron ", "Tertiary", -111.5), ("Z-9", "Zz", "n", "a", -111.2),
                         ("b-2", "Tk", "", None, -111.4)])
    run.members = {"100k": ["A-1", "B-2", "C-3"]}
    run.sums = {"A-1": "sumA", "C-3": "sumC"}

    result = identify.build_changed()

    assert result.built == ["100k"] and result.errors == []
    features = run.inputs[0]
    # Own polygons in member order, then the seamless ones of the member without; Z-9 is no member.
    assert _codes(features[:3]) == [("A-1", "Qal"), ("A-1", "Qaf"), ("C-3", "Jn")]
    assert sorted(_codes(features[3:])) == [("B-2", "Tc"), ("B-2", "Tk")]
    by_code = {f["properties"]["unit_code"]: f["properties"] for f in features[3:]}
    seamless = by_code["Tc"]
    assert seamless == {"series_id": "B-2", "unit_code": "Tc", "unit_name": "Claron", "age": "Tertiary",
                        "description": None, "source_label": "seamless"}
    assert by_code["Tk"]["unit_name"] is None  # blank becomes null
    assert gcs.get_bytes(identify.tiles_object("100k")) == b"tiles"
    state = json.loads(gcs.get_bytes(identify.state_object("100k")))
    assert state["members"] == ["A-1", "B-2", "C-3"] and state["features"] == {"own": 3, "seamless": 2}
    assert state["sources"] == {"A-1": "sumA", "B-2": None, "C-3": "sumC"}


def test_an_unchanged_tier_is_not_built_again_and_a_changed_checksum_or_membership_is(run, tmp_path):
    _own(tmp_path, "A-1", ["Qal"], -112.0)
    _own(tmp_path, "C-3", ["Jn"], -111.0)
    run.members = {"100k": ["A-1"]}
    run.sums = {"A-1": "sumA", "C-3": "sumC"}
    assert identify.build_changed().built == ["100k"]

    again = identify.build_changed()
    assert again.built == [] and again.unchanged == ["100k"] and len(run.inputs) == 1

    run.sums["A-1"] = "sumA2"  # a member's unit polygons were rebuilt
    assert identify.build_changed().built == ["100k"] and len(run.inputs) == 2

    run.members = {"100k": ["A-1", "C-3"]}  # a map joined the tier
    assert identify.build_changed().built == ["100k"] and len(run.inputs) == 3
    assert _codes(run.inputs[2]) == [("A-1", "Qal"), ("C-3", "Jn")]
    assert identify.build_changed().unchanged == ["100k"]


def test_a_tier_whose_members_all_use_the_seamless_layer_rebuilds_when_that_file_changes(run, tmp_path):
    _seamless(tmp_path, [("B-2", "Tc", "Claron", "Tertiary", -111.5)])
    run.members = {"500k": ["B-2"]}
    assert identify.build_changed().built == ["500k"]
    assert identify.build_changed().unchanged == ["500k"]

    _seamless(tmp_path, [("B-2", "Tc", "Claron", "Tertiary", -111.5), ("B-2", "Qa", "Alluvium", "Q", -111.4)])

    assert identify.build_changed().built == ["500k"]
    assert len(run.inputs[1]) == 2


def test_a_missing_seamless_file_fails_the_tier_loudly_and_other_tiers_still_build(run, tmp_path, capsys):
    _own(tmp_path, "A-1", ["Qal"], -112.0)
    run.members = {"24k": ["A-1"], "100k": ["B-2"]}
    run.sums = {"A-1": "sumA"}

    result = identify.build_changed()

    assert result.built == ["24k"]
    assert len(result.errors) == 1 and "100k" in result.errors[0] and "units.parquet" in result.errors[0]


def test_a_tier_with_no_polygons_builds_nothing(run, tmp_path):
    _seamless(tmp_path, [("Z-9", "Zz", "n", "a", -111.2)])
    run.members = {"250k": ["B-2"]}

    result = identify.build_changed()

    assert result.empty == ["250k"] and result.built == []
    with pytest.raises(FileNotFoundError):
        gcs.get_bytes(identify.state_object("250k"))

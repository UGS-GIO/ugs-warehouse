from __future__ import annotations

import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import build_basemap
from ugs_warehouse.basemap import utah_quads


def test_extract_covers_the_whole_quad_grid():
    # Includes the sliver column past 114W.
    west, south, east, north = map(float, build_basemap.grid_bbox(list(utah_quads())).split(","))
    assert (west, south, east, north) == (-114.125, 37.0, -109.0, 42.0)


def test_latest_build_is_the_newest_daily_key(monkeypatch):
    builds = [{"key": "20260922.pmtiles"}, {"key": "20260924.pmtiles"}, {"key": "20260923.pmtiles"},
              {"key": "zz-notes.txt"}, {"size": 1}]
    seen = {}

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        return io.BytesIO(json.dumps(builds).encode())

    monkeypatch.setattr(build_basemap.urllib.request, "urlopen", fake_urlopen)
    assert build_basemap.latest_build() == "20260924.pmtiles"
    assert seen["ua"], "the metadata host 403s Python's default User-Agent"


# Layer names and zooms match the live Protomaps Utah cut (metadata "Protomaps Basemap" 4.15.2).
PROTOMAPS_LAYERS = [
    "boundaries", "buildings", "earth", "landcover", "landuse", "places", "pois", "roads", "water",
]

GOOD_HEADER = json.dumps({
    "tile_compression": "gzip",
    "tile_type": "mvt",
    "minzoom": 0,
    "maxzoom": 14,
    "bounds": [-114.125, 37.0, -109.0, 42.0],
    "center": [-111.5, 39.5, 7],
})

GOOD_METADATA = json.dumps({
    "name": "Protomaps Basemap",
    "version": "4.15.2",
    "vector_layers": [{"id": layer} for layer in PROTOMAPS_LAYERS],
})


def _fake_pmtiles(header=GOOD_HEADER, metadata=GOOD_METADATA, verify_fails_for=()):
    """Stands in for subprocess.run across every `pmtiles` call verify_archives makes, the same
    way tests/test_check_grants.py mocks subprocess.run for gcloud. `verify_fails_for` is an
    iterable of archive file names (e.g. "overview.pmtiles") whose `pmtiles verify` should raise,
    like a real corrupt archive would.
    """
    def run(cmd, **kwargs):
        assert cmd[0] == "pmtiles"
        if cmd[1] == "verify":
            path = cmd[2]
            if any(path.endswith(name) for name in verify_fails_for):
                raise subprocess.CalledProcessError(
                    1, cmd, output="", stderr="Root directory offset=0 must not be 0"
                )
            return SimpleNamespace(stdout="Completed verify in 1ms.\n", stderr="")
        if cmd[1] == "show" and cmd[-1] == "--header-json":
            return SimpleNamespace(stdout=header, stderr="")
        if cmd[1] == "show" and cmd[-1] == "--metadata":
            return SimpleNamespace(stdout=metadata, stderr="")
        raise AssertionError(f"unexpected pmtiles invocation: {cmd}")
    return run


def test_verify_archives_passes_a_good_build(monkeypatch, tmp_path):
    monkeypatch.setattr(build_basemap.subprocess, "run", _fake_pmtiles())
    build_basemap.verify_archives(tmp_path)  # no raise


def test_verify_archives_stops_when_pmtiles_verify_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        build_basemap.subprocess, "run",
        _fake_pmtiles(verify_fails_for=("overview.pmtiles",)),
    )
    with pytest.raises(RuntimeError, match="overview.pmtiles"):
        build_basemap.verify_archives(tmp_path)


def test_verify_archives_stops_on_missing_required_layer(monkeypatch, tmp_path):
    present = [layer for layer in PROTOMAPS_LAYERS if layer != "water"]
    bad_metadata = json.dumps({
        "name": "Protomaps Basemap", "version": "4.15.2",
        "vector_layers": [{"id": layer_id} for layer_id in present],
    })
    monkeypatch.setattr(build_basemap.subprocess, "run", _fake_pmtiles(metadata=bad_metadata))
    with pytest.raises(RuntimeError, match="water"):
        build_basemap.verify_archives(tmp_path)


def test_verify_archives_stops_on_non_mvt_tile_type(monkeypatch, tmp_path):
    bad_header = json.dumps({**json.loads(GOOD_HEADER), "tile_type": "png"})
    monkeypatch.setattr(build_basemap.subprocess, "run", _fake_pmtiles(header=bad_header))
    with pytest.raises(RuntimeError, match="tile_type"):
        build_basemap.verify_archives(tmp_path)


def test_verify_archives_stops_on_wrong_maxzoom(monkeypatch, tmp_path):
    bad_header = json.dumps({**json.loads(GOOD_HEADER), "maxzoom": 10})
    monkeypatch.setattr(build_basemap.subprocess, "run", _fake_pmtiles(header=bad_header))
    with pytest.raises(RuntimeError, match="maxzoom"):
        build_basemap.verify_archives(tmp_path)


@pytest.mark.parametrize("error", [
    FileNotFoundError(2, "No such file or directory", "pmtiles"),
    PermissionError(13, "Permission denied", "pmtiles"),
])
def test_verify_archives_explains_a_pmtiles_cli_it_cannot_run(monkeypatch, tmp_path, error):
    def run(cmd, **kwargs):
        raise error

    monkeypatch.setattr(build_basemap.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="could not run the pmtiles CLI"):
        build_basemap.verify_archives(tmp_path)


@pytest.mark.parametrize("metadata", ["null", '"text"', "[]", '{"vector_layers": "roads"}', "{}"])
def test_verify_archives_stops_on_metadata_without_a_layer_list(monkeypatch, tmp_path, metadata):
    monkeypatch.setattr(build_basemap.subprocess, "run", _fake_pmtiles(metadata=metadata))
    with pytest.raises(RuntimeError, match="no vector_layers list"):
        build_basemap.verify_archives(tmp_path)

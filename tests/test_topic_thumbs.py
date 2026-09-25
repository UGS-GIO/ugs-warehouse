"""The topic-thumbnail job runs unattended every night, so it has to stamp the item as it is at write
time (not the copy read before a render), keep one topic's failure from stopping the rest while
still failing the run, and get new thumbnails into the rollup index the viewer lists from without
waiting for the next ingest to refresh the catalog.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ugs_warehouse.core import config, gcs, stac
from ugs_warehouse.vector import thumbs

ROLLUP = f"{config.STAC_PREFIX}/ugs-serving-topics/items.json"


@pytest.fixture
def store(monkeypatch) -> dict[str, bytes]:
    """An in-memory bucket behind every gcs call the job and the real refresh_catalog make."""
    s: dict[str, bytes] = {}

    def get_bytes(path):
        if path not in s:
            raise FileNotFoundError(path)
        return s[path]

    def put_bytes(data, path, **_):
        s[path] = data
        return gcs.FileMeta(len(data))

    def upload(local_path, path, **_):
        with open(local_path, "rb") as f:
            s[path] = f.read()
        return gcs.FileMeta(len(s[path]))

    # Content-derived stand-in for the GCS generation, so any write or delete a test makes to the
    # dict directly changes it, the way a concurrent writer changes the real one.
    def version(path):
        return {"version": hashlib.sha256(s[path]).hexdigest()}

    def get_bytes_versioned(path):
        if path not in s:
            raise FileNotFoundError(path)
        return s[path], version(path)

    def put_bytes_if_unchanged(data, path, expected, **_):
        if path not in s or version(path) != expected:
            raise gcs.Changed(path)
        s[path] = data
        return gcs.FileMeta(len(data))

    monkeypatch.setattr(gcs, "get_bytes", get_bytes)
    monkeypatch.setattr(gcs, "put_bytes", put_bytes)
    monkeypatch.setattr(gcs, "upload", upload)
    monkeypatch.setattr(gcs, "get_bytes_versioned", get_bytes_versioned, raising=False)
    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", put_bytes_if_unchanged, raising=False)
    monkeypatch.setattr(gcs, "exists", lambda path: path in s)
    monkeypatch.setattr(gcs, "delete", lambda path: s.pop(path, None))
    monkeypatch.setattr(gcs, "list_paths", lambda pre: sorted(k for k in s if k.startswith(pre)))
    monkeypatch.setattr(config, "EXTERNAL_CATALOGS", [])
    monkeypatch.setattr(thumbs, "REFRESH_RETRY_SECONDS", 0)
    monkeypatch.delenv("CLOUD_RUN_TASK_COUNT", raising=False)
    monkeypatch.delenv("CLOUD_RUN_TASK_INDEX", raising=False)
    return s


def _item_path(stem: str, schema: str = "wetlands") -> str:
    return f"{config.STAC_PREFIX}/ugs-serving-topics/{schema}/{stem}/{stem}.json"


def _publish(stem: str, schema: str = "wetlands", *, style_url: str | None = None,
             bbox: list[float] | None = None, **props) -> str:
    """Write a serving-topic item the way the vector ingest lays it out."""
    bbox = bbox or [-114.05, 37.0, -109.04, 42.0]
    properties = {"title": stem, "ugs:dbt_schema": schema, **props}
    if style_url:
        properties["ugs:renders"] = {"default": {"style_url": style_url}}
    return stac.write_item(stac.build_item(
        item_id=stem, collection=schema, collection_path=f"ugs-serving-topics/{schema}",
        geometry=stac.bbox_polygon(bbox), bbox=bbox, datetime_iso="2026-09-01T00:00:00Z",
        properties=properties,
        assets={"pmtiles": {"href": f"https://cdn.example/{stem}.pmtiles",
                            "type": "application/vnd.pmtiles", "roles": ["data"]}}))


def _renders(monkeypatch, *, during=None, fail_for: tuple[str, ...] = (),
             styles: list[dict] | None = None) -> None:
    """Stand in for headless Chromium: write a WebP, optionally doing something mid-render. Each render
    writes different bytes, as a real re-render usually does, so its file fields differ."""
    renders = 0

    def render(style, bbox, out):
        nonlocal renders
        renders += 1
        if styles is not None:
            styles.append(style)
        if any(stem in style["sources"]["v"]["url"] for stem in fail_for):
            raise RuntimeError("WebGL context lost")
        if during:
            during()
        with open(out, "wb") as f:
            f.write(b"RIFF\x00\x00\x00\x00WEBPfake" + b"." * renders)
    monkeypatch.setattr(thumbs, "_render_webp", render)


def _rendered_stems(styles: list[dict]) -> list[str]:
    return [s["sources"]["v"]["url"].rsplit("/", 1)[-1].removesuffix(".pmtiles") for s in styles]


def _run(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["ugs_warehouse.vector.thumbs", *args])
    return thumbs.main()


def _thumbnail(store, path: str) -> dict | None:
    return json.loads(store[path])["assets"].get("thumbnail")


def _index_thumbnail(store, stem: str) -> dict | None:
    entry = next(e for e in json.loads(store[ROLLUP])["items"] if e["id"] == stem)
    return entry["assets"].get("thumbnail")


def _ingest_thumbnail(monkeypatch, stem: str, schema: str = "wetlands") -> dict | None:
    """The thumbnail asset the vector ingest would stamp on this topic's item right now."""
    from ugs_warehouse.vector import sink_stac
    from ugs_warehouse.vector.topics import Topic

    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_table_columns", lambda con, view: [])
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    for name in ("attach_renders", "attach_classification", "attach_iso"):
        monkeypatch.setattr(sink_stac.stac, name, lambda item: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda item: "")
    sink_stac.write(Topic(schema=schema, layer=f"{stem}_current"), None, "v",
                    bbox=[-114.05, 37.0, -109.04, 42.0], row_count=5)
    return captured["assets"].get("thumbnail")


def test_a_thumbnail_is_a_webp(store, monkeypatch):
    """Full-size PNG previews were most of the catalog's page weight (#372)."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    types: dict[str, str] = {}
    upload = gcs.upload

    def recording(local_path, p, **kw):
        types[p] = kw["content_type"]
        return upload(local_path, p, **kw)

    monkeypatch.setattr(gcs, "upload", recording)
    assert _run(monkeypatch, "--all") == 0
    assert types == {f"{config.THUMBS_PREFIX}/wetlands_riverine/wetlands_riverine.webp": "image/webp"}
    assert _thumbnail(store, path)["type"] == "image/webp"


def test_the_ingest_stamps_the_thumbnail_this_job_writes(store, monkeypatch):
    """Both write the item's thumbnail asset, so they must agree on the image and its type, or
    every ingest would point the item back at a file this job no longer writes."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    job = _thumbnail(store, path)
    assert _ingest_thumbnail(monkeypatch, "wetlands_riverine") == job


def test_a_render_that_is_not_webp_fails_instead_of_uploading(store, monkeypatch):
    """The image is uploaded and stamped as image/webp, so a render in any other format fails."""
    path = _publish("wetlands_riverine")

    def png_render(style, bbox, out):
        with open(out, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")

    monkeypatch.setattr(thumbs, "_render_webp", png_render)
    assert _run(monkeypatch, "--all") == 1
    assert _thumbnail(store, path) is None
    assert not [p for p in store if p.startswith(f"{config.THUMBS_PREFIX}/wetlands_riverine/")]


def test_an_out_of_range_quality_stops_the_job_at_startup():
    """Checked once when the job starts, not rediscovered by every topic's render."""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "TOPIC_THUMB_QUALITY": "150"}
    r = subprocess.run([sys.executable, "-c", "import ugs_warehouse.vector.thumbs"],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "TOPIC_THUMB_QUALITY" in r.stderr


def test_stamping_keeps_what_an_ingest_wrote_mid_render(store, monkeypatch):
    path = _publish("wetlands_riverine")

    def ingest_lands():
        item = json.loads(store[path])
        item["properties"]["ugs:row_count"] = 4242
        store[path] = json.dumps(item).encode()

    _renders(monkeypatch, during=ingest_lands)
    assert _run(monkeypatch, "--all") == 0
    item = json.loads(store[path])
    assert item["properties"]["ugs:row_count"] == 4242
    assert item["assets"]["thumbnail"]["href"].endswith("/wetlands_riverine/wetlands_riverine.webp")


def test_a_stamp_keeps_the_catalog_item_conventions(store, monkeypatch):
    """Same content type, cache policy and usage hint the ingest writes, so a stamped item doesn't
    flip to no-cache or lose its asset description until the next reingest."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    headers: dict[str, dict] = {}
    put = gcs.put_bytes_if_unchanged

    def recording(data, p, version, **kw):
        headers[p] = kw
        return put(data, p, version, **kw)

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", recording)
    assert _run(monkeypatch, "--all") == 0
    assert headers[path]["content_type"] == "application/geo+json"
    assert headers[path]["cache_control"] == gcs.CACHE_CATALOG
    assert _thumbnail(store, path)["description"] == stac.USAGE_THUMBNAIL


def test_the_stamp_lands_on_the_item_where_it_actually_lives(store, monkeypatch):
    """The listed path is the truth; a property that disagrees with it must not send the stamp
    somewhere else."""
    path = _publish("wetlands_riverine", **{"ugs:dbt_schema": "hazards"})
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    assert _thumbnail(store, path) is not None
    assert f"{config.THUMBS_PREFIX}/wetlands_riverine/wetlands_riverine.webp" in store


def test_a_leftover_pre_split_copy_is_left_alone(store, monkeypatch):
    """Items nest as <catalog>/<schema>/<id>/<id>.json. A copy left at <catalog>/<id>/<id>.json by
    the pre-split layout (scripts/prune_flat_topic_items.py) isn't the item the viewer lists, and
    taking it too would process the same topic twice, possibly in two shards at once."""
    nested = _publish("wetlands_riverine")
    flat = f"{config.STAC_PREFIX}/ugs-serving-topics/wetlands_riverine/wetlands_riverine.json"
    store[flat] = before = store[nested]
    assert thumbs._topic_item_paths() == [nested]
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    # Exit code not asserted: with the copy present, refresh_catalog writes a flat collection index at
    # the rollup's path, so the run fails its index check.
    _run(monkeypatch, "--all", "--force")
    assert _rendered_stems(styles) == ["wetlands_riverine"]
    assert store[flat] == before
    assert _thumbnail(store, nested) is not None


def test_an_item_whose_id_disagrees_with_its_path_fails_the_run(store, monkeypatch):
    item = json.loads(store[_publish("wetlands_riverine")])
    item["id"] = "wetlands_elsewhere"
    store[_item_path("wetlands_riverine")] = json.dumps(item).encode()
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 1


def test_a_topic_retired_mid_render_stays_retired(store, monkeypatch):
    path = _publish("wetlands_riverine")
    _renders(monkeypatch, during=lambda: store.pop(path))
    assert _run(monkeypatch, "--all") == 0
    assert path not in store
    assert not [p for p in store if p.startswith(f"{config.THUMBS_PREFIX}/wetlands_riverine/")]


def test_a_retired_topics_thumbnail_that_will_not_delete_fails_the_run(store, monkeypatch):
    """gcs.delete swallows errors; an orphan image would be bound to any later topic with this id."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch, during=lambda: store.pop(path))
    monkeypatch.setattr(gcs, "delete", lambda p: None)
    assert _run(monkeypatch, "--all") == 1


def test_a_topic_retired_before_it_is_read_is_not_a_failure(store, monkeypatch):
    gone = _publish("wetlands_gone")
    _publish("wetlands_riverine")
    listed = gcs.list_paths

    def list_then_retire(prefix):
        paths = listed(prefix)
        store.pop(gone, None)
        return paths

    monkeypatch.setattr(gcs, "list_paths", list_then_retire)
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    assert gone not in store


def test_an_unreadable_item_fails_the_run_without_stopping_the_others(store, monkeypatch):
    good = _publish("wetlands_riverine")
    store[_item_path("wetlands_broken")] = b"{not json"
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 1
    assert _thumbnail(store, good) is not None


def test_a_render_failure_fails_the_run_without_stopping_the_others(store, monkeypatch):
    broken = _publish("wetlands_riverine")
    fine = _publish("wetlands_stressors")
    _renders(monkeypatch, fail_for=("wetlands_riverine",))
    assert _run(monkeypatch, "--all") == 1
    assert _thumbnail(store, broken) is None
    assert _thumbnail(store, fine) is not None


def test_an_unexpected_error_on_one_topic_fails_the_run_without_stopping_the_others(store, monkeypatch):
    _publish("wetlands_riverine")
    fine = _publish("wetlands_stressors")
    _renders(monkeypatch)
    upload = gcs.upload

    def flaky(local_path, path, **kw):
        if "wetlands_riverine" in path:
            raise OSError("503 from GCS")
        return upload(local_path, path, **kw)

    monkeypatch.setattr(gcs, "upload", flaky)
    assert _run(monkeypatch, "--all") == 1
    assert _thumbnail(store, fine) is not None


def test_a_bbox_outside_wgs84_fails_the_run(store, monkeypatch):
    """A bbox in metres means the item's CRS is mislabeled; that is worth an alert, not a skip."""
    _publish("wetlands_riverine", bbox=[228000.0, 4094000.0, 673000.0, 4652000.0])
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 1


def test_a_bound_style_that_will_not_fetch_fails_instead_of_rendering_sand(store, monkeypatch):
    """Rendering sand for a styled topic on a CDN blip would swap its preview out and back."""
    path = _publish("wetlands_riverine", style_url="https://cdn.example/styles/riverine.json")

    def unreachable(url, timeout=30):
        raise OSError("CDN unreachable")

    monkeypatch.setattr(thumbs, "_fetch", unreachable)
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 1
    assert styles == []
    assert _thumbnail(store, path) is None


def test_a_failed_stamp_is_redone_with_the_new_render(store, monkeypatch):
    """The sidecar marks a thumbnail as done. Written before the stamp, a stamp that failed would
    leave the item describing an older render until the style next changed."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    put = gcs.put_bytes_if_unchanged

    def item_write_fails(data, p, version, **kw):
        raise OSError("503 from GCS")

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", item_write_fails)
    assert _run(monkeypatch, "--all") == 1
    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", put)
    assert _run(monkeypatch, "--all") == 0
    assert _thumbnail(store, path)["file:size"] > 0


def test_a_new_thumbnail_reaches_the_rollup_index(store, monkeypatch):
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    assert _index_thumbnail(store, "wetlands_riverine")["href"].endswith(
        "/wetlands_riverine/wetlands_riverine.webp")


def test_an_index_left_behind_by_an_earlier_run_is_caught_up(store, monkeypatch):
    """Every item current but items.json stale (a crash before the refresh, or another shard's
    refresh landing last): the next run still has to fix the index."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    index = json.loads(store[ROLLUP])
    for entry in index["items"]:
        entry["assets"].pop("thumbnail", None)
    store[ROLLUP] = json.dumps(index).encode()
    assert _run(monkeypatch, "--all") == 0
    assert _index_thumbnail(store, "wetlands_riverine") is not None


def test_a_night_with_nothing_to_do_writes_nothing(store, monkeypatch):
    """Rebuilding the whole catalog when nothing changed is churn that can race an ingest's own
    refresh, so a no-op night must not write anything."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    writes: list[str] = []
    put, put_if = gcs.put_bytes, gcs.put_bytes_if_unchanged
    monkeypatch.setattr(gcs, "put_bytes", lambda data, p, **kw: (writes.append(p), put(data, p, **kw))[1])
    monkeypatch.setattr(gcs, "put_bytes_if_unchanged",
                        lambda data, p, v, **kw: (writes.append(p), put_if(data, p, v, **kw))[1])
    assert _run(monkeypatch, "--all") == 0
    assert writes == []


def test_a_sidecar_that_will_not_read_is_logged_and_re_rendered(store, monkeypatch, capsys):
    """A sidecar read that keeps failing re-renders the topic every night; the log has to say why."""
    _publish("wetlands_riverine")
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 0
    sidecar = thumbs.sha_object("wetlands_riverine")
    get = gcs.get_bytes

    def sidecar_fails(p):
        if p == sidecar:
            raise OSError("503 from GCS")
        return get(p)

    monkeypatch.setattr(gcs, "get_bytes", sidecar_fails)
    styles.clear()
    capsys.readouterr()
    assert _run(monkeypatch, "--all") == 0
    assert _rendered_stems(styles) == ["wetlands_riverine"]
    logged = capsys.readouterr()
    assert any(sidecar in line and "503 from GCS" in line
               for line in (logged.out + logged.err).splitlines())


def test_the_shards_split_the_topics_between_them_without_overlap(store, monkeypatch):
    """--force so every topic a shard takes renders; otherwise an overlap hides behind the skip."""
    stems = [f"wetlands_t{i}" for i in range(7)]
    for stem in stems:
        _publish(stem)
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "3")
    for i in range(3):
        monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(i))
        assert _run(monkeypatch, "--all", "--force") == 0
    assert sorted(_rendered_stems(styles)) == stems


def _draws_on(layer: dict, geometry: str) -> bool:
    """Whether MapLibre would apply `layer` to a feature of `geometry` type. Only the filter forms
    the renderer emits: none (every feature) or a `match` on `geometry-type`."""
    flt = layer.get("filter")
    if flt is None:
        return True
    op, getter, types, hit, miss = flt
    assert (op, getter) == ("match", ["geometry-type"]), f"unsupported filter form: {flt}"
    return hit if geometry in types else miss


def test_an_unstyled_polygon_topic_gets_no_vertex_dots(store, monkeypatch):
    """MapLibre applies a circle layer to every vertex of a line or polygon, so the fallback's point
    markers have to be gated to points, as the viewer's GEOM_FILTER does (viewer/src/map/map-model.ts).
    Ungated, a statewide polygon layer renders as a cloud of dots."""
    _publish("wetlands_riverine")
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 0
    layers = [lyr for lyr in styles[0]["layers"] if lyr["type"] != "background"]
    circles = [lyr for lyr in layers if lyr["type"] == "circle"]
    assert circles
    assert not any(_draws_on(c, g) for c in circles for g in ("Polygon", "LineString"))
    assert all(_draws_on(c, "Point") for c in circles)
    assert any(lyr["type"] == "fill" and _draws_on(lyr, "Polygon") for lyr in layers)


def test_changing_the_fallback_redraws_only_the_topics_drawn_with_it(store, monkeypatch):
    """The content hash covers what is drawn, so an edit to the sand fallback re-renders the
    unstyled topics without a manual version bump, and leaves styled topics alone."""
    style_url = "https://cdn.example/styles/stressors.json"
    styled = json.dumps({"layers": [{"id": "f", "type": "fill", "paint": {"fill-color": "#123456"}}]})
    monkeypatch.setattr(thumbs, "_fetch", lambda url, timeout=30: styled.encode())
    _publish("wetlands_riverine")
    _publish("wetlands_stressors", style_url=style_url)
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 0
    styles.clear()
    edited = [{**lyr, "paint": {**lyr["paint"]}} for lyr in thumbs.SAND_LAYERS]
    edited[0]["paint"]["fill-color"] = "#c9b27f"
    monkeypatch.setattr(thumbs, "SAND_LAYERS", edited)
    assert _run(monkeypatch, "--all") == 0
    assert _rendered_stems(styles) == ["wetlands_riverine"]


def test_a_refresh_that_leaves_the_index_behind_fails_the_run(store, monkeypatch):
    """Checked again after refreshing: a mismatch that survives a refresh (the rollup written in a
    shape this job doesn't expect, say) has to alert rather than repeat quietly every night."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    monkeypatch.setattr(stac, "refresh_catalog", lambda: None)
    assert _run(monkeypatch, "--all") == 1


def test_a_corrupt_index_is_rebuilt_rather_than_crashing_the_run(store, monkeypatch):
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    store[ROLLUP] = b"\x00 not an index"
    assert _run(monkeypatch, "--all") == 0
    assert _index_thumbnail(store, "wetlands_riverine") is not None


def test_a_bound_style_that_is_not_a_style_fails_instead_of_rendering_sand(store, monkeypatch):
    """An error page served with a 200 is as broken as a failed fetch."""
    path = _publish("wetlands_riverine", style_url="https://cdn.example/styles/riverine.json")
    monkeypatch.setattr(thumbs, "_fetch", lambda url, timeout=30: b"<html>502 Bad Gateway</html>")
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 1
    assert styles == []
    assert _thumbnail(store, path) is None


def test_a_forced_render_whose_stamp_fails_is_redone(store, monkeypatch):
    """A --force re-render with an unchanged style must not leave the old, still-matching sidecar
    behind, or the item keeps the previous render's file fields."""
    _publish("wetlands_riverine")
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 0
    put = gcs.put_bytes_if_unchanged

    def item_write_fails(data, p, version, **kw):
        raise OSError("503 from GCS")

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", item_write_fails)
    assert _run(monkeypatch, "--all", "--force") == 1
    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", put)
    styles.clear()
    assert _run(monkeypatch, "--all") == 0
    assert _rendered_stems(styles) == ["wetlands_riverine"]


@pytest.mark.parametrize("body", [b"[]", b'{"error": "rate limited"}', b'{"layers": "fill"}',
                                  b'{"layers": ["fill"]}'])
def test_a_bound_style_without_a_layer_list_fails_instead_of_rendering_sand(store, monkeypatch, body):
    """A 200 whose JSON isn't a style (not an object, no layer list, or layers that aren't objects)
    is as broken as a failed fetch: the topic fails, it doesn't render sand or a partial style."""
    _publish("wetlands_riverine", style_url="https://cdn.example/styles/riverine.json")
    monkeypatch.setattr(thumbs, "_fetch", lambda url, timeout=30: body)
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 1
    assert styles == []


def test_a_topic_retired_after_its_stamp_does_not_fail_the_recheck(store, monkeypatch):
    """Retired between the stamp and the refresh, the topic is rightly absent from the rebuilt index;
    that is not the index lagging."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    refresh = stac.refresh_catalog

    def retired_first():
        store.pop(path, None)
        refresh()

    monkeypatch.setattr(stac, "refresh_catalog", retired_first)
    assert _run(monkeypatch, "--all") == 0


def test_a_refresh_that_loses_a_race_is_retried_before_it_fails_the_run(store, monkeypatch):
    """A burst of promotes starts one run per layer, and each refreshes the catalog; a refresh that
    listed before this run's stamp can land last. A retry catches it instead of paging anyone."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    refresh = stac.refresh_catalog
    attempts: list[int] = []

    def overwritten_once():
        attempts.append(1)
        if len(attempts) > 1:
            refresh()

    monkeypatch.setattr(stac, "refresh_catalog", overwritten_once)
    assert _run(monkeypatch, "--all") == 0
    assert len(attempts) == 2


def test_a_refresh_that_raises_is_retried_before_it_fails_the_run(store, monkeypatch):
    """Concurrent refreshes rewrite the same catalog objects; a rate-limited write inside one is
    another lost race, not a reason to fail the run."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    refresh = stac.refresh_catalog
    attempts: list[int] = []

    def rate_limited_once():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("429 rateLimitExceeded on catalog.json")
        refresh()

    monkeypatch.setattr(stac, "refresh_catalog", rate_limited_once)
    assert _run(monkeypatch, "--all") == 0
    assert len(attempts) == 2


def test_a_refresh_that_keeps_raising_fails_the_run_and_says_why(store, monkeypatch, capsys):
    """A broken refresh (a bug, a permission error) must not read as a lost race in the failure."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)

    def denied():
        raise PermissionError("403 storage.objects.create denied on items.json")

    monkeypatch.setattr(stac, "refresh_catalog", denied)
    assert _run(monkeypatch, "--all") == 1
    assert "403 storage.objects.create" in capsys.readouterr().err


def test_a_last_refresh_that_raises_still_gets_a_final_check(store, monkeypatch):
    """Another run's refresh can land while ours is failing; the run checks once more before failing."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    others_refresh = stac.refresh_catalog
    attempts: list[int] = []

    def rate_limited():
        attempts.append(1)
        if len(attempts) == thumbs.REFRESH_ATTEMPTS:
            others_refresh()
        raise OSError("429 rateLimitExceeded on catalog.json")

    monkeypatch.setattr(stac, "refresh_catalog", rate_limited)
    assert _run(monkeypatch, "--all") == 0


def test_a_refresh_another_run_made_during_the_wait_is_not_redone(store, monkeypatch):
    """Re-checked after the wait before refreshing again: a later refresh from another run already
    carries this run's stamp, and piling on another full refresh only adds contention."""
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    others_refresh = stac.refresh_catalog
    ours: list[int] = []
    monkeypatch.setattr(stac, "refresh_catalog", lambda: ours.append(1))
    monkeypatch.setattr(thumbs.time, "sleep", lambda seconds: others_refresh())
    assert _run(monkeypatch, "--all") == 0
    assert len(ours) == 1


def _between_read_and_write(monkeypatch, path: str, then, *, every: bool = False) -> None:
    """Run `then()` right after the stamp reads `path` (the item's second read; the first is the
    listing's) and before its write lands: another writer in that gap. `every` keeps doing it on
    each re-read, a writer that never lets up."""
    reads = {"n": 0}
    for name in ("get_bytes", "get_bytes_versioned"):
        real = getattr(gcs, name, None)
        if real is None:
            continue

        def read(p, _real=real):
            out = _real(p)
            if p == path:
                reads["n"] += 1
                if reads["n"] == 2 or (every and reads["n"] >= 2):
                    then()
            return out

        monkeypatch.setattr(gcs, name, read)


def test_a_write_between_the_stamps_read_and_its_write_is_kept(store, monkeypatch):
    """The gap is a fraction of a second, but a GCS generation match closes it rather than narrowing
    it: the stamp re-reads and merges instead of writing back what it read."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)

    def ingest_lands():
        item = json.loads(store[path])
        item["properties"]["ugs:row_count"] = 4242
        store[path] = json.dumps(item).encode()

    _between_read_and_write(monkeypatch, path, ingest_lands)
    assert _run(monkeypatch, "--all") == 0
    item = json.loads(store[path])
    assert item["properties"]["ugs:row_count"] == 4242
    assert item["assets"]["thumbnail"]["href"].endswith("/wetlands_riverine/wetlands_riverine.webp")


def test_a_topic_retired_between_the_stamps_read_and_its_write_is_not_recreated(store, monkeypatch):
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    _between_read_and_write(monkeypatch, path, lambda: store.pop(path, None))
    assert _run(monkeypatch, "--all") == 0
    assert path not in store
    assert not [p for p in store if p.startswith(f"{config.THUMBS_PREFIX}/wetlands_riverine/")]


def test_a_stamp_that_keeps_losing_the_race_fails_loudly_without_reverting(store, monkeypatch):
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    writes = {"n": 0}

    def busy_writer():
        writes["n"] += 1
        item = json.loads(store[path])
        item["properties"]["ugs:row_count"] = writes["n"]
        store[path] = json.dumps(item).encode()

    _between_read_and_write(monkeypatch, path, busy_writer, every=True)
    assert _run(monkeypatch, "--all") == 1
    item = json.loads(store[path])
    assert item["properties"]["ugs:row_count"] == writes["n"]
    assert "thumbnail" not in item["assets"]


def test_new_data_redraws_the_preview_and_an_unchanged_reingest_does_not(store, monkeypatch):
    """The ingest's `ugs:content_hash` changes exactly when the data or tiling does."""
    path = _publish("wetlands_riverine", **{"ugs:content_hash": "147506:aaa:v2|-r1"})
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 0
    item = json.loads(store[path])
    item["properties"]["ugs:content_hash"] = "150001:bbb:v2|-r1"
    store[path] = json.dumps(item).encode()
    assert _run(monkeypatch, "--all") == 0
    assert _run(monkeypatch, "--all") == 0
    assert _rendered_stems(styles) == ["wetlands_riverine", "wetlands_riverine"]

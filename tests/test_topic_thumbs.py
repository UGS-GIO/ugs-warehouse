"""The topic-thumbnail job runs unattended every night, so it has to stamp the item as it is at write
time (not the copy read before a render), keep one topic's failure from stopping the rest while
still failing the run, and get new thumbnails into the rollup index the viewer lists from without
waiting for the next ingest to refresh the catalog.
"""
from __future__ import annotations

import json
import sys

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

    monkeypatch.setattr(gcs, "get_bytes", get_bytes)
    monkeypatch.setattr(gcs, "put_bytes", put_bytes)
    monkeypatch.setattr(gcs, "upload", upload)
    monkeypatch.setattr(gcs, "exists", lambda path: path in s)
    monkeypatch.setattr(gcs, "delete", lambda path: s.pop(path, None))
    monkeypatch.setattr(gcs, "list_paths", lambda pre: sorted(k for k in s if k.startswith(pre)))
    monkeypatch.setattr(config, "EXTERNAL_CATALOGS", [])
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
    """Stand in for headless Chromium: write a PNG, optionally doing something mid-render. Each render
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
            f.write(b"\x89PNG\r\n\x1a\nfake" + b"." * renders)
    monkeypatch.setattr(thumbs, "_render_png", render)


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
    assert item["assets"]["thumbnail"]["href"].endswith("/wetlands_riverine/wetlands_riverine.png")


def test_a_stamp_keeps_the_catalog_item_conventions(store, monkeypatch):
    """Same content type, cache policy and usage hint the ingest writes, so a stamped item doesn't
    flip to no-cache or lose its asset description until the next reingest."""
    path = _publish("wetlands_riverine")
    _renders(monkeypatch)
    headers: dict[str, dict] = {}
    put = gcs.put_bytes

    def recording(data, p, **kw):
        headers[p] = kw
        return put(data, p, **kw)

    monkeypatch.setattr(gcs, "put_bytes", recording)
    assert _run(monkeypatch, "--all") == 0
    assert headers[path]["content_type"] == "application/geo+json"
    assert headers[path]["cache_control"] == gcs.CACHE_CATALOG
    assert _thumbnail(store, path)["description"] == stac.USAGE_THUMBNAIL


def test_the_stamp_lands_on_the_item_where_it_actually_lives(store, monkeypatch):
    """The listed path is the truth; a property that disagrees with it must not send the stamp (or
    the cleanup) somewhere else."""
    path = _publish("wetlands_riverine", **{"ugs:dbt_schema": "hazards"})
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    assert _thumbnail(store, path) is not None
    assert f"{config.THUMBS_PREFIX}/wetlands_riverine/wetlands_riverine.png" in store


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
    """gcs.delete swallows errors; an orphan PNG would be bound to any later topic with this id."""
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
    put = gcs.put_bytes

    def item_write_fails(data, p, **kw):
        if p == path:
            raise OSError("503 from GCS")
        return put(data, p, **kw)

    monkeypatch.setattr(gcs, "put_bytes", item_write_fails)
    assert _run(monkeypatch, "--all") == 1
    monkeypatch.setattr(gcs, "put_bytes", put)
    assert _run(monkeypatch, "--all") == 0
    assert _thumbnail(store, path)["file:size"] > 0


def test_a_new_thumbnail_reaches_the_rollup_index(store, monkeypatch):
    _publish("wetlands_riverine")
    _renders(monkeypatch)
    assert _run(monkeypatch, "--all") == 0
    assert _index_thumbnail(store, "wetlands_riverine")["href"].endswith(
        "/wetlands_riverine/wetlands_riverine.png")


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
    put = gcs.put_bytes
    monkeypatch.setattr(gcs, "put_bytes", lambda data, p, **kw: (writes.append(p), put(data, p, **kw))[1])
    assert _run(monkeypatch, "--all") == 0
    assert writes == []


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
    path = _publish("wetlands_riverine")
    styles: list[dict] = []
    _renders(monkeypatch, styles=styles)
    assert _run(monkeypatch, "--all") == 0
    put = gcs.put_bytes

    def item_write_fails(data, p, **kw):
        if p == path:
            raise OSError("503 from GCS")
        return put(data, p, **kw)

    monkeypatch.setattr(gcs, "put_bytes", item_write_fails)
    assert _run(monkeypatch, "--all", "--force") == 1
    monkeypatch.setattr(gcs, "put_bytes", put)
    styles.clear()
    assert _run(monkeypatch, "--all") == 0
    assert _rendered_stems(styles) == ["wetlands_riverine"]

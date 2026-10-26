"""Which title a vector item gets: override > registry > prior > style render > prettified id."""
from __future__ import annotations

import pytest

from ugs_warehouse.core import stac
from ugs_warehouse.vector import sink_stac
from ugs_warehouse.vector.topics import Topic

RENDER_TITLE = "Geologic units (1:500,000)"


def _title(monkeypatch, *, override=None, display=None, prior=None, render=True) -> str:
    written: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(stac, "manual_override", lambda iid: {"title": override} if override else {})
    monkeypatch.setattr(stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: False)
    monkeypatch.setattr(stac, "build_item",
                        lambda **k: {"properties": k["properties"], "assets": k["assets"]})

    def renders(item):
        if render:
            item["properties"]["ugs:renders"] = {"default": {"title": RENDER_TITLE}}

    monkeypatch.setattr(stac, "attach_renders", renders)
    monkeypatch.setattr(stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(stac, "write_item", lambda i: written.update(i) or "stac/path.json")
    monkeypatch.setattr(sink_stac, "_published", lambda t: None)
    sink_stac.write(Topic(schema="mapping", layer="geolmap_geolunits_500k_current"), None, "v",
                    title=prior, metadata={"display_name": display} if display else None)
    return written["properties"]["title"]


def test_override_wins(monkeypatch):
    assert _title(monkeypatch, override="Hand title", display="Registry title") == "Hand title"


@pytest.mark.parametrize("raw", ["geolunits_500k", "geolmap_geolunits_500k"])
def test_raw_titles_fall_back_to_the_render_title(monkeypatch, raw):
    assert _title(monkeypatch, display=raw, prior=raw) == RENDER_TITLE


def test_human_prior_title_is_kept(monkeypatch):
    assert _title(monkeypatch, prior="Geologic units") == "Geologic units"


def test_prettified_id_is_last(monkeypatch):
    assert _title(monkeypatch, prior="geolunits_500k", render=False) == "Geolmap Geolunits 500K"

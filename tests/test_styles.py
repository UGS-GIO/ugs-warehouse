"""ugs-styles -> `ugs:renders` block + `style` asset (core.styles + stac.attach_renders)."""
import pytest

from ugs_warehouse.core import stac, styles

MANIFEST = (
    {"itemId": "hazards_qfaults", "render": "default", "kind": "vector",
     "assets": ["pmtiles"], "path": "styles/hazards_qfaults/default.json"},
    {"itemId": "enmin_gravity", "render": "default", "kind": "raster",
     "assets": ["cog"], "colormap_name": "viridis", "rescale": [0, 100],
     "path": "styles/enmin_gravity/default.json"},
    {"layer": "legacy_layer", "render": "default", "kind": "vector",  # older layer-keyed entry
     "assets": ["pmtiles"], "path": "styles/legacy_layer/default.json"},
)


@pytest.fixture(autouse=True)
def _manifest(monkeypatch):
    monkeypatch.setattr(styles, "_manifest", lambda: MANIFEST)


def test_vector_render_has_style_url_and_asset():
    renders, asset = styles.renders_for("hazards_qfaults", {"pmtiles", "data"})
    assert renders["default"]["assets"] == ["pmtiles"]
    assert renders["default"]["style_url"].endswith("/styles/hazards_qfaults/default.json")
    assert asset and asset["roles"] == ["style"]


def test_raster_render_carries_colormap_not_style_url():
    renders, asset = styles.renders_for("enmin_gravity", {"cog"})
    assert renders["default"]["colormap_name"] == "viridis"
    assert renders["default"]["rescale"] == [0, 100]
    assert "style_url" not in renders["default"]
    assert asset is None  # raster renders carry no GL style asset


def test_layer_keyed_entry_is_tolerated():
    renders, _ = styles.renders_for("legacy_layer", {"pmtiles"})
    assert "default" in renders


def test_skip_when_target_asset_absent():
    # item id matches but the item has no pmtiles asset -> no render
    renders, asset = styles.renders_for("hazards_qfaults", {"data"})
    assert renders == {} and asset is None


def test_no_match_is_graceful():
    assert styles.renders_for("nonexistent", {"pmtiles"}) == ({}, None)


def test_attach_renders_mutates_item():
    item = {"id": "hazards_qfaults", "properties": {}, "assets": {"pmtiles": {"href": "x"}}}
    stac.attach_renders(item)
    # Emitted as `ugs:renders` (prefixed), NOT the STAC render extension — see stac.attach_renders.
    assert "ugs:renders" in item["properties"]
    assert "render" not in " ".join(item.get("stac_extensions") or [])  # no render-ext declaration
    assert item["assets"]["style"]["roles"] == ["style"]


def test_attach_renders_noop_without_match():
    item = {"id": "nope", "properties": {}, "assets": {}}
    stac.attach_renders(item)
    assert "ugs:renders" not in item["properties"]

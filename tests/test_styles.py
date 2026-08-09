"""ugs-styles -> `ugs:renders` block + `style` asset (core.styles + stac.attach_renders)."""
import pytest

from ugs_warehouse.core import stac, styles

MANIFEST = (
    {"itemId": "hazards_qfaults", "render": "default", "kind": "vector",
     "assets": ["pmtiles"], "path": "styles/hazards_qfaults/default.json",
     "field": "fault_class", "legend": [{"label": "Core", "color": "#5E3C99", "values": ["A", "B"]}]},
    {"itemId": "enmin_gravity", "render": "default", "kind": "raster",
     "assets": ["cog"], "colormap_name": "viridis", "rescale": [0, 100],
     "path": "styles/enmin_gravity/default.json"},
    {"itemId": "enmin_ut_counties", "render": "default", "kind": "vector",
     "assets": ["pmtiles"], "path": "styles/enmin_ut_counties/default.json",
     "glyphs": "fonts/{fontstack}/{range}.pbf"},
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


def test_vector_render_passes_field_and_legend_verbatim():
    renders, _ = styles.renders_for("hazards_qfaults", {"pmtiles"})
    assert renders["default"]["field"] == "fault_class"
    assert renders["default"]["legend"] == [{"label": "Core", "color": "#5E3C99", "values": ["A", "B"]}]


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


# ---------------------------------------------------------------- classification from filters
# ugs-styles' `graduated` archetype emits one filtered layer per class. Enumerated fields get `==`
# (exact value); continuous fields get half-open `>=`/`<` bins. Both must yield legend labels.

def _classes(layers, monkeypatch):
    monkeypatch.setattr(styles, "_fetch_layers", lambda _url: tuple(layers))
    return styles.classification_classes("https://example.invalid/s.json")


def _fill(filt, color):
    return {"type": "fill", "filter": filt, "paint": {"fill-color": color}}


def test_enumerated_classes_label_by_value(monkeypatch):
    # `graduated` with `values` — e.g. debris-flow runout dfsi_thr 0.0 / 0.3 / 0.6.
    layers = [
        _fill(["all", ["has", "dfsi_thr"], ["==", ["get", "dfsi_thr"], 0.0]], "#9e9ac8"),
        _fill(["all", ["has", "dfsi_thr"], ["==", ["get", "dfsi_thr"], 0.6]], "#3f007d"),
    ]
    out = _classes(layers, monkeypatch)
    assert [c["title"] for c in out] == ["0.0", "0.6"]
    assert [c.get("color_hint") for c in out] == ["9E9AC8", "3F007D"]


def test_binned_classes_label_as_ranges(monkeypatch):
    # `graduated` with `breaks` — a lower+upper pair reads as one bin; the last class is open.
    layers = [
        _fill(["all", ["has", "P_20"], [">=", ["get", "P_20"], 0.2], ["<", ["get", "P_20"], 0.4]], "#fed572"),
        _fill(["all", ["has", "P_20"], [">=", ["get", "P_20"], 0.8]], "#8d0026"),
    ]
    out = _classes(layers, monkeypatch)
    assert [c["title"] for c in out] == ["0.2 – 0.4", "≥ 0.8"]


def test_mirrored_comparison_operands(monkeypatch):
    # `['<', 0.4, ['get', f]]` means the field is *greater* than 0.4 — don't read it backwards.
    out = _classes([_fill(["<", 0.4, ["get", "P_20"]], "#fd8d3c")], monkeypatch)
    assert [c["title"] for c in out] == ["> 0.4"]


def test_uniform_style_is_not_a_classification(monkeypatch):
    assert _classes([{"type": "fill", "paint": {"fill-color": "#888888"}}], monkeypatch) == []


def test_label_render_carries_absolute_glyphs():
    """Label layers draw nothing without them, and the manifest path is CDN-relative."""
    renders, _ = styles.renders_for("enmin_ut_counties", {"pmtiles"})
    assert renders["default"]["glyphs"].endswith("/fonts/{fontstack}/{range}.pbf")
    assert renders["default"]["glyphs"].startswith("http")
    assert "glyphs" not in styles.renders_for("hazards_qfaults", {"pmtiles"})[0]["default"]

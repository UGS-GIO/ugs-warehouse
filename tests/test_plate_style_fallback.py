"""A publication's plate Item (`<SID>_plate`) falls back to the style keyed by `<SID>`; layers do not."""
import json

from ugs_warehouse import restyle as R
from ugs_warehouse.core import gcs, stac, styles

PUB_STYLE = {"itemId": "OFR-1", "render": "default", "kind": "raster", "assets": ["cog"],
             "colormap_name": "viridis", "path": "styles/OFR-1/default.json"}
COLL = "ugs-publications/OFR/OFR-1"


def _mem_gcs(monkeypatch):
    store: dict[str, bytes] = {}
    monkeypatch.setattr(gcs, "put_bytes", lambda b, p, **k: store.__setitem__(p, b))
    monkeypatch.setattr(gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(gcs, "list_paths", lambda pre: [k for k in store if k.startswith(pre)])
    return store


def _manifest(monkeypatch, *entries):
    monkeypatch.setattr(styles, "_manifest", lambda: tuple(entries))
    monkeypatch.setattr(styles, "warm", lambda: len(entries))


def _pub_item(item_id):
    return stac.build_item(item_id=item_id, collection="OFR", collection_path=COLL,
                           geometry=None, bbox=None, datetime_iso=None,
                           properties={"title": "x", "ugs:series_id": "OFR-1"},
                           assets={"cog": {"href": "h", "type": "image/tiff"}})


def _props(store, iid):
    return json.loads(store[stac.item_object_path(COLL, iid)].decode())["properties"]


def test_plate_falls_back_to_the_pub_style(monkeypatch):
    _manifest(monkeypatch, PUB_STYLE)
    plate = _pub_item("OFR-1_plate")
    stac.attach_renders(plate)
    assert plate["properties"]["ugs:renders"]["default"]["colormap_name"] == "viridis"


def test_plate_own_style_wins_over_the_pub_style(monkeypatch):
    _manifest(monkeypatch, PUB_STYLE, {**PUB_STYLE, "itemId": "OFR-1_plate", "colormap_name": "magma"})
    plate = _pub_item("OFR-1_plate")
    stac.attach_renders(plate)
    assert plate["properties"]["ugs:renders"]["default"]["colormap_name"] == "magma"


def test_layer_items_get_no_fallback(monkeypatch):
    _manifest(monkeypatch, PUB_STYLE)
    for iid in ("OFR-1_units", "OFR-1_faults"):
        layer = _pub_item(iid)
        stac.attach_renders(layer)
        assert "ugs:renders" not in layer["properties"]


def test_a_layer_whose_id_ends_in_plate_is_not_the_plate(monkeypatch):
    # A layer labelled `geo__plate` is the Item `OFR-1_geo__plate`; its pub's plate is `OFR-1_plate`.
    _manifest(monkeypatch, {**PUB_STYLE, "itemId": "OFR-1_geo_"})
    layer = _pub_item("OFR-1_geo__plate")
    assert stac.plate_base_id(layer) is None
    stac.attach_renders(layer)
    assert "ugs:renders" not in layer["properties"]
    assert stac.plate_base_id(_pub_item("OFR-1_plate")) == "OFR-1"


def test_a_plate_suffixed_id_outside_publications_gets_no_fallback(monkeypatch):
    _manifest(monkeypatch, {**PUB_STYLE, "itemId": "topic"})
    item = stac.build_item(item_id="topic_plate", collection="ugs-serving-topics", geometry=None,
                           bbox=None, datetime_iso=None, properties={"title": "x"},
                           assets={"cog": {"href": "h", "type": "image/tiff"}})
    stac.attach_renders(item)
    assert "ugs:renders" not in item["properties"]


def test_restyle_binds_the_plate_but_not_its_layers(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch, PUB_STYLE)
    stac.write_item(_pub_item("OFR-1_plate"))
    stac.write_item(_pub_item("OFR-1_units"))

    assert R.restyle(collection="ugs-publications") == 1

    assert "ugs:renders" in _props(store, "OFR-1_plate")
    assert "ugs:renders" not in _props(store, "OFR-1_units")


def test_report_counts_a_plate_bound_by_the_pub_id_as_styled(monkeypatch, capsys):
    _mem_gcs(monkeypatch)
    _manifest(monkeypatch, PUB_STYLE)
    monkeypatch.setattr(styles, "refresh", lambda: 1)
    stac.write_item(_pub_item("OFR-1_plate"))

    assert R.report(collection="ugs-publications") == 0
    out = capsys.readouterr().out
    assert "1 styled" in out and "ORPHAN" not in out

"""A publication layer's generated style binds through attach_renders and the restyle job, below an
explicit ugs-styles style, on the layer's Item or, for a single-file publication, on its Collection."""
import json

from ugs_warehouse import restyle as R
from ugs_warehouse.core import gcs, stac, styles
from ugs_warehouse.pubs import sink_stac

GEN = "https://cdn.example/geolmap/vectors/OFR-1/units.style.json"
COLL = "ugs-publications/OFR/OFR-1"
PM = {"href": "https://cdn.example/units.pmtiles", "type": "application/vnd.pmtiles"}
EXPLICIT = {"itemId": "OFR-1__units", "render": "default", "kind": "vector", "assets": ["pmtiles"],
            "path": "styles/OFR-1__units/default.json"}


def _mem_gcs(monkeypatch):
    store: dict[str, bytes] = {}
    monkeypatch.setattr(gcs, "put_bytes", lambda b, p, **k: store.__setitem__(p, b))
    monkeypatch.setattr(gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(gcs, "list_paths", lambda pre: [k for k in store if k.startswith(pre)])
    return store


def _manifest(monkeypatch, *entries):
    monkeypatch.setattr(styles, "_manifest", lambda: tuple(entries))
    monkeypatch.setattr(styles, "warm", lambda: len(entries))


def _layer_item(item_id="OFR-1__units", generated=GEN, assets=None, coll_path=COLL):
    props = {"title": "Units", "ugs:series_id": "OFR-1", **({stac.GENERATED_STYLE: generated} if generated else {})}
    return stac.build_item(item_id=item_id, collection="OFR-1", collection_path=coll_path, geometry=None,
                           bbox=None, datetime_iso=None, properties=props,
                           assets={"pmtiles": PM} if assets is None else assets)


def _collection(generated=GEN, assets=None):
    doc = stac.build_collection(
        collection_id="OFR-1", collection_path=COLL, title="OFR-1", description="d",
        extent={"spatial": {"bbox": [[-114, 37, -109, 42]]}, "temporal": {"interval": [[None, None]]}},
        assets={"pmtiles": PM} if assets is None else assets, item_titles={},
        fields={"ugs:series_id": "OFR-1", **({stac.GENERATED_STYLE: generated} if generated else {})})
    return doc


def test_a_generated_style_binds_as_the_default_render_and_a_style_asset(monkeypatch):
    _manifest(monkeypatch)
    item = _layer_item()
    stac.attach_renders(item)
    assert item["properties"]["ugs:renders"]["default"] == {
        "title": "Publication layer style", "assets": ["pmtiles"], "style_url": GEN,
        "generated": True}
    style = item["assets"]["style"]
    assert style["href"] == GEN and style["roles"] == ["style"] and style["type"] == "application/vnd.mapbox.style+json"


def test_an_ugs_styles_style_wins_over_the_generated_one(monkeypatch):
    _manifest(monkeypatch, EXPLICIT)
    item = _layer_item()
    stac.attach_renders(item)
    assert item["properties"]["ugs:renders"]["default"]["style_url"].endswith("styles/OFR-1__units/default.json")
    assert item["assets"]["style"]["href"].endswith("styles/OFR-1__units/default.json")


def test_no_pointer_or_no_pmtiles_asset_binds_nothing(monkeypatch):
    _manifest(monkeypatch)
    for item in (_layer_item(generated=None), _layer_item(assets={"data": {"href": "h"}})):
        stac.attach_renders(item)
        assert "ugs:renders" not in item["properties"] and "style" not in item["assets"]


def test_a_collection_carries_the_binding_as_top_level_members(monkeypatch):
    _manifest(monkeypatch)
    coll = _collection()
    stac.attach_renders(coll)
    assert coll["ugs:renders"]["default"]["style_url"] == GEN
    assert coll["assets"]["style"]["roles"] == ["style"]
    assert coll["assets"]["style"]["type"] == "application/vnd.mapbox.style+json"
    assert "properties" not in coll


def test_restyle_binds_a_layer_item_and_a_single_file_collection(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch)
    other = "ugs-publications/OFR/OFR-2"  # a publication with Items; OFR-1 is single-file
    stac.write_item(_layer_item("OFR-2__units", coll_path=other))
    stac.write_collection(_collection(), children=0)

    n = R.restyle(collection="all")

    assert n == 2
    item = json.loads(store[stac.item_object_path(other, "OFR-2__units")])
    coll = json.loads(store[stac.collection_object_path(COLL)])
    assert item["properties"]["ugs:renders"]["default"]["style_url"] == GEN
    assert coll["ugs:renders"]["default"]["style_url"] == GEN
    assert coll["assets"]["style"]["href"] == GEN
    # The Collection keeps its links and README companions; only the binding changed.
    assert [lk["rel"] for lk in coll["links"]] == [lk["rel"] for lk in _collection()["links"]]


def test_restyle_lets_a_later_ugs_styles_style_replace_the_generated_binding(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch)
    stac.write_item(_layer_item())
    R.restyle(collection="all")
    _manifest(monkeypatch, EXPLICIT)

    R.restyle(collection="all")

    item = json.loads(store[stac.item_object_path(COLL, "OFR-1__units")])
    assert item["properties"]["ugs:renders"]["default"]["style_url"].endswith("styles/OFR-1__units/default.json")


def test_restyle_binds_a_collection_style_keyed_by_the_publication_id(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch, {**EXPLICIT, "itemId": "OFR-1", "path": "styles/OFR-1/default.json"})
    stac.write_collection(_collection(), children=0)

    R.restyle(collection="all")

    coll = json.loads(store[stac.collection_object_path(COLL)])
    assert coll["ugs:renders"]["default"]["style_url"].endswith("styles/OFR-1/default.json")


def test_restyle_leaves_a_collection_that_names_no_style_untouched(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch)
    stac.write_collection(_collection(generated=None), children=0)
    before = store[stac.collection_object_path(COLL)]

    assert R.restyle(collection="all") == 0
    assert store[stac.collection_object_path(COLL)] == before


def test_the_default_scope_does_not_rewrite_publication_collections(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch)
    stac.write_collection(_collection(), children=0)
    before = store[stac.collection_object_path(COLL)]

    assert R.restyle() == 0
    assert store[stac.collection_object_path(COLL)] == before


def _build(layers):
    return sink_stac.build_publication(
        {"series_id": "OFR-1", "pub_name": "x", "pub_year": "2026"}, [], geom=None, bbox=None,
        vector_layers=["units"], layers={"units": {"rows": 1, "bbox": None, **layers}}, registered_dois=frozenset())


def test_a_layer_item_names_its_generated_style_only_when_it_is_tiled_and_styled():
    coll, items = sink_stac.build_publication(
        {"series_id": "OFR-1", "pub_name": "x", "pub_year": "2026"}, [], geom=None, bbox=None,
        vector_layers=["units", "faults"], layers={
            "units": {"rows": 1, "bbox": None, "pmtiles_layer": "units", "style": "units.style.json"},
            "faults": {"rows": 1, "bbox": None, "pmtiles_layer": "faults"}}, registered_dois=frozenset())
    by_id = {it["id"]: it for it in items}
    assert by_id["OFR-1__units"]["properties"][stac.GENERATED_STYLE].endswith(
        "geolmap/vectors/OFR-1/units.style.json")
    assert stac.GENERATED_STYLE not in by_id["OFR-1__faults"]["properties"]
    assert stac.GENERATED_STYLE not in coll


def test_a_single_file_collection_carries_the_generated_style_with_its_pmtiles():
    coll, items = _build({"pmtiles_layer": "units", "style": "units.style.json"})
    assert items == []
    assert coll[stac.GENERATED_STYLE].endswith("geolmap/vectors/OFR-1/units.style.json")
    assert "pmtiles" in coll["assets"]


def test_a_single_file_collection_gets_a_style_asset_so_portolan_sees_one(monkeypatch):
    _manifest(monkeypatch)
    coll, _ = _build({"pmtiles_layer": "units", "style": "units.style.json"})
    stac.attach_renders(coll)
    assert coll["assets"]["style"]["roles"] == ["style"]
    assert coll["ugs:renders"]["default"]["assets"] == ["pmtiles"]


def test_restyle_fails_loud_on_an_object_it_cannot_read_and_still_rebinds_the_rest(monkeypatch, capsys):
    import pytest

    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch)
    stac.write_item(_layer_item("OFR-2__units", coll_path="ugs-publications/OFR/OFR-2"))
    bad = stac.item_object_path("ugs-publications/OFR/OFR-3", "OFR-3__units")
    store[bad] = b"{not json"

    with pytest.raises(RuntimeError, match=r"could not read 1 object"):
        R.restyle(collection="all")

    assert bad in capsys.readouterr().err
    good = json.loads(store[stac.item_object_path("ugs-publications/OFR/OFR-2", "OFR-2__units")])
    assert good["properties"]["ugs:renders"]["default"]["style_url"] == GEN


def test_restyle_lists_the_scope_once(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _manifest(monkeypatch)
    stac.write_collection(_collection(), children=0)
    listed: list[str] = []
    monkeypatch.setattr(gcs, "list_paths", lambda pre: listed.append(pre) or list(store))

    R.restyle(collection="all")

    assert len(listed) == 1

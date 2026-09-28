"""End-to-end smoke validation of the pubs producer's ACTUAL STAC output.

The unit tests mock GCS and assert field-by-field; this proves the *whole* item a real load emits
is spec-valid against the published STAC 1.1 + extension schemas, and — the part no unit test covers
— that a mosaic's `derived_from` href points at exactly where its member publication's item is
authored to live, including a foreign-published member that routes to `ugs-external` (the
cross-collection 404 class from ALL-5912). This is the local seed of a CI smoke test.

Needs network to fetch the extension schemas (like test_stac_validation.py). On a machine whose
Python can't verify the schema host's cert, run with `SSL_CERT_FILE=$(python -m certifi)`.
"""
from unittest.mock import patch

import pytest

pystac = pytest.importorskip("pystac")
pytest.importorskip("jsonschema")  # pystac[validation]
from pystac.validation import validate_dict  # noqa: E402

from ugs_warehouse.core import config, stac  # noqa: E402
from ugs_warehouse.pubs import geolmap_mosaics as gm  # noqa: E402
from ugs_warehouse.pubs import identity, sink_stac  # noqa: E402

_UTAH = ([-114, 37, -109, 42], stac.bbox_polygon([-114, 37, -109, 42]))


def _validate(item: dict) -> None:
    # write_item strips the private layout key before serialization; mirror that.
    validate_dict({k: v for k, v in item.items() if k != "_collection_path"})


def _build_pub(p: dict, **kw) -> dict:
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        return sink_stac.build_item(p, [], **kw)


def _published_href(item: dict) -> str:
    """The CDN URL where this item is authored to live — its own self location on the catalog."""
    return config.public_url(stac.item_object_path(item["_collection_path"], item["id"]))


def test_fully_loaded_pub_item_is_spec_valid():
    """One map pub exercising every new path at once: COG + units + all vector layers + a companion
    table (Table ext) + an edition (Versioning ext) + footprint (Projection ext) — the item a
    fully-populated real map would emit. If any extension is mis-declared, validation fails here."""
    bbox, geom = _UTAH
    item = _build_pub(
        {"series_id": "GQ-968", "series": "GQ", "pub_name": "Geologic map of Foo",
         "pub_scale": "1:24,000", "pub_year": "1990", "pub_publisher": "USGS"},
        has_cog=True, has_units=True, geom=geom, bbox=bbox,
        vector_layers=["gems__ContactsAndFaults", "gems__MapUnitPolys"],
        companion_tables=[{"label": "gems__DescriptionOfMapUnits",
                           "columns": [{"name": "MapUnit"}, {"name": "Age"}]}],
        edition={"version": "2",
                 "predecessor_href": "https://maps-assets.geology.utah.gov/"
                                     "stac/ugs-publications/GQ/GQ-900/GQ-900.json"},
    )
    _validate(item)  # against the real STAC 1.1 + Table/Versioning/Projection schemas
    exts = item["stac_extensions"]
    assert stac.TABLE_EXT in exts and stac.VERSION_EXT in exts and stac.PROJ_EXT in exts
    assert "gems__ContactsAndFaults" in item["assets"]
    assert item["assets"]["gems__DescriptionOfMapUnits"]["table:columns"]
    assert any(lnk["rel"] == "related"
               and lnk["href"].endswith("/geologic-maps-24k/geologic-maps-24k.json")
               for lnk in item["links"])  # COG map -> its scale-tier mosaic


def test_mosaic_is_valid_and_derived_from_hrefs_match_member_item_locations():
    """The 404-class proof at the authoring layer: for each member — including a foreign-published
    one that routes to ugs-external — the mosaic's `derived_from` href equals EXACTLY where that
    member's own item is authored to live. Match here => the link resolves once both are published."""
    bbox, geom = _UTAH
    ugs = {"series_id": "GQ-968", "series": "GQ", "pub_name": "Geologic map of Foo",
           "pub_publisher": "USGS", "pub_scale": "1:24,000"}
    ext = {"series_id": "BYU-1", "series": "BYU", "pub_name": "A thesis map",
           "pub_publisher": "BYU", "pub_scale": "1:24,000"}
    ugs_item = _build_pub(ugs, has_cog=True, geom=geom, bbox=bbox)
    ext_item = _build_pub(ext, has_cog=True, geom=geom, bbox=bbox)
    # The two members live in different top-level collections:
    assert ugs_item["_collection_path"].startswith(identity.PUBLICATIONS_COLLECTION)
    assert ext_item["_collection_path"].startswith(identity.EXTERNAL_COLLECTION)

    captured: dict = {}
    with patch.object(gm.stac, "write_item", side_effect=lambda it: captured.setdefault("item", it)):
        gm._write_item("24k", ["GQ-968", "BYU-1"], gm.mosaic_object("24k"),
                       {"GQ-968": ugs, "BYU-1": ext}, bounds=[-114.0, 37.0, -109.0, 42.0])
    mosaic = captured["item"]

    _validate(mosaic)  # derived_from is a plain link; the mosaic must still be spec-valid
    assert mosaic["properties"]["ugs:topic"] == "geologic"
    derived = {lnk["href"] for lnk in mosaic["links"] if lnk["rel"] == "derived_from"}
    assert _published_href(ugs_item) in derived   # -> ugs-publications/GQ/GQ-968/GQ-968.json
    assert _published_href(ext_item) in derived   # -> ugs-external/BYU/BYU-1/BYU-1.json (NOT a 404)

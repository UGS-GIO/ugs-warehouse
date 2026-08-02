"""Schema-validate what the builders actually emit against the real STAC 1.0 + extension schemas.

The catalog is a *static* product — its JSON is the whole contract, so a malformed item/collection
or a mis-shaped extension field is a silent break. `ruff`/unit tests won't catch that; only validation
against the published schemas will. Skips cleanly if `pystac` (dev+validation extra) isn't installed.
"""
import pytest

pystac = pytest.importorskip("pystac")
pytest.importorskip("jsonschema")  # pystac[validation]
from pystac.validation import validate_dict  # noqa: E402

from ugs_warehouse.core import stac  # noqa: E402


def _validate(doc: dict) -> None:
    # validate_dict schema-checks the raw JSON against core + declared extension schemas WITHOUT
    # resolving links (our root/parent hrefs are relative and only resolve inside the live tree).
    validate_dict(doc)


def _drop_private(item: dict) -> dict:
    # write_item strips the internal layout key before serialization; mirror that here.
    return {k: v for k, v in item.items() if k != "_collection_path"}


def test_pub_item_with_cog_validates():
    """A map pub: footprint geometry (4326) + a COG asset carrying asset-level proj:code (3857).
    Exercises the projection ext v2.0.0 and the mixed-CRS asset override."""
    item = stac.build_item(
        item_id="OFR-100", collection="OFR", collection_path="ugs-publications/OFR",
        geometry=stac.bbox_polygon([-114, 37, -109, 42]), bbox=[-114, 37, -109, 42],
        datetime_iso="2026-01-01T00:00:00Z",
        properties={"title": "Test map", "ugs:series_id": "OFR-100"},
        assets={
            "cog": {"href": "https://x/OFR-100.cog.tif",
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                    "roles": ["data", "cloud-optimized"], "proj:code": "EPSG:3857",
                    "data_type": "uint8",
                    "bands": [{"name": "red"}, {"name": "green"},
                              {"name": "blue"}, {"name": "alpha"}]},
        },
        stac_extensions=[stac.PROJ_EXT], proj_epsg=4326,
    )
    d = _drop_private(item)
    assert d["stac_version"] == "1.1.0"
    assert len(d["assets"]["cog"]["bands"]) == 4  # STAC 1.1 common bands, not raster:bands
    _validate(d)


def test_aspatial_item_omits_bbox_validates():
    """A non-spatial pub: null geometry → bbox must be ABSENT (not null) per item spec."""
    item = stac.build_item(
        item_id="B-1", collection="B", collection_path="ugs-publications/B",
        geometry=None, bbox=None, datetime_iso="2026-01-01T00:00:00Z",
        properties={"title": "Bulletin"}, assets={},
    )
    d = _drop_private(item)
    assert "bbox" not in d
    _validate(d)


def test_mirrored_asset_alternate_validates():
    """A mirrored publication file: our CDN href plus the publisher's copy as an `alternate`.
    Exercises the alternate-assets ext, whose `alternate` entries each require an href."""
    item = stac.build_item(
        item_id="OFR-593", collection="OFR", collection_path="ugs-publications/OFR",
        geometry=None, bbox=None, datetime_iso="2015-01-01T00:00:00Z",
        properties={"title": "Mirrored pub"},
        assets={
            "publication": {
                "href": "https://cdn.example/pubs/files/open_file_reports/OFR-593/OFR-593.pdf",
                "type": "application/pdf", "title": "Publication", "roles": ["data"],
                "alternate:name": "Warehouse CDN",
                "alternate": {"publisher": {
                    "href": "https://ugspub.nr.utah.gov/publications/open_file_reports/OFR-593/OFR-593.pdf",
                    "alternate:name": "UGS publications site",
                    "title": "Publisher copy (ugspub.nr.utah.gov)"}},
            },
        },
        stac_extensions=[stac.ALTERNATE_ASSETS_EXT],
    )
    _validate(_drop_private(item))


def test_collection_validates_with_license_and_providers():
    """The collection builder emits license (SPDX), providers, and a rel:license link."""
    coll = stac._collection_doc(
        collection="OFR", path="ugs-publications/OFR", item_ids=["OFR-100"],
    )
    assert coll["license"] == "CC-BY-4.0"
    assert coll["providers"] and coll["providers"][0]["name"] == "Utah Geological Survey"
    assert any(link["rel"] == "license" for link in coll["links"])
    _validate(coll)

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


def _rgba_cog(path) -> None:
    """A tiny RGBA uint8 COG in EPSG:3857, the shape harvest.py produces."""
    import numpy as np
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="COG", height=32, width=32, count=4, dtype="uint8",
                       crs="EPSG:3857", transform=from_origin(-12.5e6, 5.0e6, 30, 30)) as ds:
        ds.write(np.zeros((4, 32, 32), "uint8"))
        ds.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]


def test_pub_item_with_cog_validates(tmp_path):
    """A map pub: footprint geometry (4326) + a COG asset whose fields rio-stac reads from a real
    COG header (asset-level proj:code 3857, STAC 1.1 `bands`). Exercises the projection ext v2.0.0
    and the mixed-CRS asset override."""
    rasterio = pytest.importorskip("rasterio")
    pytest.importorskip("rio_stac")
    from ugs_warehouse.pubs.sink_stac import cog_asset_fields

    _rgba_cog(tmp_path / "c.tif")
    with rasterio.open(tmp_path / "c.tif") as ds:
        fields = cog_asset_fields(ds)
    assert fields["proj:code"] == "EPSG:3857"
    assert fields["proj:shape"] == [32, 32]
    assert fields["data_type"] == "uint8"
    assert "nodata" not in fields
    assert [b["description"] for b in fields["bands"]] == ["red", "green", "blue", "alpha"]

    item = stac.build_item(
        item_id="OFR-100", collection="OFR", collection_path="ugs-publications/OFR",
        geometry=stac.bbox_polygon([-114, 37, -109, 42]), bbox=[-114, 37, -109, 42],
        datetime_iso="2026-01-01T00:00:00Z",
        properties={"title": "Test map", "ugs:series_id": "OFR-100"},
        assets={
            "cog": {"href": "https://x/OFR-100.cog.tif",
                    "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                    "roles": ["data", "cloud-optimized"], **fields},
        },
        stac_extensions=[stac.PROJ_EXT], proj_epsg=4326,
    )
    d = _drop_private(item)
    assert d["stac_version"] == "1.1.0"
    assert not any(k.startswith(("raster:", "eo:")) for k in d["assets"]["cog"])
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


def test_item_with_foreign_keys_validates():
    """`ugs:foreign_keys` is a UGS-prefixed custom field, not a declared extension. STAC allows
    prefixed extras without a schema, so adding `reference.href` (#214) must not break validation."""
    from ugs_warehouse.vector import related

    fk = related._foreign_key(
        {"sourceColumn": "quad", "targetDomainTopic": "mapping_quads_24k", "targetColumn": "quad_id"})
    assert fk["reference"]["href"].endswith("/mapping_quads_24k/mapping_quads_24k.parquet")

    item = stac.build_item(
        item_id="enmin_ucrc_wells", collection="emp",
        collection_path="ugs-serving-topics/emp",
        geometry=stac.bbox_polygon([-114, 37, -109, 42]), bbox=[-114, 37, -109, 42],
        datetime_iso="2026-01-01T00:00:00Z",
        properties={"title": "UCRC Wells"},
        assets={"data": {"href": "https://x/enmin_ucrc_wells.parquet",
                         "type": "application/vnd.apache.parquet",
                         "roles": ["data"], "ugs:foreign_keys": [fk]}},
    )
    _validate(_drop_private(item))


def test_an_undated_publication_has_a_valid_flagged_interval():
    """STAC has no "unknown" date: a pub with no year gets the source's interval and a flag."""
    from ugs_warehouse.pubs import sink_stac as pubs_sink

    item = _drop_private(pubs_sink.build_item(
        {"series_id": "MD-134-6", "series": "MD", "pub_year": "",
         "pub_name": "List of Beaver County Properties"}, [], override={}))
    props = item["properties"]
    assert props["datetime"] is None
    assert props["start_datetime"] == pubs_sink.EARLIEST_RECORD
    assert props["end_datetime"] > props["start_datetime"]
    assert props["ugs:date_unknown"] is True
    _validate(item)

"""Selective mirror of publication source files (#120) — path mapping, planning, asset rewrite."""
from __future__ import annotations

from ugs_warehouse.core import stac
from ugs_warehouse.pubs import identity, mirror, sink_stac

UGSPUB = identity.UGSPUB


def test_object_path_preserves_the_legacy_path():
    assert identity.pub_file_object(UGSPUB + "open_file_reports/OFR-593/OFR-593.pdf") \
        == "pubs/files/open_file_reports/OFR-593/OFR-593.pdf"


def test_object_path_keeps_same_named_files_in_different_series_apart():
    """Two pubs both attach a `Table1.xls`; path-preserving means they can't collide."""
    a = identity.pub_file_object(UGSPUB + "open_file_reports/OFR-593/Table1.xls")
    b = identity.pub_file_object(UGSPUB + "open_file_reports/OFR-100/Table1.xls")
    assert a != b


def test_escaped_and_raw_spaces_mirror_to_one_object():
    """The pubs DB is inconsistent about encoding; both spellings are the same file."""
    escaped = identity.pub_file_object(UGSPUB + "maps/M%20299/plate%201.pdf")
    raw = identity.pub_file_object(UGSPUB + "maps/M 299/plate 1.pdf")
    assert escaped == raw == "pubs/files/maps/M 299/plate 1.pdf"
    # ...and the href re-encodes, so what lands in the item is a usable URL.
    assert "%20" in identity.pub_file_url(escaped)
    assert " " not in identity.pub_file_url(escaped)


def test_foreign_hosts_and_ambiguous_urls_are_not_mirrored():
    assert identity.pub_file_object("https://pubs.usgs.gov/of/2019/1234/report.pdf") is None
    assert identity.pub_file_object(UGSPUB + "data.php?pub=OFR-593") is None  # path ≠ the bytes
    assert identity.pub_file_object(UGSPUB + "a/../../etc/passwd") is None    # escapes the prefix
    assert identity.pub_file_object("") is None


def test_plan_selects_only_the_requested_series():
    pubs = [{"series_id": "OFR-760", "pub_url": "open_file_reports/ofr-760/ofr-760.pdf"},
            {"series_id": "OFR-999", "pub_url": "open_file_reports/ofr-999/ofr-999.pdf"}]
    att = [{"series_id": "OFR-760", "pub_url": "geotifs/24k/OFR-760.zip"},
           {"series_id": "OFR-999", "pub_url": "open_file_reports/ofr-999/tables.zip"}]
    got = mirror.plan(pubs, att, {"OFR-760"})
    assert [sid for sid, _, _ in got] == ["OFR-760", "OFR-760"]
    assert {obj for _, _, obj in got} == {
        "pubs/files/open_file_reports/ofr-760/ofr-760.pdf",
        "pubs/files/geotifs/24k/OFR-760.zip",
    }


def test_plan_takes_everything_when_unfiltered_and_dedupes_shared_files():
    """A GeoTIFF zip attached to two pubs is one object — download it once."""
    pubs = [{"series_id": "M-1", "pub_url": "maps/m-1/m-1.pdf"},
            {"series_id": "M-2", "pub_url": "maps/m-2/m-2.pdf"}]
    att = [{"series_id": "M-1", "pub_url": "geotifs/100k/Shared.zip"},
           {"series_id": "M-2", "pub_url": "geotifs/100k/Shared.zip"}]
    objs = [obj for _, _, obj in mirror.plan(pubs, att, None)]
    assert len(objs) == len(set(objs)) == 3


def test_plan_skips_pubs_with_no_downloadable_url():
    assert mirror.plan([{"series_id": "SNT-58-2", "pub_url": ""}], [], None) == []


def _item(mirrored):
    return sink_stac.build_item(
        # full_citation set so build_item takes it as the description instead of reaching into the
        # bucket for the previously-published one (preserve-on-empty).
        {"series_id": "OFR-593", "pub_name": "Test", "pub_year": "2015", "full_citation": "Test 2015",
         "pub_url": "open_file_reports/OFR-593/OFR-593.pdf"},
        [{"series_id": "OFR-593", "extra_data": "GeoTIFF zip",
          "pub_url": "geotifs/100k/RushValley.zip"}],
        mirrored=mirrored,
    )


def test_mirrored_asset_serves_from_the_cdn_and_keeps_the_publisher_copy():
    obj = "pubs/files/open_file_reports/OFR-593/OFR-593.pdf"
    pub = _item({obj})["assets"]["publication"]
    assert pub["href"] == identity.pub_file_url(obj)
    assert pub["alternate"]["publisher"]["href"] == UGSPUB + "open_file_reports/OFR-593/OFR-593.pdf"
    assert pub["type"] == "application/pdf"  # media type still reads off the filename


def test_unmirrored_assets_stay_pointed_at_the_publisher():
    """The selective mirror is partial by design — a file we don't hold must not claim a CDN href."""
    item = _item({"pubs/files/open_file_reports/OFR-593/OFR-593.pdf"})
    zip_asset = item["assets"]["geotiff_zip"]
    assert zip_asset["href"] == UGSPUB + "geotifs/100k/RushValley.zip"
    assert "alternate" not in zip_asset


def test_extension_is_declared_only_when_something_is_mirrored():
    assert stac.ALTERNATE_ASSETS_EXT in _item({"pubs/files/open_file_reports/OFR-593/OFR-593.pdf"})["stac_extensions"]
    assert stac.ALTERNATE_ASSETS_EXT not in (_item(None).get("stac_extensions") or [])
    assert stac.ALTERNATE_ASSETS_EXT not in (_item(set()).get("stac_extensions") or [])

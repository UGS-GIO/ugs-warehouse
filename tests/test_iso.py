"""STAC → ISO 19139 metadata export."""
import xml.etree.ElementTree as ET

from ugs_warehouse.core import iso

ITEM = {
    "id": "hazards_qfaults",
    "bbox": [-114.0, 37.0, -109.0, 42.0],
    "properties": {
        "title": "Quaternary Faults", "description": "Fault traces",
        "datetime": "2026-06-01T00:00:00Z", "proj:code": "EPSG:4326",
    },
    "assets": {
        "data": {"href": "https://x/a.parquet", "title": "GeoParquet"},
        "ducklake": {"href": "gs://b/x", "title": "DuckLake"},  # non-http → skipped
    },
}


def test_well_formed_xml():
    ET.fromstring(iso.stac_to_iso19139(ITEM))  # raises if malformed


def test_contains_key_fields():
    xml = iso.stac_to_iso19139(ITEM)
    assert "Quaternary Faults" in xml
    assert "EPSG:4326" in xml
    assert "-114.0" in xml and "42.0" in xml  # bbox bounds
    assert "Utah Geological Survey" in xml
    assert "https://x/a.parquet" in xml       # http asset → transfer option
    assert "gs://b/x" not in xml              # gs:// asset excluded (not browser-fetchable)


def test_keywords_and_topic_category():
    item = {
        "id": "x", "bbox": [0, 1, 2, 3],
        "properties": {"keywords": ["faults", "quaternary"], "ugs:topic_category": "geoscientificInformation"},
        "assets": {},
    }
    xml = iso.stac_to_iso19139(item)
    ET.fromstring(xml)
    assert "<gmd:descriptiveKeywords>" in xml
    assert "faults" in xml and "quaternary" in xml
    assert "geoscientificInformation" in xml


def test_topic_category_defaults():
    xml = iso.stac_to_iso19139({"id": "x", "bbox": [], "properties": {}, "assets": {}})
    assert "geoscientificInformation" in xml  # default when unset


def test_escapes_special_chars():
    xml = iso.stac_to_iso19139({"id": "x", "properties": {"title": "A & B <z>"}, "bbox": [], "assets": {}})
    ET.fromstring(xml)  # still well-formed
    assert "&amp;" in xml and "&lt;" in xml

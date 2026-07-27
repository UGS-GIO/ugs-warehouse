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


def test_topic_category_omitted_when_uncurated():
    """#53: no blanket default — an uncurated topic emits no <gmd:topicCategory> at all."""
    xml = iso.stac_to_iso19139({"id": "x", "bbox": [], "properties": {}, "assets": {}})
    ET.fromstring(xml)
    assert "topicCategory" not in xml
    assert "geoscientificInformation" not in xml


# --- #52: republished federal data must not be attributed to UGS -------------------------

USGS = "U.S. Geological Survey"
DISCLAIMER = "This information is preliminary and subject to revision."


def _federal_item(**props):
    return {"id": "hazards_debrisflow_cottonwood_i15", "bbox": [0, 1, 2, 3],
            "properties": {"title": "Cottonwood debris flow", **props}, "assets": {}}


def test_dataset_contact_uses_curated_point_of_contact():
    xml = iso.stac_to_iso19139(_federal_item(**{"ugs:point_of_contact": USGS}))
    root = ET.fromstring(xml)
    gmd = "{http://www.isotc211.org/2005/gmd}"
    # <gmd:contact> is the METADATA contact — still us, we authored the record.
    meta_org = root.find(f"{gmd}contact/{gmd}CI_ResponsibleParty/{gmd}organisationName/"
                         "{http://www.isotc211.org/2005/gco}CharacterString")
    assert meta_org.text == iso.ORG
    # <gmd:pointOfContact> is the DATASET contact — the originating agency.
    ident = root.find(f"{gmd}identificationInfo/{gmd}MD_DataIdentification")
    ds_org = ident.find(f"{gmd}pointOfContact/{gmd}CI_ResponsibleParty/{gmd}organisationName/"
                        "{http://www.isotc211.org/2005/gco}CharacterString")
    assert ds_org.text == USGS


def test_dataset_contact_falls_back_to_ugs():
    xml = iso.stac_to_iso19139(_federal_item())
    assert xml.count(iso.ORG) == 2  # metadata contact + dataset contact


def test_use_constraints_and_lineage_reach_the_record():
    xml = iso.stac_to_iso19139(_federal_item(**{
        "ugs:use_constraints": DISCLAIMER, "ugs:lineage": "Derived from USGS runout modeling."}))
    ET.fromstring(xml)
    assert DISCLAIMER in xml
    assert "<gmd:otherConstraints>" in xml
    assert "otherRestrictions" in xml       # the code that makes otherConstraints meaningful
    assert "Derived from USGS runout modeling." in xml
    assert "<gmd:dataQualityInfo>" in xml


def test_constraints_and_lineage_omitted_when_absent():
    xml = iso.stac_to_iso19139(_federal_item())
    assert "resourceConstraints" not in xml
    assert "dataQualityInfo" not in xml


def test_escapes_special_chars():
    xml = iso.stac_to_iso19139({"id": "x", "properties": {"title": "A & B <z>"}, "bbox": [], "assets": {}})
    ET.fromstring(xml)  # still well-formed
    assert "&amp;" in xml and "&lt;" in xml

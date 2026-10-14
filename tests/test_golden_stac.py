"""Golden JSON for the publication and serving-topic builders.

The shared `core/stac` helpers are refactored under these: any change to the published Collection
or Item documents shows up as a diff against `tests/golden/stac_docs.json`. Regenerate it on
purpose with `UPDATE_GOLDEN=1 pytest tests/test_golden_stac.py`.
"""
import json
import os
from pathlib import Path

import pytest

from ugs_warehouse.core import stac
from ugs_warehouse.pubs import sink_stac as pub_sink
from ugs_warehouse.vector import sink_stac as vec_sink
from ugs_warehouse.vector.topics import Topic

GOLDEN = Path(__file__).parent / "golden" / "stac_docs.json"

FOOT = {"type": "Polygon", "coordinates": [[[-112.5, 40.5], [-112.0, 40.5], [-112.0, 41.0],
                                             [-112.5, 41.0], [-112.5, 40.5]]]}
BBOX = [-112.5, 40.5, -112.0, 41.0]
P = {"series_id": "OFR-784DM", "pub_name": "Interim Geologic Map of Mills Junction",
     "pub_year": "2026", "pub_url": "https://ugspub.nr.utah.gov/publications/ofr/ofr-784dm.pdf",
     "pub_publisher": "Utah Geological Survey", "series": "Open File Report", "pub_scale": "1:24,000",
     "full_citation": "Utah Geological Survey, 2026, Interim Geologic Map of Mills Junction: OFR-784DM."}
ATT = [{"pub_url": "https://ugspub.nr.utah.gov/publications/ofr/ofr-784dm.zip", "extra_data": "GIS Data - Zip"}]
DMU_COLS = [{"name": "MapUnit", "type": "string"}, {"name": "Age", "type": "string"}]
MUP_TO_DMU = {"origin": "geo__MapUnitPolys", "origin_cols": ["MapUnit"],
              "destination": "geo__DMU", "destination_cols": ["MapUnit"],
              "cardinality": "many_to_one", "detected_by": "inferred", "confidence": 100, "overlap": 100.0}
LAYER_BBOX = [-112.4, 40.6, -112.1, 40.9]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(stac, "prior_collection_field", lambda *a, **k: None)
    monkeypatch.setattr(stac, "prior_property", lambda *a, **k: None)


def _pub(**kw) -> dict:
    coll, items = pub_sink.build_publication(P, ATT, geom=FOOT, bbox=BBOX, fp_source="cog", **kw)
    return {"collection": coll, "items": {it["id"]: it for it in items}}


def _gems() -> dict:
    """A GeMS map: plate, units, tiled and plain layers, tables, a relationship and a reference table."""
    return _pub(
        has_cog=True, cog_fields={"proj:code": "EPSG:3857"}, has_units=True, has_thumb=True,
        has_cover=True, mosaic_tier="24k",
        vector_layers=["geo__MapUnitPolys", "geo__Faults"],
        companion_tables=[{"label": "geo__DMU", "columns": DMU_COLS},
                          {"label": "geo__Glossary", "columns": None}],
        layers={"geo__MapUnitPolys": {"rows": 3, "bbox": LAYER_BBOX, "kind": "layer",
                                      "geometry_types": ["Polygon"], "proj_code": "EPSG:26912",
                                      "pmtiles_layer": "geo__MapUnitPolys"},
                "geo__Faults": {"rows": 2, "bbox": LAYER_BBOX, "geometry_types": ["LineString"],
                                "proj_wkt2": "PROJCRS[\"NAD83 / UTM zone 12N\"]"},
                "geo__DMU": {"rows": 41, "kind": "table"},
                "geo__Glossary": {"rows": 9}},
        relationships=[MUP_TO_DMU])


def _single_file() -> dict:
    """A publication whose only data is one tiled layer, carried on the Collection."""
    return _pub(vector_layers=["faults"],
                layers={"faults": {"rows": 3, "bbox": LAYER_BBOX, "geometry_types": ["LineString"],
                                   "proj_code": "EPSG:26912", "pmtiles_layer": "faults"}})


def _plate() -> dict:
    return _pub(has_cog=True, cog_fields={"proj:code": "EPSG:3857"}, has_thumb=True)


def _serving_topic(monkeypatch) -> dict:
    written: list[dict] = []
    monkeypatch.setattr(vec_sink, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(vec_sink, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(vec_sink, "_table_columns", lambda c, v: [{"name": "uwi", "type": "string"}])
    monkeypatch.setattr(vec_sink, "_publication_datetime", lambda c, v: "2026-01-02T03:04:05+00:00")
    monkeypatch.setattr(vec_sink.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(vec_sink.stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(vec_sink.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(vec_sink.gcs, "exists", lambda p: False)
    monkeypatch.setattr(vec_sink.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(vec_sink.stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(vec_sink.stac, "stamp_file_meta", lambda i: None)
    monkeypatch.setattr(vec_sink.stac, "write_item", lambda i: written.append(i) or "stac/path.json")
    related = {"assets": {"boxes": {"href": "h", "type": "application/vnd.apache.parquet",
                                    "roles": ["data", "related"], "title": "UCRC core boxes",
                                    "table:columns": [{"name": "uwi"}]}},
               "links": [{"rel": "related", "href": "u", "type": "application/geo+json", "title": "quads"}],
               "foreign_keys": [{"fields": ["quad"],
                                 "reference": {"resource": "mapping_quads_24k",
                                               "href": "https://cdn.example/quads.parquet",
                                               "fields": ["quad_id"]}}]}
    vec_sink.write(Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current"), None, "v",
                   related=related)
    (item,) = written
    return item


def test_published_documents_match_the_golden_json(monkeypatch):
    docs = {"gems": _gems(), "single_file": _single_file(), "plate": _plate(),
            "serving_topic": _serving_topic(monkeypatch)}
    got = json.dumps(docs, indent=2, default=str) + "\n"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(got)
    assert got == GOLDEN.read_text()

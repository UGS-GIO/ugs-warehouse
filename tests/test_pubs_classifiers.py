"""Pure helpers the pubs catalog derives fields from: topic, county, graph authors/keywords."""
import pytest

from ugs_warehouse.pubs import counties, graph, topic


@pytest.mark.parametrize(("title", "expected"), [
    ("Geologic map of the Kolob coal field", "mineral-energy"),  # a theme beats the generic rule
    ("Landslide inventory map", "hazards"),
    ("Geologic map of the Ogden quadrangle", "geologic"),
    ("Annual report", "other"),
    (None, "other"),
])
def test_topic_classify(title, expected):
    assert topic.classify(title) == expected


def test_topic_reads_keywords_too():
    assert topic.classify("Utah", keywords="groundwater") == "hydro"


def test_county_of_a_point():
    pytest.importorskip("shapely")
    assert counties.county_of(40.7608, -111.891) == "Salt Lake"
    assert counties.county_of_pub({"lat": "37.1", "longitude": "-113.58"}) == "Washington"


@pytest.mark.parametrize(("lat", "lon"), [
    (0, 0), (40.0, -115.0), (35.0, -111.0), ("nan", "-111"), (None, None), ("x", "y"),
])
def test_no_county_outside_utah_or_without_coords(lat, lon):
    assert counties.county_of(lat, lon) is None


def test_graph_authors_primary_first_and_deduplicated():
    p = {"pub_author": "Grant Willis", "pub_sec_author": "Jones and Lee; grant willis"}
    assert graph._authors(p) == ["Grant Willis", "Jones", "Lee"]


@pytest.mark.parametrize(("sec", "expected"), [
    ("Elizabeth L. Miller, and Jeffrey Lee", ["Elizabeth L. Miller", "Jeffrey Lee"]),
    ("C.L. Hubs, R.R. Miller and E. Antevs", ["C.L. Hubs", "R.R. Miller", "E. Antevs"]),
    ("W. A. Yonkee, and Thomas C. Chidsey, Jr", ["W. A. Yonkee", "Thomas C. Chidsey, Jr"]),
    ("S.W. Hazen, Jr.", ["S.W. Hazen, Jr."]),
    ("John Doe, V, and Jane Smith", ["John Doe, V", "Jane Smith"]),
    ("Jr., Jane Smith", ["Jane Smith"]),
])
def test_graph_authors_real_secondary_formats(sec, expected):
    assert graph._authors({"pub_sec_author": sec}) == expected


def test_graph_topics_split_keywords():
    assert graph._topics({"keywords": "faults; Utah, , maps"}) == ["faults", "Utah", "maps"]


def test_graph_slug():
    assert graph._slug(" Great Salt Lake! ") == "great-salt-lake"

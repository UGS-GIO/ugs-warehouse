"""Quad-based edition graph: grouping, ordering, the conservative exclusion rules, and the
ingest wiring that hands each pub's edition to `sink_stac.build_item`."""
from unittest.mock import patch

from ugs_warehouse.pubs import editions


def test_same_quad_same_scale_links_and_deprecates_including_a_dm_variant():
    pubs = [
        {"series_id": "M-100", "quad_name": "Alta", "pub_year": "1980", "pub_scale": "1:24,000"},
        {"series_id": "M-100DM", "quad_name": "Alta", "pub_year": "2005", "pub_scale": "1:24,000"},
        {"series_id": "OFR-9", "quad_name": "Provo", "pub_year": "1999", "pub_scale": "1:24,000"},
    ]
    g = editions.edition_graph(pubs)

    assert g["M-100"]["version"] == "1980"
    assert g["M-100"]["deprecated"] is True
    assert g["M-100"]["predecessor_href"] is None
    assert g["M-100"]["successor_href"].endswith("/M-100DM/M-100DM.json")

    # a DM edition is its own publication, not collapsed into its base series_id.
    assert g["M-100DM"]["version"] == "2005"
    assert g["M-100DM"]["deprecated"] is False
    assert g["M-100DM"]["predecessor_href"].endswith("/M-100/M-100.json")
    assert g["M-100DM"]["successor_href"] is None

    # a lone map for its quad: present (so its own `version` still stamps), never linked.
    assert g["OFR-9"]["deprecated"] is False
    assert g["OFR-9"]["predecessor_href"] is None and g["OFR-9"]["successor_href"] is None


def test_same_quad_different_scale_is_not_linked():
    pubs = [
        {"series_id": "M-1", "quad_name": "Salina Quad", "pub_year": "1986", "pub_scale": "1:24,000"},
        {"series_id": "OFR-1", "quad_name": "Salina Quad", "pub_year": "2016", "pub_scale": "1:250,000"},
    ]
    g = editions.edition_graph(pubs)

    # same quad, different scale tier -> two separate (quad, scale) groups, each with 1 member.
    assert g["M-1"]["deprecated"] is False and g["M-1"]["successor_href"] is None
    assert g["OFR-1"]["deprecated"] is False and g["OFR-1"]["predecessor_href"] is None


def test_missing_or_unparseable_scale_excludes_from_every_group(capsys):
    pubs = [
        {"series_id": "SS-163", "quad_name": "Tickville Spring Quad", "pub_year": "2018",
         "pub_scale": ""},
        {"series_id": "M-214", "quad_name": "Tickville Spring Quad", "pub_year": "2005",
         "pub_scale": "1:24,000"},
    ]
    g = editions.edition_graph(pubs)

    assert "SS-163" not in g  # blank scale -> not placed in any group at all
    assert g["M-214"]["deprecated"] is False  # the only validly-scaled member of its group

    out = capsys.readouterr().out
    assert "SS-163" in out and "pub_scale" in out


def test_missing_or_tied_pub_year_is_logged_and_left_unlinked(capsys):
    pubs = [
        {"series_id": "M-1", "quad_name": "Escalante Quad", "pub_year": "1990",
         "pub_scale": "1:24,000"},
        {"series_id": "OFR-1", "quad_name": "Escalante Quad", "pub_year": "",
         "pub_scale": "1:24,000"},
        {"series_id": "M-2", "quad_name": "Escalante Quad", "pub_year": "2010",
         "pub_scale": "1:24,000"},
        {"series_id": "M-3", "quad_name": "Escalante Quad", "pub_year": "2010",
         "pub_scale": "1:24,000"},
    ]
    g = editions.edition_graph(pubs)

    assert "OFR-1" not in g  # blank pub_year -> ambiguous position
    assert "M-2" not in g  # tied with M-3 -> ambiguous which is newer
    assert "M-3" not in g
    # M-1 is the only unambiguous year in the group -- alone by elimination, not linked.
    assert g["M-1"]["deprecated"] is False
    assert g["M-1"]["successor_href"] is None

    out = capsys.readouterr().out
    assert "OFR-1" in out
    assert "M-2" in out and "M-3" in out


def test_build_catalog_wires_edition_into_build_item():
    from ugs_warehouse.pubs.ingest import build_catalog

    with patch("ugs_warehouse.pubs.source.read_pubs") as mock_read, \
         patch("ugs_warehouse.pubs.source.read_attachments", return_value=[]), \
         patch("ugs_warehouse.pubs.ingest._ids_with_suffix", return_value=set()), \
         patch("ugs_warehouse.pubs.ingest._contents_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.ingest._threed_classes_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.ingest._overrides_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.ingest._build_search_corpus"), \
         patch("ugs_warehouse.pubs.ingest._unit_ids", return_value=set()), \
         patch("ugs_warehouse.pubs.ingest._mirrored_files", return_value=set()), \
         patch("ugs_warehouse.pubs.ingest._cog_footprints", return_value={}), \
         patch("ugs_warehouse.pubs.sink_stac.build_item") as mock_build, \
         patch("ugs_warehouse.core.stac.attach_renders"), \
         patch("ugs_warehouse.core.stac.attach_iso"), \
         patch("ugs_warehouse.core.styles.warm"), \
         patch("ugs_warehouse.core.stac.write_item"), \
         patch("ugs_warehouse.core.stac.refresh_catalog"):

        mock_read.return_value = [
            {"series_id": "M-1", "quad_name": "Escalante Quad", "pub_year": "1990",
             "pub_scale": "1:24,000"},
            {"series_id": "M-2", "quad_name": "Escalante Quad", "pub_year": "2010",
             "pub_scale": "1:24,000"},
        ]

        count = build_catalog(skip_refresh=True)

        assert count == 2
        assert mock_build.call_count == 2
        by_sid = {c.args[0]["series_id"]: c.kwargs["edition"] for c in mock_build.call_args_list}
        assert by_sid["M-1"]["deprecated"] is True
        assert by_sid["M-1"]["successor_href"].endswith("/M-2/M-2.json")
        assert by_sid["M-2"]["deprecated"] is False
        assert by_sid["M-2"]["predecessor_href"].endswith("/M-1/M-1.json")

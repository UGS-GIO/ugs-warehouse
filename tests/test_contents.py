"""Survey Notes TOC parser (pubs.contents) — hermetic, operates on extracted text (no PDF/network)."""
from ugs_warehouse.pubs import contents

# A real Survey Notes contents page (raw pdftotext), masthead noise interleaved.
TOC_TEXT = """\
Contents
Secrets of Great Salt Lake...................................................... 1
New Energy & Minerals Interactive Web Applications..... 4
Energy & Mineral News................................................................... 6
Teacher's Corner............................................................................... 7
GeoSights............................................................................................ 8
Glad You Asked................................................................................10
Survey News..........................................................................................12
Design | John Good
State of Utah
  Spencer J. Cox, Governor
"""


def test_parse_extracts_titles_and_pages():
    out = contents._parse(TOC_TEXT)
    titles = [e["title"] for e in out]
    assert "Secrets of Great Salt Lake" in titles
    assert "Glad You Asked" in titles
    by_title = {e["title"]: e["page"] for e in out}
    assert by_title["Survey News"] == 12
    # Masthead lines (Design, State of Utah, Governor) are skipped.
    assert "State of Utah" not in titles
    assert all("governor" not in t.lower() for t in titles)


def test_parse_handles_back_cover():
    out = contents._parse("New Publications..............................back cover\n")
    assert out == [{"title": "New Publications", "page": None}]


def test_extract_returns_empty_under_min_entries(monkeypatch):
    # A page that yields too few dot-leader lines → treated as a failed parse (graceful []).
    monkeypatch.setattr(contents.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "Just prose, no contents here.\n"})())
    assert contents.extract("/nope.pdf") == []

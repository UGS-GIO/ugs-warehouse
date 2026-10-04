"""The full-text database is rebuilt only when its inputs changed."""
from __future__ import annotations

import pytest

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import fts, identity, source

PUB = {"series_id": "B-10", "pub_name": "Bulletin 10", "series": "Bulletin", "pub_year": 1950,
       "pub_url": "bulletins/b-10.pdf"}


@pytest.fixture
def builds(monkeypatch):
    built: list[list[str]] = []

    def fake_build_db(paths, meta):
        built.append(paths)
        gcs.put_bytes(b"db", fts.FTS_OBJECT, content_type="application/octet-stream")
        return len(paths)

    monkeypatch.setattr(fts, "_build_db", fake_build_db)
    monkeypatch.setattr(source, "read_pubs", lambda: [dict(PUB)])
    gcs.put_bytes(b"granite", identity.pub_fulltext_object("B-10"), content_type="text/plain")
    return built


def test_a_second_run_with_nothing_changed_skips(builds):
    assert fts.build() == 1
    assert fts.build() == 0
    assert len(builds) == 1


def test_a_rewritten_sidecar_rebuilds(builds):
    fts.build()
    gcs.put_bytes(b"basalt", identity.pub_fulltext_object("B-10"), content_type="text/plain")
    fts.build()
    assert len(builds) == 2


def test_a_changed_title_rebuilds(builds, monkeypatch):
    fts.build()
    monkeypatch.setattr(source, "read_pubs", lambda: [dict(PUB, pub_name="Bulletin 10, revised")])
    fts.build()
    assert len(builds) == 2


def test_force_and_a_missing_database_rebuild(builds):
    fts.build()
    fts.build(force=True)
    gcs.delete(fts.FTS_OBJECT)
    fts.build()
    assert len(builds) == 3

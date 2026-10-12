"""The cover/full-text job records a PDF with no text layer, so a later run does not fetch it again."""
from __future__ import annotations

import pytest

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import contents, identity, thumbs

PUB = {"series_id": "B-10", "pub_url": "bulletins/b-10.pdf"}


def test_a_pdf_without_text_is_recorded_and_skipped_next_run(monkeypatch):
    gcs.put_bytes(b"cover", thumbs.cover_object("B-10"), content_type="image/webp")
    downloads: list[str] = []

    def fake_download(url, dst, **_):
        downloads.append(url)
        open(dst, "wb").close()

    monkeypatch.setattr(thumbs, "download", fake_download)
    monkeypatch.setattr(contents, "full_text", lambda pdf: "")

    assert thumbs.thumb_one(PUB) == "ok"
    assert gcs.get_bytes(identity.pub_fulltext_object("B-10")) == b""

    assert thumbs.thumb_one(PUB) == "skip:exists"
    assert len(downloads) == 1

    # --force retries it
    thumbs.thumb_one(PUB, force=True)
    assert len(downloads) == 2


def test_the_skip_check_uses_one_listing(monkeypatch):
    gcs.put_bytes(b"cover", thumbs.cover_object("B-10"), content_type="image/webp")
    gcs.put_bytes(b"text", identity.pub_fulltext_object("B-10"), content_type="text/plain")
    monkeypatch.setattr(gcs, "exists", lambda _: pytest.fail("HEAD per pub"))

    assert thumbs.thumb_one(PUB, existing=thumbs.existing_outputs()) == "skip:exists"

"""The cover/full-text job records a PDF with no text layer, so a later run does not fetch it again."""
from __future__ import annotations

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import contents, identity, thumbs

PUB = {"series_id": "B-10", "pub_url": "bulletins/b-10.pdf"}


def test_a_pdf_without_text_is_recorded_and_skipped_next_run(monkeypatch):
    gcs.put_bytes(b"cover", thumbs.cover_object("B-10"), content_type="image/webp")
    downloads: list[str] = []
    monkeypatch.setattr(thumbs, "download",
                        lambda url, dst, **_: downloads.append(url) or open(dst, "wb").close())
    monkeypatch.setattr(contents, "full_text", lambda pdf: "")

    assert thumbs.thumb_one(PUB) == "ok"
    assert gcs.get_bytes(identity.pub_fulltext_object("B-10")) == b""

    assert thumbs.thumb_one(PUB) == "skip:exists"
    assert len(downloads) == 1

    # --force retries it
    thumbs.thumb_one(PUB, force=True)
    assert len(downloads) == 2

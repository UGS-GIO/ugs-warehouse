"""Catalog JSON is stored gzipped: 25:1 on the big indexes, and GCS transcodes it back for any
client that doesn't send `Accept-Encoding: gzip`, so consumers see the same document either way.
"""
from __future__ import annotations

import gzip
import json
from unittest.mock import MagicMock, patch

import pytest

from ugs_warehouse.core import gcs


def _captured(**kwargs):
    with patch.object(gcs, "_store", return_value=object()), \
         patch.object(gcs.obs, "put") as put:
        meta = gcs.put_bytes(b'{"a": 1}', "some/path.json", content_type="application/json",
                             **kwargs)
    return put.call_args, meta


def test_compress_stores_gzip_bytes_and_tags_the_encoding() -> None:
    call, _ = _captured(compress=True)
    body = call.args[2]
    assert gzip.decompress(body) == b'{"a": 1}'
    assert call.kwargs["attributes"]["Content-Encoding"] == "gzip"


def test_uncompressed_write_sets_no_encoding_header() -> None:
    call, _ = _captured()
    assert call.args[2] == b'{"a": 1}'
    assert "Content-Encoding" not in call.kwargs["attributes"]


def test_file_meta_describes_the_document_not_the_stored_bytes() -> None:
    # file:size / file:checksum describe what a consumer receives, so they must match the plain
    # bytes — otherwise a client verifying a downloaded item would see a mismatch.
    _, plain = _captured()
    _, gzipped = _captured(compress=True)
    assert plain == gzipped
    assert plain.size == len(b'{"a": 1}')


def test_get_bytes_gunzips_a_compressed_body() -> None:
    doc = json.dumps({"hello": "world"}).encode()

    class _Resp:
        def bytes(self):
            return gzip.compress(doc)

    with patch.object(gcs, "_store", return_value=object()), \
         patch.object(gcs.obs, "get", return_value=_Resp()):
        assert gcs.get_bytes("some/path.json") == doc


def test_get_bytes_passes_plain_bodies_through() -> None:
    doc = json.dumps({"hello": "world"}).encode()

    class _Resp:
        def bytes(self):
            return doc

    with patch.object(gcs, "_store", return_value=object()), \
         patch.object(gcs.obs, "get", return_value=_Resp()):
        assert gcs.get_bytes("some/path.json") == doc


def test_get_bytes_reraises_404_without_falling_back() -> None:
    # A genuine 404 (obstore raises FileNotFoundError) must propagate as-is — no wasted second
    # round-trip, and callers that catch FileNotFoundError (review_catalog, prior_property) keep
    # returning "absent" rather than erroring. (#341)
    gcs_client = MagicMock()
    with patch.object(gcs, "_store", return_value=object()), \
         patch.object(gcs.obs, "get", side_effect=FileNotFoundError("no such object")), \
         patch.object(gcs, "_gcs_client", gcs_client):
        with pytest.raises(FileNotFoundError):
            gcs.get_bytes("stac/missing.json")
    gcs_client.assert_not_called()


def test_exists_returns_false_on_404_without_falling_back() -> None:
    gcs_client = MagicMock()
    with patch.object(gcs, "_store", return_value=object()), \
         patch.object(gcs.obs, "head", side_effect=FileNotFoundError("no such object")), \
         patch.object(gcs, "_gcs_client", gcs_client):
        assert gcs.exists("warehouse/stac/missing.json") is False
    gcs_client.assert_not_called()


def test_store_asks_gcs_for_the_stored_gzip_bytes(monkeypatch) -> None:
    # Without it GCS transcodes a gzipped object and drops Content-Length, which obstore can't read.
    made = {}
    monkeypatch.setattr(gcs, "_cached_stores", {})
    monkeypatch.setattr(gcs, "GCSStore", lambda **kw: made.update(kw) or object())
    monkeypatch.setattr(gcs, "_credentials", lambda: "google-auth")
    gcs._store("b")
    assert made["client_options"]["default_headers"]["Accept-Encoding"] == "gzip"
    # obstore's own token request would carry the header and fail on the gzipped reply.
    assert made["credential_provider"] == "google-auth"

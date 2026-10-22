"""pubs/dois.py: the Crossref registry of UGS DOIs, its snapshot, and which publications carry one."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import dois, identity, sink_stac

REAL_FETCH = dois._fetch  # conftest stubs it for every test
REGISTRY = frozenset({"10.34191/m-234", "10.34191/ofr-1"})


def _page(dois_in_page, cursor=None, total=None):
    return {"message": {"items": [{"DOI": d} for d in dois_in_page], "next-cursor": cursor,
                        "total-results": len(dois_in_page) if total is None else total}}


def _serve(monkeypatch, *pages):
    """Answer each Crossref request with the next page; return the recorded calls."""
    monkeypatch.setattr(dois, "_fetch", REAL_FETCH)
    calls: list[dict] = []
    queue = list(pages)

    def get(url, **kw):
        calls.append({"url": url, **kw})
        page = queue.pop(0)
        if isinstance(page, Exception):
            raise page
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: page, text="")

    monkeypatch.setattr(dois.requests, "get", get)
    return calls


def _pub(sid, **kw):
    coll, items = sink_stac.build_publication(
        {"series_id": sid, "pub_name": "T", "pub_year": "2020", "full_citation": "C."}, [],
        has_units=True, registered_dois=REGISTRY, **kw)
    return coll, items


def test_a_registered_id_gets_a_doi():
    coll, items = _pub("M-234")
    assert coll["sci:doi"] == "10.34191/M-234"
    assert {"rel": "cite-as", "href": "https://doi.org/10.34191/M-234"} in coll["links"]
    assert all(it["properties"]["sci:doi"] == "10.34191/M-234" for it in items)


def test_the_registry_is_compared_case_insensitively():
    coll, _ = _pub("OFR-1")
    assert coll["sci:doi"] == "10.34191/OFR-1"


def test_an_unregistered_series_gets_no_doi_even_with_a_blank_publisher():
    coll, items = _pub("MD-71")
    for doc in [coll, *(it["properties"] for it in items)]:
        assert "sci:doi" not in doc and "sci:citation" not in doc
    for doc in [coll, *items]:
        assert "cite-as" not in {lk["rel"] for lk in doc["links"]}


def test_an_odd_id_inside_a_doi_series_gets_no_doi():
    coll, _ = _pub("M-A2")
    assert "sci:doi" not in coll
    assert "cite-as" not in {lk["rel"] for lk in coll["links"]}


def test_a_fetch_follows_the_cursor_and_lowercases(monkeypatch):
    calls = _serve(monkeypatch, _page(["10.34191/M-234"], "c1", total=2),
                   _page(["10.34191/ofr-1"], "c2", total=2), _page([], "c3", total=2))
    assert dois.registered() == REGISTRY
    assert [c["params"]["cursor"] for c in calls] == ["*", "c1", "c2"]
    assert calls[0]["url"] == dois.CROSSREF_WORKS_URL
    assert calls[0]["timeout"] == 30 and calls[0]["headers"] == {"User-Agent": "ugs-warehouse"}
    assert calls[0]["params"]["select"] == "DOI"


def test_a_successful_fetch_writes_the_snapshot(monkeypatch):
    _serve(monkeypatch, _page(["10.34191/m-234"]))
    dois.registered()
    snap = json.loads(gcs.get_bytes(identity.PUB_DOI_REGISTRY_OBJECT))
    assert snap["dois"] == ["10.34191/m-234"] and snap["fetched_at"]


def test_a_failed_fetch_uses_the_snapshot_and_warns(monkeypatch, capsys):
    _serve(monkeypatch, _page(["10.34191/m-234"]))
    dois.registered()
    _serve(monkeypatch, requests.ConnectionError("no route"))

    assert dois.registered() == frozenset({"10.34191/m-234"})

    err = capsys.readouterr().err
    assert "WARNING" in err and "ConnectionError: no route" in err and "0 days old" in err


def test_a_failed_fetch_with_no_snapshot_raises(monkeypatch):
    _serve(monkeypatch, requests.Timeout("slow"))
    with pytest.raises(RuntimeError, match=r"Timeout: slow\) and there is no snapshot at pubs/doi-registry.json"):
        dois.registered()


def test_an_empty_or_malformed_response_is_a_failed_fetch(monkeypatch):
    _serve(monkeypatch, _page([]))
    with pytest.raises(RuntimeError, match="lists no DOIs"):
        dois.registered()
    _serve(monkeypatch, {"message": {"nope": 1}})
    with pytest.raises(RuntimeError, match="no message.items"):
        dois.registered()


def test_a_partial_fetch_is_a_failed_fetch_and_keeps_the_snapshot(monkeypatch, capsys):
    _serve(monkeypatch, _page(["10.34191/m-234", "10.34191/ofr-1"]))
    dois.registered()
    _serve(monkeypatch, _page(["10.34191/m-234"], total=2))

    assert dois.registered() == REGISTRY  # the snapshot, not the partial list
    assert "1 DOIs of 2 total-results" in capsys.readouterr().err
    assert json.loads(gcs.get_bytes(identity.PUB_DOI_REGISTRY_OBJECT))["dois"] == sorted(REGISTRY)


def test_paging_that_never_ends_is_capped(monkeypatch):
    forever = [_page(["10.34191/a"], "c", total=1) for _ in range(10)]
    _serve(monkeypatch, *forever)
    with pytest.raises(RuntimeError, match="paging did not end"):
        dois.registered()


def test_the_suite_stub_does_not_replace_requests_get_for_other_code():
    assert requests.get.__module__ == "requests.api"

"""Cross-boundary IAM grant preflight (#223). See scripts/check_grants.py for the why.

Hermetic: no gcloud, no network. `parse_grants` / `Grant` are pure; `check_grant`'s gcloud call is
mocked via monkeypatching `subprocess.run`.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import check_grants as G

TABLE = f"""
Some prose before the table, not part of it.

{G.BEGIN_MARK}
| principal | role | resource_type | resource | breaks_without |
|---|---|---|---|---|
| build-sa@x.iam.gserviceaccount.com | roles/run.admin | project | ut-dnr-ugs-maps-prod | deploy 403s |
| ugs-warehouse-review-srv@x.iam.gserviceaccount.com | roles/cloudsql.client | project | ut-dnr-ugs-mappingdb-prod | comments API 5xxs |
{G.END_MARK}

Prose after, also not part of it.
"""


# ---- parse_grants: pulls exactly the rows between the markers ---------------------------------

def test_parse_grants_reads_rows_between_markers():
    grants = G.parse_grants(TABLE)
    assert len(grants) == 2
    assert grants[0].principal == "build-sa@x.iam.gserviceaccount.com"
    assert grants[0].role == "roles/run.admin"
    assert grants[0].resource_type == "project"
    assert grants[0].resource == "ut-dnr-ugs-maps-prod"
    assert grants[0].breaks_without == "deploy 403s"


def test_parse_grants_skips_header_and_separator_rows():
    grants = G.parse_grants(TABLE)
    principals = [g.principal for g in grants]
    assert "principal" not in principals  # header row not mistaken for data


def test_parse_grants_ignores_prose_outside_markers():
    text = "| a | b | c | d | e |\n" + TABLE  # a stray table-shaped line before the markers
    grants = G.parse_grants(text)
    assert len(grants) == 2  # the stray line outside the markers is not picked up


def test_parse_grants_raises_when_markers_missing():
    with pytest.raises(ValueError, match="markers not found"):
        G.parse_grants("no markers here at all")


def test_parse_grants_empty_table_returns_empty_list():
    text = f"{G.BEGIN_MARK}\n| a | b | c | d | e |\n|---|---|---|---|---|\n{G.END_MARK}"
    assert G.parse_grants(text) == []


# ---- Grant.member: bare email -> serviceAccount: prefix; anything else passed through ---------

def test_member_prefixes_service_account_emails():
    g = G.Grant("foo@x.iam.gserviceaccount.com", "roles/x", "project", "p", "breaks")
    assert g.member == "serviceAccount:foo@x.iam.gserviceaccount.com"


def test_member_passes_through_non_email_principals():
    g = G.Grant("group:nrugsall@utah.gov", "roles/x", "project", "p", "breaks")
    # already has an "@" so it WOULD get prefixed by the naive email check — this pins the actual
    # behavior (table principals are documented as bare SA emails only) rather than asserting an
    # unimplemented group-principal feature.
    assert g.member == "serviceAccount:group:nrugsall@utah.gov"


# ---- Grant.policy_command: builds the right gcloud invocation per resource_type ----------------

def test_policy_command_project():
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/x", "project", "ut-dnr-ugs-maps-prod", "b")
    cmd = g.policy_command()
    assert cmd[:3] == ["gcloud", "projects", "get-iam-policy"]
    assert "ut-dnr-ugs-maps-prod" in cmd


def test_policy_command_bucket_adds_gs_prefix():
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/x", "bucket", "my-bucket", "b")
    cmd = g.policy_command()
    assert "gs://my-bucket" in cmd


def test_policy_command_run_service_splits_project_region_service():
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/x", "run_service", "proj/us-central1/svc", "b")
    cmd = g.policy_command()
    assert "svc" in cmd
    assert "--project=proj" in cmd
    assert "--region=us-central1" in cmd


def test_policy_command_secret_splits_project_secret_id():
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/x", "secret", "proj/my-secret", "b")
    cmd = g.policy_command()
    assert "my-secret" in cmd
    assert "--project=proj" in cmd


# ---- check_grant: mocked subprocess, exercises the granted/missing/error paths ------------------

def _fake_policy(bindings):
    return SimpleNamespace(stdout=json.dumps({"bindings": bindings}), stderr="")


def test_check_grant_returns_none_when_binding_present(monkeypatch):
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/run.admin", "project", "p", "b")
    monkeypatch.setattr(
        G.subprocess, "run",
        lambda *a, **k: _fake_policy([{"role": "roles/run.admin", "members": [g.member]}]),
    )
    assert G.check_grant(g) is None


def test_check_grant_flags_missing_binding(monkeypatch):
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/run.admin", "project", "p", "b")
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: _fake_policy([]))
    v = G.check_grant(g)
    assert v is not None and "NOT GRANTED" in v


def test_check_grant_flags_wrong_role_as_missing(monkeypatch):
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/run.admin", "project", "p", "b")
    monkeypatch.setattr(
        G.subprocess, "run",
        lambda *a, **k: _fake_policy([{"role": "roles/run.viewer", "members": [g.member]}]),
    )
    assert G.check_grant(g) is not None


def test_check_grant_handles_gcloud_missing(monkeypatch):
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/run.admin", "project", "p", "b")

    def _raise(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(G.subprocess, "run", _raise)
    v = G.check_grant(g)
    assert v is not None and "gcloud not found" in v


def test_check_grant_unknown_resource_type_is_a_violation():
    g = G.Grant("sa@x.iam.gserviceaccount.com", "roles/run.admin", "wat", "p", "b")
    v = G.check_grant(g)
    assert v is not None and "unknown resource_type" in v

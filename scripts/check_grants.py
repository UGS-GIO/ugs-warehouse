#!/usr/bin/env python3
"""Cross-boundary IAM grant preflight — issue #223.

Every capability this system needs that crosses a project/resource boundary (build project ->
serving project, deploy SA -> another SA, etc.) has historically been found by something 403ing in
the middle of a deploy, not by reading a list — `infra/iam.tf`'s own comments document three of
these ("found the hard way"). This script is the list: it reads the ONE table in `docs/DEPLOY.md`
(between the `<!-- check-grants:begin -->` / `:end` markers) and confirms each binding actually
exists in GCP right now.

The table is the source of truth, not this script — add a row there when a new capability is
discovered, and this script starts checking it. That is also why this stays decoupled from
`infra/iam.tf`: several rows here are NOT Terraform-managed (a different project's IAM, a grant
made out-of-band pending its own follow-up issue) and never will be, so a script that only checked
tofu-managed bindings would miss exactly the ones most likely to bite.

Needs GCP read permissions to run (`*.getIamPolicy` on each resource type below) — never write.
Safe to run from the personal box; no impersonation, no state, no `tofu` required.

Run: `just check-grants` or `python3 scripts/check_grants.py`. Exit 0 = every row granted, 1 = at
least one missing (prints which, and what it says will break).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "DEPLOY.md"

BEGIN_MARK = "<!-- check-grants:begin -->"
END_MARK = "<!-- check-grants:end -->"

# resource_type -> gcloud args that print that resource's IAM policy as JSON bindings.
# {resource} is substituted from the table's `resource` column for that row.
_POLICY_CMD: dict[str, list[str]] = {
    "project": ["gcloud", "projects", "get-iam-policy", "{resource}", "--format=json"],
    "bucket": ["gcloud", "storage", "buckets", "get-iam-policy", "gs://{resource}", "--format=json"],
    "service_account": [
        "gcloud", "iam", "service-accounts", "get-iam-policy", "{resource}", "--format=json",
    ],
    # resource = "<project>/<region>/<service>"
    "run_service": [
        "gcloud", "run", "services", "get-iam-policy", "{service}",
        "--project={project}", "--region={region}", "--format=json",
    ],
    # resource = "<project>/<secret_id>"
    "secret": [
        "gcloud", "secrets", "get-iam-policy", "{secret_id}",
        "--project={project}", "--format=json",
    ],
}


@dataclass(frozen=True)
class Grant:
    principal: str
    role: str
    resource_type: str
    resource: str
    breaks_without: str

    @property
    def member(self) -> str:
        # Table principals are bare emails; GCP bindings prefix with the principal kind.
        return f"serviceAccount:{self.principal}" if "@" in self.principal else self.principal

    def policy_command(self) -> list[str]:
        template = _POLICY_CMD[self.resource_type]
        if self.resource_type == "run_service":
            project, region, service = self.resource.split("/", 2)
            fmt = {"project": project, "region": region, "service": service}
        elif self.resource_type == "secret":
            project, secret_id = self.resource.split("/", 1)
            fmt = {"project": project, "secret_id": secret_id}
        else:
            fmt = {"resource": self.resource}
        return [part.format(**fmt) for part in template]


_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$"
)


def parse_grants(doc_text: str) -> list[Grant]:
    """Extract the table between the markers. First matching row is the header — skipped, along
    with the `|---|---|...` separator row (detected by every cell being dashes)."""
    try:
        block = doc_text.split(BEGIN_MARK, 1)[1].split(END_MARK, 1)[0]
    except IndexError:
        raise ValueError(
            f"{DOC}: check-grants markers not found — expected {BEGIN_MARK} ... {END_MARK}"
        ) from None

    grants: list[Grant] = []
    seen_header = False
    for line in block.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.groups()]
        if not seen_header:
            seen_header = True  # header row
            continue
        if all(re.fullmatch(r"-+", c) for c in cells):
            continue  # markdown separator row
        principal, role, resource_type, resource, breaks_without = cells
        grants.append(Grant(principal, role, resource_type, resource, breaks_without))
    return grants


def check_grant(grant: Grant) -> str | None:
    """Returns None if granted, else a violation message."""
    if grant.resource_type not in _POLICY_CMD:
        return f"{grant.resource_type}: unknown resource_type (typo in docs/DEPLOY.md table?)"
    try:
        out = subprocess.run(
            grant.policy_command(), capture_output=True, text=True, check=True, timeout=30
        )
    except FileNotFoundError:
        return "gcloud not found on PATH"
    except subprocess.TimeoutExpired:
        return "gcloud timed out (network/auth?)"
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip().splitlines()[-1] if e.stderr else "unknown error"
        return f"could not read IAM policy — {stderr}"

    try:
        policy = json.loads(out.stdout)
    except json.JSONDecodeError:
        return "gcloud returned non-JSON output (unexpected)"

    for binding in policy.get("bindings", []):
        if binding.get("role") == grant.role and grant.member in binding.get("members", []):
            return None
    return f"NOT GRANTED — {grant.role} on {grant.resource_type}:{grant.resource}"


def run(doc_path: Path = DOC) -> tuple[list[tuple[Grant, str]], list[Grant]]:
    """Returns (violations, all_grants). violations is [(grant, message), ...] for ungranted rows."""
    grants = parse_grants(doc_path.read_text(encoding="utf-8"))
    violations = [(g, v) for g in grants for v in [check_grant(g)] if v is not None]
    return violations, grants


def main() -> int:
    try:
        violations, grants = run()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if not grants:
        print(f"ERROR: no grant rows parsed from {DOC} — check the table markers/format.", file=sys.stderr)
        return 1

    bad = {id(g) for g, _ in violations}
    for g in grants:
        ok = id(g) not in bad
        mark = "\u2713" if ok else "\u2717"
        print(f"  {mark} {g.principal} \u2192 {g.role} on {g.resource_type}:{g.resource}")

    if violations:
        sys.stdout.flush()  # keep the row list ahead of stderr when a terminal interleaves them
        print(f"\n{len(violations)} of {len(grants)} grant(s) missing:", file=sys.stderr)
        for g, msg in violations:
            print(f"  - {msg}", file=sys.stderr)
            print(f"    breaks: {g.breaks_without}", file=sys.stderr)
        return 1

    print(f"\ncheck-grants: OK — all {len(grants)} grants present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

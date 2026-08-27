#!/usr/bin/env python3
"""Warehouse row-identity doc-sync gate — the ugs-warehouse SIBLING of governance §10.2.

The canonical contract lives in dataELT (docs/row-identity-governance.md) and owns the model, the
frozen recipe, and the producer invariants. This repo keeps the CONSUMER view in docs/IDENTITY.md,
which REFERENCES that contract (fetch-not-copy) and records what the warehouse must guarantee when
it consumes the durable key. This gate keeps IDENTITY.md honest against the warehouse's identity
machinery.

Unlike the dataELT and ugs-ingest siblings (bash + a GitHub Actions workflow, diff-gated, fetching
the canonical contract with a token), this one is a hermetic Python check with no diff and no
network: it runs in the warehouse's PRIMARY CI (the Cloud Build pytest step — the actual merge
gate) via tests/test_identity_doc_sync.py, and standalone here for pre-commit / manual use. It
guards the doc's STRUCTURAL fidelity: rename or delete an identity file, drop a citation, let a
`path:line` fall off the end of its file, or remove the canonical reference, and CI goes red. It
does NOT detect a semantic change WITHIN a file that leaves the shape intact — that stays review's
and the §8 nightly diff's job, not this gate's. Cross-repo drift (IDENTITY.md vs the canonical
contract) is likewise the nightly diff's job.

Four local checks:
  paths-exist : every path in IDENTITY_PATHS still exists — the gate's own list can't silently rot.
  coverage    : every identity path is CITED (by basename) in IDENTITY.md — a new identity file, or
                an existing one left undocumented, fails.
  line-ref    : every `ourfile.py:line` IDENTITY.md cites resolves — the file exists and the cited
                line is within it. Catches a citation stranded past the end of a shrunken/renamed
                file; in-range only, so a move that stays within the file is not caught (by design —
                that's review's job, not the gate's).
  canonical   : IDENTITY.md references the canonical dataELT contract (fetch-not-copy).

Run standalone: `python3 scripts/check_identity_doc_sync.py` (exit 0 = OK, 1 = out of sync).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "IDENTITY.md"

# Warehouse identity machinery (governance §10.2, consumer side): the vector-module files that
# implement consumer invariants C1–C5. fingerprint.py reads has_ugs_key for skip-unchanged
# detection but defines no identity invariant, so it is deliberately NOT here. When the id-flip
# (#174) wires ugs_key onto the featureserv OGC/Esri surface, add that path here in the same PR
# that documents it.
IDENTITY_PATHS = [
    "src/ugs_warehouse/vector/introspect.py",     # defines UGS_KEY / FEATURE_ID / has_ugs_key
    "src/ugs_warehouse/vector/sink_ducklake.py",  # C1 MERGE + NULL guard, C2 hashdiff, C4 de-arm
    "src/ugs_warehouse/vector/sink_stac.py",      # C3 ugs:primary_key stamp
    "src/ugs_warehouse/vector/sink_pmtiles.py",   # C5 MVT tile id = feature_id; ugs_key stays a property
    "src/ugs_warehouse/vector/transform.py",      # C2/C5 mints feature_id (the Hilbert render label)
]

# The consumer doc must REFERENCE the canonical contract, never copy the recipe. Match on the
# contract's filename so either form of the reference counts — the backticked repo-path
# (dataELT/docs/row-identity-governance.md) or the full GitHub URL (whose /blob/<ref>/ segment the
# exact repo-path string would miss).
CANONICAL_REF = "row-identity-governance.md"

_CITE_RE = re.compile(r"([A-Za-z0-9_./-]+\.py):(\d+)(?:-(\d+))?")


def check_paths_exist(repo: Path, identity_paths: list[str]) -> list[str]:
    return [
        f"{p}: listed in IDENTITY_PATHS but not found in the repo — a rename/delete of identity "
        f"machinery; update both this list and docs/IDENTITY.md."
        for p in identity_paths
        if not (repo / p).exists()
    ]


def check_coverage(doc_text: str, identity_paths: list[str]) -> list[str]:
    return [
        f"{p}: identity machinery not cited in docs/IDENTITY.md — add a conformance row citing "
        f"'{Path(p).name}' (or, if it is no longer identity machinery, drop it from IDENTITY_PATHS)."
        for p in identity_paths
        if Path(p).name not in doc_text
    ]


def check_line_refs(doc_text: str, repo: Path, identity_paths: list[str]) -> list[str]:
    """Every `path.py:line[-line]` the doc cites — for one of OUR identity files — must resolve: the
    file exists and the highest cited line is within it. Citations to other files (a cross-repo .py,
    the recipe) are not policed here."""
    # Basename-scoped: a cite resolves by filename, so the cited path PREFIX is not checked (a
    # wrong-dir cite to a real identity basename still resolves) and duplicate basenames would
    # collapse to one. Every identity basename is unique today — keep it that way.
    bases = {Path(p).name: p for p in identity_paths}
    violations: list[str] = []
    for m in _CITE_RE.finditer(doc_text):
        base = Path(m.group(1)).name
        if base not in bases:
            continue
        f = repo / bases[base]
        if not f.exists():
            violations.append(f"{m.group(0)}: cited file not found at {bases[base]} (stale reference)")
            continue
        n_lines = len(f.read_text(encoding="utf-8").splitlines())
        hi = int(m.group(3) or m.group(2))
        if hi > n_lines:
            violations.append(
                f"{m.group(0)}: cites line {hi} but {bases[base]} has {n_lines} lines — the code "
                f"moved; update the citation in docs/IDENTITY.md."
            )
    return violations


def check_canonical_ref(doc_text: str) -> list[str]:
    if CANONICAL_REF not in doc_text:
        return [
            f"docs/IDENTITY.md must reference the canonical contract '{CANONICAL_REF}' "
            f"(fetch-not-copy) — it looks removed or the path changed."
        ]
    return []


def run(doc_path: Path = DOC, repo: Path = REPO, identity_paths: list[str] | None = None) -> list[str]:
    paths = IDENTITY_PATHS if identity_paths is None else identity_paths
    text = doc_path.read_text(encoding="utf-8")
    return (
        check_paths_exist(repo, paths)
        + check_coverage(text, paths)
        + check_line_refs(text, repo, paths)
        + check_canonical_ref(text)
    )


def main() -> int:
    violations = run()
    if violations:
        print(
            "ERROR: row-identity doc-sync (ugs-warehouse) — docs/IDENTITY.md is out of sync with the "
            "identity machinery:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print("row-identity doc-sync (ugs-warehouse): OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

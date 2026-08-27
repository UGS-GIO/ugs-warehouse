"""Warehouse row-identity doc-sync gate (governance §10.2, consumer side).

The last of these tests IS the gate: it asserts docs/IDENTITY.md stays in sync with the warehouse's
identity machinery, and runs in the primary Cloud Build pytest CI. The rest unit-test the pure
checks the gate is built from. See scripts/check_identity_doc_sync.py for the why.
"""
from __future__ import annotations

from scripts import check_identity_doc_sync as C

# ---- coverage: every identity path is cited (by basename) in the doc --------------------------

def test_coverage_flags_an_uncited_identity_path():
    paths = ["a/introspect.py", "b/sink_stac.py"]
    violations = C.check_coverage("we cite introspect.py but not the other", paths)
    assert len(violations) == 1 and "sink_stac.py" in violations[0]


def test_coverage_passes_when_all_paths_cited():
    assert C.check_coverage("introspect.py and sink_stac.py both appear", ["x/introspect.py", "y/sink_stac.py"]) == []


# ---- line-ref: every `ourfile.py:line` citation resolves (file exists + line in range) --------

def _seed(tmp_path, lines):
    (tmp_path / "sub").mkdir(exist_ok=True)
    (tmp_path / "sub" / "foo.py").write_text("\n".join(f"l{i}" for i in range(lines)) + "\n")
    return ["sub/foo.py"]


def test_line_refs_passes_for_an_in_range_citation(tmp_path):
    paths = _seed(tmp_path, 5)
    assert C.check_line_refs("see foo.py:3", tmp_path, paths) == []


def test_line_refs_flags_a_citation_past_end_of_file(tmp_path):
    paths = _seed(tmp_path, 5)
    violations = C.check_line_refs("see foo.py:9-12", tmp_path, paths)
    assert len(violations) == 1 and "foo.py:9-12" in violations[0]


def test_line_refs_flags_a_single_line_citation_past_end_of_file(tmp_path):
    # the single-line branch (no range) — guards against a regression like `hi = int(group(3) or 0)`
    # that would silently stop flagging single-line cites while the range test above stayed green
    paths = _seed(tmp_path, 5)
    violations = C.check_line_refs("see foo.py:99", tmp_path, paths)
    assert len(violations) == 1 and "foo.py:99" in violations[0]


def test_line_refs_resolve_by_basename_ignoring_the_cited_prefix(tmp_path):
    # pins the basename-scoping: a cite's directory prefix is not checked — it resolves against the
    # identity path with that basename, then is still range-checked. Deliberate; a future change that
    # starts honoring the prefix should update this test on purpose.
    paths = _seed(tmp_path, 5)
    assert C.check_line_refs("see wrong/dir/foo.py:3", tmp_path, paths) == []
    assert len(C.check_line_refs("see wrong/dir/foo.py:99", tmp_path, paths)) == 1


def test_line_refs_flags_a_citation_to_a_missing_file(tmp_path):
    (tmp_path / "sub").mkdir(exist_ok=True)  # sub/ exists but foo.py does not
    violations = C.check_line_refs("see foo.py:1", tmp_path, ["sub/foo.py"])
    assert len(violations) == 1 and "not found" in violations[0].lower()


def test_line_refs_ignores_citations_to_files_it_does_not_govern(tmp_path):
    paths = _seed(tmp_path, 5)
    # a .py citation whose basename isn't one of our identity files (e.g. a cross-repo cite) is skipped
    assert C.check_line_refs("the canonical recipe lives in recipe.js and other.py:999", tmp_path, paths) == []


# ---- canonical: the consumer doc must REFERENCE the canonical contract (fetch-not-copy) --------

def test_canonical_ref_is_required():
    assert C.check_canonical_ref("a doc with no upstream reference")  # non-empty → violation


def test_canonical_ref_present_passes():
    assert C.check_canonical_ref(f"references {C.CANONICAL_REF} for the recipe") == []


# ---- paths-exist: the gate's own identity-path list must not go stale -------------------------

def test_paths_exist_flags_a_stale_path(tmp_path):
    assert len(C.check_paths_exist(tmp_path, ["nope/gone.py"])) == 1


def test_paths_exist_holds_for_the_real_identity_paths():
    assert C.check_paths_exist(C.REPO, C.IDENTITY_PATHS) == []


# ---- THE GATE: the real doc is in sync with the real identity machinery ------------------------

def test_identity_doc_is_in_sync_with_the_machinery():
    """docs/IDENTITY.md cites every identity path, every line-ref resolves, and it references the
    canonical contract. A change to the identity machinery that skips the doc trips one of these."""
    assert C.run() == []

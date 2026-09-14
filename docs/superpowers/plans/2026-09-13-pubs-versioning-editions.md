# Pubs Versioning / Editions + Write-Once Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make authoritative published map data immutable and give the catalog real edition semantics — a revised map becomes a new STAC Item linked to the one it supersedes, and a published data object can never be silently overwritten.

**Architecture:** Immutability is an application + catalog invariant, scoped to authoritative data — *not* a bucket-wide lock. The published originals (source bundle) and the published COG are write-once (a `gcs.upload_write_once` guard refuses to overwrite them); editions are expressed with the STAC Versioning Indicators extension (`version`/`deprecated` + `predecessor-version`/`successor-version` links); edition detection is quad-based off the authoritative footprint source. Derived assets (GeoParquet, mosaics) stay rebuildable; metadata stays curatable. GCS object versioning (already enabled) is an accident backstop only.

**Tech Stack:** Python 3, `obstore` (GCS), pytest. Producer package `src/ugs_warehouse/`.

**Spec:** `docs/superpowers/specs/2026-09-13-geomap-warehouse-publications-ingest-design.md` (ALL-5911 / ALL-5912). Read §4.4 before starting.

## Global Constraints

- **STAC version:** `1.1.0` (already the repo default in `core/stac.py`).
- **Versioning extension:** `https://stac-extensions.github.io/version/v1.2.0/schema.json` — fields `version`, `deprecated`; rels `predecessor-version`, `successor-version`, `latest-version`. Verified live.
- **Immutability is scoped, app-layer:** authoritative = published originals + COG (write-once). Derivatives rebuildable; metadata curatable. **No content-hash paths. No bucket-wide / WORM lock.**
- **Tests:** `pip install -e ".[dev]"` (add `.[pubs]` to un-skip raster-dep tests); run with `pytest -q`. No `conftest.py` exists — fixtures are defined inline per test file (mirror `_mem_gcs` from `tests/test_stac.py`).
- **Fail loud:** never swallow an error; a poison publication is reported, not silently skipped.
- **Commits:** Conventional Commits; no AI/tool attribution anywhere (repo rule). Include `[ALL-5912]` only in the PR title later, not in commits.

---

## File Structure

- `src/ugs_warehouse/core/stac.py` — add `VERSION_EXT` constant; auto-declare it in `build_item` when a `version`/`deprecated` property is present (mirrors the existing `FILE_EXT`/`PROJ_EXT` auto-declare).
- `src/ugs_warehouse/core/gcs.py` — add `WriteOnceViolation` + `upload_write_once` (never overwrite an existing object).
- `src/ugs_warehouse/pubs/sink_stac.py` — accept an optional `edition` arg in `build_item`; emit `version`/`deprecated` properties + `predecessor-version`/`successor-version` links.
- `src/ugs_warehouse/pubs/harvest.py` — route the authoritative COG upload through `upload_write_once`; make `--force` refuse to clobber a published object with a clear message.
- `src/ugs_warehouse/pubs/editions.py` *(new)* — quad-based edition graph: group publications by quad, order by year, compute predecessor/successor hrefs + `deprecated`.
- `src/ugs_warehouse/pubs/ingest.py` — call the edition graph and pass each item its `edition`.
- Tests: `tests/test_stac.py`, `tests/test_gcs.py`, `tests/test_pubs_editions.py` *(new)*.

Task order: 1 → 2 → 3 → 4. Tasks 1–3 have no external dependency. Task 4 has a confirmation gate (the authoritative quad source) before any code.

---

### Task 1: STAC Versioning extension support in `build_item`

**Files:**
- Modify: `src/ugs_warehouse/core/stac.py` (constants block ~L39-53; `build_item` extension auto-declare ~L148-157)
- Test: `tests/test_stac.py`

**Interfaces:**
- Produces: `stac.VERSION_EXT` (str constant); `build_item` auto-appends it to `stac_extensions` when `properties` contains `version` or `deprecated`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stac.py
def test_build_item_declares_versioning_extension_when_versioned():
    item = stac.build_item(
        item_id="M-299DM", collection="M", collection_path="ugs-publications/M",
        geometry=None, bbox=None, datetime_iso="1998-01-01T00:00:00Z",
        properties={"version": "1998", "deprecated": False}, assets={},
    )
    assert stac.VERSION_EXT in item["stac_extensions"]
    assert item["properties"]["version"] == "1998"
    assert item["properties"]["deprecated"] is False


def test_build_item_omits_versioning_extension_when_unversioned():
    item = stac.build_item(
        item_id="M-299DM", collection="M", collection_path="ugs-publications/M",
        geometry=None, bbox=None, datetime_iso=None, properties={}, assets={},
    )
    assert stac.VERSION_EXT not in (item.get("stac_extensions") or [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stac.py::test_build_item_declares_versioning_extension_when_versioned -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'VERSION_EXT'`.

- [ ] **Step 3: Add the constant**

In `core/stac.py`, in the extension-constants block (next to `FILE_EXT`):

```python
VERSION_EXT = "https://stac-extensions.github.io/version/v1.2.0/schema.json"
```

- [ ] **Step 4: Auto-declare it in `build_item`**

In `build_item`, alongside the existing `FILE_EXT`/`PROJ_EXT` auto-declare (after `exts = list(stac_extensions or [])`):

```python
    if ("version" in props or "deprecated" in props) and VERSION_EXT not in exts:
        exts.append(VERSION_EXT)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_stac.py -q -k versioning`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add src/ugs_warehouse/core/stac.py tests/test_stac.py
git commit -m "feat(stac): declare Versioning Indicators extension when an item carries version/deprecated"
```

---

### Task 2: `sink_stac.build_item` emits edition version fields + links

**Files:**
- Modify: `src/ugs_warehouse/pubs/sink_stac.py` (`build_item` signature ~L152; `extra_links` ~L236; properties dict ~L266-296)
- Test: `tests/test_stac.py`

**Interfaces:**
- Consumes: `stac.VERSION_EXT` (Task 1).
- Produces: `pubs.sink_stac.build_item(..., edition: dict | None = None)`, where `edition` = `{"version": str | None, "deprecated": bool, "predecessor_href": str | None, "successor_href": str | None}`. When present it adds `version`/`deprecated` to `properties` and `predecessor-version`/`successor-version` links.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stac.py  (mirror the existing prior_property/manual_override patch pattern)
def test_pub_item_emits_edition_version_and_supersession_links():
    edition = {
        "version": "2005", "deprecated": True,
        "predecessor_href": None,
        "successor_href": "https://maps-assets.geology.utah.gov/warehouse/stac/"
                          "ugs-publications/M/M-233/M-233.json",
    }
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item(
            {"series_id": "M-159", "pub_name": "Old Ed", "series": "M", "pub_year": "1980"},
            [], edition=edition,
        )
    assert item["properties"]["version"] == "2005"
    assert item["properties"]["deprecated"] is True
    assert stac.VERSION_EXT in item["stac_extensions"]
    succ = [l for l in item["links"] if l["rel"] == "successor-version"]
    assert succ and succ[0]["href"].endswith("/M-233/M-233.json")
    assert not [l for l in item["links"] if l["rel"] == "predecessor-version"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stac.py::test_pub_item_emits_edition_version_and_supersession_links -q`
Expected: FAIL — `build_item() got an unexpected keyword argument 'edition'`.

- [ ] **Step 3: Add the `edition` param + link emission**

In `sink_stac.build_item`, add `edition: dict | None = None` to the signature. After the existing `extra_links = [...]` block:

```python
    ed = edition or {}
    if ed.get("predecessor_href"):
        extra_links.append({"rel": "predecessor-version", "href": ed["predecessor_href"],
                            "type": "application/geo+json"})
    if ed.get("successor_href"):
        extra_links.append({"rel": "successor-version", "href": ed["successor_href"],
                            "type": "application/geo+json"})
```

- [ ] **Step 4: Add the version/deprecated properties**

In the `properties={...}` dict passed to `stac.build_item`, add (near `ugs:scale`):

```python
            **({"version": v} if (v := ed.get("version")) else {}),
            **({"deprecated": True} if ed.get("deprecated") else {}),
```

(`build_item` from Task 1 auto-declares `VERSION_EXT` from these.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_stac.py -q -k "edition or pub_item"`
Expected: PASS (new test + the existing pub-item tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/ugs_warehouse/pubs/sink_stac.py tests/test_stac.py
git commit -m "feat(pubs): emit STAC version/deprecated + predecessor/successor links for map editions"
```

---

### Task 3: `upload_write_once` guard for authoritative objects

**Files:**
- Modify: `src/ugs_warehouse/core/gcs.py` (near `upload`/`exists`)
- Modify: `src/ugs_warehouse/pubs/harvest.py` (the COG upload ~L555; `--force` handling ~L410-421)
- Test: `tests/test_gcs.py`

**Interfaces:**
- Consumes: `gcs.exists`, `gcs.upload` (existing).
- Produces: `gcs.WriteOnceViolation` (Exception); `gcs.upload_write_once(local_path, object_path, *, content_type, cache_control=None) -> FileMeta` — uploads only if `object_path` does not already exist; raises `WriteOnceViolation` otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gcs.py
def test_upload_write_once_refuses_to_overwrite(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(gcs, "exists", lambda p: p in seen)
    monkeypatch.setattr(gcs, "upload",
                        lambda local, path, **k: seen.setdefault(path, True) or gcs.FileMeta(1, "x"))
    f = tmp_path / "a.tif"; f.write_bytes(b"x")
    gcs.upload_write_once(str(f), "geolmap/cogs/M-1.cog.tif", content_type="image/tiff")
    with pytest.raises(gcs.WriteOnceViolation):
        gcs.upload_write_once(str(f), "geolmap/cogs/M-1.cog.tif", content_type="image/tiff")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gcs.py::test_upload_write_once_refuses_to_overwrite -q`
Expected: FAIL — `AttributeError: ... 'WriteOnceViolation'`.

- [ ] **Step 3: Implement the guard**

In `core/gcs.py`:

```python
class WriteOnceViolation(Exception):
    """Attempt to overwrite an existing write-once (authoritative published) object."""


def upload_write_once(local_path: str, object_path: str, *, content_type: str,
                      cache_control: str | None = None) -> FileMeta:
    """Upload only if `object_path` does not already exist. A published object is never
    overwritten — a revision must be published as a new edition (new object path)."""
    if exists(object_path):
        raise WriteOnceViolation(object_path)
    return upload(local_path, object_path, content_type=content_type, cache_control=cache_control)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gcs.py::test_upload_write_once_refuses_to_overwrite -q`
Expected: PASS.

- [ ] **Step 5: Route the COG through the guard + fix `--force`**

In `harvest.py`, replace the COG `gcs.upload(cog, pub.cog_object, ...)` (~L555) with `gcs.upload_write_once(...)`. In `harvest_one`, when `force` is set and `gcs.exists(pub.cog_object)`, do **not** proceed to overwrite — log and return a distinct outcome:

```python
    if force and gcs.exists(pub.cog_object):
        hlog("REFUSE overwrite of published COG; publish a revision as a new edition (series_id)",
             step="resolve", level="ERROR", category="unexpected")
        return "fail:write-once"
```

- [ ] **Step 6: Write + run a test for the harvest guard path**

```python
# tests/test_pubs_harvest.py  (guard-only; no raster deps needed for this path)
def test_harvest_refuses_force_overwrite_of_published_cog(monkeypatch):
    from ugs_warehouse.pubs import harvest
    monkeypatch.setattr(harvest.gcs, "exists", lambda p: True)
    assert harvest.harvest_one("M-299DM", force=True) == "fail:write-once"
```

Run: `pytest tests/test_pubs_harvest.py -q -k write_once`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ugs_warehouse/core/gcs.py src/ugs_warehouse/pubs/harvest.py tests/test_gcs.py tests/test_pubs_harvest.py
git commit -m "feat(pubs): write-once guard for authoritative COG uploads; --force no longer clobbers a published map"
```

---

### Task 4: Quad-based edition detection → wire into ingest

> **GATE (do first, no code):** Confirm the authoritative quad↔series source with the front-end / Clinton. Candidate: the AGOL `Geologic_Map_Footprints_View` (`quad_name` + `series_id` the portal already uses). Confirm (a) it is authoritative, and (b) `quad_name` is populated for the corpus. If not confirmed, STOP and report — do not guess a source. Record the confirmed source at the top of `editions.py`.

**Files:**
- Create: `src/ugs_warehouse/pubs/editions.py`
- Modify: `src/ugs_warehouse/pubs/ingest.py` (`build_catalog` / `process_pub`)
- Test: `tests/test_pubs_editions.py`

**Interfaces:**
- Produces: `editions.edition_graph(pubs: list[dict]) -> dict[str, dict]` — maps `series_id` → the `edition` dict Task 2 consumes (`version`, `deprecated`, `predecessor_href`, `successor_href`). Same-quad maps are ordered by `pub_year`; each older map gets `deprecated: True` + a `successor-version` href to the next-newer same-quad map; each newer map gets a `predecessor-version` href to the previous. `version` = `pub_year`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pubs_editions.py
from ugs_warehouse.pubs import editions

def test_edition_graph_links_and_deprecates_same_quad_maps():
    pubs = [
        {"series_id": "M-100", "quad_name": "Alta", "pub_year": "1980"},
        {"series_id": "M-233", "quad_name": "Alta", "pub_year": "2005"},
        {"series_id": "OFR-9", "quad_name": "Provo", "pub_year": "1999"},
    ]
    g = editions.edition_graph(pubs)
    assert g["M-100"]["deprecated"] is True
    assert g["M-100"]["successor_href"].endswith("/M-233/M-233.json")
    assert g["M-233"]["deprecated"] is False
    assert g["M-233"]["predecessor_href"].endswith("/M-100/M-100.json")
    assert g["OFR-9"]["deprecated"] is False          # lone map for its quad
    assert g["OFR-9"]["predecessor_href"] is None and g["OFR-9"]["successor_href"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pubs_editions.py -q`
Expected: FAIL — `ModuleNotFoundError: ugs_warehouse.pubs.editions`.

- [ ] **Step 3: Implement `edition_graph`**

```python
# src/ugs_warehouse/pubs/editions.py
"""Quad-based edition graph. Authoritative quad<->series source: <CONFIRMED SOURCE — fill from the gate>."""
from . import sink_stac
from ..core import config, stac


def _item_href(series_id: str) -> str:
    from .sink_stac import series_code, item_id_for
    code = series_code(series_id)
    iid = item_id_for(series_id)
    return config.public_url(stac.item_object_path(f"ugs-publications/{code}", iid))


def edition_graph(pubs: list[dict]) -> dict[str, dict]:
    by_quad: dict[str, list[dict]] = {}
    for p in pubs:
        q = (p.get("quad_name") or "").strip()
        if q:
            by_quad.setdefault(q, []).append(p)
    out: dict[str, dict] = {}
    for group in by_quad.values():
        ordered = sorted(group, key=lambda p: (p.get("pub_year") or ""))
        for i, p in enumerate(ordered):
            sid = p["series_id"]
            newer = ordered[i + 1] if i + 1 < len(ordered) else None
            older = ordered[i - 1] if i > 0 else None
            out[sid] = {
                "version": (p.get("pub_year") or "").strip() or None,
                "deprecated": newer is not None,
                "successor_href": _item_href(newer["series_id"]) if newer else None,
                "predecessor_href": _item_href(older["series_id"]) if older else None,
            }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pubs_editions.py -q`
Expected: PASS.

- [ ] **Step 5: Wire into `ingest.build_catalog`**

In `ingest.py`, after reading pubs, compute the graph once and pass each pub its edition into `process_pub` → `sink_stac.build_item(..., edition=graph.get(sid))`:

```python
    from . import editions
    graph = editions.edition_graph(pubs)
    # ... inside process_pub(p): edition = graph.get((p.get("series_id") or "").strip())
    #     item = sink_stac.build_item(p, atts, ..., edition=edition)
```

- [ ] **Step 6: Add an ingest-level test (patched, no GCS/network)**

```python
# tests/test_pubs_editions.py — mirror test_build_catalog_series_filter's patch set,
# asserting sink_stac.build_item is called with edition= for a same-quad pair.
```

Run: `pytest tests/test_pubs_editions.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ugs_warehouse/pubs/editions.py src/ugs_warehouse/pubs/ingest.py tests/test_pubs_editions.py
git commit -m "feat(pubs): quad-based edition graph — link + deprecate superseded map editions"
```

---

## Deferred (not this plan)

- **Infra backstop:** optionally lengthen GCS noncurrent-version retention on *just* the pub-data prefixes (a prefix-scoped lifecycle rule). This is an ops/Terraform change on `ut-dnr-ugs-maps-prod-public`, in Clinton's infra domain — track separately, not in this code plan.
- **Source-bundle write-once:** apply `upload_write_once` to the mirrored original bundle once ALL-5913 introduces the `source_bundle` asset (that plan owns the mirror change).

## Self-Review

- **Spec coverage (§4.4):** write-once discipline → Task 3; STAC Versioning (version/deprecated + predecessor/successor, deprecate old) → Tasks 1+2+4; quad-based detection → Task 4; "not bucket-wide/WORM" honored (no lock, guard is app-layer); backstop noted as deferred. ✓
- **Type consistency:** the `edition` dict shape (`version`/`deprecated`/`predecessor_href`/`successor_href`) is identical in Task 2 (consumer) and Task 4 (producer). ✓
- **Placeholders:** the only intentional blank is `<CONFIRMED SOURCE>` in `editions.py`, filled by the Task 4 gate. ✓
- **Open item for review:** Task 3 makes `--force` refuse to overwrite a published COG (revisions must be a new series_id). If a same-`series_id` re-render is a real workflow, we'd instead need a version-suffixed object path — flag at review.

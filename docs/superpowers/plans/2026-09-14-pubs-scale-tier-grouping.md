# Scale-tier layers as first-class grouped items — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the scale-tier serving layers (the `ugs-geologic-maps` raster mosaics) first-class *grouped* items — a `ugs:scale_tier` classification on each publication, a bidirectional whole/members `rel:"related"` relationship between each mosaic and the publications it stitches, and a `ugs:topic` on the mosaic items so they're discoverable — using the warehouse's existing `rel:"related"` idiom, not a new construct.

**Architecture:** A mosaic item (`geologic-maps-{tier}`) is a raster stitch of the per-map COGs binned by publication scale (`geolmap_mosaics._group_by_tier`). Its true members are the COG-bearing pubs in that bin. We (1) extract the scale-parsing + tier vocabulary to a shared `pubs/scale.py` (breaking the `sink_stac`↔`geolmap_mosaics` import cycle and giving `editions.py` a stable home for `tier_of`), (2) have `sink_stac.build_item` stamp a factual `ugs:scale_tier` on every publication with a parseable scale and, for COG-bearing maps only, a `rel:"related"` link to their tier mosaic, and (3) have `geolmap_mosaics._write_item` stamp `ugs:topic` + one `rel:"related"` link back to each member publication's real item. Member hrefs resolve each pub's collection via `collection_group(p)` — a pub lives in `ugs-publications`, `ugs-external`, or `ugs-mining-district-files`, NOT always `ugs-publications`.

**Tech Stack:** Python 3, pystac, pytest. Package `src/ugs_warehouse/`.

**Spec:** `docs/superpowers/specs/2026-09-13-geomap-warehouse-publications-ingest-design.md` (§4.5 scale-tier grouping, §4.6 STAC conformance). ALL-5922 under ALL-5911.

## Global Constraints
- Use `rel:"related"` (IANA-registered; existing idiom in `vector/related.py`) — no new link construct. Href = `config.public_url(stac.item_object_path(collection_path, item_id))`.
- **Member hrefs MUST respect `collection_group(p)`.** Hardcoding `ugs-publications` routes a foreign-published quad map (BYU/AAPG/USU/UU → `ugs-external`) to a 404 — this is the exact silent-supersession-href bug the ALL-5912 whole-branch review caught. Resolve each member's collection per-pub.
- Backward compatible: a pub with no parseable scale gets no `ugs:scale_tier` and no mosaic link (item unchanged); a mosaic still builds with member links added.
- No AI/tool attribution anywhere; ruff enforced (E741/E702); `git add` only files you change (never `-A`).
- Tests: `. .venv/bin/activate`; run the specific test files (NOT `pytest -q` — a pre-existing `No module named 'scripts'` collection error + `test_stac_validation.py` SSL failures are unrelated to this work).

---

## Verified codebase facts (grounded 2026-09-14 against branch head b456329 — cite these, don't re-derive)
- `geolmap_mosaics.py`: `TIERS = ("24k","250k","500k")` (line 41); `DEFAULT_TIER = "24k"` (42); `SCALE_LABEL = {"24k":"1:24,000","250k":"1:250,000","500k":"1:500,000"}` (33). `_denominator(raw)` (56-75) parses free-text scale → `1:N`. `tier_of(raw)` (78-87): `d<=62_500 -> "24k"`, `d<=350_000 -> "250k"`, else `"500k"`, `None` when unparseable. `_group_by_tier()` (101-114) reads `source.read_pubs()`, bins every `_cog_sids()` (UPPER series ids) by `tier_of(pub_scale) or DEFAULT_TIER`, returns `{tier: [UPPER_sid,...]}`. `build_tier(tier, sids, maxz=None)` (122-173) stitches + calls `_write_item(tier, len(sids), obj)` (171). `_write_item(tier, n_maps, obj)` (176-192) builds the mosaic item via `stac.build_item(item_id=f"geologic-maps-{tier}", collection="ugs-geologic-maps", ..., properties={"title":..,"ugs:scale":tier,"ugs:map_count":n_maps}, assets={"tiles":..}, proj_epsg=4326)` then `stac.write_item(item)`. NO member links, NO `ugs:topic` today. `mosaic_object(tier)` (52) → `{identity.MOSAIC_PREFIX}/geologic-maps-{tier}.pmtiles`.
- `editions.py` imports `from .geolmap_mosaics import tier_of` (line 41), uses it at line 115, and references "`geolmap_mosaics.tier_of`" in its module docstring (line 16). **Task 1 must repoint both** to `scale`.
- `units_pmtiles.py` has its OWN unrelated `DEFAULT_TIER = "intermediate"` + `SCALE_LAYERS` (the small/intermediate/large pg_featureserv bands, line 110/133). **Do NOT touch it** — different vocabulary.
- `sink_stac.py`: does NOT import `geolmap_mosaics` (so extracting `scale.py` fully breaks the only cycle). `build_item(p, attachments, *, geom=, bbox=, ..., has_cog=False, ..., vector_layers=, companion_tables=, override=, contents=, mirrored=, edition=)` (164-175). Builds `extra_links` (305-316, starts with the `via` link), `extensions` (322-330), `properties` (345-378), then `return stac.build_item(item_id=item_id, collection=code, collection_path=f"{group}/{code}", ..., extra_links=extra_links, ...)` (341-384). `code = series_code(sid)` (332), `group = collection_group(p)` (333). Helpers here: `series_code(sid)` (79), `item_id_for(sid)` (86, preserves case), `collection_group(p)` (141-151, → `identity.PUBLICATIONS_COLLECTION` / `identity.EXTERNAL_COLLECTION` / `identity.MINING_DISTRICT_COLLECTION`).
- `ingest.py` calls `sink_stac.build_item(..., has_cog=up in cogs, has_units=..., ...)` (207-209) — `has_cog` and `p` (with `pub_scale`) already flow. **No ingest change needed.**
- `core/stac.py`: `build_item(*, ..., extra_links=None, ..., collection_path=None)` (126-184) appends `extra_links` into `item["links"]` (148); stores `_collection_path = collection_path or collection` (183). `item_object_path(collection_path, item_id)` (191-194) → `f"{config.STAC_PREFIX}/{collection_path}/{item_id}/{item_id}.json"`. `prettify(stem)` (91-97) → `re.sub(r"[_\-]+"," ",stem).strip().title()` (lowercases; use `pub_name` for a real title where available). `write_item(item)` (332) does the GCS write.
- The mosaic item is stored at `{STAC_PREFIX}/ugs-geologic-maps/geologic-maps-{tier}/geologic-maps-{tier}.json` (collection with no `collection_path`). A pub is stored at `{STAC_PREFIX}/{collection_group(p)}/{series_code}/{item_id}/{item_id}.json`.
- `topic.py`: `classify(title, keywords)` → topic key; `"geologic"` is a real key (RULES line 29). Hardcoding `"geologic"` on the mosaic is consistent.
- Test conventions (`tests/test_stac.py`): `from ugs_warehouse.pubs import sink_stac as pubs_sink`; a pub-item test builds `pubs_sink.build_item({...}, [])` under `with patch("ugs_warehouse.core.stac.prior_property", return_value=""), patch("ugs_warehouse.core.stac.manual_override", return_value={}):`. `identity.PUBLICATIONS_COLLECTION` / `identity.EXTERNAL_COLLECTION` are the collection-name constants (lines 30-38). No mosaic test file exists yet.

---

## File Structure
- Create `src/ugs_warehouse/pubs/scale.py` — the scale-parsing + tier vocabulary (`_denominator`, `tier_of`, `SCALE_LABEL`, `DEFAULT_TIER`), moved verbatim from `geolmap_mosaics.py` so `geolmap_mosaics`, `sink_stac`, and `editions` all import them without a cycle.
- Modify `src/ugs_warehouse/pubs/geolmap_mosaics.py` — import the moved symbols from `scale`; keep `TIERS`/`TIER_MAXZOOM`/`OVERVIEW_LEVELS`; thread member pub records so `_write_item` adds `ugs:topic` + per-member `rel:"related"` links.
- Modify `src/ugs_warehouse/pubs/editions.py` — repoint `tier_of` import (+ docstring) to `scale`.
- Modify `src/ugs_warehouse/pubs/sink_stac.py` — `build_item` adds `ugs:scale_tier` + a COG-gated `rel:"related"` link to the pub's tier mosaic.
- Tests: `tests/test_pubs_scale.py` (new), `tests/test_stac.py` (pub side), `tests/test_geolmap_mosaics.py` (new, mosaic side).

Task order 1 → 2 → 3 (Task 1 unblocks the imports for 2 and 3).

---

### Task 1: Extract scale-parsing + tier vocabulary to `pubs/scale.py`
**Files:** Create `src/ugs_warehouse/pubs/scale.py`; Modify `src/ugs_warehouse/pubs/geolmap_mosaics.py`, `src/ugs_warehouse/pubs/editions.py`; Test `tests/test_pubs_scale.py`.
**Interfaces:**
- Produces: `scale._denominator(raw: str) -> int | None`, `scale.tier_of(raw: str) -> str | None`, `scale.SCALE_LABEL: dict[str,str]`, `scale.DEFAULT_TIER: str` — behavior byte-identical to today's `geolmap_mosaics` definitions.
- This is a pure refactor: no behavior change, no new public behavior. `TIERS` stays in `geolmap_mosaics` (build/CLI iteration set; `sink_stac` doesn't need it).

- [ ] **Step 1:** Read `geolmap_mosaics.py:33-87` (`SCALE_LABEL`, `DEFAULT_TIER`, `_denominator`, `tier_of`) to move them verbatim. Confirm `geolmap_mosaics` uses `tier_of`/`SCALE_LABEL`/`DEFAULT_TIER` at `_group_by_tier` (108-111) and `_write_item` (179) and `main` CLI; confirm `editions.py:41` imports `tier_of`.
- [ ] **Step 2 (test, RED):** Create `tests/test_pubs_scale.py`:
```python
from ugs_warehouse.pubs import scale


def test_tier_of_bins_by_denominator():
    assert scale.tier_of("1:24,000") == "24k"
    assert scale.tier_of("1:62,500") == "24k"       # upper bound of the finest tier
    assert scale.tier_of("1:100,000") == "250k"     # there is NO 100k tier
    assert scale.tier_of("1:250,000") == "250k"
    assert scale.tier_of("1:350,000") == "250k"     # upper bound of the intermediate tier
    assert scale.tier_of("1:500,000") == "500k"
    assert scale.tier_of("1 inch = 1 mile") == "250k"   # _denominator -> 63,360; >62,500 -> 250k
    assert scale.tier_of("1 inch = 2000 feet") == "24k"  # _denominator -> 24,000
    assert scale.tier_of("") is None
    assert scale.tier_of("not a scale") is None


def test_scale_vocabulary_constants():
    assert scale.DEFAULT_TIER == "24k"
    assert scale.SCALE_LABEL["24k"] == "1:24,000"
    assert set(scale.SCALE_LABEL) == {"24k", "250k", "500k"}
```
  Run → FAIL (module missing). These values are the CONTRACT — `_denominator` handles the `1 inch = N feet` (×12) and `1 inch = N mile` (×63,360) forms too; `1 inch = 2000 feet` → 24,000 → `24k`, `1 inch = 1 mile` → 63,360 → `250k`. If any assertion here disagrees with the moved code, the code is authoritative — STOP and report (a mismatch means the move wasn't verbatim).
- [ ] **Step 3:** Create `scale.py`: module docstring (one line — "Publication scale → serving scale-tier (24k/250k/500k). Shared by geolmap_mosaics, editions, and sink_stac."), `from __future__ import annotations`, `import re`, then `SCALE_LABEL`, `DEFAULT_TIER`, `_denominator`, `tier_of` moved verbatim. In `geolmap_mosaics.py`: delete those four definitions; add `from .scale import tier_of, SCALE_LABEL, DEFAULT_TIER` (keep `import re` only if still used elsewhere in the file — `mosaic_object`/`_group_by_tier` don't need it after the move; check and drop the now-unused `re` import if nothing else uses it, else keep). In `editions.py`: change line 41 to `from .scale import tier_of` and update the docstring reference (line 16) from "`geolmap_mosaics.tier_of`" to "`scale.tier_of`". **Do NOT touch `units_pmtiles.py`** (its `DEFAULT_TIER`/`SCALE_LAYERS` are a different vocabulary).
- [ ] **Step 4:** Run `pytest tests/test_pubs_scale.py tests/test_pubs_editions.py -q` — new tests GREEN, editions regression GREEN (its `tier_of` import now resolves via `scale`). Then `ruff check src/ugs_warehouse/pubs/scale.py src/ugs_warehouse/pubs/geolmap_mosaics.py src/ugs_warehouse/pubs/editions.py`.
- [ ] **Step 5: Commit** — `refactor(pubs): extract scale-tier parsing + vocabulary to pubs/scale.py`.

---

### Task 2: `build_item` classifies each pub by scale tier + links COG maps to their mosaic
**Files:** Modify `src/ugs_warehouse/pubs/sink_stac.py`; Test `tests/test_stac.py`.
**Interfaces:** Consumes `scale.tier_of`/`scale.SCALE_LABEL`/`scale.DEFAULT_TIER` (Task 1) and the existing `has_cog` param. No signature change — tier derived internally from `p["pub_scale"]`. No ingest change (`has_cog` + `p` already flow, ingest.py:207-209).

Design (two distinct concerns — keep them separate):
- **`ugs:scale_tier` property** = `tier_of(p["pub_scale"])` when parseable, on EVERY pub (a factual classification of the pub's own scale). Omit when unparseable — do NOT fabricate a tier.
- **pub→mosaic `rel:"related"` link** only when `has_cog` (a non-COG pub is not stitched into the raster mosaic, so it has no mosaic to belong to). Link tier = `tier_of(p["pub_scale"]) or DEFAULT_TIER` — mirrors `_group_by_tier` membership exactly (a COG map with an unparseable scale is binned to `DEFAULT_TIER`, so it links there even though it has no `ugs:scale_tier` property).

- [ ] **Step 1 (test, RED):** add to `tests/test_stac.py`:
```python
def _build(p, **kw):
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        return pubs_sink.build_item(p, [], **kw)


def test_pub_scale_tier_classification_and_mosaic_link():
    # COG map, parseable scale: gets the property AND a link to its tier mosaic.
    item = _build({"series_id": "GQ-968", "series": "GQ", "pub_scale": "1:24,000"}, has_cog=True)
    assert item["properties"]["ugs:scale_tier"] == "24k"
    rel = [l for l in item["links"] if l["rel"] == "related"]
    assert len(rel) == 1
    assert rel[0]["href"].endswith("/ugs-geologic-maps/geologic-maps-24k/geologic-maps-24k.json")

    # COG map, UNPARSEABLE scale: no property, but still linked to the default tier (mosaic membership).
    item = _build({"series_id": "M-1", "series": "M", "pub_scale": "n/a"}, has_cog=True)
    assert "ugs:scale_tier" not in item["properties"]
    rel = [l for l in item["links"] if l["rel"] == "related"]
    assert len(rel) == 1 and rel[0]["href"].endswith("/geologic-maps-24k/geologic-maps-24k.json")

    # non-COG pub, parseable scale: classified, but NOT linked (not a mosaic member).
    item = _build({"series_id": "OFR-5", "series": "OFR", "pub_scale": "1:500,000"}, has_cog=False)
    assert item["properties"]["ugs:scale_tier"] == "500k"
    assert not [l for l in item["links"] if l["rel"] == "related"]
```
- [ ] **Step 2:** Run `pytest tests/test_stac.py -q` → FAIL.
- [ ] **Step 3:** In `sink_stac.py`, module-level `from .scale import tier_of, SCALE_LABEL, DEFAULT_TIER` (dependency-light — no cycle; `scale` imports nothing from `pubs`). In `build_item`, after `sid`/`item_id` are set, compute `scale_tier = tier_of(p.get("pub_scale"))`. Append the mosaic link into `extra_links` (after the `via`/`cite-as`/edition links, near line 316) when `has_cog`:
```python
    if has_cog:
        link_tier = scale_tier or DEFAULT_TIER
        extra_links.append({
            "rel": "related",
            "href": config.public_url(stac.item_object_path("ugs-geologic-maps",
                                                             f"geologic-maps-{link_tier}")),
            "type": "application/geo+json",
            "title": f"Utah geologic maps — {SCALE_LABEL.get(link_tier, link_tier)} seamless mosaic"})
```
   In the `properties=` dict, add (near the other `ugs:*` fields, e.g. after `ugs:scale` line 354):
```python
            **({"ugs:scale_tier": scale_tier} if scale_tier else {}),
```
- [ ] **Step 4:** Run `pytest tests/test_stac.py -q` (GREEN, existing pub-item tests unchanged) + `ruff check src/ugs_warehouse/pubs/sink_stac.py`.
- [ ] **Step 5: Commit** — `feat(pubs): classify publications by scale tier + link COG maps to their tier mosaic`.

---

### Task 3: Mosaic items carry `ugs:topic` + collection-correct `rel:"related"` links to members
**Files:** Modify `src/ugs_warehouse/pubs/geolmap_mosaics.py`; Test `tests/test_geolmap_mosaics.py` (new).
**Interfaces:** Consumes `sink_stac.collection_group`/`series_code`/`item_id_for` (function-level import to keep the module load order obvious — `geolmap_mosaics` → `sink_stac` → `scale`, no cycle since `sink_stac` never imports `geolmap_mosaics`). Threads member pub records from `_group_by_tier` (which already reads `source.read_pubs()`) down to `_write_item`.

- [ ] **Step 1:** Confirm from Task-1's read: `_group_by_tier` returns `{tier: [UPPER_sid]}` and `build()` is its only caller; `build_tier` calls `_write_item(tier, len(sids), obj)`. The member sids are UPPER (from `_cog_sids()`), but a pub item's id/collection use the pub record's ORIGINAL-case `series_id` — so member links must be built from the pub record, keyed by UPPER sid.
- [ ] **Step 2 (test, RED):** create `tests/test_geolmap_mosaics.py`:
```python
from unittest.mock import patch

from ugs_warehouse.pubs import geolmap_mosaics as gm
from ugs_warehouse.pubs import identity


def test_mosaic_stamps_topic_and_links_members_by_real_collection():
    by_sid = {
        # USGS-authored Utah quad -> main catalog (ugs-publications)
        "GQ-968": {"series_id": "GQ-968", "pub_publisher": "USGS", "pub_name": "Geologic map of Foo"},
        # foreign publisher -> ugs-external (the ALL-5912 404 trap)
        "BYU-1": {"series_id": "BYU-1", "pub_publisher": "BYU", "pub_name": "A thesis map"},
    }
    captured = {}
    with patch.object(gm.stac, "write_item", side_effect=lambda it: captured.setdefault("item", it)):
        # ORPHAN-9 has a COG but no pub record -> must be skipped (no item to link to)
        gm._write_item("24k", ["GQ-968", "BYU-1", "ORPHAN-9"], gm.mosaic_object("24k"), by_sid)
    item = captured["item"]
    assert item["properties"]["ugs:topic"] == "geologic"
    assert item["properties"]["ugs:map_count"] == 3   # count still reflects every stitched COG
    rel = [l for l in item["links"] if l["rel"] == "related"]
    hrefs = [l["href"] for l in rel]
    assert len(rel) == 2  # ORPHAN-9 skipped
    assert any(h.endswith(f"/{identity.PUBLICATIONS_COLLECTION}/GQ/GQ-968/GQ-968.json") for h in hrefs)
    assert any(h.endswith(f"/{identity.EXTERNAL_COLLECTION}/BYU/BYU-1/BYU-1.json") for h in hrefs)
```
  Run `pytest tests/test_geolmap_mosaics.py -q` → FAIL.
- [ ] **Step 3:** Implement:
  - `_group_by_tier()` → return `tuple[dict[str, list[str]], dict[str, dict]]`: build `pubs_by_sid = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}` once (reuse it for the existing `scale_by_sid` lookups so pubs are read only once), and `return groups, pubs_by_sid`.
  - `build()`: `groups, by_sid = _group_by_tier()`; pass `by_sid` into each `build_tier(tier, groups.get(tier, []), by_sid, maxz=maxz)`.
  - `build_tier(tier, sids, by_sid, maxz=None)`: unchanged raster path (still uses the `sids` strings for `_vsigs`/listfile); change the final call to `_write_item(tier, sids, obj, by_sid)`.
  - `_write_item(tier, sids, obj, by_sid)`: compute `n_maps = len(sids)` inside (keep `ugs:map_count`). Add `"ugs:topic": "geologic"` to properties. Build member links:
```python
    from . import sink_stac  # function-level: sink_stac never imports geolmap_mosaics, no cycle
    related = []
    for sid in sids:                      # sids are UPPER (from _cog_sids)
        p = by_sid.get(sid)
        if not p:
            continue                      # COG present but no pub record -> no STAC item exists to link
        real_sid = (p.get("series_id") or "").strip()
        coll = f"{sink_stac.collection_group(p)}/{sink_stac.series_code(real_sid)}"
        related.append({
            "rel": "related",
            "href": config.public_url(stac.item_object_path(coll, sink_stac.item_id_for(real_sid))),
            "type": "application/geo+json",
            "title": (p.get("pub_name") or "").strip() or stac.prettify(real_sid)})
```
   Pass `extra_links=related` into the `stac.build_item(...)` call in `_write_item`.
- [ ] **Step 4:** Run `pytest tests/test_geolmap_mosaics.py -q` (GREEN) + `pytest tests/test_pubs_editions.py -q` (regression: `_group_by_tier` shape changed — confirm nothing else consumes it; it's private, only `build()` calls it) + `ruff check src/ugs_warehouse/pubs/geolmap_mosaics.py`.
- [ ] **Step 5: Commit** — `feat(pubs): mosaic items carry ugs:topic + collection-correct rel:related links to members`.

---

## Open decisions (resolved in-plan)
- **Import cycle:** resolved by Task 1 (`scale.py` is dependency-light; `sink_stac` imports `scale`, not `geolmap_mosaics`). Task 3's `sink_stac` import is function-level for clarity, but module-level would also be safe (no cycle exists after Task 1).
- **`ugs:topic` for mosaics = `"geologic"`** (a real `topic.RULES` key). The aggregate is geologic maps even though an individual coal-field map classifies as `mineral-energy`. A finer per-tier topic is a follow-up if ever wanted.
- **Symmetry:** mosaic→member (Task 3, COG-bearing members) and pub→mosaic (Task 2, gated on `has_cog`, `tier_of or DEFAULT_TIER`) describe the SAME membership, so the relationship is bidirectional and consistent. `ugs:scale_tier` is the broader factual classifier carried by every parseable-scale pub (COG or not).
- **Member-link volume:** the 24k mosaic may carry ~800+ `rel:"related"` links → a large `links` array on that one item. Acceptable (it is the whole/members truth); flag at review only if item size becomes a real concern.
- **Duplicate UPPER series_id** in `pubs_by_sid` is last-wins (same behavior as the existing `scale_by_sid` comprehension); the edition work already logs dup series_ids. Not a blocker.

## Self-Review
- Spec §4.5 coverage: whole/members `rel:related` both directions → Tasks 2+3; `ugs:scale_tier` → Task 2; `ugs:topic` on mosaics → Task 3; existing idiom reused (no new construct) → all. ✓
- Backward compat: unparseable-scale pub unchanged (Task 2); mosaic still builds, member links additive (Task 3); `editions` import repointed (Task 1). ✓
- Correctness guardrail: member hrefs use `collection_group(p)` (the ALL-5912 lesson) + original-case `series_id`; orphan COGs (no pub record) skipped, not 404-linked. ✓
- Type consistency: `_group_by_tier` new return `(groups, by_sid)` consumed only by `build()`; `build_tier`/`_write_item` signatures updated together. ✓

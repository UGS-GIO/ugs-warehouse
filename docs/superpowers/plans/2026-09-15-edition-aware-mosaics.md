# Edition-aware geologic-map mosaics + footprints quad source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the geologic-map mosaics stitch only the **current edition per quad** (drop superseded editions before the VRT), and fix the root cause that has edition detection silently producing nothing catalog-wide — `editions.py` reads `quad_name` off the pub feed, but the prod feed (`pubs_feed_v1`) has no such column. Source `quad_name` from the **staged footprints parquet** (join by `series_id`) instead.

**Architecture:** `editions.edition_graph` already groups by (quad, scale tier), orders by `pub_year`, and marks older editions `deprecated`. Its only defect is the quad source (`p.get("quad_name")`, always blank in prod). Task 1 sources `quad_name` from `geolmap/footprints/footprints.parquet` (built by `pubs.footprints`; has `series_id` + `quad_name` for all 1050 map footprints) — which fixes edition detection everywhere. Task 2 has `geolmap_mosaics` consult that edition graph and drop `deprecated` series from each tier's member list before `gdalbuildvrt`, gated by `--editions current|all`, plus a `--quads`/test-item path for a scoped demo build.

**Tech Stack:** Python 3, duckdb (base dep — reads the parquet), pytest. Package `src/ugs_warehouse/`.

**Spec:** `docs/superpowers/specs/2026-09-13-geomap-warehouse-publications-ingest-design.md` (edition/versioning model). ALL-5954 under ALL-5911.

## Global Constraints
- `editions.py` is **import-cheap**: no `[pubs]` raster stack (geopandas/rasterio/pyogrio) at module load, and its tests run on base + `[dev]` only. Keep the parquet read behind a function-level import; **duckdb is a base dep** (confirmed in CI install), so read the parquet with duckdb, NOT geopandas/pyarrow-at-module-level.
- **Fail loud:** if the staged footprints parquet is missing, do NOT silently produce zero editions — print a clear `[editions]` error naming the fix (`python -m ugs_warehouse.pubs.footprints`). (This exact silent no-op is the bug we're fixing.)
- Selection rule = **newest `pub_year` per (quad, tier) wins** — verified 2026-09-15 to match the portal's curated current list (geolmapfilter.php) 46/46 multi-edition groups, 0 divergence. Do not reinvent it; `edition_graph` already does it.
- No AI/tool attribution anywhere; ruff enforced (E741/E702); `git add` only files you change (never `-A`).
- Tests: `. .venv/bin/activate` (create with `python3 -m venv .venv && pip install -e ".[dev]"` if absent — Task 1 needs only `[dev]`; Task 2 needs `[dev]` too, the mosaic tests mock the raster shell-outs). Run the specific test files, NOT `pytest -q` (pre-existing unrelated `scripts` collection error + `test_stac_validation.py` cert failures).

---

## Verified codebase facts (grounded 2026-09-15 against 494f528 — cite, don't re-derive)
- `editions.py`: `edition_graph(pubs)` (104-127) is the entry point. Only quad source = `quad = (p.get("quad_name") or "").strip()` (line 112); blank → `continue` (skipped as "not a quad map", line 113-114). Grouping key `(norm_quad, tier_of(pub_scale))` (115-121); `_link_group` (67-101) orders by `_year_key(pub_year)` (4-digit only), newest = no successor = current, older → `deprecated: True` + predecessor/successor hrefs. DM/DR are ordinary members (newest wins). Import-cheap (module imports only `config`, `stac`, `scale.tier_of`; `sink_stac` lazy at line 52).
- `ingest.py` calls `editions.edition_graph(pubs)` once over the full corpus (before `--series`/`--limit` narrowing), passes `edition=graph.get(sid)` into `build_item`. **No change needed in ingest** — fixing the quad source makes it start producing editions automatically.
- `geolmap_mosaics.py`: `_group_by_tier()` (65-80) → `(groups, pubs_by_sid)`; reads `source.read_pubs()` once into `pubs_by_sid` (UPPER sid → pub). Bins every `_cog_sids()` (UPPER) by `tier_of(pub_scale) or DEFAULT_TIER`. `build(scales, maxz)` (≈195) unpacks `groups, by_sid = _group_by_tier()` and calls `build_tier(tier, groups.get(tier,[]), by_sid, maxz)`. `build_tier` (88+) writes the **listfile** (95-96: one `_vsigs(sid)` per member) → `gdalbuildvrt` (115) — the listfile IS the VRT input, so filtering `sids` before build_tier is "drop before the VRT". `_write_item(tier, sids, obj, by_sid)` writes the mosaic STAC item (id `geologic-maps-{tier}`). `main()` has `--scale {24k,250k,500k,all}` + `--maxzoom`. `TIERS=("24k","250k","500k")`, `DEFAULT_TIER="24k"`.
- Staged footprints parquet: `https://maps-assets.geology.utah.gov/geolmap/footprints/footprints.parquet` (bucket object `geolmap/footprints/footprints.parquet`; prefix = `identity.FOOTPRINTS_PREFIX` = `"geolmap/footprints"`). Columns include `series_id`, `quad_name` (all 1050 rows populated), `pub_year`, `scale`. Read server-side via duckdb over the bucket or the CDN URL.
- `config.public_url(obj)` → CDN URL; `config.BUCKET` = the private bucket; `identity.FOOTPRINTS_PREFIX` = `"geolmap/footprints"`.

---

## File Structure
- Modify `src/ugs_warehouse/pubs/editions.py` — add `quad_by_series()` (duckdb read of the staged footprints parquet → `{UPPER series_id: quad_name}`, fail-loud if absent); `edition_graph(pubs, quad_by_sid=None)` uses it (footprints-sourced quad, feed value as fallback).
- Modify `src/ugs_warehouse/pubs/geolmap_mosaics.py` — `_group_by_tier(editions="current", quads=None)` drops deprecated (via `editions.edition_graph`) and optionally restricts to `quads`; `build`/`main` gain `--editions current|all` + `--quads` + a test-item suffix when scoped.
- Tests: `tests/test_pubs_editions.py` (extend — inject `quad_by_sid`), `tests/test_geolmap_mosaics.py` (extend — filter behavior).

Task order 1 → 2 (Task 2 consumes Task 1's `edition_graph`/`quad_by_series`).

---

### Task 1: `editions.py` sources `quad_name` from the staged footprints parquet
**Files:** Modify `src/ugs_warehouse/pubs/editions.py`; Test `tests/test_pubs_editions.py`.
**Interfaces:**
- Produces: `quad_by_series() -> dict[str, str]` (`{UPPER series_id: quad_name}`, read from the staged footprints parquet via duckdb; raises/prints-fail-loud if the parquet is missing). `edition_graph(pubs, quad_by_sid: dict[str,str] | None = None)` — when `quad_by_sid` is None it calls `quad_by_series()`; per pub, `quad = quad_by_sid.get(sid.upper()) or (p.get("quad_name") or "").strip()`. Existing call site `edition_graph(pubs)` is unchanged (default loads the parquet).

- [ ] **Step 1 (test, RED):** In `tests/test_pubs_editions.py`, add a test that passes an **injected** `quad_by_sid` (no parquet read) modeling a Park City-style quad: pubs `[{"series_id":"GQ-852","pub_year":"1971","pub_scale":"1:24,000"}, {"series_id":"OFR-677","pub_year":"2017","pub_scale":"1:24,000"}, {"series_id":"M-296DM","pub_year":"2022","pub_scale":"1:24,000"}]` (all with blank/absent `quad_name`), `quad_by_sid={"GQ-852":"Park City East Quad","OFR-677":"Park City East Quad","M-296DM":"Park City East Quad"}`. Call `editions.edition_graph(pubs, quad_by_sid=qmap)`. Assert: `graph["M-296DM"]["deprecated"] is False` (newest = current), `graph["GQ-852"]["deprecated"] is True` and `graph["OFR-677"]["deprecated"] is True` (older), and `graph["GQ-852"]["successor_href"]` is non-null. Run → FAIL (`edition_graph` has no `quad_by_sid` kwarg / ignores it).
- [ ] **Step 2:** Run `pytest tests/test_pubs_editions.py -q` → FAIL.
- [ ] **Step 3:** Implement:
  - Add `quad_by_series()`:
```python
def quad_by_series() -> dict[str, str]:
    """{UPPER series_id -> quad_name} from the staged footprints parquet (geolmap/footprints/
    footprints.parquet, built by pubs.footprints). Fail loud if it's absent — without it edition
    detection can't group quads (the silent-no-op bug this fixes)."""
    import duckdb  # base dep; kept function-level to preserve editions.py's import-cheapness
    from . import identity
    url = config.public_url(f"{identity.FOOTPRINTS_PREFIX}/footprints.parquet")
    try:
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs;")
        rows = con.execute(
            f"SELECT upper(series_id), quad_name FROM read_parquet('{url}') "
            "WHERE coalesce(quad_name,'') <> ''").fetchall()
    except Exception as e:  # noqa: BLE001 — surface it, don't silently degrade
        raise RuntimeError(
            f"[editions] staged footprints parquet unreadable at {url} ({e}); "
            "run `python -m ugs_warehouse.pubs.footprints` first") from e
    return {str(s): str(q) for s, q in rows if s and q}
```
  - Change `edition_graph` signature to `edition_graph(pubs, quad_by_sid: dict[str, str] | None = None)`; near the top, `if quad_by_sid is None: quad_by_sid = quad_by_series()`. In the loop, replace line 112 with `quad = (quad_by_sid.get(sid.upper()) or (p.get("quad_name") or "")).strip()` (footprints-authoritative, feed value as fallback so a pub absent from footprints still uses any feed quad).
- [ ] **Step 4 (protect existing tests from the live CDN):** The 6 existing direct-call tests in `tests/test_pubs_editions.py` (`test_same_quad_same_scale_…`, `…different_scale…`, `…unparseable_scale…`, `…tied_pub_year…`, `…real_collection_group`) each call `editions.edition_graph(pubs)` with `quad_name` set in-feed. With the new default (`None` → `quad_by_series()`) they would hit the live CDN — change each call to `editions.edition_graph(pubs, quad_by_sid={})` so they exercise the feed-fallback (unchanged assertions, no network). `test_build_catalog_wires_edition_into_build_item` (line 105) reaches `edition_graph` through `build_catalog` and can't pass the arg — add `patch("ugs_warehouse.pubs.editions.quad_by_series", return_value={})` to its `with` block (its pubs set `quad_name`, so `{}` + feed-fallback keeps M-1/M-2 grouping intact). Run `pytest tests/test_pubs_editions.py -q` (GREEN) + `ruff check src/ugs_warehouse/pubs/editions.py`.
- [ ] **Step 5: Commit** — `fix(pubs): source edition quad_name from staged footprints parquet (edition detection was silently no-op)`.

---

### Task 2: `geolmap_mosaics` drops deprecated editions before the VRT (`--editions current|all` + scoped demo)
**Files:** Modify `src/ugs_warehouse/pubs/geolmap_mosaics.py`; Test `tests/test_geolmap_mosaics.py`.
**Interfaces:** Consumes `editions.edition_graph` + `editions.quad_by_series` (Task 1). `_group_by_tier(editions="current", quads=None)`; `build(scales, maxz, editions="current", quads=None)`; `main` adds `--editions {current,all}` (default current) + `--quads "Quad A,Quad B"` (restrict members to those quad_names; when set, write to a test item id so the real tier isn't clobbered).

- [ ] **Step 1:** Confirm from Task-1 grounding: `_group_by_tier` reads `pubs_by_sid` (UPPER sid → pub). Load the footprints quad map **once** — `qmap = editions.quad_by_series()` — and reuse it for both the deprecation graph and the `--quads` filter (avoids a second CDN read). `graph = editions.edition_graph(list(pubs_by_sid.values()), quad_by_sid=qmap)` → `{sid: {deprecated,...}}` (keys original-case) → `deprecated_upper = {s.upper() for s,e in graph.items() if e["deprecated"]}`.
- [ ] **Step 2 (test, RED):** In `tests/test_geolmap_mosaics.py`, add a test: patch `gm._cog_sids` to return `{"GQ-852","OFR-677","M-296DM"}` (all Park City East, all with COGs), patch `gm.source.read_pubs` to return those pubs (scale 1:24,000, years 1971/2017/2022), and patch `gm.editions.edition_graph` to return `{"GQ-852":{"deprecated":True},"OFR-677":{"deprecated":True},"M-296DM":{"deprecated":False}}`. Call `gm._group_by_tier(editions="current")` → assert the `24k` group == `["M-296DM"]` (deprecated dropped). Call `_group_by_tier(editions="all")` → assert all three present. Run → FAIL.
- [ ] **Step 3:** Implement:
  - `_group_by_tier(editions: str = "current", quads=None)`: after building `pubs_by_sid`, when `editions == "current"` OR `quads` is set, load `qmap = editions.quad_by_series()` once. Compute `deprecated_upper` via `editions.edition_graph(list(pubs_by_sid.values()), quad_by_sid=qmap)` only when `editions == "current"` (skip it for `--editions all`). If `quads` is given, normalize the requested quad set (casefold) and keep only sids whose `qmap.get(sid)` casefolds into it. In the bin loop, `if editions=="current" and sid in deprecated_upper: continue`.
  - `build(scales, maxz, editions="current", quads=None)`: pass `editions`/`quads` to `_group_by_tier`; when `quads` is set, thread a **keyword-only** `suffix="-test"` (default `""`) into `build_tier`→`mosaic_object`/`_write_item` so the item id becomes `geologic-maps-{tier}-test` and the object `…/geologic-maps-{tier}-test.pmtiles` — never overwrites the real tier. Do NOT call `refresh_catalog()` for a scoped/`--quads` build.
  - `mosaic_object(tier, suffix="")` / `build_tier(..., *, suffix="")` / `_write_item(tier, sids, obj, by_sid, *, suffix="")`: the suffix is an **optional trailing keyword arg** (default `""`) so the existing positional call `_write_item("24k", [...], obj, by_sid)` in the test is unaffected; when set, the item id is `f"geologic-maps-{tier}{suffix}"`.
  - `main`: add `--editions {current,all}` (default `current`) and `--quads` (comma-separated quad names); pass into `build`.
- [ ] **Step 4:** Run `pytest tests/test_geolmap_mosaics.py -q` (GREEN — the existing `test_mosaic_stamps_topic_and_links_members_by_real_collection` calls `_write_item` positionally with 4 args and must stay green untouched; the new suffix arg is keyword-only with a default) + `ruff check src/ugs_warehouse/pubs/geolmap_mosaics.py`.
- [ ] **Step 5: Commit** — `feat(pubs): edition-aware mosaics — drop superseded editions before the VRT (--editions current|all, scoped --quads)`.

---

## Post-merge demo (operational, not a code task)
After Tasks 1-2 merge + cloudbuild redeploys the jobs, run the Park City chunk both ways to a scratch item and diff:
```
gcloud run jobs execute ugs-geolmap-mosaics --args=-m,ugs_warehouse.pubs.geolmap_mosaics,--scale,24k,--quads,"Park City East Quad,Park City West Quad",--editions,all
gcloud run jobs execute ugs-geolmap-mosaics --args=-m,ugs_warehouse.pubs.geolmap_mosaics,--scale,24k,--quads,"Park City East Quad,Park City West Quad",--editions,current
```
Expect `--editions all` → 6 members stacked; `--editions current` → only `M-296DM`+`M-297DM`. Also re-ingest a slice (`ingest --series M --limit …`) and confirm the Park City items now carry `version`/`deprecated`/predecessor links (edition detection fixed).

## Open decisions (resolved in-plan)
- **Default `--editions current`** — the authoritative mosaic should show current maps; `all` is the QA/legacy view.
- **duckdb over the CDN URL** for the parquet read (server-side, our own infra) keeps `editions.py` import-cheap (no geopandas). If reading the private bucket is preferred over the CDN, `gcs.get_bytes` + duckdb on the bytes is an equivalent swap — implementer's call, but keep it duckdb + function-level.
- **Fail-loud on missing parquet** is deliberate (the bug we're fixing was a silent no-op).

## Self-Review
- Root-cause coverage: quad source moved feed→footprints (Task 1) → edition detection produces editions catalog-wide (fixes 5912's silent no-op) AND feeds the mosaic filter (Task 2). ✓
- Faithfulness: newest-year selection verified == portal curation (0 divergence). ✓
- Backward compat: `edition_graph(pubs)` call site unchanged (default loads footprints); `--editions all` reproduces today's behavior; scoped `--quads` writes to a test item, never clobbers the real tier. ✓
- Import-cheap preserved: duckdb function-level, base dep. ✓

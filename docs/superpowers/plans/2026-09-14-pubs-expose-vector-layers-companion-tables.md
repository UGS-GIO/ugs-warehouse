# Expose all pub vector layers + companion tables as STAC assets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface every extracted per-publication GeMS vector layer (contacts, faults, points, …) and the non-spatial companion tables (DescriptionOfMapUnits / CorrelationOfMapUnits) as STAC assets — the data is already (mostly) in GCS but orphaned from the catalog, so feature access can't reach it.

**Architecture:** `vectors.py` already extracts every *spatial* layer to `geolmap/vectors/{series_id}/{label}.parquet` but drops non-spatial tables via an `if geomtype:` filter. This plan: (1) also extract companion tables, (2) discover all per-pub layer/table labels at ingest, (3) attach one STAC asset per layer + per companion table (companion tables carry `table:columns` under the STAC Table extension). Raw schema is preserved verbatim (no rename/coercion) — already the `to_parquet` behavior.

**Tech Stack:** Python 3, pyogrio/geopandas, obstore (GCS), pystac, pytest. Package `src/ugs_warehouse/`.

**Spec:** `docs/superpowers/specs/2026-09-13-geomap-warehouse-publications-ingest-design.md` (§1.3 gap table, §4.2 asset taxonomy, §4.6 STAC conformance, §5 components). ALL-5913 under ALL-5911.

## Global Constraints

- Preserve raw schemas verbatim — no rename/coercion/harmonization of columns or types.
- STAC 1.1.0; companion (non-spatial) table assets use the Table extension (`stac.TABLE_EXT`, `table:columns`) — already the idiom in `vector/sink_stac.py`.
- Tests: `. .venv/bin/activate` (recreate with `python3 -m venv .venv && pip install -e ".[dev,pubs]"` if absent — the vector path needs the `[pubs]` raster/geo deps); run the specific test files, not `pytest -q` (pre-existing `No module named 'scripts'` collection error on unrelated files).
- No AI/tool attribution anywhere; ruff enforced (E741/E702); `git add` only files you change (never `-A`).
- Backward compatible: the existing `units` asset and any current pub-item behavior must not regress.

---

## File Structure

- `src/ugs_warehouse/pubs/vectors.py` — extend `_sources()` (or add a sibling) to also yield non-spatial layers; extract them as plain Parquet; return the extracted labels split into `spatial` vs `table`.
- `src/ugs_warehouse/pubs/sink_stac.py` — `build_item` gains `vector_layers: list[str]` and `companion_tables: list[dict]` params; attaches one asset per entry; declares `TABLE_EXT` when any companion asset carries `table:columns`.
- `src/ugs_warehouse/pubs/ingest.py` — add discovery of `geolmap/vectors/{sid}/` layers + companion tables, keyed by series_id; pass into `build_item`.
- Tests: `tests/test_stac.py` (asset attachment), `tests/test_pubs_vectors.py` *(new, if absent)* (companion extraction).

Task order 1 → 2 → 3. Task 2 (the `build_item` interface) is the contract Task 3 (ingest) feeds.

---

### Task 1: Capture non-spatial companion tables in `vectors.py`

**Files:** Modify `src/ugs_warehouse/pubs/vectors.py`; Test `tests/test_pubs_vectors.py`.

**Interfaces:**
- Produces: extraction now writes non-spatial layers to `geolmap/vectors/{series_id}/{label}.parquet` (same prefix, plain Parquet), and the extractor returns the set of extracted labels partitioned into spatial vs non-spatial (e.g. `{"spatial": [...], "tables": [...]}`), so ingest/build_item can tell them apart.

- [ ] **Step 1: Write the failing test** — build a tiny fixture `.gdb` (or mock `pyogrio.list_layers`/`read_dataframe`) with one spatial layer + one `DescriptionOfMapUnits` non-spatial table; assert the extractor emits BOTH a spatial parquet and a table parquet, and reports the table under `tables`. (If a real `.gdb` fixture is impractical, mock `pyogrio.list_layers` to return `[("ContactsAndFaults","LineString"),("DescriptionOfMapUnits",None)]` and `read_dataframe`/`read_file` to return small frames; assert both are written.)
- [ ] **Step 2: Run test → FAIL** (`pytest tests/test_pubs_vectors.py -q`).
- [ ] **Step 3: Implement** — in `_sources()` (vectors.py:32-53) stop dropping non-spatial layers: yield them too, tagged as non-spatial. In the extraction loop (vectors.py:104-116), branch: spatial layer → `gpd.read_file(..., engine="pyogrio")` → `gdf.to_parquet` (GeoParquet, as today); non-spatial table → read WITHOUT geometry (`pyogrio.read_dataframe(path, layer=name, read_geometry=False)` — verify the exact pyogrio arg against the installed version) → `df.to_parquet` (plain Parquet). Upload both to `{VECTORS_PREFIX}/{series_id}/{label}.parquet`. Do NOT rename or coerce columns. Return the labels partitioned spatial/tables.
- [ ] **Step 4: Run test → PASS.**
- [ ] **Step 5: Commit** — `feat(pubs): extract non-spatial GeMS companion tables alongside vector layers`.

---

### Task 2: `build_item` attaches every vector layer + companion table

**Files:** Modify `src/ugs_warehouse/pubs/sink_stac.py`; Test `tests/test_stac.py`.

**Interfaces:**
- Consumes: nothing new (pure builder).
- Produces: `build_item(..., vector_layers: list[str] | None = None, companion_tables: list[dict] | None = None)` where each `vector_layers` entry is a layer label (asset href = `{VECTORS_PREFIX}/{sid}/{label}.parquet`), and each `companion_tables` entry is `{"label": str, "columns": list[dict] | None}` (columns → `table:columns`). Attaches one asset per layer (`roles:["data"]`, GeoParquet media type) and one per table (`roles:["data"]`, Parquet media type, `table:columns` when present); declares `stac.TABLE_EXT` if any asset carries `table:columns`. The existing `units` asset is unchanged (kept for backward compatibility).

- [ ] **Step 1: Write the failing test** (mirror `test_cog_assets_keep_the_cloud_optimized_media_type`'s patch pattern):

```python
def test_pub_item_exposes_all_vector_layers_and_companion_tables():
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item(
            {"series_id": "M-100", "series": "M"}, [],
            vector_layers=["gems__ContactsAndFaults", "gems__MapUnitPolys"],
            companion_tables=[{"label": "gems__DescriptionOfMapUnits",
                               "columns": [{"name": "MapUnit"}, {"name": "Age"}]}],
        )
    a = item["assets"]
    assert "gems__ContactsAndFaults" in a and a["gems__ContactsAndFaults"]["href"].endswith(
        "geolmap/vectors/M-100/gems__ContactsAndFaults.parquet")
    assert a["gems__DescriptionOfMapUnits"]["table:columns"] == [{"name": "MapUnit"}, {"name": "Age"}]
    assert pubs_sink.stac.TABLE_EXT in item["stac_extensions"]
```

- [ ] **Step 2: Run test → FAIL** (`build_item()` has no `vector_layers` kwarg).
- [ ] **Step 3: Implement** — add the two kwargs to `build_item` (sink_stac.py:152-161). After the existing asset block, attach one asset per `vector_layers` label (href via `config.public_url(f"{vectors.VECTORS_PREFIX}/{sid.upper()}/{label}.parquet")`, `type` = `PARQUET_MIME`, `roles:["data"]`, `title` from the label) and one per `companion_tables` entry (same href pattern, `roles:["data"]`, plus `table:columns` when `columns` present). In the extensions block (near sink_stac.py:227), append `stac.TABLE_EXT` if any asset has `table:columns` (mirror the vector-side check). Keep the `units` asset as-is.
- [ ] **Step 4: Run test → PASS** (+ existing pub-item tests still green).
- [ ] **Step 5: Commit** — `feat(pubs): attach all vector layers + companion tables as STAC assets`.

---

### Task 3: Ingest discovers every layer + companion table and wires them in

**Files:** Modify `src/ugs_warehouse/pubs/ingest.py`; Test `tests/test_stac.py` (or a small ingest test).

**Interfaces:**
- Consumes: `build_item`'s new params (Task 2); the partitioned extraction output (Task 1).
- Produces: `build_catalog` discovers, per series_id, the spatial layer labels and companion-table labels present under `{VECTORS_PREFIX}/{sid}/`, and passes them to `build_item`.

- [ ] **Step 1: Write the failing test** — mirror `test_build_catalog_series_filter`'s patch set, but stub `gcs.list_paths` for `VECTORS_PREFIX` to return two layer parquets + one companion-table parquet for a series, and assert `sink_stac.build_item` is called with `vector_layers`/`companion_tables` populated for that sid.
- [ ] **Step 2: Run test → FAIL.**
- [ ] **Step 3: Implement** — add `_vector_layers_by_sid()` (list `VECTORS_PREFIX`, group `{sid}/{label}.parquet` → per-sid label lists) and split spatial vs companion by the label naming (companion labels are the GeMS non-spatial table names, e.g. `*DescriptionOfMapUnits`, `*CorrelationOfMapUnits` — or carry the spatial/table split forward from Task 1's manifest if one is written; prefer a manifest/marker over name-guessing if cheap). For companion `table:columns`, read the parquet schema (pyarrow `parquet.read_schema`) at build time or leave `columns=None` (a follow-up can enrich). Pass `vector_layers=` and `companion_tables=` into the `build_item` call (ingest.py:184-190).
- [ ] **Step 4: Run test → PASS.**
- [ ] **Step 5: Commit** — `feat(pubs): discover + wire all vector layers + companion tables into pub items`.

---

## Open decisions (resolve in-plan / flag at review)

- **`units` asset vs the `geolmap/vectors` MapUnitPolys layer** are likely the same data from two prefixes (`geolmap/units` vs `geolmap/vectors`). This plan keeps the existing `units` asset untouched for backward compatibility and adds the full-layer assets alongside; reconciling/deduping them is a follow-up (flag at review — don't silently break `units`).
- **`table:columns` source:** reading each companion parquet's schema at ingest adds N schema reads. Acceptable; if too costly, ship `columns=None` (asset still attached) and enrich in a follow-up.
- **Spatial vs companion split:** prefer a small manifest written by Task 1 (authoritative) over guessing from label names at discovery time.

## Self-Review

- Spec coverage: §4.2 "expose all per-pub vector layers + companion tables" → Tasks 1-3; raw-schema preservation → Task 1 (no coercion); Table ext conformance (§4.6) → Task 2. ✓
- Type consistency: `vector_layers: list[str]` and `companion_tables: list[{label, columns}]` are identical in Task 2 (consumer) and Task 3 (producer). ✓
- Backward compat: `units` asset + existing tests untouched (Task 2 keeps it; new params default None). ✓

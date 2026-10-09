# Design: Aspatial tables as first-class STAC Items, with a uniform relationship model

- **Jira:** ALL-6049 (umbrella: decision + rollout; GH issue #413, under epic ALL-3865). Relates: ALL-5913 (superseded conclusion), ALL-5922, ALL-5550, ALL-5557, ALL-5837, ALL-5861 (builder-ownership dependency).
- **Related:** ALL-5550 / ALL-5557 (canonical dataset+grouping model, in progress), ALL-5837 (land-raw+curate re-arch, GeMS target, in progress), ALL-5911 (geomap corpus load, parent task, in progress), ALL-5913 (companion tables shipped as STAC **assets**, Done — its item-model conclusion is superseded here), ALL-5922 (scale-tier whole/members + `derived_from`, Done — extended here), warehouse #347 / #348 (the bugs this grew from). See §9 for how they reconcile.
- **Date:** 2026-09-26 (publications section added 2026-10-03)
- **Status:** Draft for review
- **Decision owner:** marshallrobinson. Reviewer: clintonlunn (warehouse).
- **Scope:** How the warehouse catalog models (a) aspatial serving tables, (b) relationships between tables, spatial or not, and (c) publications and the data harvested with them. Verified against the STAC 1.1.0 spec, the STAC Table extension, Portolan v0.2.0, and the largest public Table-extension catalog (Microsoft Planetary Computer). Out of scope: the discovery/browse UI, and the ISO/curation fields.

---

## TL;DR

Model **every serving table as its own STAC Item**, spatial or aspatial, uniformly. Give an aspatial table an **area-of-interest (AOI) footprint** (the union of the spatial items it relates to, falling back to a buffered Utah outline), never a null geometry. Express relationships with **three standard link relations, kept distinct**: `related` (+ `ugs:foreign_keys` for the join columns) for table-to-table foreign keys, `derived_from` for product lineage (a mosaic built from source items), and the structural `root/parent/child/collection/item` for the tree.

This is the plainest, most standard model available: it is what the STAC Table extension says to do, what Planetary Computer does (including for pure lookup tables), and it is Portolan-compatible. It replaces the current "materialize the aspatial table onto its spatial parent" shape, which cannot represent a shared authority table, cannot represent an aspatial-to-aspatial relationship, and produced the dangling-link bugs (#347). Nothing here invents a construct beyond the one field STAC still has no standard for (`ugs:foreign_keys`), which we already have.

Publications follow the same rule. Each publication becomes a **Collection** that holds its PDF and original files as collection-level assets, with its plate, layers and tables as Items (§4.6). A publication with no data is a Collection with no Items.

**Settled decisions:** AOI footprint, not null geometry (§4.2); publication as Collection with the PDF on the Collection (§4.6).

---

## 1. Problem — verified current state

The vector producer emits one STAC Item per **spatial** serving topic. An **aspatial** table (a lookup/authority table, or a child table like well core logs / boxes / photos) gets **no item**: `vector/ingest.py`'s geometry guard `SKIP`s any table with zero geometry rows before the sinks run, so aspatial tables are only ever **materialized as a `roles:["data","related"]` Parquet asset onto a spatial parent item** (`vector/related.py`). Relationships come from declared foreign keys in `raw.schema_registry.relationships` (Frictionless-style, enforced by dbt `relationships` tests); the producer reads them and projects them into `rel:related` links + a namespaced `ugs:foreign_keys` field + the Table extension `table:columns`.

That projection is direction-and-spatiality dependent (four cases), and that is where the friction lives:

| | outgoing (T references X) | incoming (child references T) |
|---|---|---|
| **spatial** other end | `rel:related` link to X's item | `rel:related` link to the child's item |
| **aspatial** other end | materialize X onto T | materialize the child onto T |

**Three things this cannot do, all of which UGS needs:**

1. **Navigate from a shared authority.** A table like `unit_descriptions` is the `1` in many 1-to-manys (geologic unit polygons, well core logs, boxes, photos, groundwater drill holes). Materialize-onto-parent gives it no anchor: there is nowhere in the catalog to stand and ask "what references `unit_descriptions`?" It also copies the table onto every parent (per-parent duplication).
2. **Aspatial-to-aspatial relationships.** Some consumers are themselves aspatial (boxes reference photos; core logs reference `unit_descriptions`). Neither end has a spatial item, so the relationship has no parent to materialize onto and cannot be expressed at all.
3. **Resolve every link.** Portolan CORE-035 is a MUST: "every link in a catalog MUST resolve." The current outgoing branch emitted a `rel:related` link to a non-existent item for an aspatial target (warehouse #347), a direct MUST violation.

The root impedance: the foreign-key graph is flat and relational; STAC is spatial-item-centric. The current model bridges them by making aspatial data second-class. This spec makes it first-class instead.

---

## 2. Goal & non-goals

**Goal:** any serving table, spatial or aspatial, is a first-class, searchable STAC Item, and the full relationship graph is navigable from any table to every related table regardless of spatial-ness, using only standard STAC constructs.

**Non-goals:**
- The discovery/browse UI (a separate effort).
- Curation/ISO fields (`ugs:topic_category`, descriptions).
- Serving aspatial items through OGC API Features. This becomes *possible* under this design (they now have items); whether to do it is a follow-on decision (§7).

---

## 3. Design principles (locked with the requester)

- **One item per table, no exceptions.** Spatial or aspatial, the `1` or the `many`, source or target, a many-to-many bridge table: each is one Item. The relationship role lives in `ugs:foreign_keys`, never in whether a table earns an item. No "promote to an item once it is shared" rule, because a conditional rule is itself the bespoke thing we are avoiding, and it fails outright on the aspatial-to-aspatial case.
- **Standard constructs only.** A namespaced `ugs:` field only where STAC has nothing (that is `ugs:foreign_keys`, and it stays). Everything else is core STAC or an official extension.
- **Distinct meanings get distinct link relations.** A foreign-key join is not lineage; do not collapse them onto one rel.
- **Derive from truth.** Relationships stay authored in `raw.schema_registry.relationships` (the DB), never hand-edited in the catalog. The producer only projects.

---

## 4. The design

### 4.1 Item per table (uniform)

Every serving table is a STAC Item in its schema's collection (`ugs-serving-topics/<schema>`), carrying its GeoParquet as a `data` asset and its schema as `table:columns` (Table extension, already emitted). This is the Table extension's own recommendation ("Use `Item` objects to catalog an individual table") and matches Planetary Computer's `fia` collection, which has one Item per table across all 51 tables including pure lookups (`county`, `survey`, `pop_stratum`).

### 4.2 AOI footprint, not null geometry (the settled decision)

An aspatial item gets a **real geometry equal to its area of interest**: the union bounding polygon of the spatial items it relates to over the FK graph, falling back to the collection's buffered-Utah extent (`core/stac.py` already defaults to `UTAH_BBOX`). `bbox` is present.

Why AOI over null geometry, both of which are spec-legal:

- STAC Best Practices **discourage** null geometry: it is reserved for "unlocated" data, and non-spatial cataloging is called "not currently supported" (they point at OGC API - Records). Null items "will likely not show up in STAC API searches" [live-check exact wording].
- The prior art does not use null: Planetary Computer's lookup-table items carry the program's AOI footprint. No public Table-extension catalog was found using null geometry, and a trail of tooling breaks on it (pgstac NOT NULL, eoAPI rejects, STAC Browser empty map).
- It breaks our *own* pipeline: `core/item_mirror.py` deliberately skips geometry-less items from `items.parquet` ("a mirror row that cannot be queried spatially is worse than an absent one"), so null-geom items would silently drop out of the mirror, which Portolan FMT-042 says MUST reproduce the catalog.
- AOI keeps aspatial items spatially searchable and touches none of that.

Accepted trade-off: an AOI is an approximation, so a bbox search anywhere in Utah returns these authority tables. The union-of-related AOI (rather than a flat Utah outline) keeps that as tight as the data allows. Note Portolan issue #198 is an open community debate on exactly this; we accept the AOI approximation deliberately.

Rejected: **null geometry** (honest but discouraged, invisible to spatial search, breaks the item mirror). It would be revisitable only if a future STAC-API front-end and a mirror fix made it worth the churn.

### 4.3 Three relationship axes, three standard relations

| Meaning | Relation | Carrier of detail | Example |
|---|---|---|---|
| Table join (data references data) | `rel:related` (both ends; symmetric by IANA) | `ugs:foreign_keys` on the item (columns + direction + cardinality) | `sites.project = projects.projectcode` |
| Product lineage (a derivative built from inputs) | `rel:derived_from` (product to sources) | n/a | a scale-tier mosaic built from source COG plates |
| Structural (the catalog tree) | `root`/`parent`/`child`/`collection`/`item` | n/a | item to its collection |

- **`related`** is the IANA-registered "identifies a related resource." Every more-specific candidate was checked and rejected: `derived_from` is lineage, `via` is the metadata source (Portolan reserves it for mirrors), `describedby` is documentation (Portolan and Planetary Computer both use it for READMEs/HTML, so overloading it would misrender), `alternate`/`canonical` are the same resource, `item`/`collection` are reserved. The Table extension defines no link relation and no FK construct; its "capture table relationships" issue has been open since 2021. So `related` is the only standard link-level construct, and it carries no columns or direction, which is exactly why `ugs:foreign_keys` still exists (see §4.4).
- **`derived_from`** is the correct, standard relation for a genuine derivative product (a mosaic from its source plates), and the pubs producer already uses it for the geologic-map scale-tier mosaics (ALL-5922). A mosaic is its own Item; it points at its sources with `derived_from`. Do not use `related` for lineage or `derived_from` for a foreign key.

### 4.4 `ugs:foreign_keys` stays (it is not redundant with `related`)

`related` says two tables are related and lets you navigate; it carries no join detail. `ugs:foreign_keys` carries the mechanical join (which column references which, and which end is the `1`), which is what a consumer needs to actually join the data and what powers the viewer's click-a-feature-see-its-related-rows. STAC has no standard FK construct, so this is the sanctioned "namespaced only when nothing standard fits" case. It shadows Frictionless Table Schema `foreignKeys`. Portolan's current answer for separate-file joins is "document the join columns and a runnable example in the README," which we should also add.

### 4.5 Materialization becomes optional

The inline `roles:["data","related"]` asset on a parent can stay as a viewing convenience (open a related table without navigating away), but it must point at the **same** Parquet href as the child item's `data` asset, never a second copy. Or drop it and let the viewer follow the `related` link to the child item's `data` asset. The child item is canonical either way.

### 4.6 Publications: each publication is a Collection

Today a publication is one Item in its series collection (`ugs-publications/<series>/`). Its PDF, plate COG, original zips and any extracted layers are all assets on that Item. Under this design:

- **The publication becomes a Collection** at `ugs-publications/<series>/<id>/`, and **each series becomes a Catalog** (decided 2026-10-03). Portolan requires collections to be leaves ("A collection MUST NOT contain a child collection", PORTO-CORE-018); STAC 1.1 allows either. Publications then have the same shape as serving topics: Catalogs group, Collections hold data. The tree stays by series, because the series comes from the publication id and never changes. Quad, county, scale and year are properties exposed as filters in the viewer, not directories. Portolan's validator derives parent/child from directories (PTL-LNK-002/006), so a grouping tree that differs from the folder tree fails it.
- **The publication's own files are collection-level assets:** the PDF (`publication`), the cover image (`thumbnail`), the ISO 19139 record (`metadata`), and the original GIS and GeoTIFF zips (`source` role). There is no separate "publication" Item.
- **Its data are Items in the Collection:** one per plate COG, one per extracted layer (GeoParquet), and one per companion table (Parquet, with the publication footprint as its AOI, §4.2). Each carries the publication's DOI through the Scientific extension (`sci:doi`, `sci:citation`) and a `cite-as` link. GeMS joins (for example `MapUnitPolys.MapUnit` to `DescriptionOfMapUnits.MapUnit`) use `related` plus `ugs:foreign_keys` (§4.3, §4.4).
- **A publication with no data** (most reports) is a Collection with its collection-level assets and no Items. This is Portolan's single-file pattern. A Collection whose only Item holds a PDF fails the validator (PTL-COL-001, checked with rashid 0.1.8 on 2026-09-28).
- **Editions** keep the version-extension links (`predecessor-version`, `successor-version`, `latest-version`), now between publication Collections. The viewer shows them as a changelog on the publication page.

Costs:

- **A one-time URL change for every publication.** The publication id moves from an Item to a Collection, which breaks `items.json`, viewer `?c=`/`?i=` links, edition links and the mosaic `derived_from` links once. The builder re-derives all of them in the same run, and the viewer maps old item links to the new Collection.
- **Portolan paperwork per data-bearing Collection:** a README with the join columns, license, providers and a collection thumbnail, plus embedded band statistics in the plate COGs (PTL-DAT-009).
- **Search and the catalog index.** `core/stac.py` `_group_items` registers only `<id>/<id>.json` Items, so a publication Collection with no Items would drop out of `items.json` and the viewer's Discover search, which indexes Items only. `refresh_catalog` needs one index entry per publication Collection so every publication stays one result, and layer and table entries need their parent publication so Discover can roll them up under it rather than listing ~190 separate `DescriptionOfMapUnits` hits. The viewer also assumes one nesting level under a catalog today.
- **Large series (decided 2026-10-03: accept the warning).** Portolan's rule that a catalog or collection with twenty or more ungrouped children SHOULD use subcatalogs (PORTO-CORE-078) is SHOULD-level, and rashid reports it as a warning without failing validation. 19 of 36 series have 20 or more publications (OFR 820, M 326). No stable grouping clears it: decade buckets stay far over 20, and year buckets still leave 17 years over 20 (OFR 1993 has 50, I 1955 has 90) while adding a URL segment. The viewer's search and filters cover browsing, so the tree stays one level of publications per series.

Current state (verified 2026-10-03 against origin/main and prod): `pubs/vectors.py` (Cloud Run job `ugs-pubs-vectors`) already extracts every shapefile and every `.gdb` layer in a publication's GIS zip to Parquet and writes a `_manifest.json`. Only 5 publications have been extracted in prod. `pubs/sink_stac.py` attaches the layers as assets on the publication Item, and `table:columns` is never populated (`pubs/ingest.py` passes `columns: None`). Spreadsheet attachments (xls, xlsx, csv) are not converted.

---

## 5. What this supersedes

- **The four-case projection matrix collapses.** Every relationship becomes one thing: a `rel:related` link between two items. Materialize-onto-parent drops to an optional convenience.
- **The `reference.href` / dangling-link class disappears** (warehouse #347). Every FK target now has an item, so every link resolves (Portolan CORE-035 satisfied). The pending #347 follow-up (drop the FK on the data asset when the target is materialized) is subsumed: with the target as its own item, the FK edge is a resolvable `related` link plus `ugs:foreign_keys`, not a dangling archive href.

---

## 6. Pipeline changes (bounded)

- **`vector/ingest.py`:** the geometry guard must stop treating "no geometry" as `SKIP`. An aspatial table takes a distinct path: emit an Item with the `data` asset, `table:columns`, the AOI footprint, and the relationship graph, but no PMTiles/tiling sinks (there is no geometry to tile).
- **`core/stac.py` `build_item`:** derive the AOI footprint (union of related spatial extents, fallback `UTAH_BBOX`) for an aspatial item.
- **`vector/related.py`:** emit `rel:related` links to and from the aspatial items (both ends), drop the materialize-as-canonical path (keep it only as an optional same-href asset). The outgoing/incoming split stops mattering: it is `related` either way.
- **`core/item_mirror.py`:** no change needed once items carry an AOI geometry (they are no longer geometry-less, so the skip no longer applies). Verify.
- **`viewer/`:** an aspatial item now has an item page; it renders the AOI outline on the map (worth an affordance that reads "area of interest, not a footprint") and leads with the table + relationships. Confirm every `items.json` consumer tolerates these items.
- **`featureserv/`:** aspatial items are now items, so they *could* become OGC API Features collections; decide in §7.
- **`pubs/vectors.py`:** raise or collect errors instead of skipping a layer or `.gdb` it cannot read; record per-layer bbox, geometry type, CRS, row count and columns in the manifest; run `--all` over every publication with a GIS zip.
- **`pubs/sink_stac.py`, `pubs/ingest.py`:** build the publication Collection with its collection-level assets and one Item per plate, layer and table; move the edition, mosaic and `cite-as` links to match (§4.6).
- **`core/stac.py` `refresh_catalog`, viewer Discover:** index publication Collections, tag child entries with their publication and roll them up in results; keep table AOIs off the Discover map.
- **Spreadsheet attachments:** convert xls, xlsx and csv to Parquet table Items, keeping the original as a `source` asset.

---

## 7. Open questions

- **OGC API Features for aspatial items?** Now possible. Non-spatial features are legal (geometry may be null in a Feature), but `featureserv/gen_db.py` builds one collection per item and expects a parquet; serving a geometry-carrying-AOI-but-really-tabular collection needs a decision on whether that is useful or misleading.
- **Collection placement.** Aspatial items in the same `ugs-serving-topics/<schema>` collection as spatial ones, or a dedicated collection? Same-collection is simpler and keeps the FK graph within a schema; a dedicated one is cleaner for browse. Recommend same-collection to start.
- **Catalog volume.** Uniform items means more items, including trivial lookups. They are searchable and filterable, so this is manageable, but worth a browse-facet (`ugs:topic` or an aspatial flag) so they do not crowd map-first views.
- **Portolan scope for the publications tree (decided 2026-10-03): conform.** The publications tree follows Portolan like the data trees, so §4.6 uses series Catalogs, leaf publication Collections and the single-file pattern for report-only publications. The twenty-children warning is accepted (§4.6 costs).
- **AOI wording check.** Confirm the STAC Best Practices "will likely not show up in STAC API searches" sentence and the stac-geoparquet null-geometry stance before quoting either verbatim.

---

## 8. Standards audit (zero-invention requirement)

**Standard, verified:** item per table; `rel:related` (IANA) on both ends with `type: application/geo+json`; `derived_from` for lineage; `table:columns`; an AOI footprint. Custom-but-allowed: the `related` asset role (best practices permit an ad-hoc role). Already-custom, kept with the "nothing standard fits" note: `ugs:foreign_keys` (Table ext relationship issue open since 2021).

**Would be invention, avoided:** a `bbox` alongside a null geometry (the JSON schema *prohibits* it, `"bbox": {"not": {}}`); any explicit spatial/aspatial marker property; a new link relation name; foreign-key semantics packed into a link's `rel`.

---

## 9. Relationship to prior work

This design does not stand alone; it intersects a partly-shipped, partly-in-flight body of work and should be sequenced against it, not merged in isolation.

**Supersedes a shipped conclusion:**
- **ALL-5913 (Done): companion tables (DMU/CMU) exposed as STAC assets.** The pubs producer today attaches the GeMS non-spatial companion tables (`DescriptionOfMapUnits`, `CorrelationOfMapUnits`, which are the "unit descriptions" authority tables) as `roles:["data"]` assets on the publication item, not as their own items. This design changes the canonical shape: those companion tables become their own Items inside the publication's Collection (§4.6), with the asset kept only as an optional same-href inline convenience. It preserves ALL-5913's data (raw schema verbatim) and supersedes only its item-model conclusion.

**Extends an accepted direction:**
- **ALL-5922 (Done): scale-tier layers as first-class grouped items (whole/members).** The pubs producer already promotes layers to first-class grouped items with `derived_from`/`related` linkage. This design applies the same "first-class item, grouped" treatment to the aspatial companion/authority tables and reuses `derived_from` for mosaic lineage exactly as ALL-5922 does.

**Fits within two larger workstreams (align, do not duplicate):**
- **Canonical dataset + grouping model (ALL-5550, ALL-5557):** groups are STAC Collections, members are Items, backed by a `catalog.groups` spine and a manifest. Aspatial items here are member Items, consistent with that model. Keep two "related" mechanisms distinct: the **FK `rel:related` + `ugs:foreign_keys`** graph is the *derived join* between tables; a **"related-set" group** in ALL-5550 is a *curated bundle*. They answer different questions and must not be collapsed.
- **Land-raw + curate re-architecture (ALL-5837):** targets GeMS as the model (so "unit descriptions" is GeMS `DescriptionOfMapUnits`) and treats STAC as derived. This is the serving-layer STAC-modeling detail of that "STAC is derived" output; it can be built on the current producers now and stays forward-compatible.

**Two producers, one model.** The vector producer already does item-per-table (this design is native there). The pubs producer does publication-item-with-layer-assets (ALL-5913). So making aspatial tables first-class items is a change in **both** producers, and in pubs it means the publication becomes a Collection and its plate, layers and tables become Items in it (§4.6).

**Subsumes two loose ends:**
- An **unfiled residual from #347 (ALL-6011, Done).** That ticket's primary fix (PR #354) materialized aspatial outgoing FK targets as related-table assets, killing the broken `rel:related` *link*. But the parent's own `data`-asset `ugs:foreign_keys.reference.href` still points at the target's *standalone* archive path (`.../geoparquet/<target>/<target>.parquet`), which 404s for an aspatial target — the data only exists at the materialized `.../<parent>/related/<target>.parquet`. Verified live on `wetlands_plants_site` (2026-09-26): the data-asset FK href returns 404; the materialized asset and the inverted FK both return 200. It's a `ugs:foreign_keys` reference, not a STAC `links` entry, so it doesn't trip Portolan CORE-035 and the viewer's related-table UX (which reads the asset) is unaffected — which is why #347 could close. Moot once the target is its own item with a real standalone archive. A one-line interim guard (point the FK href at the materialized path, or drop the data-asset FK for aspatial targets) is possible if we want the 404 gone before this design lands.
- The never-filed "OGC API Features for aspatial tables" question (folded into §7).

---

## 10. References

- STAC 1.1.0 Item spec (geometry nullable; `bbox` REQUIRED if geometry non-null, PROHIBITED if null; JSON schema `item.json` null branch `"bbox": {"not": {}}`); PR radiantearth/stac-spec#854 "Null geometry clarification."
- STAC Best Practices, "Unlocated Items" / "Data that is not spatial" (discourages null geometry; points at OGC API - Records).
- STAC Table extension v1.3.0 (Item-per-table guidance; `table:columns`); issue stac-extensions/table#2 "Capture table relationships" (open since 2021-08-19; no standard FK construct).
- IANA Link Relation Types: `related`, `derived_from` semantics; Frictionless Table Schema `foreignKeys` (unidirectional, source-declared).
- Portolan v0.2.0: CORE-019 (null geometry allowed for non-spatial), CORE-035 (every link MUST resolve), FMT-034/036 (its own tabular = leaf Collection with AOI bbox), FMT-042 (item mirror MUST reproduce); issue portolan-spec#198 (open debate on tabular AOI).
- Prior art: Microsoft Planetary Computer `fia` collection (one Item per table, lookups included, AOI footprint, relationships in prose).
- Verification pass 2026-09-26 fetched the above live. A few wordings are flagged [live-check] in §4.2/§7.

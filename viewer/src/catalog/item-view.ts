// Pure, unit-tested item view-model: the field getters, the one "home category" taxonomy, the
// byline, the format buckets, and the curated/derived metadata partition — everything the catalog
// item + discovery cards + the item-detail page derive from a STAC item. Framework/DOM-free: it reads
// the STAC shapes (type-only ItemRef) plus the already-pure stac.ts helpers + map-model.validBbox, so
// it runs in the node test env. Consolidates the getters that had drifted between browse.tsx and
// discovery-model.ts into ONE source of truth (both now import from here).
import type { ItemRef } from "./browse";
import { validBbox } from "@/map/map-model";
import {
  type Asset, assetKind, cogAsset, parquetAsset, pmtilesLink, rasterTilesAsset,
  type TableColumn, zarrAsset,
} from "@/stac";

// ---- field getters (null-safe; the single home for what browse.tsx + discovery-model.ts duplicated) ----
export const propsOf = (it: ItemRef): Record<string, unknown> =>
  (it.data?.properties ?? {}) as Record<string, unknown>;
// The STAC item id (== the publication series id / layer stem), else the item folder from the href.
export const itemIdOf = (it: ItemRef): string =>
  String(it.data?.id ?? it.href.split("/").slice(-2)[0] ?? it.href);

export const title = (it: ItemRef): string => String(propsOf(it).title ?? itemIdOf(it));
// The item id IS the series id (DS-8, OFR-647…) / the layer stem — the mono code shown on a card.
export const series = (it: ItemRef): string => itemIdOf(it);

// The id worth printing ABOVE a title, or undefined when it only repeats it. A publication's id is
// its citation ("DS-9" over "Geologic map of…") and earns the line; a serving topic's is the title
// with a schema prefix ("geolmap_strat_columns_geologic_history_book" over
// "strat_columns_geologic_history_book"), which is noise rendered louder than the name.
const squash = (v: string) => v.toLowerCase().replace(/[^a-z0-9]/g, "");
export const seriesLabel = (it: ItemRef): string | undefined => {
  const id = series(it);
  const t = squash(title(it));
  const i = squash(id);
  if (!t || !i) return id || undefined;
  return i === t || i.includes(t) || t.includes(i) ? undefined : id;
};
// collId is the unique collection key (e.g. `ugs-publications/B`); the leaf folder is the label.
export const collectionLabel = (collId: string): string => collId.split("/").pop() ?? collId;
// The sub-catalog / top collection a collId hangs under (`ugs-serving-topics/emp` → `ugs-serving-topics`).
export const collectionRoot = (collId: string): string => collId.split("/")[0] ?? collId;

// Coarse "type" for the facet/column: a publication's type, else a layer's topic, else its series.
export const typeOf = (it: ItemRef): string => {
  const p = propsOf(it);
  return String(p["ugs:pub_type"] ?? p["ugs:topic"] ?? p["ugs:series"] ?? "");
};
export const scale = (it: ItemRef): string => String(propsOf(it)["ugs:scale"] ?? "");
export const author = (it: ItemRef): string => String(propsOf(it)["ugs:author"] ?? "");
export const county = (it: ItemRef): string => String(propsOf(it)["ugs:county"] ?? "");

// ISO date (YYYY-MM-DD), or "" when absent/non-string. Lexicographic on ISO == chronological.
export const dateOf = (it: ItemRef): string =>
  (typeof propsOf(it).datetime === "string" ? (propsOf(it).datetime as string).slice(0, 10) : "");
export const year = (it: ItemRef): number | null => {
  const y = parseInt(dateOf(it).slice(0, 4), 10);
  return Number.isFinite(y) ? y : null;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Show the date precision we actually have. UGS pubs carry year-only (stored as a Jan-1 placeholder)
// or year+month; the day is never real — so don't render a misleading "2026-01-01". Sorting still uses
// the raw ISO from dateOf; this is display-only.
export const fmtDate = (iso: string): string => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  if (m === "01" && d === "01") return y;                 // year-only placeholder → just the year
  if (d === "01") return `${MONTHS[+m - 1] ?? m} ${y}`;   // month precision → "Sep 2026"
  return iso;                                             // genuine full date
};

// "Has geometry" = a valid lon/lat bbox or an explicit geometry — i.e. it can draw on the map.
export const hasGeometry = (it: ItemRef): boolean =>
  Boolean(validBbox(it.data?.bbox) || it.data?.geometry);

// The doc id bridging a MiniSearch hit (built by search-index.toSearchDoc) back to its item.
export const docIdOf = (it: ItemRef): string => `${it.collId}/${itemIdOf(it)}`;



// A row count where the warehouse published one (serving topics), else undefined.
export const rowCount = (it: ItemRef): number | undefined => {
  const n = propsOf(it)["ugs:row_count"];
  return typeof n === "number" ? n : undefined;
};
export const recordCountLabel = (it: ItemRef): string | undefined => {
  const n = rowCount(it);
  return n === undefined ? undefined : `${n.toLocaleString()} ${n === 1 ? "row" : "rows"}`;
};

// ---- coarse content kind (a badge/label) — mirrors ugs-data-catalog datasetKind over StacDoc ----
export type ItemKind = "vector" | "raster" | "publication" | "other";
const PUBLICATION_ROOTS = new Set(["ugs-publications", "ugs-external"]);
const isPublication = (it: ItemRef): boolean =>
  PUBLICATION_ROOTS.has(collectionRoot(it.collId))
  || propsOf(it)["ugs:pub_type"] != null
  || propsOf(it)["ugs:series"] != null;

export const itemKind = (it: ItemRef): ItemKind => {
  if (isPublication(it)) return "publication";
  if (cogAsset(it.data) || rasterTilesAsset(it.data)) return "raster";
  const hasParquet = it.data ? parquetAsset(it.data) : undefined;
  if (propsOf(it)["ugs:dbt_schema"] != null || pmtilesLink(it.data) || hasParquet || zarrAsset(it.data))
    return "vector";
  return "other";
};
const KIND_LABELS: Record<ItemKind, string> = {
  vector: "Vector data", raster: "Raster", publication: "Publication", other: "Dataset",
};
export const kindLabel = (it: ItemRef): string => KIND_LABELS[itemKind(it)];

// ---- one "home" category (first match) — ported from ugs-data-catalog/src/lib/categorize.ts over
// the warehouse STAC shape: serving topics by `ugs:dbt_schema`, the top collections by collId root,
// publications by root/pub_type/series. Each category names the discovery facet a landing tile lands on.
export type CategoryResult = { key: string; label: string };
type Category = CategoryResult & {
  match: (it: ItemRef) => boolean;
  facet: { key: "category"; value: string };
};

// A category is keyed by `ugs:dbt_schema` (serving topics), by collection root, or both.
// Real warehouse schemas: emp / mapping / hazards / wetlands (+ groundwater reserved).
const bySchema = (schema: string) => (it: ItemRef) => propsOf(it)["ugs:dbt_schema"] === schema;
const byRoot = (root: string) => (it: ItemRef) => collectionRoot(it.collId) === root;
const cat = (key: string, label: string, match: (it: ItemRef) => boolean): Category =>
  ({ key, label, match, facet: { key: "category", value: key } });

// The ordered taxonomy: topics (by schema) resolve before collection then publication fallbacks.
export const CATEGORIES: Category[] = [
  cat("hazards", "Hazards", bySchema("hazards")),
  cat("energy-minerals", "Energy & Minerals", bySchema("emp")),
  // ONE category, two shapes of the same subject. The `mapping` serving tables and the
  // ugs-geologic-maps mosaic collection were separate categories whose labels differed by a single
  // letter ("Geologic Mapping" vs "Geologic Maps") — and geolmap_geolunits_500k and
  // geologic-maps-500k are the same 1:500k units, one as a table, one as a seamless tile layer.
  // Nothing distinguished them to a reader, so the split only ever split the subject.
  cat("geologic-maps", "Geologic Maps",
    (it) => bySchema("mapping")(it) || byRoot("ugs-geologic-maps")(it)),
  cat("wetlands", "Wetlands", bySchema("wetlands")),
  cat("groundwater", "Groundwater", bySchema("groundwater")),
  cat("rasters", "Rasters", byRoot("ugs-rasters")),
  cat("mining-district-files", "Mining District Files", byRoot("ugs-mining-district-files")),
  cat("publications", "Publications", isPublication),
];
const OTHER: CategoryResult = { key: "other", label: "Other" };

// The item's one home category (first match), else Other.
export const categorize = (it: ItemRef): CategoryResult => {
  const hit = CATEGORIES.find((c) => c.match(it));
  return hit ? { key: hit.key, label: hit.label } : OTHER;
};
// A category key → its human label (for a facet chip built from the URL, before items resolve).
export const categoryLabel = (key: string): string =>
  CATEGORIES.find((c) => c.key === key)?.label ?? (key === "other" ? "Other" : key);

// ---- format buckets (assetKind + the PMTiles web-map link) → the human labels a Format facet uses ----
const FORMAT_LABEL: Partial<Record<ReturnType<typeof assetKind>, string>> = {
  cog: "COG", zarr: "Datacube", threeD: "3D", pdf: "PDF", image: "Image", parquet: "GeoParquet", text: "Text",
};
export const formatsOf = (it: ItemRef): string[] => {
  const out = new Set<string>();
  for (const a of Object.values(it.data?.assets ?? {})) {
    if ((a as Asset).roles?.includes("thumbnail")) continue; // a thumbnail isn't the dataset's format
    const label = FORMAT_LABEL[assetKind(a as Asset)];
    if (label) out.add(label);
  }
  if (pmtilesLink(it.data)) out.add("PMTiles"); // vector tiles ride a web-map LINK, not an asset
  return [...out];
};

// ---- the item-detail byline: pub imprint · author · scale · date, only the parts present ----
export const bylineParts = (it: ItemRef): string[] => {
  const p = propsOf(it);
  const parts: string[] = [];
  const pubType = p["ugs:pub_type"];
  const seriesId = p["ugs:series_id"] ?? p["ugs:series"];
  if (pubType) parts.push(seriesId ? `${pubType} ${seriesId}` : String(pubType));
  if (p["ugs:author"]) parts.push(String(p["ugs:author"]));
  if (p["ugs:scale"]) parts.push(`Scale ${p["ugs:scale"]}`);
  const d = dateOf(it);
  if (d) parts.push(fmtDate(d));
  return parts;
};

// ---- curated vs derived metadata partition (the item-detail "Metadata" aside) ----
// Curated = human-authored at upload; Derived = machine-stamped by the pipeline. Only keys actually
// present + non-empty become rows; keys with their own UI (renders/contents/foreign_keys) are skipped,
// and description rides the header prose. Everything else still shows in the full PropertyTable.
export type MetaRow = { label: string; value: string };
type MetaDef = { key: string; label: string; fmt?: (v: unknown) => string };

const num = (v: unknown): string => (typeof v === "number" ? v.toLocaleString() : String(v));
const asList = (v: unknown): string => (Array.isArray(v) ? v.map(String).join(", ") : String(v));

const CURATED_DEFS: MetaDef[] = [
  { key: "ugs:author", label: "Author" },
  { key: "ugs:pub_type", label: "Publication type" },
  { key: "ugs:series", label: "Series" },
  { key: "ugs:series_id", label: "Series ID" },
  { key: "ugs:scale", label: "Scale" },
  { key: "ugs:volume", label: "Volume" },
  { key: "ugs:issue", label: "Issue" },
  { key: "ugs:county", label: "County" },
  { key: "keywords", label: "Keywords", fmt: asList },
  { key: "ugs:published", label: "Published" },
  { key: "license", label: "License" },
];
const DERIVED_DEFS: MetaDef[] = [
  { key: "ugs:dbt_schema", label: "Schema" },
  { key: "ugs:layer", label: "Serving layer" },
  { key: "ugs:row_count", label: "Records", fmt: num },
  { key: "ugs:mappable_count", label: "Mappable", fmt: num },
  { key: "ugs:primary_key", label: "Primary key", fmt: asList },
  { key: "proj:code", label: "Coordinate system" },
  { key: "ugs:summary_fields", label: "Summary fields", fmt: asList },
  { key: "datetime", label: "Ingested", fmt: (v) => (typeof v === "string" ? fmtDate(v.slice(0, 10)) : String(v)) },
];

const rowsFrom = (props: Record<string, unknown>, defs: MetaDef[]): MetaRow[] =>
  defs.flatMap((d) => {
    const v = props[d.key];
    if (v === undefined || v === null || v === "") return [];
    return [{ label: d.label, value: (d.fmt ?? String)(v) }];
  });

export const curatedDerived = (props: Record<string, unknown>): { curated: MetaRow[]; derived: MetaRow[] } => ({
  curated: rowsFrom(props, CURATED_DEFS),
  derived: rowsFrom(props, DERIVED_DEFS),
});

// Re-export the column-schema type so item-detail's schema table can import one thing from here.
export type { TableColumn };

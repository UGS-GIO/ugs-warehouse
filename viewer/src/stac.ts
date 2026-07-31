// STAC data layer — TanStack Query over the static catalog (catalog.json -> collections
// -> items) on the CDN. No fetch useEffects; components call these hooks. JSON items stay
// the source of truth (the warehouse writes them via core/stac).
import { useQueries, useQuery } from "@tanstack/react-query";

// Default catalog is build-time overridable (VITE_CATALOG_URL) so the INTERNAL/review deploy bakes
// the review catalog (review/stac) as its default while the public deploy keeps warehouse/stac —
// same codebase, one env var (the two-deploy, topology-enforced design). Runtime ?catalog= still wins.
export const DEFAULT_CATALOG =
  (import.meta as { env?: Record<string, string> }).env?.VITE_CATALOG_URL
  || "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json";
// ?catalog=<url> to point at sandbox / another deployment. Resolved to an absolute
// URL (a relative ?catalog=/stac/... would otherwise be an invalid base for `abs`).
export const CATALOG_URL = new URL(
  new URLSearchParams(location.search).get("catalog") || DEFAULT_CATALOG,
  location.href,
).href;

// Review deploy = catalog points at review/stac. Gates the review-only UI (diff, comments, dashboard).
// NOT a security boundary — real protection is server-side: the /api/comments route and review data only
// exist behind IAP on the review deploy. This flag just hides dead UI on the public build.
// `?review=1` forces it on for LOCAL DEV preview ONLY (gated to dev builds so prod can't toggle it).
const DEV = Boolean((import.meta as { env?: Record<string, unknown> }).env?.DEV);
export const IS_REVIEW = CATALOG_URL.includes("/review/") ||
  (DEV && new URLSearchParams(location.search).get("review") === "1");

export type Link = {
  rel: string;
  href: string;
  title?: string;
  "pmtiles:layers"?: string[];
  "ugs:item_count"?: number;
  "ugs:mappable_count"?: number;
};
// ugs:foreign_keys — a UGS-prefixed custom field (FK join detail has no STAC extension). This
// resource's `fields` reference `reference.resource`'s (a serving-topic stem) `reference.fields`.
// Value shape mirrors Frictionless Table Schema. Emitted by the warehouse from the schema registry.
export type ForeignKey = { fields: string[]; reference: { resource: string; fields: string[] } };
export type Asset = {
  href: string; title?: string; type?: string; roles?: string[];
  "ugs:foreign_keys"?: ForeignKey[]; "table:columns"?: TableColumn[];
};
export type StacDoc = {
  id?: string;
  type?: string;
  description?: string;
  links?: Link[];
  geometry?: GeoJSON.Geometry | null;
  bbox?: number[];
  properties?: Record<string, unknown>;
  assets?: Record<string, Asset>;
};

const abs = (href: string, base: string) => new URL(href, base).href;

export const childLinks = (d: StacDoc | undefined, base: string): Link[] =>
  (d?.links ?? []).filter((l) => l.rel === "child").map((l) => ({ ...l, href: abs(l.href, base) }));

export const itemLinks = (d: StacDoc | undefined, base: string): Link[] =>
  (d?.links ?? []).filter((l) => l.rel === "item").map((l) => ({ ...l, href: abs(l.href, base) }));

export const pmtilesLink = (d: StacDoc | undefined): Link | undefined =>
  (d?.links ?? []).find((l) => l.rel === "pmtiles");

// Landing page for the publication (rel=via) + the cite-as DOI, where present.
export const viaLink = (d: StacDoc | undefined): Link | undefined =>
  (d?.links ?? []).find((l) => l.rel === "via");
export const citeLink = (d: StacDoc | undefined): Link | undefined =>
  (d?.links ?? []).find((l) => l.rel === "cite-as");

// Registry-driven relationships (the warehouse emits these from raw.schema_registry):
//  • related links — the FK graph (item ↔ related serving-topic item)
//  • related assets — aspatial child tables materialised as Parquet (roles incl "related")
//  • own foreign keys — this layer's columns → another topic (on its non-related data asset)
export const relatedLinks = (d: StacDoc | undefined): Link[] =>
  (d?.links ?? []).filter((l) => l.rel === "related");
export const relatedAssets = (d: StacDoc | undefined): { key: string; asset: Asset }[] =>
  Object.entries(d?.assets ?? {})
    .filter(([, a]) => a.roles?.includes("related"))
    .map(([key, asset]) => ({ key, asset }));
export const ownForeignKeys = (d: StacDoc | undefined): ForeignKey[] =>
  Object.values(d?.assets ?? {})
    .filter((a) => !a.roles?.includes("related"))
    .flatMap((a) => a["ugs:foreign_keys"] ?? []);

// Survey Notes "In this issue": [{title, page}] parsed from the issue PDF's table of contents
// (warehouse, ugs:contents) — page is null for "back cover". Undefined when the item carries none.
export type ContentsEntry = { title: string; page: number | null };
export const contentsOf = (d: StacDoc | undefined): ContentsEntry[] | undefined => {
  const c = (d?.properties as Record<string, unknown> | undefined)?.["ugs:contents"];
  return Array.isArray(c) && c.length ? (c as ContentsEntry[]) : undefined;
};

// Preview image: the thumbnail asset (role=thumbnail) where the harvest produced one.
export const thumbnailAsset = (d: StacDoc | undefined): Asset | undefined => {
  const assets = Object.values(d?.assets ?? {});
  return assets.find((a) => a.roles?.includes("thumbnail"))
    ?? assets.find((a) => a.type?.startsWith("image/"));
};

// OGC API Features endpoint base (the duckdb_featureserv service). Set at build time via
// VITE_FEATURES_BASE, or per-session via ?features=<url>. Empty → the link is hidden (no dead
// link). A serving-topic's STAC item id == its featureserv collection id.
export const FEATURES_BASE = (
  new URLSearchParams(location.search).get("features")
  || ((import.meta as { env?: Record<string, string> }).env?.VITE_FEATURES_BASE)
  || ""
).replace(/\/+$/, "");
export const featuresCollectionUrl = (id: string): string | undefined =>
  FEATURES_BASE ? `${FEATURES_BASE}/collections/${id}` : undefined;

// XYZ vector tiles + Esri VectorTileServer (the `tiles/` service). Same shape as FEATURES_BASE:
// baked at build time, overridable per session, and empty hides the links rather than printing
// dead ones. The service serves every topic that has PMTiles, keyed by STAC item id.
export const TILES_BASE = (
  new URLSearchParams(location.search).get("tiles")
  || ((import.meta as { env?: Record<string, string> }).env?.VITE_TILES_BASE)
  || ""
).replace(/\/+$/, "");
export const xyzTilesUrl = (id: string): string | undefined =>
  TILES_BASE ? `${TILES_BASE}/tiles/${id}/{z}/{x}/{y}.mvt` : undefined;
export const tilesStyleUrl = (id: string, render?: string): string | undefined =>
  TILES_BASE ? `${TILES_BASE}/styles/${id}.json${render ? `?render=${encodeURIComponent(render)}` : ""}` : undefined;
// ArcGIS Pro / AGOL. One service per render, because Pro fetches the style with no query string
// and so cannot reach `?render=` — see tiles/README.md.
export const esriVectorTileUrl = (id: string, render?: string): string | undefined =>
  TILES_BASE
    ? `${TILES_BASE}/esri/${id}${render ? `/${encodeURIComponent(render)}` : ""}/VectorTileServer`
    : undefined;

// The Cloud-Optimized GeoTIFF asset (range-readable, rendered client-side via cog://).
export const cogAsset = (d: StacDoc | undefined): Asset | undefined =>
  Object.values(d?.assets ?? {}).find(
    (a) => a.type?.includes("profile=cloud-optimized")
      || a.roles?.includes("cloud-optimized")
      || a.href.endsWith(".cog.tif"),
  );

// A RASTER PMTiles asset (the per-scale geologic-map mosaics) — rendered as raster tiles via the
// pmtiles:// protocol. Distinguished from VECTOR PMTiles, which are declared as a web-map LINK
// (see pmtilesLink), not an asset: a `visual` pmtiles ASSET is a raster mosaic.
export const rasterTilesAsset = (d: StacDoc | undefined): Asset | undefined =>
  Object.values(d?.assets ?? {}).find(
    (a) => a.type?.includes("pmtiles")
      && (a.roles?.includes("visual") || (a as { "ugs:render"?: string })["ugs:render"] === "raster"),
  );

// STAC Table extension: the GeoParquet `data` asset's column schema (name + type). Undefined
// pre-reingest (the extension isn't emitted yet) → callers fall back / hide the panel.
export type TableColumn = { name: string; type?: string; description?: string };
export const tableColumns = (d: StacDoc | undefined): TableColumn[] | undefined => {
  const data = (d?.assets ?? {})["data"] as (Asset & { "table:columns"?: TableColumn[] }) | undefined;
  const cols = data?.["table:columns"];
  return Array.isArray(cols) && cols.length ? cols : undefined;
};

// The stable domain-key column used to anchor row-level review comments (default 'pk'). A layer can
// override via ugs:primary_key so a row comment resolves to the same feature across the viewer, the
// hazards-review map viewer, and PostGIS — unlike the ephemeral feature_id.
export const primaryKeyOf = (d: StacDoc | undefined): string =>
  String((d?.properties as Record<string, unknown> | undefined)?.["ugs:primary_key"] ?? "pk");

// STAC Classification extension: categorical value/name/color from properties.classification:classes
// → legend entries. Undefined pre-reingest → the legend falls back to deriving from the GL style.
export const classificationEntries = (
  d: StacDoc | undefined,
): Array<{ label: string; color: string }> | undefined => {
  const cls = (d?.properties as Record<string, unknown> | undefined)?.["classification:classes"];
  if (!Array.isArray(cls) || !cls.length) return undefined;
  return cls.map((c) => {
    const o = c as { name?: unknown; title?: unknown; value?: unknown; color_hint?: unknown };
    return {
      // `title` is the human label; `name` is a machine token (slug). Prefer the label.
      label: String(o.title ?? o.name ?? o.value ?? ""),
      color: typeof o.color_hint === "string" ? `#${o.color_hint}` : "#888888",
    };
  });
};

// classification:classes as a value→hex map (keyed by the class `value`/`name` token, e.g. a MapUnit)
// — for feature fills, where the standard legend (label→color) isn't enough. The built-in mechanism
// for per-unit authored colors; the warehouse stamps it (core/styles.classification_classes).
export const classificationColors = (d: StacDoc | undefined): Record<string, string> => {
  const cls = (d?.properties as Record<string, unknown> | undefined)?.["classification:classes"];
  const out: Record<string, string> = {};
  if (Array.isArray(cls)) {
    for (const c of cls) {
      const o = c as { name?: unknown; value?: unknown; color_hint?: unknown };
      const key = o.value ?? o.name;
      if (key != null && typeof o.color_hint === "string") out[String(key)] = `#${o.color_hint}`;
    }
  }
  return out;
};

// One entry of the STAC render extension (a named way to draw the layer).
export type RenderBlock = {
  title?: string; assets?: string[]; style_url?: string; sprite?: string;
  legend?: { label: string; color: string }[];   // explicit legend for icon renders (no paint)
};

// All renders on an item (empty when none). A layer can carry several (e.g. wells:
// by-purpose + by-boxtype) — the viewer offers a switcher over these.
export const rendersOf = (d?: StacDoc): Record<string, RenderBlock> =>
  (d?.properties as { "ugs:renders"?: Record<string, RenderBlock> } | undefined)?.["ugs:renders"] ?? {};

// Default MapLibre GL style_url from the `ugs:renders` block (ugs-styles bridge), if bound.
// Falls back to the first render. Undefined when the item carries no `ugs:renders`.
export const defaultStyleUrl = (d: StacDoc | undefined): string | undefined => {
  const renders = (d?.properties as { "ugs:renders"?: Record<string, { style_url?: string }> } | undefined)?.["ugs:renders"];
  if (!renders) return undefined;
  return (renders.default ?? Object.values(renders)[0])?.style_url;
};

async function fetchJson(url: string): Promise<StacDoc> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
  return r.json();
}

/** Fetch + cache any STAC doc by URL. `enabled` gates on a selected url. */
export function useStac(url?: string) {
  return useQuery({
    queryKey: ["stac", url],
    queryFn: () => fetchJson(url as string),
    enabled: Boolean(url),
    staleTime: 5 * 60_000,
  });
}

/** Fetch many STAC docs by URL in parallel (shares the ["stac", url] cache with
 *  useStac, so opening one later is instant). Powers counts + the catalog tables. */
export function useDocs(urls: string[]) {
  const results = useQueries({
    queries: urls.map((u) => ({
      queryKey: ["stac", u],
      queryFn: () => fetchJson(u),
      staleTime: 5 * 60_000,
    })),
  });
  return {
    docs: results.map((r, i) => ({ url: urls[i], data: r.data })),
    isLoading: results.some((r) => r.isLoading),
    loaded: results.filter((r) => r.data).length,
  };
}

// ---- MapLibre GL style layers (the `layers` array of a bound style_url) ----
// Via TanStack Query so it aborts on unmount, dedupes, and caches. null = loading/error (→ neutral).
async function fetchStyleLayers(url: string, signal?: AbortSignal): Promise<Record<string, unknown>[] | null> {
  const r = await fetch(url, { signal });
  const d = await r.json();
  return Array.isArray(d?.layers) ? (d.layers as Record<string, unknown>[]) : null;
}
export function useStyleLayers(styleUrl?: string): Record<string, unknown>[] | null {
  const { data } = useQuery({
    queryKey: ["gl-style-layers", styleUrl],
    queryFn: ({ signal }) => fetchStyleLayers(styleUrl as string, signal),
    enabled: Boolean(styleUrl),
    staleTime: 5 * 60_000,
  });
  return styleUrl ? (data ?? null) : null;
}
// ---- Live ugs-styles legend (icon renders) ----
// An icon render bakes its colors into a sprite PNG, so `legendFromStyle` has no paint to read and
// the legend has to come from ugs-styles. The copy on the STAC item is a SNAPSHOT taken whenever
// `attach_renders` last ran, so it goes stale the moment a style publishes — read the published
// manifest instead, which is the same source the snapshot was made from, minus the staleness.
//
// The manifest sits two levels above a render's style_url:
//   .../styles/styles/<layer>/<render>.json  ->  .../styles/index.json
const manifestUrlOf = (styleUrl: string): string | undefined => {
  try { return new URL("../../index.json", styleUrl).href; } catch { return undefined; }
};

type ManifestEntry = {
  itemId?: string; render?: string; field?: string;
  legend?: { label: string; color: string; values?: { value: string; color: string; label?: string }[] }[];
};

// `no-cache` forces a revalidation rather than trusting the manifest's max-age — a publish that
// just landed should show up here immediately, which is the whole point of reading it live.
async function fetchStylesManifest(url: string, signal?: AbortSignal): Promise<ManifestEntry[]> {
  const r = await fetch(url, { signal, cache: "no-cache" });
  const d = await r.json();
  return Array.isArray(d) ? (d as ManifestEntry[]) : [];
}

/**
 * The published legend for one (item, render), read live from ugs-styles. Returns undefined while
 * loading, on failure, or when the manifest has no entry — callers fall back to the STAC snapshot.
 */
export function useLiveLegend(styleUrl: string | undefined, itemId: string | undefined, renderId: string | undefined) {
  const url = styleUrl ? manifestUrlOf(styleUrl) : undefined;
  const { data } = useQuery({
    queryKey: ["styles-manifest", url],
    queryFn: ({ signal }) => fetchStylesManifest(url as string, signal),
    enabled: Boolean(url),
    staleTime: 60_000,
  });
  if (!data || !itemId || !renderId) return undefined;
  const hit = data.find((e) => e.itemId === itemId && e.render === renderId);
  return hit?.legend?.length ? { entries: hit.legend, field: hit.field } : undefined;
}

/** Same, for a set of layers at once (Map view overlays). Returns id→layers for those that resolved. */
export function useStyleLayersFor(layers: { id: string; styleUrl?: string }[]): Record<string, Record<string, unknown>[]> {
  const withStyle = layers.filter((l) => l.styleUrl);
  const results = useQueries({
    queries: withStyle.map((l) => ({
      queryKey: ["gl-style-layers", l.styleUrl],
      queryFn: ({ signal }: { signal?: AbortSignal }) => fetchStyleLayers(l.styleUrl as string, signal),
      staleTime: 5 * 60_000,
    })),
  });
  const out: Record<string, Record<string, unknown>[]> = {};
  withStyle.forEach((l, i) => { const d = results[i].data; if (d) out[l.id] = d; });
  return out;
}

// ---- COG (GeoTIFF) extents, read from the cog:// metadata, keyed + cached by href ----
// staleTime:Infinity (a COG's extent is immutable); retry:1 so a transient blip on a bbox-less COG
// isn't cached as a permanent failure. Returns href→bbox for those that resolved.
async function fetchCogBox(cogHref: string): Promise<[number, number, number, number] | null> {
  const { ensureCogProtocol } = await import("./cog");
  await ensureCogProtocol();
  const { getCogMetadata } = await import("@geomatico/maplibre-cog-protocol");
  const meta = await getCogMetadata(cogHref);
  const bb = meta?.bbox ? (meta.bbox as number[]).slice(0, 4) : null;
  return (bb && bb.length >= 4 ? bb : null) as [number, number, number, number] | null;
}
export function useCogBoxes(hrefs: (string | undefined)[]): Record<string, [number, number, number, number]> {
  const urls = [...new Set(hrefs.filter((h): h is string => Boolean(h)))];
  const results = useQueries({
    queries: urls.map((href) => ({
      queryKey: ["cog-bbox", href],
      queryFn: () => fetchCogBox(href),
      staleTime: Infinity,
      retry: 1,
    })),
  });
  const out: Record<string, [number, number, number, number]> = {};
  urls.forEach((href, i) => { const bb = results[i].data; if (bb) out[href] = bb; });
  return out;
}

// ---- compact items index (items.json, one fetch per collection) ----
// Each entry is a mini StacDoc (id, bbox, a props subset, asset + web-map-link summaries)
// — enough to render the list table, facets, and map overlays without N item.json fetches.
// The full item.json stays the source of truth and loads on open (useStac).
export type ItemsIndex = { type?: string; collection?: string; count?: number; items: StacDoc[] };

// items.json sits next to collection.json (…/<collection>/items.json).
const indexUrlFor = (collectionHref: string) =>
  collectionHref.replace(/collection\.json(\?.*)?$/, "items.json");

/** Fetch the compact items index for each given collection. Per-collection result carries
 *  the parsed index when present, or an error (e.g. 404 on a pre-index catalog) so the
 *  caller can fall back to per-item fetches. `retry: false` — a 404 is a fast, final miss. */
export function useIndexes(collections: { id: string; href: string }[]) {
  const results = useQueries({
    queries: collections.map((c) => ({
      queryKey: ["index", c.href],
      queryFn: () => fetchJson(indexUrlFor(c.href)) as Promise<unknown>,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  return collections.map((c, i) => ({
    id: c.id,
    href: c.href,
    index: results[i].data as ItemsIndex | undefined,
    isLoading: results[i].isLoading,
    missing: Boolean(results[i].error),
  }));
}

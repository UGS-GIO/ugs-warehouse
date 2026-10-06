// STAC data layer — TanStack Query over the static catalog (catalog.json -> collections
// -> items) on the CDN. No fetch useEffects; components call these hooks. JSON items stay
// the source of truth (the warehouse writes them via core/stac).
import { useQueries, useQuery } from "@tanstack/react-query";
import { qk } from "@/query-keys";

// Import guard: the pure item view-model (item-view.ts) reuses this module's asset helpers and is
// unit-tested in the node env, where there is no `location`. Read it through this shim so the module
// is importable without a DOM; in the browser it IS `location`, so behavior is unchanged.
const LOC: { search: string; href: string } =
  typeof location !== "undefined" ? location : { search: "", href: "http://localhost/" };

// Default catalog is build-time overridable (VITE_CATALOG_URL) so the INTERNAL/review deploy bakes
// the review catalog (review/stac) as its default while the public deploy keeps warehouse/stac —
// same codebase, one env var (the two-deploy, topology-enforced design). Runtime ?catalog= still wins.
export const DEFAULT_CATALOG =
  (import.meta as { env?: Record<string, string> }).env?.VITE_CATALOG_URL
  || "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json";
// ?catalog=<url> to point at sandbox / another deployment. Resolved to an absolute
// URL (a relative ?catalog=/stac/... would otherwise be an invalid base for `abs`).
export const CATALOG_URL = new URL(
  new URLSearchParams(LOC.search).get("catalog") || DEFAULT_CATALOG,
  LOC.href,
).href;

// Review deploy = catalog points at review/stac. Gates the review-only UI (diff, comments, dashboard).
// NOT a security boundary — real protection is server-side: the /api/comments route and review data only
// exist behind IAP on the review deploy. This flag just hides dead UI on the public build.
// `?review=1` forces it on for LOCAL DEV preview ONLY (gated to dev builds so prod can't toggle it).
const DEV = Boolean((import.meta as { env?: Record<string, unknown> }).env?.DEV);
export const IS_REVIEW = CATALOG_URL.includes("/review/") ||
  (DEV && new URLSearchParams(LOC.search).get("review") === "1");

// Unique collection key = the path from the catalog root to the collection folder, so a folder name
// that repeats across sub-catalogs (e.g. `B` under both ugs-external and ugs-publications) stays
// distinct: `ugs-external/B` vs `ugs-publications/B`. Derivable from any href — a collection.json,
// a catalog.json (sub-catalog), or an item's .json — without needing the loaded tree.
export const collKeyOf = (href?: string): string | undefined => {
  if (!href) return undefined;
  const clean = href.split(/[?#]/)[0];                  // drop any ?query / #hash before the $-anchored strips
  const base = CATALOG_URL.replace(/[^/]*$/, "");       // …/stac/
  const s = (clean.startsWith(base) ? clean.slice(base.length) : clean)
    .replace(/\/?(collection|catalog)\.json$/, "")      // a collection/sub-catalog url → its folder path
    .replace(/\/[^/]+\/[^/]+\.json$/, "");              // an item url → drop /<id>/<id>.json
  return s || undefined;
};
export const idOf = (href: string) => href.split("/").slice(-2)[0]; // item id = its folder name

// In-app catalog route (?c=<collection key>&i=<item id>) for a STAC item .json href, reusing
// collKeyOf so the `c` key matches how the catalog tree keys its collections. A foreign href (not
// under our catalog root) has no in-app route, so it is returned unchanged as a direct link.
export const catalogItemHref = (stacHref: string): string => {
  const base = CATALOG_URL.replace(/[^/]*$/, "");
  let abs: string;
  try { abs = new URL(stacHref, base).href; } catch { return stacHref; }  // resolve a relative catalog link
  const c = abs.startsWith(base) ? collKeyOf(abs) : undefined;            // foreign / off-catalog → passthrough
  return c ? `?c=${encodeURIComponent(c)}&i=${encodeURIComponent(idOf(abs))}` : stacHref;
};

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
// `href` is optional: topics reingest on their own schedule, so both shapes coexist until all cycle.
export type ForeignKey = {
  fields: string[];
  reference: { resource: string; href?: string; fields: string[] };
};
export type Asset = {
  href: string; title?: string; type?: string; roles?: string[];
  description?: string;   // one-line usage hint (display / query / download) stamped by core/stac (#280)
  "proj:code"?: string;   // per-asset CRS; overrides the item's (a reprojected COG carries its own)
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

// Click-join descriptors for a clicked feature of THIS layer: each related (aspatial child) asset
// carrying an FK back to this layer, flattened to the single-column equality we can run — the
// related row's childField = the clicked feature's parentField value. Composite (multi-column) keys
// are skipped: a partial join would return wrong rows, so surface nothing rather than bad data.
export type RelatedJoin = { key: string; title: string; href: string; childField: string; parentField: string };
export const relatedJoins = (d: StacDoc | undefined): RelatedJoin[] =>
  relatedAssets(d).flatMap(({ key, asset }) =>
    (asset["ugs:foreign_keys"] ?? [])
      .filter((fk) => fk.fields.length === 1 && fk.reference.fields.length === 1)
      .map((fk) => ({ key, title: asset.title ?? key, href: asset.href,
                      childField: fk.fields[0], parentField: fk.reference.fields[0] })));

// Per-asset usage hint (STAC `description`) — the warehouse's "display vs query vs download"
// guidance, stamped in core/stac so one item says which asset/endpoint to use (#280). Returns only
// the assets that carry a hint, paired with their key; a listing can render these. Rendering and
// asset selection elsewhere are unchanged — this only surfaces the metadata.
export const assetUsages = (d: StacDoc | undefined): { key: string; asset: Asset; usage: string }[] =>
  Object.entries(d?.assets ?? {})
    .filter(([, a]) => Boolean(a.description))
    .map(([key, asset]) => ({ key, asset, usage: asset.description as string }));

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

// OGC API Features endpoint base (the featureserv/ pygeoapi service). Set at build time via
// VITE_FEATURES_BASE, or per-session via ?features=<url>. Empty → the link is hidden (no dead
// link). A serving-topic's STAC item id == its featureserv collection id.
export const FEATURES_BASE = (
  new URLSearchParams(LOC.search).get("features")
  || ((import.meta as { env?: Record<string, string> }).env?.VITE_FEATURES_BASE)
  || ""
).replace(/\/+$/, "");
export const featuresCollectionUrl = (id: string): string | undefined =>
  FEATURES_BASE ? `${FEATURES_BASE}/collections/${id}` : undefined;

// XYZ vector tiles + Esri VectorTileServer (the `tiles/` service). Same shape as FEATURES_BASE:
// baked at build time, overridable per session, and empty hides the links rather than printing
// dead ones. The service serves every topic that has PMTiles, keyed by STAC item id.
export const TILES_BASE = (
  new URLSearchParams(LOC.search).get("tiles")
  || ((import.meta as { env?: Record<string, string> }).env?.VITE_TILES_BASE)
  || ""
).replace(/\/+$/, "");
export const xyzTilesUrl = (id: string): string | undefined =>
  TILES_BASE ? `${TILES_BASE}/tiles/${id}/{z}/{x}/{y}.mvt` : undefined;
export const tilesStyleUrl = (id: string, render?: string): string | undefined =>
  TILES_BASE ? `${TILES_BASE}/styles/${id}.json${render ? `?render=${encodeURIComponent(render)}` : ""}` : undefined;
// ArcGIS Pro / AGOL. One service per render, because Pro fetches the style with no query string
// and so cannot reach `?render=` — see tiles/README.md.
//
// The `/rest/services` prefix is required, not cosmetic: AGOL's "Add layer from URL" matches
// the path against ArcGIS Server's REST layout and rejects anything else before it makes a single
// request ("This service type is not supported"). The service also answers on `/esri/...`, but
// that form cannot be added in AGOL — so never hand it out here.
// Pass the topic's full render list so this can mirror the service's folder rule: a topic whose
// only render is `default` is a root-level SERVICE and must be addressed WITHOUT a render segment.
// Two reasons — `/rest/services` lists it as a bare service, so the render form would disagree and
// AGOL would make two portal items for one layer; and Esri takes the layer TITLE from the URL's
// last segment, so the `/default/` form imports as a layer named literally "Default".
// Omitting `renders` keeps the render segment, which is the safe default for an unknown topic.
export const esriVectorTileUrl = (id: string, render?: string, renders?: string[]): string | undefined => {
  if (!TILES_BASE) return undefined;
  const soloDefault = renders?.length === 1 && renders[0] === "default";
  const seg = render && !soloDefault ? `/${encodeURIComponent(render)}` : "";
  return `${TILES_BASE}/rest/services/${encodeURIComponent(id)}${seg}/VectorTileServer`;
};

const isCog = (a: Asset) =>
  a.type?.includes("profile=cloud-optimized")
  || a.roles?.includes("cloud-optimized")
  || a.href.endsWith(".cog.tif");

// The Cloud-Optimized GeoTIFF asset. An item may carry SEVERAL: the canonical raster in its source
// projection plus a reprojected derivative. `visual` marks the one meant to be drawn, so it wins —
// taking whichever came first in the assets object picked the native-CRS copy and the map threw
// "COG projection EPSG:26912 is not supported" (warehouse#84).
export const cogAsset = (d: StacDoc | undefined): Asset | undefined => {
  const cogs = Object.values(d?.assets ?? {}).filter(isCog);
  return cogs.find((a) => a.roles?.includes("visual")) ?? cogs[0];
};

// Can THIS client paint this COG over a web-mercator basemap? Only the `visual` derivative, or a
// COG that is already web mercator (or states no CRS, which is how a single-projection item reads).
// The renderer cannot reproject: handed a native-CRS raster it throws rather than drawing.
export const isDrawableCog = (a: Asset, d?: StacDoc): boolean => {
  if (!isCog(a)) return false;
  if (a.roles?.includes("visual")) return true;
  const crs = a["proj:code"] ?? (d?.properties as Record<string, unknown> | undefined)?.["proj:code"];
  return crs == null || crs === "EPSG:3857";
};

// The COG to draw, or undefined when the item has raster data but no render path — drawing nothing
// beats throwing on a projection this client cannot reproject. `visual` goes first: an items.json
// entry carries no proj:code, so the native-CRS COG would also pass as drawable.
export const cogRenderAsset = (d: StacDoc | undefined): Asset | undefined => {
  const assets = Object.values(d?.assets ?? {});
  return assets.find((a) => isCog(a) && a.roles?.includes("visual")) ?? assets.find((a) => isDrawableCog(a, d));
};

// A RASTER PMTiles asset (the per-scale geologic-map mosaics) — rendered as raster tiles via the
// pmtiles:// protocol. Marked `ugs:render: raster`: a vector layer's PMTiles is an asset with the
// `visual` role too (besides its web-map link), so the role says nothing about the tile type.
export const rasterTilesAsset = (d: StacDoc | undefined): Asset | undefined =>
  Object.values(d?.assets ?? {}).find(
    (a) => a.type?.includes("pmtiles") && (a as { "ugs:render"?: string })["ugs:render"] === "raster",
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

// The columns that identify a row, named by the producer (`ugs:summary_fields`). Consumers that
// can't show every column lead with these — the viewer's phone record cards. Absent → they guess.
export const summaryFieldsOf = (d: StacDoc | undefined): string[] => {
  const v = (d?.properties as Record<string, unknown> | undefined)?.["ugs:summary_fields"];
  return Array.isArray(v) ? v.map(String) : [];
};

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

// The URL each fetched doc came from: static catalogs link relatively (`../collection.json`) and
// often carry no `self`, so resolving a doc's links needs it.
const fetchedFrom = new WeakMap<object, string>();

async function fetchJson(url: string): Promise<StacDoc> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
  const doc = await r.json() as StacDoc;
  if (doc && typeof doc === "object") fetchedFrom.set(doc, url);
  return doc;
}

/** The query that fetches one STAC doc, shared by useStac and anything else needing the same doc. */
export const stacDocQuery = (url: string) => ({ queryKey: qk.stac(url), queryFn: () => fetchJson(url) });

/** A doc's collection, as an absolute URL: its `collection` (else `parent`) link, resolved against
 *  `base`, else the URL it was fetched from, else its `self` link. */
export function collectionHrefOf(doc: StacDoc | undefined, base?: string): string | undefined {
  const links = doc?.links ?? [];
  const link = links.find((l) => l.rel === "collection") ?? links.find((l) => l.rel === "parent");
  const absolute = (u?: string) => (u && /^[a-z][a-z0-9+.-]*:/i.test(u) ? u : undefined);
  const from = absolute(base) ?? (doc ? fetchedFrom.get(doc) : undefined) ?? absolute(links.find((l) => l.rel === "self")?.href);
  if (!link) return undefined;
  try {
    return new URL(link.href, from).href;
  } catch {
    return undefined;   // relative link and nothing to resolve it against
  }
}

/** Fetch + cache any STAC doc by URL. `enabled` gates on a selected url. */
export function useStac(url?: string) {
  return useQuery({ ...stacDocQuery(url as string), enabled: Boolean(url) });
}

/** Fetch many STAC docs by URL in parallel (shares the ["stac", url] cache with
 *  useStac, so opening one later is instant). Powers counts + the catalog tables. */
export function useDocs(urls: string[]) {
  const results = useQueries({
    queries: urls.map((u) => ({
      queryKey: qk.stac(u),
      queryFn: () => fetchJson(u),
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
    queryKey: qk.styleLayers(styleUrl),
    queryFn: ({ signal }) => fetchStyleLayers(styleUrl as string, signal),
    enabled: Boolean(styleUrl),
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
    queryKey: qk.stylesManifest(url),
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
      queryKey: qk.styleLayers(l.styleUrl),
      queryFn: ({ signal }: { signal?: AbortSignal }) => fetchStyleLayers(l.styleUrl as string, signal),
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
  const { ensureCogProtocol } = await import("./map/cog");
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
      queryKey: qk.cogBbox(href),
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
export type ItemsIndex = { type?: string; collection?: string | null; count?: number; items: StacDoc[] };

/** The catalog's root items.json: every item of this catalog in one request. */
export const ROOT_INDEX_URL = CATALOG_URL.replace(/catalog\.json(\?.*)?$/, "items.json");

export function useRootIndex(enabled: boolean) {
  return useQuery({
    queryKey: qk.index(ROOT_INDEX_URL),
    queryFn: async () => validIndex(await fetchJson(ROOT_INDEX_URL) as ItemsIndex, ROOT_INDEX_URL),
    enabled,
    retry: false,
  });
}

export const rootIndexItems = (idx: ItemsIndex) => idx.items.flatMap((d) => {
  const self = d.links?.find((l) => l.rel === "self")?.href;
  if (!self) return [];
  const href = new URL(self, ROOT_INDEX_URL).href;
  return [{ collId: collKeyOf(href) ?? "", href, data: d }];
});

// An index with no item list is an error, so the caller falls back to per-item links.
const validIndex = (doc: ItemsIndex, url: string): ItemsIndex => {
  if (!Array.isArray(doc?.items)) throw new Error(`no items list in ${url}`);
  return doc;
};

// items.json sits next to collection.json (…/<collection>/items.json). A nesting sub-catalog may
// publish one too (ugs-serving-topics), rolling up every child collection into one fetch.
const indexUrlFor = (collectionHref: string) =>
  collectionHref.replace(/(collection|catalog)\.json(\?.*)?$/, "items.json");

/** Whether a collection is in this catalog (same origin). */
export const hasItemsIndex = (collectionHref: string, catalogUrl = CATALOG_URL): boolean => {
  try {
    return new URL(collectionHref, LOC.href).origin === new URL(catalogUrl, LOC.href).origin;
  } catch {
    return false;
  }
};

/** Fetch the compact items index for each given collection. Per-collection result carries
 *  the parsed index when present, or an error (e.g. 404 on a pre-index catalog) so the
 *  caller can fall back to per-item fetches. `retry: false` — a 404 is a fast, final miss. */
export function useIndexes(collections: { id: string; href: string }[]) {
  const results = useQueries({
    queries: collections.map((c) => ({
      queryKey: qk.index(c.href),
      // A federated catalog may publish a STAC ItemCollection (`features`).
      queryFn: async () => {
        const doc = await fetchJson(indexUrlFor(c.href)) as ItemsIndex & { features?: StacDoc[] };
        return validIndex(doc?.features ? { ...doc, items: doc.features } : doc, c.href);
      },
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

export const isParquetAsset = (a: Asset): boolean =>
  Boolean(a.type?.includes("parquet")) || a.href.endsWith(".parquet");

export const parquetAsset = (item: StacDoc): Asset | undefined =>
  Object.values(item.assets ?? {}).find(isParquetAsset);

export const ducklakeAsset = (item: StacDoc): Asset | undefined =>
  Object.entries(item.assets ?? {}).find(([k, a]) => k === "ducklake"
    || a.roles?.includes("ducklake") || a.href.includes("ducklake"))?.[1];

// ---- datacube extension (zarr) ----
// https://github.com/stac-extensions/datacube — what turns a bare store href into something
// renderable: which dims are spatial vs. temporal, and which variables can be drawn.
export type CubeDimension = { type?: string; axis?: string; extent?: unknown[]; values?: unknown[]; step?: unknown };
export type CubeVariable = { dimensions?: string[]; type?: string; unit?: string; description?: string };

const cubeProp = <T,>(item: StacDoc, key: string): Record<string, T> =>
  ((item.properties?.[key] ?? (item as Record<string, unknown>)[key]) as Record<string, T>) ?? {};

export const cubeDimensions = (item: StacDoc): Record<string, CubeDimension> =>
  cubeProp<CubeDimension>(item, "cube:dimensions");

// Only variables STAC calls `data` are drawable; auxiliary/coordinate entries are metadata.
export const cubeVariables = (item: StacDoc): Record<string, CubeVariable> => {
  const all = cubeProp<CubeVariable>(item, "cube:variables");
  const data = Object.entries(all).filter(([, v]) => v.type === undefined || v.type === "data");
  return Object.fromEntries(data.length ? data : Object.entries(all));
};

// The dim a time picker drives. `type` is authoritative; the name is the fallback.
export const timeDimensionOf = (item: StacDoc): string | undefined =>
  Object.entries(cubeDimensions(item)).find(([n, d]) => d.type === "temporal" || n === "time")?.[0];

// Every dim that isn't x/y. ZarrLayer requires ALL of them pinned or sliced, and they are not all
// temporal — the climatology cubes key on `month` (type "other"), so keying off time alone throws.
export const nonSpatialDimensions = (item: StacDoc): string[] =>
  Object.entries(cubeDimensions(item))
    .filter(([n, d]) => d.type !== "spatial" && !["x", "y", "lat", "lon", "latitude", "longitude"].includes(n))
    .map(([n]) => n);

// One position along a non-spatial dim. The calendar fields a dated series resolves to drive the
// picker's selects (yearly: year; monthly: + month; daily: + day); anything else is just a label.
export type CubeStep = { label: string; year?: number; month?: number; day?: number };

export const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Resolution = "Y" | "M" | "D";

const monthStep = (month: number): CubeStep => ({ label: MONTH_NAMES[month - 1], month });

function dateStep(d: Date, res: Resolution): CubeStep {
  const year = d.getUTCFullYear(), month = d.getUTCMonth() + 1, day = d.getUTCDate();
  if (res === "Y") return { label: String(year), year };
  if (res === "M") return { label: `${MONTH_NAMES[month - 1]} ${year}`, year, month };
  return { label: `${MONTH_NAMES[month - 1]} ${day}, ${year}`, year, month, day };
}

const parseDate = (v: unknown): Date | undefined => {
  const d = typeof v === "string" ? new Date(v) : undefined;
  return d && !Number.isNaN(d.getTime()) ? d : undefined;
};

// Listed dates carry no step: the finest field that ever varies is the resolution.
const inferResolution = (ds: Date[]): Resolution =>
  ds.some((d) => d.getUTCDate() !== 1) ? "D" : ds.some((d) => d.getUTCMonth() !== 0) ? "M" : "Y";

// Generating more than this from an extent means a step we misread, not a real axis.
const MAX_STEPS = 5000;

/**
 * The steps of a non-spatial dim, in array order: from `values` when listed, else from `extent` +
 * `step` (yearly/monthly/daily ISO durations, or integers). Empty when STAC can't enumerate it.
 */
export function cubeSteps(item: StacDoc, dim: string): CubeStep[] {
  const d = cubeDimensions(item)[dim];
  if (!d) return [];
  const isMonth = dim === "month" || (d as { unit?: string }).unit === "month";
  const asMonth = (v: unknown) => (isMonth && Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 12
    ? monthStep(v as number) : undefined);

  if (Array.isArray(d.values) && d.values.length) {
    const dates = d.values.map(parseDate);
    if (dates.every(Boolean)) {
      const res = inferResolution(dates as Date[]);
      return (dates as Date[]).map((t) => dateStep(t, res));
    }
    return d.values.map((v) => asMonth(v) ?? { label: String(v) });
  }
  const [lo, hi] = d.extent ?? [];
  const start = parseDate(lo), end = parseDate(hi);
  if (start && end) {
    const m = typeof d.step === "string" ? /^P(\d+)([YMD])$/.exec(d.step) : null;
    if (!m) return [];
    const n = Number(m[1]), res = m[2] as Resolution;
    if (n <= 0) return [];   // P0D would never advance
    const out: CubeStep[] = [];
    for (let i = 0; out.length < MAX_STEPS; i += n) {
      const t = new Date(Date.UTC(
        start.getUTCFullYear() + (res === "Y" ? i : 0),
        start.getUTCMonth() + (res === "M" ? i : 0),
        start.getUTCDate() + (res === "D" ? i : 0),
      ));
      if (t > end) break;
      out.push(dateStep(t, res));
    }
    return out;
  }
  if (typeof lo === "number" && typeof hi === "number") {
    const step = typeof d.step === "number" && d.step > 0 ? d.step : 1;
    if ((hi - lo) / step >= MAX_STEPS) return [];
    const out: CubeStep[] = [];
    for (let v = lo; v <= hi; v += step) out.push(asMonth(v) ?? { label: String(v) });
    return out;
  }
  return [];
}

// Every pinned dim with its steps — what the step pickers draw and the layer slices by.
export const cubeStepDims = (item: StacDoc): Record<string, CubeStep[]> =>
  Object.fromEntries(nonSpatialDimensions(item).map((n) => [n, cubeSteps(item, n)]));

// A step as it reads in a URL: the date for a dated series (2010, 2010-07, 2010-07-15), the month
// number for a month dim, else the index. Survives the series growing at either end.
export function stepKey(step: CubeStep, index: number): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  if (step.year !== undefined) {
    return [String(step.year), step.month && pad(step.month), step.day && pad(step.day)].filter(Boolean).join("-");
  }
  return step.month !== undefined ? String(step.month) : String(index);
}

/** The picked step per dim, by key, as indexes. Unpicked or unknown: the latest step of a dated
 *  series (what people come for), else the first. */
export function resolveSelection(
  stepDims: Record<string, CubeStep[]>,
  picked: Record<string, string> = {},
): Record<string, number> {
  return Object.fromEntries(Object.entries(stepDims).map(([d, steps]) => {
    const at = picked[d] === undefined ? -1 : steps.findIndex((st, i) => stepKey(st, i) === picked[d]);
    const last = Math.max(0, steps.length - 1);
    return [d, at >= 0 ? at : steps[last]?.year !== undefined ? last : 0];
  }));
}

// A render-extension block (stac-extensions/render), titiler-style; `variable` picks a datacube
// variable the way titiler-xarray does.
type StacRender = { assets?: string[]; rescale?: number[][]; variable?: string };

/**
 * A datacube variable's display stretch from one STAC doc (an item, or its collection): the standard
 * render extension (`renders`), then the warehouse's `ugs:renders` (same shape). The render naming
 * this variable wins, else `default`. Undefined when the doc says nothing.
 */
export function cubeRenderRescale(item: StacDoc, variable: string): [number, number] | undefined {
  const props = (item.properties ?? {}) as Record<string, unknown>;
  for (const key of ["renders", "ugs:renders"]) {
    const renders = (props[key] ?? (item as Record<string, unknown>)[key]) as Record<string, StacRender> | undefined;
    if (!renders) continue;
    const r = Object.values(renders).find((x) => x.variable === variable) ?? renders.default;
    const [lo, hi] = r?.rescale?.[0] ?? [];
    if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) return [lo, hi];
  }
  return undefined;
}

export const zarrAsset = (item: StacDoc | undefined): Asset | undefined =>
  Object.values(item?.assets ?? {}).find((a) => assetKind(a) === "zarr");

const extOf = (href: string) => (href.split("?")[0].split(".").pop() ?? "").toLowerCase();

export type AssetKind = "cog" | "zarr" | "threeD" | "pdf" | "image" | "parquet" | "text" | "other";
export const KIND_RANK: Record<AssetKind, number> = { cog: 0, zarr: 0, threeD: 1, pdf: 2, parquet: 3, image: 4, text: 5, other: 9 };

export function assetKind(a: Asset): AssetKind {
  const t = (a.type ?? "").toLowerCase();
  const ext = extOf(a.href);
  if (a.roles?.includes("3d-vector") || ext.includes("3d") || a.href.includes("3d_polygons")) return "threeD";
  // Zarr before COG: a datacube asset carries no extension, only the media type.
  if (t.includes("zarr") || ext === "zarr") return "zarr";
  if (t.includes("profile=cloud-optimized") || a.roles?.includes("cloud-optimized") || a.href.endsWith(".cog.tif")) return "cog";
  if (t === "application/pdf" || ext === "pdf") return "pdf";
  if (t.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (t.includes("parquet") || ext === "parquet") return "parquet";
  if (t.startsWith("text/") || ["csv", "txt", "tsv"].includes(ext)) return "text";
  return "other";
}

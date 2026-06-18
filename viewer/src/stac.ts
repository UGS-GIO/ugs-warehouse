// STAC data layer — TanStack Query over the static catalog (catalog.json -> collections
// -> items) on the CDN. No fetch useEffects; components call these hooks. JSON items stay
// the source of truth (the warehouse writes them via core/stac).
import { useQueries, useQuery } from "@tanstack/react-query";

export const DEFAULT_CATALOG =
  "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json";
// ?catalog=<url> to point at sandbox / another deployment. Resolved to an absolute
// URL (a relative ?catalog=/stac/... would otherwise be an invalid base for `abs`).
export const CATALOG_URL = new URL(
  new URLSearchParams(location.search).get("catalog") || DEFAULT_CATALOG,
  location.href,
).href;

export type Link = {
  rel: string;
  href: string;
  title?: string;
  "pmtiles:layers"?: string[];
  "ugs:item_count"?: number;
};
export type Asset = { href: string; title?: string; type?: string; roles?: string[] };
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

// The Cloud-Optimized GeoTIFF asset (range-readable, rendered client-side via cog://).
export const cogAsset = (d: StacDoc | undefined): Asset | undefined =>
  Object.values(d?.assets ?? {}).find(
    (a) => a.type?.includes("profile=cloud-optimized")
      || a.roles?.includes("cloud-optimized")
      || a.href.endsWith(".cog.tif"),
  );

// Default MapLibre GL style_url from the render extension (ugs-styles bridge), if bound.
// Falls back to the first render. Undefined when the item carries no `renders`.
export const defaultStyleUrl = (d: StacDoc | undefined): string | undefined => {
  const renders = (d?.properties as { renders?: Record<string, { style_url?: string }> } | undefined)?.renders;
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

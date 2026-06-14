// STAC data layer — TanStack Query over the static catalog (catalog.json -> collections
// -> items) on the CDN. No fetch useEffects; components call these hooks. JSON items stay
// the source of truth (the warehouse writes them via core/stac).
import { useQuery } from "@tanstack/react-query";

export const DEFAULT_CATALOG =
  "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json";
// ?catalog=<url> to point at sandbox / another deployment.
export const CATALOG_URL =
  new URLSearchParams(location.search).get("catalog") || DEFAULT_CATALOG;

export type Link = {
  rel: string;
  href: string;
  title?: string;
  "pmtiles:layers"?: string[];
};
export type Asset = { href: string; title?: string; type?: string; roles?: string[] };
export type StacDoc = {
  id?: string;
  type?: string;
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

// Utah's 7.5' quads by name, from public/utah-quads.json (scripts/build_quad_gazetteer.py). The
// file sits at the app root, so the service worker installs it with the shell and quad search
// works offline. The bbox comes from the code, by the grid the offline basemap uses.
import type MiniSearch from "minisearch";
import { quadOf } from "@/offline/basemap";
import type { Bounds } from "./place-locator";

export const QUADS_URL = `${import.meta.env.BASE_URL}utah-quads.json`;

export type QuadHit = { name: string; code: string; bbox: Bounds };
export type QuadSearch = (q: string) => QuadHit[];

const LIMIT = 5;
// "Moab quad" and "moab quadrangle" find Moab.
const norm = (q: string) => q.trim().toLowerCase().replace(/\s+quad(rangle)?$/, "");

/** A search over the gazetteer's `[name, code]` rows. Exact and prefix name matches rank first. */
export async function quadSearch(rows: [string, string][]): Promise<QuadSearch> {
  const { default: MiniSearchCtor } = await import("minisearch");
  const index: MiniSearch<{ id: string; name: string }> = new MiniSearchCtor({
    fields: ["name"], storeFields: ["name"],
    searchOptions: { prefix: true, combineWith: "AND" },
  });
  index.addAll(rows.map(([name, code]) => ({ id: code, name })));
  return (q) => {
    const text = norm(q);
    if (text.length < 2) return [];
    const rank = (name: string) => (name.toLowerCase() === text ? 0 : name.toLowerCase().startsWith(text) ? 1 : 2);
    return index.search(text)
      .map((h, i) => ({ name: String(h.name), code: String(h.id), i }))
      .sort((a, b) => rank(a.name) - rank(b.name) || a.i - b.i)
      .slice(0, LIMIT)
      .map(({ name, code }) => {
        const { west, south, east, north } = quadOf(code);
        return { name, code, bbox: [west, south, east, north] };
      });
  };
}

export async function loadQuadSearch(signal?: AbortSignal): Promise<QuadSearch> {
  const r = await fetch(QUADS_URL, { signal });
  if (!r.ok) throw new Error(`quad gazetteer ${r.status}`);
  return quadSearch(await r.json());
}

/** Whether a hit should sit above the locator's places: its name starts with what was typed. */
export const isStrongQuad = (hit: QuadHit, q: string): boolean =>
  hit.name.toLowerCase().startsWith(norm(q));

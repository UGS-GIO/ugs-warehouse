// Utah's 7.5' and 30'x60' quadrangles by name, from public/utah-quads.json
// (scripts/build_quad_gazetteer.py). The file sits at the app root, so the service worker installs
// it with the shell and quad search works offline. The bbox comes from the code, by the grid the
// offline basemap uses: a 30'x60' sheet is coded by its SE 7.5' cell.
import type MiniSearch from "minisearch";
import { quadOf } from "@/offline/basemap";
import type { Bounds } from "./place-locator";

export const QUADS_URL = `${import.meta.env.BASE_URL}utah-quads.json`;

export type Scale = "24k" | "100k";
export type Gazetteer = Record<Scale, [string, string][]>;
export type QuadHit = { name: string; code: string; scale: Scale; bbox: Bounds };
export type QuadSearch = (q: string) => QuadHit[];

const LIMIT = 5;
// A trailing "quad", "quadrangle", "30x60" or "100k" is dropped; the last two pick the 30'x60's.
const SUFFIX = /\s+(quad(rangle)?|30\s*['′]?\s*[x×]\s*60\s*['′]?(\s+quad(rangle)?)?|100k)$/;
const parse = (q: string) => {
  const raw = q.trim().toLowerCase();
  const m = SUFFIX.exec(raw);
  return { text: m ? raw.slice(0, m.index) : raw, only100k: !!m && !m[1].startsWith("quad") };
};

export function bboxOf(code: string, scale: Scale): Bounds {
  const { west, south, east, north } = quadOf(code);
  return scale === "24k" ? [west, south, east, north] : [east - 1, south, east, south + 0.5];
}

/** A search over the gazetteer. Exact and prefix name matches rank first. */
export async function quadSearch(g: Gazetteer): Promise<QuadSearch> {
  const { default: MiniSearchCtor } = await import("minisearch");
  type Doc = { id: string; name: string; code: string; scale: Scale };
  const index: MiniSearch<Doc> = new MiniSearchCtor({
    fields: ["name"], storeFields: ["name", "code", "scale"],
    searchOptions: { prefix: true, combineWith: "AND" },
  });
  for (const scale of ["24k", "100k"] as const) {
    index.addAll(g[scale].map(([name, code]) => ({ id: `${scale}:${code}`, name, code, scale })));
  }
  return (q) => {
    const { text, only100k } = parse(q);
    if (text.length < 2) return [];
    const rank = (name: string) => {
      const lower = name.toLowerCase();
      return lower === text ? 0 : lower.startsWith(text) ? 1 : 2;
    };
    return index.search(text, only100k ? { filter: (h) => h.scale === "100k" } : undefined)
      .map((h, i) => ({ name: String(h.name), code: String(h.code), scale: h.scale as Scale, i }))
      .sort((a, b) => rank(a.name) - rank(b.name) || a.i - b.i)
      .slice(0, LIMIT)
      .map(({ name, code, scale }) => ({ name, code, scale, bbox: bboxOf(code, scale) }));
  };
}

export async function loadQuadSearch(signal?: AbortSignal): Promise<QuadSearch> {
  const r = await fetch(QUADS_URL, { signal });
  if (!r.ok) throw new Error(`quad gazetteer ${r.status}`);
  return quadSearch(await r.json());
}

/** Whether a hit should sit above the locator's places: its name starts with what was typed. */
export const isStrongQuad = (hit: QuadHit, q: string): boolean =>
  hit.name.toLowerCase().startsWith(parse(q).text);

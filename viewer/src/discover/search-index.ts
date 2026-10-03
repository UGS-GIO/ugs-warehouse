// The shared MiniSearch index builder + the flat doc shapes it indexes. Extracted from search.tsx so
// the Discover view searches the SAME index (one builder, one config) rather than a second engine.
// No React; MiniSearch is the only runtime import, so this stays out of the main bundle — both
// consumers (the lazy Search view and the lazy Discover view) pull it into their own chunks.
import MiniSearch, { type Query, type SearchOptions, type SearchResult } from "minisearch";
import type { StacDoc } from "@/stac";

export type Article = {
  id: string; sid: string; volume: number | null; issue?: string;
  pdf?: string; title: string; page: number | null; text: string; topic?: string;
};

// A catalog item flattened for search (pub or vector layer). `ids` are the ids people type to find it:
// its own and a publication's series id.
export type CatalogDoc = {
  id: string; collId: string; itemId: string; title: string; keywords?: string; meta?: string; ids?: string[];
};

export type Hit = {
  id: string; kind: "article" | "item"; title: string; text?: string; keywords?: string; idText?: string;
  sid?: string; pdf?: string; page?: number | null; volume?: number | null; issue?: string;
  collId?: string; itemId?: string; topic?: string; score: number;
};

// Indexed per id: the whole id without hyphens ("OFR771") and what follows the series code ("771"),
// so "OFR-771", "OFR771" and "OFR-77" all match. The series code is never a word on its own: "mp"
// or "md" would fuzzy-match ordinary words like "map" and "mid".
const idTerms = (ids: string[]): string =>
  ids.flatMap((id) => (id.includes("-")
    ? [id.replace(/-/g, ""), id.slice(id.indexOf("-") + 1).replace(/-/g, "")]
    : [id])).join(" ");

// An id as typed, ignoring case, with spaces standing in for hyphens ("ofr 771" is OFR-771).
const idKey = (s: string): string => s.trim().toUpperCase().replace(/\s+/g, "-");
// Looser still, without hyphens ("OFR771"). Different ids can share it (MD-86-7 and MD-867).
const looseKey = (s: string): string => idKey(s).replace(/-/g, "");

// Records which doc a key names; a key two docs share names neither (null).
const claim = (keys: Map<string, string | null>, key: string, docId: string): void => {
  const cur = keys.get(key);
  keys.set(key, cur === undefined || cur === docId ? docId : null);
};

// A query that is a whole id returns that item first, as the header search's exact match does. The
// item is looked up, not searched for: ranking can't promise it (M-290 loses to a title full of
// m-words), and an id like CR-91-14DF needn't tokenize into a match at all.
class CatalogIndex extends MiniSearch<Hit> {
  private readonly byKey = new Map<string, string | null>();
  private readonly byLooseKey = new Map<string, string | null>();

  addIds(docId: string, ids: string[]): void {
    for (const id of ids) {
      claim(this.byKey, idKey(id), docId);
      claim(this.byLooseKey, looseKey(id), docId);
    }
  }

  // The doc a whole-id query names, if exactly one does. An ambiguous exact key stops there rather
  // than falling back to the looser one.
  private named(query: string): string | undefined {
    const exact = this.byKey.get(idKey(query));
    if (exact !== undefined) return exact ?? undefined;
    return this.byLooseKey.get(looseKey(query)) ?? undefined;
  }

  override search(query: Query, options?: SearchOptions): SearchResult[] {
    const hits = super.search(query, options);
    // A caller's own options (a filter, other fields) stay in charge of what comes back.
    const id = typeof query === "string" && !options ? this.named(query) : undefined;
    if (id === undefined) return hits;
    const top = hits[0]?.score ?? 1;
    const found = hits.find((h) => h.id === id);
    const exact = found
      ? { ...found, score: Math.max(found.score, top) }
      : { ...this.getStoredFields(id), id, score: top, terms: [], queryTerms: [], match: {} };
    return [exact, ...hits.filter((h) => h.id !== id)];
  }
}

// One catalog item (a compact index record or a full item) → the flat CatalogDoc the index consumes.
// Discover and the header search both build their docs here, so they index items identically, and a
// hit's `id` (`{collId}/{itemId}`) round-trips to the item.
export function toSearchDoc(collId: string, d: StacDoc): CatalogDoc {
  const p = (d.properties ?? {}) as Record<string, unknown>;
  const itemId = String(d.id);
  return {
    id: `${collId}/${itemId}`,
    collId,
    itemId,
    title: String(p.title ?? d.id),
    keywords: String(p.keywords ?? ""),
    meta: [p["ugs:series"], p["ugs:topic"], p["ugs:pub_type"]].filter(Boolean).join(" · "),
    ids: [...new Set([itemId, p["ugs:series_id"]].filter((v): v is string => typeof v === "string" && v !== ""))],
  };
}

// Build the combined index + a flat doc list (the latter powers field-only queries like `series:GQ`,
// which have no keyword to hand MiniSearch). Plain function, memoized by the caller. Pass `[]` articles
// to index catalog items only (the Discover view's case).
export function buildIndex(articles: Article[], catalog: CatalogDoc[]) {
  const docs: Hit[] = [
    ...articles.map((a) => ({
      id: a.id, kind: "article" as const, title: a.title, text: a.text, keywords: "",
      sid: a.sid, pdf: a.pdf, page: a.page, volume: a.volume, issue: a.issue, topic: a.topic, score: 0,
    })),
    ...catalog.map((c) => ({
      id: c.id, kind: "item" as const, title: c.title, text: c.meta ?? "", keywords: c.keywords ?? "",
      idText: idTerms(c.ids ?? []), collId: c.collId, itemId: c.itemId, page: null, score: 0,
    })),
  ];
  const ms = new CatalogIndex({
    fields: ["title", "text", "keywords", "idText"],
    storeFields: ["kind", "title", "text", "keywords", "sid", "pdf", "page", "volume", "issue", "collId", "itemId", "topic"],
    searchOptions: { boost: { title: 4 }, prefix: true, fuzzy: 0.2, combineWith: "AND" },
  });
  ms.addAll(docs);
  for (const c of catalog) ms.addIds(c.id, c.ids ?? []);
  return { index: ms, docs };
}

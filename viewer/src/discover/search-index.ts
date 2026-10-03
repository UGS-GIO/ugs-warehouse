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
// A key without the format letters after its number: OFR-771DM is cited as OFR-771, M-290DR as M-290.
const bareKey = (key: string): string => key.replace(/(\d)[A-Z]+$/, "$1");

// Which doc a key names, and the id it came from; null when two different ids share the key.
type Claim = { docId: string; id: string } | null;
// Ids that differ only by case are one publication listed twice, so the first listing keeps the key.
const claim = (keys: Map<string, Claim>, key: string, docId: string, id: string): void => {
  const cur = keys.get(key);
  if (cur === undefined) keys.set(key, { docId, id });
  else if (cur && cur.docId !== docId && idKey(cur.id) !== idKey(id)) keys.set(key, null);
};

// A query that is a whole id returns that item first, as the header search's exact match does. The
// item is looked up, not searched for: ranking can't promise it (M-290 loses to a title full of
// m-words), and an id like CR-91-14DF needn't tokenize into a match at all.
class CatalogIndex extends MiniSearch<Hit> {
  private readonly byKey = new Map<string, Claim>();
  private readonly byLooseKey = new Map<string, Claim>();
  private readonly byBareKey = new Map<string, Claim>();
  private readonly byLooseBareKey = new Map<string, Claim>();

  addIds(docId: string, ids: string[]): void {
    for (const id of ids) {
      const key = idKey(id);
      const loose = looseKey(id);
      const bare = bareKey(key);
      const looseBare = bareKey(loose);
      claim(this.byKey, key, docId, id);
      claim(this.byLooseKey, loose, docId, id);
      if (bare !== key) claim(this.byBareKey, bare, docId, id);
      if (looseBare !== loose) claim(this.byLooseBareKey, looseBare, docId, id);
    }
  }

  // The doc a whole-id query names, if exactly one does: by the id as typed, then without hyphens,
  // then without format letters. The first form some doc has decides, so an ambiguous one stops
  // there rather than falling back to a looser form.
  private named(query: string): string | undefined {
    const key = idKey(query);
    const loose = looseKey(query);
    const forms: [Map<string, Claim>, string][] = [
      [this.byKey, key], [this.byLooseKey, loose], [this.byBareKey, key], [this.byLooseBareKey, loose],
    ];
    for (const [keys, k] of forms) {
      const found = keys.get(k);
      if (found !== undefined) return found?.docId;
    }
    return undefined;
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
    // Typos count only in words of four letters or more: one slip in a short code or a number
    // ("ofr" to "of", "771" to "791") lands on something else entirely.
    searchOptions: {
      boost: { title: 4 }, prefix: true, combineWith: "AND",
      fuzzy: (term: string) => (term.length < 4 || /\d/.test(term) ? false : 0.2),
    },
  });
  ms.addAll(docs);
  for (const c of catalog) ms.addIds(c.id, c.ids ?? []);
  return { index: ms, docs };
}

// The shared MiniSearch index builder + the flat doc shapes it indexes. Extracted from search.tsx so
// the Discover view searches the SAME index (one builder, one config) rather than a second engine.
// No React; MiniSearch is the only runtime import, so this stays out of the main bundle — both
// consumers (the lazy Search view and the lazy Discover view) pull it into their own chunks.
import MiniSearch, { type SearchResult } from "minisearch";
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
  id: string; kind: "article" | "item"; title: string; text?: string; keywords?: string;
  // The item's ids, one per line: `ids` indexes each whole, `idParts` by the part after the series code.
  ids?: string; idParts?: string;
  sid?: string; pdf?: string; page?: number | null; volume?: number | null; issue?: string;
  collId?: string; itemId?: string; topic?: string; score: number;
};

// An id with case, spaces and hyphens dropped, so OFR-771, ofr771 and OFR 771 read the same.
const compactId = (s: string): string => s.toLowerCase().replace(/[\s-]/g, "");

// The terms `ids` indexes for one id: its compact form ("ofr771dm") and that form without the format
// letters after the number ("ofr771"), since OFR-771DM is cited as OFR-771.
const wholeIdTerms = (id: string): string[] => {
  const compact = compactId(id);
  return [...new Set([compact, compact.replace(/(\d)[a-z]+$/, "$1")])];
};

// The term `idParts` indexes: what follows the series code ("771dm"), so a partial id like OFR-77
// matches in a ranked search. It stays out of the exact lookup, where a bare number would name an
// item. Never the series code alone: "mp" or "md" would fuzzy-match ordinary words like "map" and "mid".
const idPart = (id: string): string => (id.includes("-") ? compactId(id.slice(id.indexOf("-") + 1)) : "");

// MiniSearch's own tokenizer and term processing, which every field but `ids` keeps.
const defaultTokenize: (text: string) => string[] = MiniSearch.getDefault("tokenize");
const defaultProcessTerm: (term: string) => string | null | undefined = MiniSearch.getDefault("processTerm");

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
    ...catalog.map((c) => {
      const ids = (c.ids ?? []).join("\n");
      return {
        id: c.id, kind: "item" as const, title: c.title, text: c.meta ?? "", keywords: c.keywords ?? "",
        ids, idParts: ids, collId: c.collId, itemId: c.itemId, page: null, score: 0,
      };
    }),
  ];
  const ms = new MiniSearch<Hit>({
    fields: ["title", "text", "keywords", "ids", "idParts"],
    storeFields: ["kind", "title", "text", "keywords", "sid", "pdf", "page", "volume", "issue", "collId", "itemId", "topic"],
    // The two id fields read whole ids, one per line; every other field keeps MiniSearch's defaults.
    tokenize: (text, field) =>
      (field === "ids" || field === "idParts" ? text.split("\n").filter(Boolean) : defaultTokenize(text)),
    processTerm: (term, field) => {
      if (field === "ids") return wholeIdTerms(term);
      if (field === "idParts") return idPart(term);
      return defaultProcessTerm(term);
    },
    // Typos count only in words of four letters or more: one slip in a short code or a number
    // ("ofr" to "of", "771" to "791") lands on something else entirely.
    searchOptions: {
      boost: { title: 4, ids: 2 }, prefix: true, combineWith: "AND",
      fuzzy: (term: string) => (term.length < 4 || /\d/.test(term) ? false : 0.2),
    },
  });
  ms.addAll(docs);
  return { index: ms, docs };
}

// The one item a whole-id query names: an exact search of the `ids` field, with no prefix or typo
// matching. More than one hit is ambiguous (MD-86-7 and MD-867 read the same), so none is named.
export function idMatch(index: MiniSearch<Hit>, q: string): SearchResult | undefined {
  const hits = index.search(q, {
    fields: ["ids"], prefix: false, fuzzy: false, tokenize: (s) => [s], processTerm: compactId,
  });
  return hits.length === 1 ? hits[0] : undefined;
}

// Ranked search with the item a whole-id query names first. Ranking alone can't promise it: M-290
// loses to a title full of m-words, and an id like CR-91-14DF needn't tokenize into a match at all.
export function searchCatalog(index: MiniSearch<Hit>, q: string): SearchResult[] {
  const named = idMatch(index, q);
  const hits = index.search(q);
  return named ? [named, ...hits.filter((h) => h.id !== named.id)] : hits;
}

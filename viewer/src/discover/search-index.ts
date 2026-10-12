// The shared MiniSearch index builder + the flat doc shapes it indexes. Extracted from search.tsx so
// the Discover view searches the SAME index (one builder, one config) rather than a second engine.
// No React; MiniSearch is the only runtime import, so this stays out of the main bundle — both
// consumers (the lazy Search view and the lazy Discover view) pull it into their own chunks.
import MiniSearch, { type SearchResult } from "minisearch";
import type { ItemRef } from "@/catalog/browse";
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
  // The item's ids, one per line: `ids` indexes each whole, `idBare` without the format letters after
  // the number, and `idParts` by the part after the series code.
  ids?: string; idBare?: string; idParts?: string;
  sid?: string; pdf?: string; page?: number | null; volume?: number | null; issue?: string;
  collId?: string; itemId?: string; topic?: string; score: number;
};

// An id with case, spaces and hyphens dropped, so OFR-771, ofr771 and OFR 771 read the same.
const compactId = (s: string): string => s.toLowerCase().replace(/[\s-]/g, "");

// The term `idBare` indexes for one id: its compact form without the format letters after the number
// ("ofr771" for OFR-771DM). DM or DR is added once a publication goes digital, so both forms are valid
// ids for it. Empty when the id has no such letters, since `ids` already holds that form.
const bareId = (id: string): string => {
  const compact = compactId(id);
  const bare = compact.replace(/(\d)[a-z]+$/, "$1");
  return bare === compact ? "" : bare;
};

// The term `idParts` indexes: what follows the series code ("771dm"), so a partial id like OFR-77
// matches in a ranked search. It stays out of the exact lookup, where a bare number would name an
// item. Never the series code alone: "mp" or "md" would fuzzy-match ordinary words like "map" and "mid".
const idPart = (id: string): string => (id.includes("-") ? compactId(id.slice(id.indexOf("-") + 1)) : "");

// MiniSearch's own tokenizer and term processing, which every field but the id fields keeps.
const defaultTokenize: (text: string) => string[] = MiniSearch.getDefault("tokenize");
const defaultProcessTerm: (term: string) => string | null | undefined = MiniSearch.getDefault("processTerm");
const ID_FIELDS = new Set(["ids", "idBare", "idParts"]);

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

// Articles and catalog docs as the flat documents the index stores.
function toHits(articles: Article[], catalog: CatalogDoc[]): Hit[] {
  return [
    ...articles.map((a) => ({
      id: a.id, kind: "article" as const, title: a.title, text: a.text, keywords: "",
      sid: a.sid, pdf: a.pdf, page: a.page, volume: a.volume, issue: a.issue, topic: a.topic, score: 0,
    })),
    ...catalog.map((c) => {
      const ids = (c.ids ?? []).join("\n");
      return {
        id: c.id, kind: "item" as const, title: c.title, text: c.meta ?? "", keywords: c.keywords ?? "",
        ids, idBare: ids, idParts: ids, collId: c.collId, itemId: c.itemId, page: null, score: 0,
      };
    }),
  ];
}

// Build the combined index + a flat doc list (the latter powers field-only queries like `series:GQ`,
// which have no keyword to hand MiniSearch). Plain function, memoized by the caller. Pass `[]` articles
// to index catalog items only (the Discover view's case).
export function buildIndex(articles: Article[], catalog: CatalogDoc[]) {
  const docs = toHits(articles, catalog);
  const ms = new MiniSearch<Hit>({
    fields: ["title", "text", "keywords", "ids", "idBare", "idParts"],
    storeFields: ["kind", "title", "text", "keywords", "sid", "pdf", "page", "volume", "issue", "collId", "itemId", "topic"],
    // The id fields read whole ids, one per line; every other field keeps MiniSearch's defaults.
    tokenize: (text, field) =>
      (ID_FIELDS.has(field ?? "") ? text.split("\n").filter(Boolean) : defaultTokenize(text)),
    processTerm: (term, field) => {
      if (field === "ids") return compactId(term);
      if (field === "idBare") return bareId(term);
      if (field === "idParts") return idPart(term);
      return defaultProcessTerm(term);
    },
    // Typos count only in words of four letters or more: one slip in a short code or a number
    // ("ofr" to "of", "771" to "791") lands on something else entirely.
    searchOptions: {
      boost: { title: 4, ids: 2, idBare: 2 }, prefix: true, combineWith: "AND",
      fuzzy: (term: string) => (term.length < 4 || /\d/.test(term) ? false : 0.2),
    },
  });
  ms.addAll(docs);
  return { index: ms, docs };
}

// One catalog index for the page, shared by Discover and the search box. `key` is App's mapLoadKey,
// which changes each time another collection streams in, so the index grows by the new items
// instead of being rebuilt; it is rebuilt only when items go away.
type Built = ReturnType<typeof buildIndex>;
let shared: { key: string; built: Built; ids: Set<string> } | null = null;
export function catalogIndex(key: string, items: ItemRef[]): Built {
  if (shared?.key === key) return shared.built;
  const docs = items.flatMap((r) => (r.data ? [toSearchDoc(r.collId, r.data)] : []));
  const ids = new Set(docs.map((d) => d.id));
  const added = shared && [...shared.ids].every((id) => ids.has(id))
    ? docs.filter((d) => !shared!.ids.has(d.id)) : null;
  if (!shared || !added) {
    shared = { key, built: buildIndex([], docs), ids };
    return shared.built;
  }
  const fresh = toHits([], added);
  shared.built.index.addAll(fresh);
  shared = { key, built: { index: shared.built.index, docs: [...shared.built.docs, ...fresh] }, ids };
  return shared.built;
}

// The one item a whole-id query names: an exact search, with no prefix or typo matching, of the whole
// ids and then of the ids without their format letters. So M-205 names M-205 even though M-205DM
// exists, and OFR-771 names OFR-771DM. More than one hit is ambiguous (MD-86-7 and MD-867 read the
// same), so none is named.
export function idMatch(index: MiniSearch<Hit>, q: string): SearchResult | undefined {
  for (const field of ["ids", "idBare"]) {
    const hits = index.search(q, {
      fields: [field], prefix: false, fuzzy: false, tokenize: (s) => [s], processTerm: compactId,
    });
    if (hits.length) return hits.length === 1 ? hits[0] : undefined;
  }
  return undefined;
}

// Ranked search with the item a whole-id query names first. Ranking alone can't promise it: M-290
// loses to a title full of m-words, and an id like CR-91-14DF needn't tokenize into a match at all.
export function searchCatalog(index: MiniSearch<Hit>, q: string): SearchResult[] {
  const named = idMatch(index, q);
  const hits = index.search(q);
  return named ? [named, ...hits.filter((h) => h.id !== named.id)] : hits;
}

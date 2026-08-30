// The shared MiniSearch index builder + the flat doc shapes it indexes. Extracted from search.tsx so
// the Discover view searches the SAME index (one builder, one config) rather than a second engine.
// No React; MiniSearch is the only runtime import, so this stays out of the main bundle — both
// consumers (the lazy Search view and the lazy Discover view) pull it into their own chunks.
import MiniSearch from "minisearch";
import type { StacDoc } from "./stac";

export type Article = {
  id: string; sid: string; volume: number | null; issue?: string;
  pdf?: string; title: string; page: number | null; text: string; topic?: string;
};

// A catalog item flattened for search (pub or vector layer).
export type CatalogDoc = {
  id: string; collId: string; itemId: string; title: string; keywords?: string; meta?: string;
};

export type Hit = {
  id: string; kind: "article" | "item"; title: string; text?: string; keywords?: string;
  sid?: string; pdf?: string; page?: number | null; volume?: number | null; issue?: string;
  collId?: string; itemId?: string; topic?: string; score: number;
};

// One catalog item (a compact index record or a full item) → the flat CatalogDoc the index consumes.
// The SAME projection App builds for the Search view (title / keywords / series·topic·pub_type meta),
// so both views index items identically and a hit's `id` (`{collId}/{itemId}`) round-trips to the item.
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
      collId: c.collId, itemId: c.itemId, page: null, score: 0,
    })),
  ];
  const ms = new MiniSearch({
    fields: ["title", "text", "keywords"],
    storeFields: ["kind", "title", "text", "keywords", "sid", "pdf", "page", "volume", "issue", "collId", "itemId", "topic"],
    searchOptions: { boost: { title: 4 }, prefix: true, fuzzy: 0.2, combineWith: "AND" },
  });
  ms.addAll(docs);
  return { index: ms, docs };
}

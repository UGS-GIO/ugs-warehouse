// Survey Notes article search, off the page's thread. Indexing ~1,000 articles takes most of a second,
// which froze the page on the first search when it ran there.
import * as Comlink from "comlink";

import { baseTerms, isEmptyQuery, matchesQuery, parseQuery, type SearchDoc } from "@/data/query";

import { type Article, buildIndex, type Hit } from "./search-index";

type Index = ReturnType<typeof buildIndex>["index"];
let index: Promise<Index> | null = null;

function load(url: string): Promise<Index> {
  return (index ??= fetch(url)
    .then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return buildIndex((await r.json()) as Article[], []).index;
    })
    .catch((e) => { index = null; throw e; }));
}

const api = {
  /** The top 20 articles for a query, in the same query language as the catalog search. */
  async search(url: string, q: string): Promise<Hit[]> {
    const query = parseQuery(q);
    if (q.trim().length < 2 && isEmptyQuery(query)) return [];
    const base = baseTerms(query);
    if (!base) return [];   // a field-only query addresses catalog metadata, not article prose
    return ((await load(url)).search(base) as unknown as Hit[])
      .filter((h) => matchesQuery(query, h as SearchDoc)).slice(0, 20);
  },
};

Comlink.expose(api);
export type ArticleApi = typeof api;

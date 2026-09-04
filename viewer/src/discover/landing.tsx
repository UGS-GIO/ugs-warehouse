// The front door (the param-less default view): a search that hands off to Discover, a category
// index with counts, and a "Recently updated" strip. It derives everything
// from the item set App has ALREADY loaded (the same mapItems the Map/Discover views use) — no new
// fetch — memoized on App's stable mapLoadKey, never the array identity, so it never re-indexes the
// ~7.6k docs on an unrelated render. Mirrors ugs-data-catalog/src/routes/index.tsx onto UDS tokens.
import { type FormEvent, useMemo, useState } from "react";

import type { ItemRef } from "../catalog/browse";
import { CATEGORIES, categorize, dateOf } from "../catalog/item-view";
import { useIsWide } from "../ui/use-breakpoint";
import { type LinkAttrs, ResultCard } from "../catalog/result-card";

const RECENT_COUNT = 6;

type Tile = { key: string; label: string; count: number };

// Count each item's one home category, then order the tiles by the taxonomy (schema topics first),
// keeping only the categories that actually have items.
function categoryTiles(items: ItemRef[]): Tile[] {
  const counts = new Map<string, number>();
  for (const it of items) {
    const { key } = categorize(it);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return CATEGORIES
    .map((c) => ({ key: c.key, label: c.label, count: counts.get(c.key) ?? 0 }))
    .filter((t) => t.count > 0);
}

// The most-recently-dated items, newest first — a real datetime only (an undated pub never jumps in).
function recentlyUpdated(items: ItemRef[]): ItemRef[] {
  return items
    .map((it) => ({ it, d: dateOf(it) }))
    .filter(({ d }) => d)
    .sort((a, b) => b.d.localeCompare(a.d))
    .slice(0, RECENT_COUNT)
    .map(({ it }) => it);
}

export function Landing({ items, itemsKey, loading, onSearch, onOpenCategory }: {
  items: ItemRef[];
  itemsKey: string;                       // App's mapLoadKey — the stable memo key for the item set
  loading: boolean;                       // the catalog crawl is still streaming → counts not final yet
  onSearch: (text: string) => void;       // → /discover?q=…
  onOpenCategory: (key: string) => void;  // → /discover?category=…
}) {
  const [text, setText] = useState("");
  const isWide = useIsWide();   // below lg a result opens the full page, not Discover's side drawer
  const withData = useMemo(() => items.filter((it) => it.data), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const tiles = useMemo(() => categoryTiles(withData), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const recent = useMemo(() => recentlyUpdated(withData), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const onSubmit = (e: FormEvent) => { e.preventDefault(); onSearch(text.trim()); };

  // A non-hover result link (Landing has no map to sync) that opens the item in Discover.
  const link = (it: ItemRef): LinkAttrs => ({
    to: isWide ? "/discover" : "/catalog",
    search: { c: it.collId, i: it.href.split("/").slice(-2)[0] },
    "data-href": it.href,
  });

  return (
    <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
      {/* No hero. This is a catalog front door, not a product page: the search and the categories
          are the content, so they start at the top of the fold instead of under a tagline.
          The h1 is sr-only — the header already shows the UGS lockup and "UGS Warehouse", so a
          visible one would be the third statement of identity in a row. It stays in the markup
          because it is the page's only h1. */}
      <section className="pt-8 pb-6">
        <h1 className="sr-only">UGS Warehouse — data catalog</h1>
        <form onSubmit={onSubmit} role="search" className="flex items-center gap-2">
          <input type="search" aria-label="Search datasets"
            placeholder="Search datasets, publications, topics…"
            value={text} onChange={(e) => setText(e.target.value)}
            className="h-10 flex-1 rounded-md border border-input bg-card px-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring" />
          <button type="submit"
            className="h-10 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:opacity-90">
            Search
          </button>
        </form>
      </section>

      <section className="pb-10">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Categories</h2>
        {/* Wait for the full crawl before showing counts — otherwise they visibly tick upward as each
            collection's index streams in (the memos re-run on mapLoadKey). */}
        {loading || tiles.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">Loading the catalog…</p>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-x-8 sm:grid-cols-2 lg:grid-cols-3">
            {tiles.map((tile) => (
              <button key={tile.key} type="button" onClick={() => onOpenCategory(tile.key)}
                className="flex items-baseline justify-between gap-4 border-b border-border py-2 text-left hover:text-primary">
                <span className="text-sm">{tile.label}</span>
                <span className="font-mono text-sm text-muted-foreground">{tile.count.toLocaleString()}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {!loading && recent.length > 0 && (
        <section aria-label="Recently updated" className="pb-20">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Recently updated</h2>
            <button type="button" onClick={() => onSearch("")} className="text-sm text-primary hover:underline">
              Browse all
            </button>
          </div>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {recent.map((it) => (
              <ResultCard key={it.href} it={it} density="compact" on={false} link={link(it)} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

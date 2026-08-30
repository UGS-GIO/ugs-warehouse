// The front door (the param-less default view): a centered hero + a big search that hands off to
// Discover, a "Browse by category" tile grid, and a "Recently updated" strip. It derives everything
// from the item set App has ALREADY loaded (the same mapItems the Map/Discover views use) — no new
// fetch — memoized on App's stable mapLoadKey, never the array identity, so it never re-indexes the
// ~7.6k docs on an unrelated render. Mirrors ugs-data-catalog/src/routes/index.tsx onto UDS tokens.
import { type FormEvent, useMemo, useState } from "react";

import type { ItemRef } from "./browse";
import { CATEGORIES, categorize, dateOf, discoverHref } from "./item-view";
import { type LinkAttrs, ResultCard } from "./result-card";

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

export function Landing({ items, itemsKey, loading, onSearch, onOpenItem, onOpenCategory }: {
  items: ItemRef[];
  itemsKey: string;                       // App's mapLoadKey — the stable memo key for the item set
  loading: boolean;                       // the catalog crawl is still streaming → counts not final yet
  onSearch: (text: string) => void;       // → ?view=discover&q=…
  onOpenItem: (href: string) => void;     // → opens the item in Discover
  onOpenCategory: (key: string) => void;  // → ?view=discover&category=…
}) {
  const [text, setText] = useState("");
  const withData = useMemo(() => items.filter((it) => it.data), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const tiles = useMemo(() => categoryTiles(withData), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const recent = useMemo(() => recentlyUpdated(withData), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const onSubmit = (e: FormEvent) => { e.preventDefault(); onSearch(text.trim()); };

  // A non-hover result link (Landing has no map to sync) that opens the item in Discover.
  const link = (it: ItemRef): LinkAttrs => ({
    href: discoverHref(it),
    "data-href": it.href,
    onClick: (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
      onOpenItem(it.href);
    },
  });

  return (
    <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
      <section className="py-16 text-center sm:py-24">
        <h1 className="mx-auto max-w-3xl font-display text-4xl tracking-tight sm:text-5xl">
          Utah&rsquo;s geoscience data, <span className="text-primary">in one place</span>.
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-muted-foreground">
          Search and explore the Utah Geological Survey warehouse — hazards, energy &amp; minerals,
          geologic maps, and the full publication library.
        </p>
        <form onSubmit={onSubmit} role="search" className="mx-auto mt-8 flex max-w-xl items-center gap-2">
          <input type="search" aria-label="Search datasets"
            placeholder="Search datasets, publications, topics…"
            value={text} onChange={(e) => setText(e.target.value)}
            className="h-11 flex-1 rounded-lg border border-input bg-card px-4 text-sm text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring" />
          <button type="submit"
            className="h-11 rounded-lg bg-primary px-5 text-sm font-semibold text-primary-foreground transition-colors hover:opacity-90">
            Search
          </button>
        </form>
      </section>

      <section className="pb-12">
        <h2 className="text-lg font-semibold tracking-tight">Browse by category</h2>
        {/* Wait for the full crawl before showing counts — otherwise they visibly tick upward as each
            collection's index streams in (the memos re-run on mapLoadKey). */}
        {loading || tiles.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">Loading the catalog…</p>
        ) : (
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {tiles.map((tile) => (
              <button key={tile.key} type="button" onClick={() => onOpenCategory(tile.key)}
                className="group flex flex-col justify-between rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-primary hover:shadow-sm">
                <span className="font-semibold text-card-foreground">{tile.label}</span>
                <span className="mt-6 font-mono text-2xl font-bold text-primary">{tile.count.toLocaleString()}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {!loading && recent.length > 0 && (
        <section aria-label="Recently updated" className="pb-20">
          <div className="flex items-baseline justify-between">
            <h2 className="text-lg font-semibold tracking-tight">Recently updated</h2>
            <button type="button" onClick={() => onSearch("")} className="text-sm font-medium text-primary hover:underline">
              Browse all →
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

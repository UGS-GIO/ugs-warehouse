// The Map view's discovery panel: a facet rail + result cards over the map's loaded items, driven by
// the SAME MiniSearch index the Search view uses (search-index.buildIndex) and synced with the live
// map — hovering a card highlights its footprint (and back), a click opens the item via the existing
// onOpenItem flow, and "Search this area" clamps results to the current viewport. All pure facet /
// filter logic lives in ./discovery-model; this file is the React shell + the map wiring. No new deps:
// Utah Design System tokens (C / toggle) + the shared UiSegmented control only.
import { useEffect, useMemo, useRef, useState } from "react";

import type { ItemRef } from "./browse";
import {
  applyFacets, collectionLabel, discoverySeries, discoveryTitle, docIdOf, extractFacets,
  type FacetCount, type FacetSelection, filterByViewport, hasGeometry,
} from "./discovery-model";
import { buildIndex, toSearchDoc } from "./search-index";
import { thumbnailAsset } from "./stac";
import { C, toggle } from "./ui";
import { UiSegmented } from "./ui/segmented";

type Layout = "gallery" | "list";
type Density = "comfortable" | "compact";
const LAYOUTS = [{ value: "gallery" as const, label: "Gallery" }, { value: "list" as const, label: "List" }];
const DENSITIES = [{ value: "comfortable" as const, label: "Comfy" }, { value: "compact" as const, label: "Compact" }];
const RESULT_CAP = 300;

export function DiscoveryPanel({ items, itemsKey, onOpenItem, hoverHref, onHoverItem, viewport }: {
  items: ItemRef[];
  itemsKey: string; // stable identity for the (deliberately unmemoized) items array — matches App's mapLoadKey
  onOpenItem: (href: string) => void;
  hoverHref?: string | null;
  onHoverItem?: (href: string | null) => void;
  viewport?: [number, number, number, number] | null; // current map bounds, for "Search this area"
}) {
  const [q, setQ] = useState("");
  const [colls, setColls] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [geometry, setGeometry] = useState<FacetSelection["geometry"]>("all");
  const [layout, setLayout] = useState<Layout>("gallery");
  const [density, setDensity] = useState<Density>("comfortable");
  const [area, setArea] = useState<[number, number, number, number] | null>(null);

  // Heavy bits memoized on the stable items key (the same pattern App uses with mapLoadKey): the
  // shared MiniSearch index and the stable facet counts. Rebuilding these every render would re-index
  // thousands of items on each keystroke.
  const withData = useMemo(() => items.filter((it) => it.data), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const index = useMemo(
    () => buildIndex([], withData.map((it) => toSearchDoc(it.collId, it.data!))).index,
    [itemsKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const facets = useMemo(() => extractFacets(withData), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Text narrows via the shared index (score-ordered); the facet + viewport filters are pure. Cheap
  // enough to run per render (a few thousand items → sub-millisecond); only the index is memoized.
  const sel: FacetSelection = { collections: colls, types, geometry };
  const results = useMemo(() => {
    let base = applyFacets(withData, sel);
    if (area) base = filterByViewport(base, area);
    const query = q.trim();
    if (query.length >= 2) {
      const order = new Map((index.search(query) as unknown as { id: string }[]).map((h, i) => [h.id, i]));
      base = base.filter((it) => order.has(docIdOf(it)))
        .sort((a, b) => (order.get(docIdOf(a)) ?? 0) - (order.get(docIdOf(b)) ?? 0));
    }
    return base.slice(0, RESULT_CAP);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemsKey, q, colls, types, geometry, area, index]);

  // Map → card: when the map reports a hovered footprint, bring that card into view.
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!hoverHref) return;
    const el = listRef.current?.querySelector(`[data-href="${hoverHref.replace(/"/g, '\\"')}"]`);
    (el as HTMLElement | null)?.scrollIntoView({ block: "nearest" });
  }, [hoverHref]);

  const toggleIn = (list: string[], set: (v: string[]) => void, key: string) =>
    set(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
  const activeFilters = colls.length + types.length + (geometry === "all" ? 0 : 1) + (area ? 1 : 0);
  const cardProps = (it: ItemRef) => ({
    "data-href": it.href,
    onMouseEnter: () => onHoverItem?.(it.href),
    onMouseLeave: () => onHoverItem?.(null),
    onClick: () => onOpenItem(it.href),
  });

  return (
    <div className="flex flex-col gap-2">
      <input value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Search these layers & pubs…" aria-label="Search discovery"
        className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-sm outline-none focus:border-primary" />

      <div className="flex flex-wrap items-center gap-1.5">
        <UiSegmented value={layout} onValueChange={setLayout} items={LAYOUTS} className="text-xs" />
        <UiSegmented value={density} onValueChange={setDensity} items={DENSITIES} className="text-xs" />
        <button type="button" onClick={() => setArea(viewport ?? null)} disabled={!viewport}
          title="Limit results to what's in the current map view"
          className={`rounded border px-2 py-1 text-xs ${area
            ? "border-primary bg-primary text-primary-foreground"
            : "border-border bg-card text-foreground hover:bg-muted disabled:opacity-50"}`}>
          Search this area
        </button>
        {area && <button type="button" onClick={() => setArea(null)} className="text-xs text-primary hover:underline">clear area</button>}
      </div>

      {/* Facet rail — collection, type, has-geometry */}
      <FacetGroup label="Collection" facets={facets.collections} selected={new Set(colls)}
        onToggle={(k) => toggleIn(colls, setColls, k)} onClear={colls.length ? () => setColls([]) : undefined} />
      <FacetGroup label="Type" facets={facets.types} selected={new Set(types)}
        onToggle={(k) => toggleIn(types, setTypes, k)} onClear={types.length ? () => setTypes([]) : undefined} />
      {facets.geometry.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">Geometry</span>
          {facets.geometry.map((f) => (
            <span key={f.key} className={toggle(geometry === f.key)}
              onClick={() => setGeometry(geometry === f.key ? "all" : (f.key as FacetSelection["geometry"]))}>
              {f.label} · {f.n}
            </span>
          ))}
          {geometry !== "all" && <span className="cursor-pointer text-xs text-primary" onClick={() => setGeometry("all")}>clear</span>}
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{results.length}{results.length === RESULT_CAP ? "+" : ""} of {withData.length}</span>
        {activeFilters > 0 && (
          <button type="button" className="text-primary hover:underline"
            onClick={() => { setColls([]); setTypes([]); setGeometry("all"); setArea(null); }}>
            reset filters
          </button>
        )}
      </div>

      <div ref={listRef} className={layout === "gallery" ? "flex flex-col gap-2" : ""}>
        {results.length === 0 ? (
          <p className="px-1 py-6 text-center text-xs text-muted-foreground">
            {withData.length ? "Nothing matches these filters." : "No items loaded yet."}
          </p>
        ) : layout === "gallery" ? (
          results.map((it) => (
            <ResultCard key={it.href} it={it} density={density} on={hoverHref === it.href} attach={cardProps(it)} />
          ))
        ) : (
          <ul className="divide-y divide-border">
            {results.map((it) => (
              <ResultRow key={it.href} it={it} density={density} on={hoverHref === it.href} attach={cardProps(it)} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// One facet group of toggle chips. A lone value isn't a filter, so a group under two options hides.
function FacetGroup({ label, facets, selected, onToggle, onClear }: {
  label: string; facets: FacetCount[]; selected: Set<string>;
  onToggle: (key: string) => void; onClear?: () => void;
}) {
  if (facets.length < 2) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
      {facets.map((f) => (
        <span key={f.key} className={toggle(selected.has(f.key))} title={f.key} onClick={() => onToggle(f.key)}>
          {f.label} · {f.n}
        </span>
      ))}
      {onClear && <span className="cursor-pointer text-xs text-primary" onClick={onClear}>clear</span>}
    </div>
  );
}

type Attach = { "data-href": string; onMouseEnter: () => void; onMouseLeave: () => void; onClick: () => void };
type ItemProps = { it: ItemRef; density: Density; on: boolean; attach: Attach };

// Gallery card — thumbnail + title + collection badge, mirroring the catalog card/badge look (C).
function ResultCard({ it, density, on, attach }: ItemProps) {
  const th = thumbnailAsset(it.data);
  const compact = density === "compact";
  return (
    <div {...attach}
      className={`flex cursor-pointer gap-2 rounded-lg border bg-card transition hover:border-primary hover:shadow-sm ${compact ? "px-2 py-1.5" : "px-2.5 py-2.5"} ${on ? "border-primary ring-1 ring-primary" : "border-border"}`}>
      <div className={`${compact ? "h-9 w-9" : "h-14 w-14"} flex shrink-0 items-center justify-center overflow-hidden rounded bg-muted`}>
        {th ? <img src={th.href} alt="" loading="lazy" className="h-full w-full object-cover" />
          : <span className="px-0.5 text-center font-mono text-[10px] leading-none text-muted-foreground">{discoverySeries(it)}</span>}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-[11px] font-semibold text-foreground" title={discoverySeries(it)}>{discoverySeries(it)}</div>
        <p className={`text-xs font-medium leading-tight text-foreground ${compact ? "line-clamp-1" : "line-clamp-2"}`}>{discoveryTitle(it)}</p>
        {!compact && (
          <div className="mt-1 flex flex-wrap items-center gap-x-1">
            <span className={C.badge}>{collectionLabel(it.collId)}</span>
            {hasGeometry(it) && <span className="mt-1 text-[10px] font-medium text-primary" title="Draws on the map">◆ map</span>}
          </div>
        )}
      </div>
    </div>
  );
}

// List row — one dense line, for scanning a lot at once.
function ResultRow({ it, density, on, attach }: ItemProps) {
  const compact = density === "compact";
  return (
    <li {...attach}
      className={`flex cursor-pointer items-baseline gap-2 px-1 ${compact ? "py-0.5" : "py-1.5"} ${on ? "bg-accent" : "hover:bg-muted"}`}>
      <span className="shrink-0 font-mono text-[11px] font-semibold text-foreground">{discoverySeries(it)}</span>
      <span className="truncate text-xs text-foreground" title={discoveryTitle(it)}>{discoveryTitle(it)}</span>
      {hasGeometry(it) && <span className="ml-auto shrink-0 text-[10px] text-primary" title="Draws on the map">◆</span>}
    </li>
  );
}

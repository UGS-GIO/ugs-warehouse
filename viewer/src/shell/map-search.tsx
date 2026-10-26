import { Combobox } from "@base-ui/react/combobox";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { ItemRef } from "@/catalog/browse";
import { setPin } from "@/map/camera";
import { loadFromUtm, parseLatLon, type Point, utmPoint } from "@/map/coords";
import { type Bounds, locate, suggest, type Suggestion } from "@/map/place-locator";
import { findPlss, parsePlss, PLSS_SECTIONS, PLSS_TOWNSHIPS, type PlssHit } from "@/map/plss";
import { isStrongQuad, loadQuadSearch, type QuadHit } from "@/map/quad-search";
import { buildUnitSearch, unitExtent, type UnitHit, unitLayers } from "@/map/unit-search";
import { idOf, parquetAsset } from "@/stac";
import { buildCatalogSearch, type ItemHit } from "./header-search-model";
import { addRecent, clearRecent, type RecentPick, useRecent } from "./recent-searches";
import { SEARCH_EXAMPLES } from "./search-examples";

type Row =
  | { type: "place"; key: string; label: string; sub: string; place: Suggestion }
  // A spot the map flies to: a quadrangle, a PLSS township or section, or typed coordinates.
  | { type: "area"; key: string; label: string; sub: string; bbox: Bounds; outline: boolean }
  | { type: "unit"; key: string; label: string; sub: string; unit: UnitHit }
  | { type: "item"; key: string; label: string; sub: string; hit: ItemHit }
  | { type: "recent"; key: string; label: string; sub: string; pick: RecentPick }
  | { type: "try"; key: string; label: string; sub: string };
type Group = { value: string; items: Row[] };

const INPUT_ID = "map-search";

// "/" focuses the search from anywhere, unless the key is being typed into a field. The input's ref
// adds the listener when it mounts and removes it when it unmounts.
function onSlash(e: KeyboardEvent) {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable='true']")) return;
  const input = document.getElementById(INPUT_ID);
  if (!input) return;
  e.preventDefault();
  input.focus();
}
function bindSlash(el: HTMLInputElement | null) {
  if (!el) return;
  window.addEventListener("keydown", onSlash);
  return () => window.removeEventListener("keydown", onSlash);
}

const itemRow = (hit: ItemHit): Row => ({ type: "item", key: hit.href, label: hit.label, sub: hit.sub, hit });
const quadRow = (q: QuadHit): Row => q.scale === "24k"
  ? { type: "area", key: `quad:${q.code}`, label: `${q.name} quadrangle`, sub: "USGS 7.5-minute map area", bbox: q.bbox, outline: true }
  : { type: "area", key: `100k:${q.code}`, label: `${q.name} 30'×60' quadrangle`, sub: "USGS 1:100,000 map area",
      bbox: q.bbox, outline: true };
const plssRow = (h: PlssHit): Row => ({ type: "area", key: `plss:${h.label}:${h.meridian}`, label: h.label,
  sub: `PLSS ${h.label.includes("Sec.") ? "section" : "township"} · ${h.meridian}`, bbox: h.bbox, outline: true });
const pointRow = (p: Point): Row => ({ type: "area", key: `pt:${p.label}`, label: p.label, sub: "Coordinates",
  bbox: [p.lon, p.lat, p.lon, p.lat], outline: false });
const unitRow = (u: UnitHit): Row => ({ type: "unit", key: `unit:${u.href}|${u.symbol}`,
  label: u.name ? `${u.symbol} · ${u.name}` : u.symbol, sub: `Geologic unit · ${u.layer}`, unit: u });
// The PLSS archives, by the serving-topic ids plss.ts names.
const archiveOf = (items: ItemRef[], id: string) => {
  const d = items.find((r) => idOf(r.href) === id)?.data;
  return d && parquetAsset(d)?.href;
};

export function MapSearch({ items, loadKey, isLayer, onPlace, onItem, onSearchAll, defaultQuery, onClear,
  className = "" }: {
  items: ItemRef[];
  loadKey: string;
  isLayer: (r: ItemRef) => boolean;
  onPlace: (b: Bounds, label: string, outline?: boolean) => void;
  onItem: (hit: { href: string; bbox?: Bounds }) => void;
  onSearchAll: (q: string) => void;
  defaultQuery?: string;   // the search already applied (Discover's ?q=)
  onClear?: () => void;    // the clear button also drops the applied search
  className?: string;
}) {
  const [q, setQ] = useState(defaultQuery ?? "");
  const text = q.trim();
  // Enter with no suggestion highlighted runs the full search, as a plain search box would.
  const highlighted = useRef<Row | undefined>(undefined);
  // The list hangs under the whole search bar, at its width, as map apps do.
  const bar = useRef<HTMLDivElement>(null);
  const recent = useRecent();

  const catalog = useQuery({
    queryKey: ["header-search-index", loadKey],
    queryFn: () => buildCatalogSearch(items, isLayer, loadKey),
    enabled: items.length > 0,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  const places = useQuery({
    queryKey: ["place-suggest", text],
    queryFn: ({ signal }) => suggest(text, signal),
    enabled: text.length >= 2,
    staleTime: Infinity,
  });
  const quads = useQuery({
    queryKey: ["quad-gazetteer"],
    queryFn: ({ signal }) => loadQuadSearch(signal),
    staleTime: Infinity,
  });
  // Loaded with the box, not on the first UTM typed, so it is cached for offline use.
  const fromUtm = useQuery({ queryKey: ["utm"], queryFn: loadFromUtm, staleTime: Infinity });
  const plssQuery = parsePlss(text);
  const plss = useQuery({
    queryKey: ["plss", plssQuery?.township, plssQuery?.section],
    queryFn: () => findPlss(plssQuery!, {
      townships: archiveOf(items, PLSS_TOWNSHIPS), sections: archiveOf(items, PLSS_SECTIONS) }),
    enabled: !!plssQuery && items.length > 0,
    staleTime: Infinity,
  });
  const layersOfUnits = unitLayers(items);
  const units = useQuery({
    queryKey: ["unit-index", loadKey],
    queryFn: () => buildUnitSearch(layersOfUnits),
    enabled: text.length >= 2 && layersOfUnits.length > 0,
    staleTime: Infinity,
  });
  const place = useMutation({
    mutationFn: locate,
    onSuccess: (bounds, s) => { addRecent({ kind: "place", label: s.text, bounds }); onPlace(bounds, s.text); },
  });
  const unit = useMutation({
    mutationFn: unitExtent,
    onSuccess: (bbox, u) => {
      const label = unitRow(u).label;
      addRecent({ kind: "layer", label, href: u.href, bbox });
      onItem({ href: u.href, bbox });
    },
  });

  const found = text.length >= 2 ? catalog.data?.(text) : undefined;
  const point = parseLatLon(text) ?? (fromUtm.data ? utmPoint(text, fromUtm.data) : null);
  const quadHits = text.length >= 2 ? quads.data?.(text) ?? [] : [];
  // Quads get their own group, above Places when a quad name starts with what was typed.
  const quadsFirst = quadHits.some((h) => isStrongQuad(h, text));
  const quadGroup = { value: "Quadrangles", items: quadHits.map(quadRow) };
  const groups: Group[] = text.length >= 2
    ? [
        { value: "Exact match", items: found?.exact ? [itemRow(found.exact)] : [] },
        { value: "Coordinates", items: point ? [pointRow(point)] : [] },
        { value: "PLSS", items: plssQuery ? (plss.data ?? []).map(plssRow) : [] },
        { value: "Layers", items: (found?.layers ?? []).map(itemRow) },
        ...(quadsFirst ? [quadGroup] : []),
        { value: "Places", items: (places.data ?? []).slice(0, 5).map((s): Row =>
            ({ type: "place", key: s.magicKey, label: s.text, sub: "Place", place: s })) },
        ...(quadsFirst ? [] : [quadGroup]),
        { value: "Geologic units", items: (units.data?.(text) ?? []).map(unitRow) },
        { value: "Publications", items: (found?.publications ?? []).map(itemRow) },
      ].filter((g) => g.items.length)
    : [
        { value: "Recent", items: recent.map((p): Row => ({
            type: "recent", key: p.kind === "place" ? `place:${p.label}` : p.href, label: p.label,
            sub: p.kind === "place" ? "Place" : p.kind === "layer" ? "Layer" : "Publication", pick: p })) },
        { value: "Try", items: SEARCH_EXAMPLES.map((t): Row => ({ type: "try", key: `try:${t.text}`, label: t.text,
            sub: t.kind })) },
      ].filter((g) => g.items.length);
  // Only while searching; the index also builds on page load.
  const busy = (text.length >= 2 && (places.isFetching || catalog.isFetching || plss.isFetching || units.isFetching))
    || place.isPending || unit.isPending;

  const pickItem = (hit: ItemHit) => {
    addRecent({ kind: hit.kind, label: hit.label, href: hit.href, bbox: hit.bbox });
    onItem(hit);
  };
  const pick = (row: Row) => {
    if (row.type === "place") place.mutate(row.place);
    else if (row.type === "area") {
      addRecent({ kind: "place", label: row.label, bounds: row.bbox, outline: row.outline });
      onPlace(row.bbox, row.label, row.outline);
    }
    else if (row.type === "unit") unit.mutate(row.unit);
    else if (row.type === "item") pickItem(row.hit);
    else if (row.type === "try") setQ(row.label);
    else if (row.pick.kind === "place") { addRecent(row.pick); onPlace(row.pick.bounds, row.pick.label, row.pick.outline); }
    else { addRecent(row.pick); onItem(row.pick); }
  };

  return (
    <Combobox.Root<Row>
      items={groups}
      filter={null}
      value={null}
      inputValue={q}
      // Only typing and picking change the text. Closing the list (Esc, a click on the map) would
      // otherwise reset it, since the box never holds a selected value.
      onInputValueChange={(v, d) => { if (d.reason === "input-change" || d.reason === "item-press") setQ(v); }}
      onValueChange={(row) => { if (row) pick(row); }}
      onItemHighlighted={(row) => { highlighted.current = row; }}
      itemToStringLabel={(row) => row.label}
    >
      <div ref={bar} className={`flex items-center gap-2 rounded-full border border-input bg-background px-3 py-1 shadow focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20 ${className}`}>
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="shrink-0 text-muted-foreground">
          <circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" />
        </svg>
        <Combobox.Input id={INPUT_ID} ref={bindSlash} placeholder="Places, quadrangles, PLSS, coordinates, units"
          title="Press / to search"
          aria-label="Search places, quadrangles, PLSS, coordinates, geologic units, layers and publications"
          onKeyDown={(e) => { if (e.key === "Enter" && !highlighted.current && text) onSearchAll(text); }}
          className="min-w-0 flex-1 bg-transparent py-0.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none" />
        {busy && <span role="status" aria-label="Searching"
          className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-muted-foreground/40 border-t-primary" />}
        {(place.isError || unit.isError) && <span className="text-xs text-destructive">not found</span>}
        {q && (
          <button type="button" aria-label="Clear the search"
            onClick={() => { setQ(""); setPin(null); onClear?.(); document.getElementById(INPUT_ID)?.focus(); }}
            className="flex size-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground">
            <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        )}
      </div>
      <Combobox.Portal>
        <Combobox.Positioner anchor={bar} sideOffset={6} align="start" className="z-50">
          <Combobox.Popup className="max-h-popup w-(--anchor-width) overflow-y-auto rounded-md border border-border bg-card py-1 text-sm text-foreground shadow-lg">
            <Combobox.Empty className="px-3 py-2 text-muted-foreground empty:hidden">
              {text.length >= 2 && !busy ? `Nothing matches “${text}”` : null}
            </Combobox.Empty>
            <Combobox.List>
              {(group: Group) => (
                <Combobox.Group key={group.value} items={group.items}>
                  <Combobox.GroupLabel className="flex items-center justify-between px-3 pb-1 pt-2 text-2xs font-bold uppercase tracking-wider text-muted-foreground">
                    {group.value}
                    {group.value === "Recent" && (
                      <button type="button" onClick={clearRecent}
                        className="text-xs font-normal normal-case tracking-normal text-primary hover:underline">Clear</button>
                    )}
                  </Combobox.GroupLabel>
                  <Combobox.Collection>
                    {(row: Row) => (
                      <Combobox.Item key={row.key} value={row}
                        className="flex cursor-pointer select-none flex-col justify-center px-3 py-1.5 outline-none data-[highlighted]:bg-muted pointer-coarse:min-h-11">
                        <span className="truncate font-medium">{row.label}</span>
                        {row.sub && <span className="truncate text-xs text-muted-foreground">{row.sub}</span>}
                      </Combobox.Item>
                    )}
                  </Combobox.Collection>
                </Combobox.Group>
              )}
            </Combobox.List>
            {text.length >= 2 && (
              <button type="button" onClick={() => onSearchAll(text)}
                className="mt-1 w-full border-t border-border px-3 py-2 text-left text-primary hover:underline">
                All results for “{text}” in Discover →
              </button>
            )}
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
  );
}

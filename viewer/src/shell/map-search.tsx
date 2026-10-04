import { Combobox } from "@base-ui/react/combobox";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { type ReactNode, useRef, useState } from "react";
import type { ItemRef } from "@/catalog/browse";
import { setPin } from "@/map/camera";
import { type Bounds, locate, suggest, type Suggestion } from "@/map/place-locator";
import { buildCatalogSearch, type ItemHit } from "./header-search-model";
import { addRecent, clearRecent, type RecentPick, useRecent } from "./recent-searches";

type Row =
  | { type: "place"; key: string; label: string; sub: string; place: Suggestion }
  | { type: "item"; key: string; label: string; sub: string; hit: ItemHit }
  | { type: "recent"; key: string; label: string; sub: string; pick: RecentPick }
  | { type: "try"; key: string; label: string; sub: string };
type Group = { value: string; items: Row[] };

const INPUT_ID = "site-search";   // the one search bar: the Map page's or Discover's, never both
const TRY = ["Moab", "faults", "landslides", "OFR-598"];

// "/" focuses the search from anywhere, unless the key is being typed into a field. The input's ref
// adds the listener when it mounts and removes it when it unmounts.
function onSlash(e: KeyboardEvent) {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target instanceof Element && e.target.closest("input, textarea, select, [contenteditable='true']")) return;
  const input = document.getElementById(INPUT_ID);
  if (!input) return;
  // Not from under an open dialog (Discover's item drawer): typing would filter the page behind it.
  const modal = document.querySelector("[aria-modal='true']");
  if (modal && !modal.contains(input)) return;
  e.preventDefault();
  input.focus();
}
function bindSlash(el: HTMLInputElement | null) {
  if (!el) return;
  window.addEventListener("keydown", onSlash);
  return () => window.removeEventListener("keydown", onSlash);
}

const PLACEHOLDER = "Search places, layers and publications";

// The bar's frame: the same pill on the Map page (with suggestions) and on Discover (which filters
// its own results as you type instead). The caller supplies the input.
function BarFrame({ className, busy, error, showClear, onClear, children }: {
  className: string; busy?: boolean; error?: string; showClear: boolean; onClear: () => void; children: ReactNode;
}) {
  return (
    <div className={`flex items-center gap-2 rounded-full border border-input bg-background px-3 py-1 shadow focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20 ${className}`}>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="shrink-0 text-muted-foreground">
        <circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" />
      </svg>
      {children}
      {busy && <span role="status" aria-label="Searching"
        className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-muted-foreground/40 border-t-primary" />}
      {error && <span className="text-xs text-destructive">{error}</span>}
      {showClear && (
        <button type="button" aria-label="Clear the search" onClick={onClear}
          className="flex size-6 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground">
          <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      )}
    </div>
  );
}

const INPUT_CLASS = "min-w-0 flex-1 bg-transparent py-0.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none [&::-webkit-search-cancel-button]:hidden";

/** The same bar without the suggestion list: Discover filters its results as the text changes. */
export function LiveSearchBar({ value, onChange, onEnter, onClear, placeholder = PLACEHOLDER, className = "" }: {
  value: string;
  onChange: (text: string) => void;
  onEnter: () => void;
  onClear: () => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <BarFrame className={className} showClear={value !== ""}
      onClear={() => { onClear(); document.getElementById(INPUT_ID)?.focus(); }}>
      <input id={INPUT_ID} type="search" value={value} placeholder={placeholder} aria-label={placeholder}
        title="Press / to search" ref={bindSlash}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) onEnter(); }}
        className={INPUT_CLASS} />
    </BarFrame>
  );
}

const itemRow = (hit: ItemHit): Row => ({ type: "item", key: hit.href, label: hit.label, sub: hit.sub, hit });

export function MapSearch({ items, loadKey, isLayer, onPlace, onItem, onSearchAll, className = "" }: {
  items: ItemRef[];
  loadKey: string;
  isLayer: (r: ItemRef) => boolean;
  onPlace: (b: Bounds, label: string) => void;
  onItem: (hit: { href: string; bbox?: Bounds }) => void;
  onSearchAll: (q: string) => void;
  className?: string;
}) {
  const [q, setQ] = useState("");
  const text = q.trim();
  // Enter with no suggestion highlighted runs the full search, as a plain search box would.
  const highlighted = useRef<Row | undefined>(undefined);
  const recent = useRecent();

  const catalog = useQuery({
    queryKey: ["header-search-index", loadKey],
    queryFn: () => buildCatalogSearch(items, isLayer),
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
  const place = useMutation({
    mutationFn: locate,
    onSuccess: (bounds, s) => { addRecent({ kind: "place", label: s.text, bounds }); onPlace(bounds, s.text); },
  });

  const found = text.length >= 2 ? catalog.data?.(text) : undefined;
  const groups: Group[] = text.length >= 2
    ? [
        { value: "Exact match", items: found?.exact ? [itemRow(found.exact)] : [] },
        { value: "Layers", items: (found?.layers ?? []).map(itemRow) },
        { value: "Places", items: (places.data ?? []).slice(0, 5).map((s): Row =>
            ({ type: "place", key: s.magicKey, label: s.text, sub: "Place", place: s })) },
        { value: "Publications", items: (found?.publications ?? []).map(itemRow) },
      ].filter((g) => g.items.length)
    : [
        { value: "Recent", items: recent.map((p): Row => ({
            type: "recent", key: p.kind === "place" ? `place:${p.label}` : p.href, label: p.label,
            sub: p.kind === "place" ? "Place" : p.kind === "layer" ? "Layer" : "Publication", pick: p })) },
        { value: "Try", items: TRY.map((t): Row => ({ type: "try", key: `try:${t}`, label: t, sub: "" })) },
      ].filter((g) => g.items.length);
  // Only while searching; the index also builds on page load.
  const busy = (text.length >= 2 && (places.isFetching || catalog.isFetching)) || place.isPending;

  const pickItem = (hit: ItemHit) => {
    addRecent({ kind: hit.kind, label: hit.label, href: hit.href, bbox: hit.bbox });
    onItem(hit);
  };
  const pick = (row: Row) => {
    if (row.type === "place") place.mutate(row.place);
    else if (row.type === "item") pickItem(row.hit);
    else if (row.type === "try") setQ(row.label);
    else if (row.pick.kind === "place") { addRecent(row.pick); onPlace(row.pick.bounds, row.pick.label); }
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
      <BarFrame className={className} busy={busy} error={place.isError ? "place not found" : undefined} showClear={q !== ""}
        onClear={() => { setQ(""); setPin(null); document.getElementById(INPUT_ID)?.focus(); }}>
        <Combobox.Input id={INPUT_ID} ref={bindSlash} placeholder={PLACEHOLDER} title="Press / to search"
          aria-label={PLACEHOLDER}
          onKeyDown={(e) => { if (e.key === "Enter" && !highlighted.current && text) onSearchAll(text); }}
          className={INPUT_CLASS} />
      </BarFrame>
      <Combobox.Portal>
        <Combobox.Positioner sideOffset={6} align="start" className="z-50">
          <Combobox.Popup className="max-h-[70vh] w-[var(--anchor-width)] min-w-72 overflow-y-auto rounded-md border border-border bg-card py-1 text-sm text-foreground shadow-lg">
            <Combobox.Empty className="px-3 py-2 text-muted-foreground empty:hidden">
              {text.length >= 2 && !busy ? `No places, layers or publications match “${text}”` : null}
            </Combobox.Empty>
            <Combobox.List>
              {(group: Group) => (
                <Combobox.Group key={group.value} items={group.items}>
                  <Combobox.GroupLabel className="flex items-center justify-between px-3 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                    {group.value}
                    {group.value === "Recent" && (
                      <button type="button" onClick={clearRecent}
                        className="text-xs font-normal normal-case tracking-normal text-primary hover:underline">Clear</button>
                    )}
                  </Combobox.GroupLabel>
                  <Combobox.Collection>
                    {(row: Row) => (
                      <Combobox.Item key={row.key} value={row}
                        className="flex cursor-pointer select-none flex-col px-3 py-1.5 outline-none data-[highlighted]:bg-muted">
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

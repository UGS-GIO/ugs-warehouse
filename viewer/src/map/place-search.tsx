import { Autocomplete } from "@base-ui/react/autocomplete";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { type Bounds, locate, suggest, type Suggestion } from "./place-locator";

export function PlaceSearch({ onPick }: { onPick: (b: Bounds) => void }) {
  const [q, setQ] = useState("");
  const text = q.trim();
  const hits = useQuery({
    queryKey: ["place-suggest", text],
    queryFn: ({ signal }) => suggest(text, signal),
    enabled: text.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  const go = useMutation({ mutationFn: locate, onSuccess: onPick });
  const items = text.length >= 2 ? hits.data ?? [] : [];
  const err = go.error ? (go.error.message === "not found" ? "not found" : "search failed")
    : hits.isError ? "search failed" : undefined;

  return (
    <Autocomplete.Root
      items={items}
      filter={null}
      value={q}
      itemToStringValue={(s: Suggestion) => s.text}
      onValueChange={(v, d) => {
        setQ(v);
        if (d.reason === "item-press") {
          const s = items.find((i) => i.text === v);
          if (s) go.mutate(s);
        }
      }}
    >
      <div className="flex items-center gap-1 rounded-md border border-border bg-card/95 p-1 text-xs shadow">
        <Autocomplete.Input placeholder="Search Utah places…" aria-label="Search Utah places"
          className="w-28 sm:w-44 rounded bg-transparent px-1.5 py-0.5 text-foreground placeholder:text-muted-foreground focus:outline-none" />
        {(go.isPending || hits.isFetching) && <span className="px-1 text-muted-foreground">…</span>}
        {err && <span className="px-1 text-destructive">{err}</span>}
      </div>
      <Autocomplete.Portal>
        <Autocomplete.Positioner sideOffset={4} align="start" className="z-50">
          <Autocomplete.Popup className="max-h-72 w-[var(--anchor-width)] min-w-56 overflow-y-auto rounded-md border border-border bg-card py-1 text-xs text-foreground shadow-lg">
            <Autocomplete.Empty className="px-3 py-1.5 text-muted-foreground empty:hidden">
              {text.length >= 2 && !hits.isFetching ? "No Utah places match" : null}
            </Autocomplete.Empty>
            <Autocomplete.List>
              {(s: Suggestion) => (
                <Autocomplete.Item key={s.magicKey} value={s}
                  className="cursor-pointer select-none px-3 py-1.5 outline-none data-[highlighted]:bg-muted">
                  {s.text}
                </Autocomplete.Item>
              )}
            </Autocomplete.List>
          </Autocomplete.Popup>
        </Autocomplete.Positioner>
      </Autocomplete.Portal>
    </Autocomplete.Root>
  );
}

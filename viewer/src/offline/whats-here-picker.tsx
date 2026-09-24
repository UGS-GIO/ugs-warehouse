// "What's here": everything at a place on the map, to show and (on phones) keep offline.
//
// Opened by a long press or right-click on the map (the quad under it) or by "Save this area"
// (the view). Everything that overlaps is listed with a checkbox and, when saving is on, its
// exact size; the actions apply to what is ticked. Saving ticks everything by default so the
// common case is one tap; showing ticks nothing, since drawing every overlapping layer at once
// is never what anyone wants.
import { Dialog } from "@base-ui/react/dialog";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toLayer, useViewCtx } from "@/app";
import type { ItemRef } from "@/catalog/browse";
import type { ActiveLayer } from "@/map/map-model";
import { type AreaPlan, planArea } from "./area";
import { type CogPlan, planCogArea } from "./cog-area";
import { ENGINE_BYTES, hasEngine } from "./engine";
import { planTableArea } from "./table-area";
import { ENGINE_KEY } from "./table-offline";
import * as queue from "./queue";
import { overviewUrl, quadUrl, stateUrl } from "./basemap";
import { useBasemapIndex } from "./basemap-download";
import { type Hit, hitLabel, identifyAt } from "./identify";
import * as opfs from "./opfs";
import { useStoredLayers } from "./use-offline";
import { type Here, quadsFor, saveBbox, type Target, whatsHere } from "./whats-here";

// Pricing walks each archive's directory; four at a time keeps a busy area from opening dozens
// of range requests at once on a phone connection.
let running = 0;
const waiting: (() => void)[] = [];
async function limit<T>(fn: () => Promise<T>): Promise<T> {
  if (running >= 4) await new Promise<void>((r) => waiting.push(r));
  running++;
  try { return await fn(); } finally { running--; waiting.shift()?.(); }
}

type Price = { bytes: number; plan?: AreaPlan; cog?: CogPlan; table?: boolean };
const BASEMAP = "__basemap__";

export function WhatsHerePicker({ target, canSave, onClose }: {
  target: Target; canSave: boolean; onClose: () => void;
}) {
  const ctx = useViewCtx();
  const stored = useStoredLayers();
  const index = useBasemapIndex();

  // Every drawable layer the app knows about, resolved exactly as the map resolves them.
  const candidates = useMemo(() => {
    const seen = new Map<string, ItemRef>();
    for (const r of [...ctx.mapItems, ...ctx.allItems]) if (!seen.has(r.href)) seen.set(r.href, r);
    return [...seen.values()].map(toLayer).filter((l): l is ActiveLayer => l !== null);
  }, [ctx.mapItems, ctx.allItems]);
  const here = useMemo(() => whatsHere(candidates, target), [candidates, target]);
  const bbox = saveBbox(target);
  const quads = quadsFor(target);
  const saveable = here.filter((h) => h.save);

  const have = new Set(stored.data?.files.map((f) => f.url));
  const stateSaved = have.has(stateUrl());
  const basemapParts = stateSaved || !index.data ? [] : [
    ...(have.has(overviewUrl()) ? [] : [{ url: overviewUrl(), bytes: index.data.overview.bytes }]),
    ...quads.filter((q) => q.code in index.data!.quads && !have.has(quadUrl(q.code)))
      .map((q) => ({ url: quadUrl(q.code), bytes: index.data!.quads[q.code].bytes })),
  ];
  const basemapOffered = canSave && !stateSaved && basemapParts.length > 0;

  const [ticked, setTicked] = useState<Set<string>>(() => new Set(canSave
    // Tables start unticked: most trips want the map, and each table costs a footer read to price.
    ? [BASEMAP, ...saveable.filter((h) => h.group !== "table" && !have.has(h.save!.url)).map((h) => h.id)] : []));
  const toggle = (id: string, on: boolean) => setTicked((prev) => {
    const next = new Set(prev);
    if (on) next.add(id); else next.delete(id);
    return next;
  });

  const prices = useQueries({
    queries: saveable.map((h) => ({
      queryKey: ["offline-price", h.save!.how, h.save!.url, bbox.join(",")],
      queryFn: (): Promise<Price> => limit(() => h.save!.how === "area"
        ? planArea(h.save!.url, bbox).then((plan) => ({ bytes: plan.bytes, plan }))
        : h.save!.how === "table"
        ? planTableArea(h.save!.url, bbox).then((cog) => ({ bytes: cog.bytes, cog, table: true }))
        : planCogArea(h.save!.url, bbox).then((cog) => ({ bytes: cog.bytes, cog }))),
      enabled: canSave && (h.group !== "table" || ticked.has(h.id)),
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const priceOf = (h: Here) => prices[saveable.indexOf(h)];

  // Showing at a point: ask every vector layer what it has exactly there, so the list is what is
  // under the click, not every layer whose extent covers it (statewide ones always do).
  const querying = !canSave && target.kind === "point";
  const vectors = here.filter((h) => h.group === "layer" && h.save?.how === "area");
  const identified = useQueries({
    queries: vectors.map((h) => ({
      queryKey: ["identify", h.save!.url, target.kind === "point" ? `${target.lon.toFixed(6)},${target.lat.toFixed(6)},${Math.round(target.zoom ?? 12)}` : ""],
      queryFn: (): Promise<Hit[]> => limit(() => target.kind === "point"
        ? identifyAt(h.save!.url, target.lon, target.lat, target.zoom ?? 12) : Promise.resolve([])),
      enabled: querying,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const hitsOf = (h: Here) => identified[vectors.indexOf(h)]?.data ?? [];
  const checking = querying ? identified.filter((q) => q.isPending).length : 0;

  const chosen = saveable.filter((h) => ticked.has(h.id));
  const basemapBytes = basemapOffered && ticked.has(BASEMAP) ? basemapParts.reduce((n, p) => n + p.bytes, 0) : 0;
  const pricing = chosen.some((h) => priceOf(h)?.isPending);
  // A table needs the table engine on the device too; it is counted once, the first time.
  const engine = useQuery({ queryKey: ENGINE_KEY, queryFn: hasEngine, staleTime: Infinity, enabled: canSave });
  const needsEngine = canSave && engine.data === false && chosen.some((h) => h.group === "table");
  const total = basemapBytes + chosen.reduce((n, h) => n + (priceOf(h)?.data?.bytes ?? 0), 0)
    + (needsEngine ? ENGINE_BYTES : 0);

  // Queued, not run here: the picker closes at once and Downloads (and the notice) track the saves.
  const save = useMutation({
    mutationFn: async () => {
      if (!opfs.fitsInQuota(total, await opfs.quota())) {
        throw new Error(`${opfs.formatBytes(total)} will not fit in this browser's storage.`);
      }
      await queue.enqueue([
        ...(needsEngine ? [{ kind: "engine" as const, label: "Table engine", bytes: ENGINE_BYTES }] : []),
        ...(basemapBytes ? basemapParts.map((p) => ({
          kind: "file" as const, url: p.url, bytes: p.bytes,
          label: `Basemap ${p.url.split("/").pop()?.replace(".pmtiles", "")}`,
        })) : []),
        ...chosen.flatMap((h) => {
          const price = priceOf(h)?.data;
          if (!price) return [];
          return [price.plan
            ? { kind: "area" as const, plan: price.plan, label: h.title, bytes: price.bytes }
            : price.table
            ? { kind: "table" as const, plan: price.cog!, label: `${h.title} (table)`, bytes: price.bytes }
            : { kind: "cog" as const, plan: price.cog!, label: h.title, bytes: price.bytes }];
        }),
      ]);
    },
  });

  const show = () => {
    const ids = here.filter((h) => ticked.has(h.id) && h.group !== "table").map((h) => h.id);
    if (ids.length) ctx.toggleLayers(ids, true);
    onClose();
  };

  const title = querying && target.kind === "point"
    ? `What's here · ${target.lat.toFixed(4)}, ${target.lon.toFixed(4)}`
    : target.kind === "point"
    ? `Here · quad ${quads[0].code}`
    : `This area · ${quads.length} quad${quads.length === 1 ? "" : "s"}`;
  // When saving, only what can actually be saved is listed: a row you can't tick is noise. That
  // drops datacubes (no offline form) and anything whose pricing failed. Showing (desktop) keeps all.
  const listed = canSave ? here.filter((h) => h.save && !priceOf(h)?.isError)
    : querying ? here.filter((h) => h.group === "map" || hitsOf(h).length > 0)
    : here.filter((h) => h.group !== "table");   // a table is only something to save
  const groups = [
    { name: "Data layers", rows: listed.filter((h) => h.group === "layer") },
    { name: "Data tables", rows: listed.filter((h) => h.group === "table") },
    { name: "Published maps", rows: listed.filter((h) => h.group === "map") },
  ].filter((g) => g.rows.length);

  const row = (h: Here) => {
    const price = canSave && h.save ? priceOf(h) : undefined;
    const savedWhole = !!h.save && have.has(h.save.url);   // the whole file is already stored
    const disabled = canSave ? !h.save || savedWhole || !!price?.isError : false;
    const found = querying ? hitsOf(h) : [];
    return (
      <div key={h.id}>
        <label className={`flex items-center gap-2 px-3 py-1.5 ${disabled ? "opacity-60" : "cursor-pointer hover:bg-hover"}`}>
          <input type="checkbox" className="h-4 w-4 accent-primary" disabled={disabled}
            checked={ticked.has(h.id) && !disabled} onChange={(e) => toggle(h.id, e.target.checked)} />
          <span className="min-w-0 flex-1">
            <span className="block truncate" title={h.title}>{h.title}</span>
            {found.length > 0 && (
              <span className="block truncate text-xs text-muted-foreground">
                {[...new Set(found.map((x) => hitLabel(x.properties)).filter(Boolean))].slice(0, 3).join(" · ")
                  || `${found.length} feature${found.length === 1 ? "" : "s"}`}
              </span>
            )}
          </span>
          {ctx.isActive(h.id) && <span className="shrink-0 text-xs text-muted-foreground">on map</span>}
          {canSave && (
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {savedWhole ? "saved" : price?.data ? opfs.formatBytes(price.data.bytes)
                : h.group === "table" && !ticked.has(h.id) ? "" : "…"}
            </span>
          )}
        </label>
        {/* The attributes themselves: what "query what's under my click" is for. Outside the
            label, so opening it doesn't tick the row. */}
        {found.length > 0 && (
          <details className="px-9 pb-1.5 text-xs">
            <summary className="cursor-pointer text-primary">Attributes</summary>
            {found.slice(0, 5).map((x, n) => (
              <dl key={n} className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 border-t border-border pt-1">
                {Object.entries(x.properties).slice(0, 20).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="break-words">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            ))}
            {found.length > 5 && <p className="mt-1 text-muted-foreground">and {found.length - 5} more here</p>}
          </details>
        )}
      </div>
    );
  };

  const btn = "rounded-md border border-border px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-50";
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !save.isPending) onClose(); }}>
      <Dialog.Portal>
        {/* Above the Utah header, which sits at z-index 3000 and otherwise covers the title bar. */}
        <Dialog.Backdrop className="fixed inset-0 z-[3100] bg-black/40" />
        <Dialog.Popup className="fixed inset-x-0 bottom-0 z-[3101] flex max-h-[85vh] flex-col rounded-t-2xl border border-border bg-background shadow-2xl md:inset-x-auto md:bottom-auto md:left-1/2 md:top-1/2 md:w-[32rem] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-xl">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Dialog.Title className="flex-1 text-base font-semibold">{title}</Dialog.Title>
            <Dialog.Close className="rounded px-2 text-muted-foreground hover:text-foreground" aria-label="Close">✕</Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto py-2">
            {checking > 0 && (
              <p className="px-4 py-1 text-xs text-muted-foreground">Checking {checking} more layer{checking === 1 ? "" : "s"}…</p>
            )}
            {!listed.length && !basemapOffered && !checking && (
              <p className="px-4 py-2 text-sm text-muted-foreground">{canSave ? "Nothing here can be saved offline." : "Nothing mapped at this spot."}</p>
            )}
            {basemapOffered && (
              <section>
                <div className="px-4 pb-1 pt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Basemap</div>
                <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-hover">
                  <input type="checkbox" className="h-4 w-4 accent-primary" checked={ticked.has(BASEMAP)}
                    onChange={(e) => toggle(BASEMAP, e.target.checked)} />
                  <span className="flex-1">Streets basemap for {quads.length === 1 ? "this quad" : `these ${quads.length} quads`}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {opfs.formatBytes(basemapParts.reduce((n, p) => n + p.bytes, 0))}
                  </span>
                </label>
              </section>
            )}
            {groups.map((g) => {
              const ids = g.rows.filter((h) => !canSave || h.save).map((h) => h.id);
              const all = ids.length > 0 && ids.every((id) => ticked.has(id));
              return (
                <section key={g.name}>
                  <div className="flex items-center px-4 pb-1 pt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    <span className="flex-1">{g.name} · {g.rows.length}</span>
                    <button type="button" className="normal-case tracking-normal text-primary hover:underline"
                      onClick={() => setTicked((prev) => {
                        const next = new Set(prev);
                        for (const id of ids) { if (all) next.delete(id); else next.add(id); }
                        return next;
                      })}>
                      {all ? "None" : "All"}
                    </button>
                  </div>
                  {g.rows.map(row)}
                </section>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
            {save.error && <p className="w-full text-sm text-destructive">{save.error.message}</p>}
            <button type="button" className={btn} disabled={save.isPending || ![...ticked].some((id) => id !== BASEMAP)}
              onClick={show}>Show on map</button>
            {canSave && (
              <button type="button" className={`${btn} ml-auto bg-primary text-primary-foreground hover:bg-primary/90`}
                disabled={save.isPending || pricing || total === 0}
                onClick={() => save.mutate(undefined, { onSuccess: onClose })}>
                {pricing ? "Pricing…" : `Save offline · ${opfs.formatBytes(total)}`}
              </button>
            )}
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

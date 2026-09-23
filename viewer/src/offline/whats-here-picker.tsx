// "What's here": everything at a place on the map, to show and (on phones) keep offline.
//
// Opened by a long press or right-click on the map (the quad under it) or by "Save this area"
// (the view). Everything that overlaps is listed with a checkbox and, when saving is on, its
// exact size; the actions apply to what is ticked. Saving ticks everything by default so the
// common case is one tap; showing ticks nothing, since drawing every overlapping layer at once
// is never what anyone wants.
import { Dialog } from "@base-ui/react/dialog";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toLayer, useViewCtx } from "@/app";
import type { ItemRef } from "@/catalog/browse";
import type { ActiveLayer } from "@/map/map-model";
import { qk } from "@/query-keys";
import { type AreaPlan, planArea, saveArea } from "./area";
import { overviewUrl, quadUrl, stateUrl } from "./basemap";
import { useBasemapIndex } from "./basemap-download";
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

async function fileSize(url: string): Promise<number> {
  const r = await fetch(url, { method: "HEAD" });
  if (!r.ok) throw new Error(`${r.status}`);
  return Number(r.headers.get("content-length")) || 0;
}

type Price = { bytes: number; plan?: AreaPlan };
const BASEMAP = "__basemap__";

export function WhatsHerePicker({ target, canSave, onClose }: {
  target: Target; canSave: boolean; onClose: () => void;
}) {
  const ctx = useViewCtx();
  const client = useQueryClient();
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
    ? [BASEMAP, ...saveable.filter((h) => !have.has(h.save!.url)).map((h) => h.id)] : []));
  const toggle = (id: string, on: boolean) => setTicked((prev) => {
    const next = new Set(prev);
    if (on) next.add(id); else next.delete(id);
    return next;
  });

  const prices = useQueries({
    queries: saveable.map((h) => ({
      queryKey: ["offline-price", h.save!.url, h.save!.how === "area" ? bbox.join(",") : ""],
      queryFn: (): Promise<Price> => limit(() => h.save!.how === "area"
        ? planArea(h.save!.url, bbox).then((plan) => ({ bytes: plan.bytes, plan }))
        : fileSize(h.save!.url).then((bytes) => ({ bytes }))),
      enabled: canSave,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const priceOf = (h: Here) => prices[saveable.indexOf(h)];

  const chosen = saveable.filter((h) => ticked.has(h.id));
  const basemapBytes = basemapOffered && ticked.has(BASEMAP) ? basemapParts.reduce((n, p) => n + p.bytes, 0) : 0;
  const pricing = chosen.some((h) => priceOf(h)?.isPending);
  const total = basemapBytes + chosen.reduce((n, h) => n + (priceOf(h)?.data?.bytes ?? 0), 0);

  const [progress, setProgress] = useState<{ n: number; of: number; what: string } | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      await navigator.storage?.persist?.().catch(() => false);
      if (!opfs.fitsInQuota(total, await opfs.quota())) {
        throw new Error(`${opfs.formatBytes(total)} will not fit in this browser's storage.`);
      }
      const jobs: { what: string; run: () => Promise<unknown> }[] = [
        ...(basemapBytes ? basemapParts.map((p) => ({ what: "Basemap", run: () => opfs.save(p.url) })) : []),
        ...chosen.flatMap((h) => {
          const price = priceOf(h)?.data;
          if (!price) return [];
          return [{ what: h.title, run: () => (price.plan ? saveArea(price.plan) : opfs.save(h.save!.url)) }];
        }),
      ];
      for (const [i, job] of jobs.entries()) {
        setProgress({ n: i, of: jobs.length, what: job.what });
        await job.run();
      }
    },
    onSettled: () => {
      setProgress(null);
      client.invalidateQueries({ queryKey: qk.offlineLayers });
      client.invalidateQueries({ queryKey: ["offline-areas"] });
      client.invalidateQueries({ queryKey: ["basemap-style"] });
    },
  });

  const show = () => {
    const ids = here.filter((h) => ticked.has(h.id)).map((h) => h.id);
    if (ids.length) ctx.toggleLayers(ids, true);
    onClose();
  };

  const title = target.kind === "point"
    ? `Here · quad ${quads[0].code}`
    : `This area · ${quads.length} quad${quads.length === 1 ? "" : "s"}`;
  const groups = [
    { name: "Data layers", rows: here.filter((h) => h.group === "layer") },
    { name: "Published maps", rows: here.filter((h) => h.group === "map") },
  ].filter((g) => g.rows.length);

  const row = (h: Here) => {
    const price = canSave && h.save ? priceOf(h) : undefined;
    const savedWhole = !!h.save && h.save.how === "file" && have.has(h.save.url);
    const disabled = canSave ? !h.save || savedWhole || !!price?.isError : false;
    return (
      <label key={h.id} className={`flex items-center gap-2 px-3 py-1.5 ${disabled ? "opacity-60" : "cursor-pointer hover:bg-hover"}`}>
        <input type="checkbox" className="h-4 w-4 accent-primary" disabled={disabled}
          checked={ticked.has(h.id) && !disabled} onChange={(e) => toggle(h.id, e.target.checked)} />
        <span className="min-w-0 flex-1 truncate" title={h.title}>{h.title}</span>
        {ctx.isActive(h.id) && <span className="shrink-0 text-xs text-muted-foreground">on map</span>}
        {canSave && (
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {!h.save ? "not saveable" : savedWhole ? "saved" : price?.isError ? "can't save"
              : price?.data ? opfs.formatBytes(price.data.bytes) : "…"}
          </span>
        )}
      </label>
    );
  };

  const btn = "rounded-md border border-border px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-50";
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !save.isPending) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Popup className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85vh] flex-col rounded-t-2xl border border-border bg-background shadow-2xl md:inset-x-auto md:bottom-auto md:left-1/2 md:top-1/2 md:w-[32rem] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-xl">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Dialog.Title className="flex-1 text-base font-semibold">{title}</Dialog.Title>
            <Dialog.Close className="rounded px-2 text-muted-foreground hover:text-foreground" aria-label="Close">✕</Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto py-2">
            {!here.length && !basemapOffered && (
              <p className="px-4 py-2 text-sm text-muted-foreground">Nothing mapped here.</p>
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
            {progress && (
              <p className="w-full truncate text-sm text-muted-foreground">
                Saving {progress.n + 1} of {progress.of}: {progress.what}. Keep this page open.
              </p>
            )}
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

// "What's here": everything at a place on the map, to show and keep offline.
//
// A long press or right-click opens it to show what is under the point; each row turns its layer
// on or off at a tap. "Save offline…" there, or "Save this area" (the view), switches it to
// saving: everything that overlaps the quad or view, ticked, with its exact size.
import { Dialog } from "@base-ui/react/dialog";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { Drawer } from "@base-ui/react/drawer";
import { DETENTS } from "@/map/map-model";
import { SHEET_POPUP } from "@/ui/sheet-chrome";
import { SheetGrabber } from "@/ui/sheet";
import { useIsDesktop } from "@/ui/use-breakpoint";
import { idOf } from "@/stac";
import { topUnit } from "@/map/identify";
import { useViewCtx } from "@/app";
import { Chevron } from "@/ui/chevron";
import { toLayer } from "@/map/to-layer";
import type { ItemRef } from "@/catalog/browse";
import type { ActiveLayer } from "@/map/map-model";
import { planArea } from "./area";
import { planCogArea } from "./cog-area";
import { ENGINE_BYTES } from "./engine";
import { planTableArea } from "./table-area";
import * as queue from "./queue";
import { overviewUrl, quadUrl, stateUrl } from "./basemap";
import { useBasemapIndex } from "./basemap-download";
import { type Hit, hitLabel, identifyAt, identifyPoints, tileAt } from "./identify";
import * as opfs from "./opfs";
import { useOffline } from "./store";
import {
  caveatOf, type Here, isSaveable, isUnitLayer, mostDetailed, otherUnit, probeOf, quadsFor, REFERENCE_IDS, ringAround,
  saveBbox, scaleOf, type Target, toleranceM,
  type Unit, unitOf, whatsHere,
} from "./whats-here";

// Pricing walks each archive's directory; four at a time keeps a busy area from opening dozens
// of range requests at once on a phone connection.
// A finishing task hands its slot straight to the next waiter, so a caller arriving in between
// cannot take it too and push the count past four.
let running = 0;
const waiting: (() => void)[] = [];
async function limit<T>(fn: () => Promise<T>): Promise<T> {
  if (running >= 4) await new Promise<void>((r) => waiting.push(r));
  else running++;
  try { return await fn(); } finally {
    const next = waiting.shift();
    if (next) next(); else running--;
  }
}

// A long press asks every layer what is at the point, through the same queue. One read is a header
// and a tile; one that takes longer than this is dropped from the list instead of holding it.
const PROBE_MS = 8_000;
const probe = <T,>(fn: () => Promise<T>): Promise<T> => limit(() => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("timed out")), PROBE_MS); });
  return Promise.race([fn(), late]).finally(() => clearTimeout(timer));
});

const BASEMAP = "__basemap__";

/** A switch's track and thumb, inside the row button that is the switch. */
const Knob = ({ on }: { on: boolean }) => (
  <span className={`relative h-6 w-10 shrink-0 rounded-full transition-colors ${on ? "bg-primary" : "bg-border"}`}>
    <span className={`absolute top-0.5 size-5 rounded-full bg-background shadow transition-all ${on ? "left-4.5" : "left-0.5"}`} />
  </span>
);
const HALF = DETENTS[1], FULL = DETENTS[2];

// Which groups a viewer has opened or closed, kept on this device. Until they choose, every group is
// open but Reference; storage can be blocked, and then the defaults hold.
const GROUPS_KEY = "whats-here-groups";
const REFERENCE = "Reference";
function readGroups(): Record<string, boolean> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(GROUPS_KEY) ?? "{}");
    return v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).filter(([, o]) => typeof o === "boolean")) : {};
  } catch { return {}; }
}

export function WhatsHerePicker({ target, startSaving, onClose }: {
  target: Target; startSaving: boolean; onClose: () => void;
}) {
  const [saving, setSaving] = useState(startSaving);
  const [opened, setOpened] = useState<string | null>(null);   // the row showing its attributes
  const [groupsOpen, setGroupsOpen] = useState(readGroups);
  const isOpen = (name: string) => groupsOpen[name] ?? name !== REFERENCE;
  const toggleGroup = (name: string) => {
    const next = { ...groupsOpen, [name]: !isOpen(name) };
    setGroupsOpen(next);
    try { localStorage.setItem(GROUPS_KEY, JSON.stringify(next)); } catch { /* kept for this visit only */ }
  };
  const listRef = useRef<HTMLDivElement>(null);
  const isDesktop = useIsDesktop();
  const [snap, setSnap] = useState<number | string>(HALF);
  const ctx = useViewCtx();
  const { files, engineBytes } = useOffline();
  const index = useBasemapIndex();

  // Every drawable layer the app knows about, resolved exactly as the map resolves them.
  const { candidates, scales } = useMemo(() => {
    const seen = new Map<string, ItemRef>();
    for (const r of [...ctx.mapItems, ...ctx.allItems]) if (!seen.has(r.href)) seen.set(r.href, r);
    const refs = [...seen.values()];
    return {
      candidates: refs.map((r) => toLayer(r)).filter((l): l is ActiveLayer => l !== null),
      scales: new Map(refs.map((r) => [idOf(r.href), scaleOf(r.data?.properties?.["ugs:scale"])])),
    };
  }, [ctx.mapItems, ctx.allItems]);
  const here = useMemo(() => whatsHere(candidates, target), [candidates, target]);
  const bbox = saveBbox(target);
  const quads = quadsFor(target);
  const saveable = here.filter(isSaveable);

  const have = new Set(files.map((f) => f.url));
  const stateSaved = have.has(stateUrl());
  const idx = index.data;
  const basemapParts = stateSaved || !idx ? [] : [
    ...(have.has(overviewUrl()) ? [] : [{ url: overviewUrl(), bytes: idx.overview.bytes }]),
    ...quads.flatMap((q) => {
      const archive = idx.quads[q.code];
      return archive && !have.has(quadUrl(q.code)) ? [{ url: quadUrl(q.code), bytes: archive.bytes }] : [];
    }),
  ];
  const basemapOffered = saving && !stateSaved && basemapParts.length > 0;

  // Tables start unticked: most trips want the map, and each table costs a footer read to price.
  const allTicked = () => new Set([BASEMAP,
    ...saveable.filter((h) => h.group !== "table" && !have.has(h.save.url)).map((h) => h.id)]);
  const [ticked, setTicked] = useState<Set<string>>(() => (startSaving ? allTicked() : new Set()));
  const startSave = () => { setTicked(allTicked()); setSaving(true); listRef.current?.scrollTo({ top: 0 }); };
  const toggle = (id: string, on: boolean) => setTicked((prev) => {
    const next = new Set(prev);
    if (on) next.add(id); else next.delete(id);
    return next;
  });

  const prices = useQueries({
    queries: saveable.map((h) => ({
      queryKey: ["offline-price", h.save.how, h.save.url, bbox.join(",")],
      // Priced by planning the save itself, so the plan is the job that gets queued.
      queryFn: (): Promise<queue.AreaJobSpec> => limit(async () => {
        const { how, url } = h.save;
        if (how === "area") {
          const plan = await planArea(url, bbox);
          return { kind: "area", plan, label: h.title, bytes: plan.bytes };
        }
        if (how === "table") {
          const plan = await planTableArea(url, bbox);
          return { kind: "table", plan, label: `${h.title} (table)`, bytes: plan.bytes };
        }
        const plan = await planCogArea(url, bbox);
        return { kind: "cog", plan, label: h.title, bytes: plan.bytes };
      }),
      enabled: saving && (h.group !== "table" || ticked.has(h.id)),
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const priceOf = (h: Here) => prices[saveable.findIndex((s) => s.id === h.id)];

  // Showing at a point: ask every layer what it has exactly there (probeOf), so the list is what is
  // under the click, not every layer whose extent covers it (statewide ones always do).
  const querying = !saving && target.kind === "point";
  const at = target.kind === "point" ? `${target.lon.toFixed(6)},${target.lat.toFixed(6)}` : "";
  const vectors = saveable.filter((h) => probeOf(h) === "identify");
  const identified = useQueries({
    queries: vectors.map((h) => ({
      queryKey: ["identify", h.save.url, `${at},${Math.round(target.kind === "point" ? target.zoom ?? 12 : 0)}`],
      queryFn: (): Promise<Hit[]> => probe(() => target.kind === "point"
        ? identifyAt(h.save.url, target.lon, target.lat, target.zoom ?? 12) : Promise.resolve([])),
      enabled: querying,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const mosaics = saveable.filter((h) => probeOf(h) === "tile");
  const tiled = useQueries({
    queries: mosaics.map((h) => ({
      queryKey: ["tile-at", h.save.url, at],
      queryFn: (): Promise<boolean> => probe(() => target.kind === "point"
        ? tileAt(h.save.url, target.lon, target.lat) : Promise.resolve(true)),
      enabled: querying,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  // A plate or mosaic with a `units` archive names the unit under the point too.
  const unitMaps = here.filter((h) => h.unitsHref);
  const unitHits = useQueries({
    queries: unitMaps.map((h) => ({
      queryKey: ["identify", h.unitsHref, `${at},${Math.round(target.kind === "point" ? target.zoom ?? 12 : 0)}`],
      queryFn: (): Promise<Hit[]> => probe(() => target.kind === "point" && h.unitsHref
        ? identifyAt(h.unitsHref, target.lon, target.lat, target.zoom ?? 12) : Promise.resolve([])),
      enabled: querying,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });
  const hitsOf = (h: Here) => identified[vectors.findIndex((v) => v.id === h.id)]?.data ?? [];
  const isThere = (h: Here) => (probeOf(h) === "identify" ? hitsOf(h).length > 0
    : probeOf(h) === "tile" ? !!tiled[mosaics.findIndex((m) => m.id === h.id)]?.data : probeOf(h) === "bbox");
  const checking = querying ? [...identified, ...tiled, ...unitHits].filter((q) => q.isPending).length : 0;

  // The geologic unit under the point, from the most detailed map that names one, and the most
  // detailed published map here, which may only be a scan.
  // `read` turns one point's hits into the unit there, so the same source can be asked around it.
  type Source = Unit & { from: Here; url: string; read: (hits: Hit[]) => Unit | null };
  const unitMapRead = (h: Here) => (hits: Hit[]): Unit | null => {
    const u = topUnit(hits);
    return u ? { symbol: u.code, name: u.name, age: u.age, description: u.description, scale: scales.get(h.id) } : null;
  };
  const unit = mostDetailed<Source>([
    ...vectors.filter((h) => isUnitLayer(h.id)).flatMap((h) => {
      const read = (hits: Hit[]) => hits.map((x) => unitOf(h.id, x.properties)).find(Boolean) ?? null;
      const u = read(hitsOf(h));
      return u ? [{ ...u, from: h, url: h.save.url, read }] : [];
    }),
    ...unitMaps.flatMap((h, i) => {
      const read = unitMapRead(h);
      const u = read(unitHits[i]?.data ?? []);
      return u && h.unitsHref ? [{ ...u, from: h, url: h.unitsHref, read }] : [];
    }),
  ]);
  // Another unit within the map's own tolerance of the point: the contact may run under the finger.
  const tol = unit?.scale ? toleranceM(unit.scale) : 0;
  const near = useQuery({
    queryKey: ["near-contact", unit?.url, at, tol],
    queryFn: async () => {
      if (!unit || target.kind !== "point") return null;
      const ring = await identifyPoints(unit.url, ringAround(target.lon, target.lat, tol), target.zoom ?? 12);
      return otherUnit(unit, ring.map(unit.read)) ?? null;
    },
    enabled: querying && !!unit && tol > 0,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const detailedMap = mostDetailed(here.filter((h) => h.group === "map" && isThere(h))
    .map((h) => ({ h, scale: scales.get(h.id) })).filter((m) => m.scale));

  // Ticks are set when the picker opens, possibly before the store's first read; what turns out to
  // be saved whole already is left out here rather than queued again.
  // What failed to price is not listed (see `listed`), so it is not saved either.
  const chosen = saveable.filter((h) => ticked.has(h.id) && !have.has(h.save.url) && !priceOf(h)?.isError);
  const basemapBytes = basemapOffered && ticked.has(BASEMAP) ? basemapParts.reduce((n, p) => n + p.bytes, 0) : 0;
  const pricing = chosen.some((h) => priceOf(h)?.isPending);
  // A table needs the table engine on the device too; it is counted once, the first time.
  const needsEngine = saving && !engineBytes && chosen.some((h) => h.group === "table");
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
          const job = priceOf(h)?.data;
          return job ? [job] : [];
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
    ? `Save offline · quad ${quads[0].code}`
    : `Save offline · ${quads.length} quad${quads.length === 1 ? "" : "s"}`;
  // When saving, only what can actually be saved is listed: a row you can't tick is noise. That
  // drops datacubes (no offline form) and anything whose pricing failed. Showing keeps all.
  const listed = saving ? here.filter((h) => h.save && !priceOf(h)?.isError)
    : querying ? here.filter(isThere)
    : here.filter((h) => h.group !== "table");   // a table is only something to save
  // What the geology card shows is not listed again below it.
  const better = detailedMap && detailedMap.h.id !== unit?.from.id
    && (detailedMap.scale ?? Infinity) < (unit?.scale ?? Infinity) ? detailedMap : undefined;
  const inCard = new Set(querying ? [unit?.from.id, better?.h.id] : []);
  const rows = listed.filter((h) => !inCard.has(h.id));
  const groups = [
    { name: "Data layers", rows: rows.filter((h) => h.group === "layer" && !REFERENCE_IDS.has(h.id)) },
    { name: "Data tables", rows: rows.filter((h) => h.group === "table") },
    { name: "Published maps", rows: rows.filter((h) => h.group === "map") },
    { name: REFERENCE, rows: rows.filter((h) => h.group === "layer" && REFERENCE_IDS.has(h.id)) },
  ].filter((g) => g.rows.length);


  // Showing: the row is a switch for its layer, so one tap puts it on the map or takes it off. What
  // is under the point leads, the layer it comes from under it; the chevron opens its attributes,
  // what "query what's under my click" is for.
  const showRow = (h: Here) => {
    const on = ctx.isActive(h.id);
    const found = hitsOf(h);
    const labels = [...new Set(found.map((x) => hitLabel(x.properties)).filter(Boolean))];
    return (
      <div key={h.id}>
        <div className="flex items-center hover:bg-hover">
          <button type="button" role="switch" aria-checked={on} onClick={() => ctx.toggleLayers([h.id], !on)}
            className="flex min-w-0 flex-1 items-center gap-3 py-1.5 pl-4 text-left pointer-coarse:min-h-12">
            <span className="min-w-0 flex-1">
              {labels.length > 0 && (
                <span className="block truncate font-semibold" title={labels.join(" · ")}>{labels.slice(0, 3).join(" · ")}</span>
              )}
              <span className={`block truncate ${labels.length ? "text-xs text-muted-foreground" : ""}`} title={h.title}>{h.title}</span>
            </span>
            <Knob on={on} />
          </button>
          {found.length > 0 ? (
            <button type="button" aria-expanded={opened === h.id} aria-label={`Attributes of ${h.title}`}
              onClick={() => setOpened(opened === h.id ? null : h.id)}
              className="flex w-11 shrink-0 items-center justify-center self-stretch text-muted-foreground">
              <Chevron open={opened === h.id} />
            </button>
          ) : <span className="w-11 shrink-0" />}
        </div>
        {opened === h.id && (
          <div className="px-4 pb-2 text-xs">
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
          </div>
        )}
      </div>
    );
  };


  const saveRow = (h: Here) => {
    const price = h.save ? priceOf(h) : undefined;
    const savedWhole = !!h.save && have.has(h.save.url);   // the whole file is already stored
    const disabled = !h.save || savedWhole || !!price?.isError;
    return (
      <label key={h.id} className={`flex items-center gap-2 px-3 py-1.5 pointer-coarse:min-h-12 ${disabled ? "opacity-60" : "cursor-pointer hover:bg-hover"}`}>
        <input type="checkbox" className="h-4 w-4 accent-primary pointer-coarse:h-5 pointer-coarse:w-5" disabled={disabled}
          checked={ticked.has(h.id) && !disabled} onChange={(e) => toggle(h.id, e.target.checked)} />
        <span className="min-w-0 flex-1 truncate" title={h.title}>{h.title}</span>
        {ctx.isActive(h.id) && <span className="shrink-0 text-xs text-muted-foreground">on map</span>}
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {savedWhole ? "saved" : price?.data ? opfs.formatBytes(price.data.bytes)
            : h.group === "table" && !ticked.has(h.id) ? "" : "…"}
        </span>
      </label>
    );
  };

  // How the point is mapped, worded no surer than the map's scale allows: its unit with a caveat
  // for the scale and any contact within that scale's tolerance, and a more detailed published
  // map when there is one, first when it is a 1:24,000 quadrangle. Each is a switch for its layer.
  const ratio = (s?: number) => (s ? `1:${s.toLocaleString()}` : "");
  const betterFirst = !!better && (better.scale ?? Infinity) <= 24000;
  const caveat = caveatOf(unit?.scale);
  const mapRow = better && (
    <button type="button" role="switch" aria-checked={ctx.isActive(better.h.id)}
      onClick={() => ctx.toggleLayers([better.h.id], !ctx.isActive(better.h.id))}
      className={`flex w-full items-center gap-3 rounded-lg text-left ${betterFirst
        ? "mt-1 bg-primary/10 p-2" : "mt-2 border-t border-border pt-2"}`}>
      <span className="min-w-0 flex-1">
        <span className="block text-xs font-semibold text-primary">Better source · more detailed map · {ratio(better.scale)}</span>
        <span className="block truncate text-sm" title={better.h.title}>{better.h.title}</span>
      </span>
      <Knob on={ctx.isActive(better.h.id)} />
    </button>
  );
  const card = querying && (unit || better) && (
    <section className="mx-3 mb-1 mt-1 rounded-xl border border-border bg-card p-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Mapped as</div>
      {betterFirst && mapRow}
      {unit && (
        <button type="button" role="switch" aria-checked={ctx.isActive(unit.from.id)}
          onClick={() => ctx.toggleLayers([unit.from.id], !ctx.isActive(unit.from.id))}
          className={`flex w-full items-start gap-3 text-left ${betterFirst ? "mt-2 border-t border-border pt-2" : "mt-1"}`}>
          <span className="min-w-0 flex-1">
            <span className="block text-lg font-semibold leading-snug">
              {unit.symbol && <span className="mr-1.5 rounded bg-muted px-1.5 font-mono text-base">{unit.symbol}</span>}
              {unit.name}
            </span>
            {unit.age && <span className="block text-sm">{unit.age}</span>}
            {unit.description && <span className="mt-1 line-clamp-4 block text-sm text-muted-foreground">{unit.description}</span>}
            {near.data && (
              <span className="mt-1 block text-sm">Near contact with {near.data.name ?? near.data.symbol}</span>
            )}
            <span className="mt-1 block text-xs text-muted-foreground">
              {unit.from.title}{unit.scale ? ` · ${ratio(unit.scale)}` : ""}{caveat ? `. ${caveat}` : ""}
            </span>
          </span>
          <Knob on={ctx.isActive(unit.from.id)} />
        </button>
      )}
      {!betterFirst && mapRow}
    </section>
  );


  const btn = "rounded-md border border-border px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-50 pointer-coarse:min-h-11";
  const list = (
    <>
      {card}
      {checking > 0 && (
        <p className="px-4 py-1 text-xs text-muted-foreground">Checking {checking} more layer{checking === 1 ? "" : "s"}…</p>
      )}
      {!listed.length && !basemapOffered && !checking && (
        <p className="px-4 py-2 text-sm text-muted-foreground">{saving ? "Nothing here can be saved offline." : "Nothing mapped at this spot."}</p>
      )}
      {basemapOffered && (
        <section>
          <div className="px-4 pb-1 pt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Basemap</div>
          <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-hover pointer-coarse:min-h-12">
            <input type="checkbox" className="h-4 w-4 accent-primary pointer-coarse:h-5 pointer-coarse:w-5" checked={ticked.has(BASEMAP)}
              onChange={(e) => toggle(BASEMAP, e.target.checked)} />
            <span className="flex-1">Streets basemap for {quads.length === 1 ? "this quad" : `these ${quads.length} quads`}</span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {opfs.formatBytes(basemapParts.reduce((n, p) => n + p.bytes, 0))}
            </span>
          </label>
        </section>
      )}
      {groups.map((g) => {
        const ids = g.rows.filter((h) => h.save).map((h) => h.id);
        const all = ids.length > 0 && ids.every((id) => ticked.has(id));
        return (
          <section key={g.name}>
            <div className="flex items-center pr-4 pt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <button type="button" aria-expanded={isOpen(g.name)} onClick={() => toggleGroup(g.name)}
                className="flex min-h-9 flex-1 items-center gap-2 px-4 text-left uppercase tracking-wider hover:text-foreground">
                <Chevron open={isOpen(g.name)} />
                <span>{g.name} · {g.rows.length}</span>
              </button>
              {saving && isOpen(g.name) && (
                <button type="button" className="normal-case tracking-normal text-primary hover:underline pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:px-3"
                  onClick={() => setTicked((prev) => {
                    const next = new Set(prev);
                    for (const id of ids) { if (all) next.delete(id); else next.add(id); }
                    return next;
                  })}>
                  {all ? "None" : "All"}
                </button>
              )}
            </div>
            {isOpen(g.name) && g.rows.map(saving ? saveRow : showRow)}
          </section>
        );
      })}
    </>
  );
  const footer = (close: ReactNode) => (
    <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
      {save.error && <p className="w-full text-sm text-destructive">{save.error.message}</p>}
      {saving ? <>
        {!startSaving && <button type="button" className={btn} disabled={save.isPending} onClick={() => setSaving(false)}>Back</button>}
        <button type="button" className={btn} disabled={save.isPending || ![...ticked].some((id) => id !== BASEMAP)}
          onClick={show}>Show on map</button>
        <button type="button" className={`${btn} ml-auto bg-primary text-primary-foreground hover:bg-primary/90`}
          disabled={save.isPending || pricing || total === 0}
          onClick={() => save.mutate(undefined, { onSuccess: onClose })}>
          {pricing ? "Pricing…" : `Save offline · ${opfs.formatBytes(total)}`}
        </button>
      </> : <>
        {opfs.isSupported() && (
          <button type="button" className={btn} onClick={startSave}>
            Save {target.kind === "point" ? "this quad" : "this area"} offline…
          </button>
        )}
        {close}
      </>}
    </div>
  );
  const closeX = "rounded px-2 text-muted-foreground hover:text-foreground pointer-coarse:-my-2 pointer-coarse:min-h-11 pointer-coarse:min-w-11";

  // Phones: the app's bottom sheet, non-modal so the map and the pin stay in view above it. Half
  // height first; a swipe down from there, Escape or ✕ closes it.
  if (!isDesktop) {
    return (
      <Drawer.Root open modal={false} snapPoints={[HALF, FULL]} snapPoint={snap}
        onSnapPointChange={(p) => { if (p === null) onClose(); else setSnap(p); }}
        onOpenChange={(open, details) => {
          if (open) return;
          if (save.isPending) details.cancel(); else onClose();
        }}>
        <Drawer.Portal>
          {/* Above the Utah header, which sits at z-index 3000. */}
          <Drawer.Viewport className="pointer-events-none fixed inset-0 z-3101 flex items-end">
            <Drawer.Popup initialFocus={false} style={{ height: `${FULL * 100}%` }} className={SHEET_POPUP}>
              <SheetGrabber label={snap === HALF ? "Expand panel to full height" : "Collapse panel"}
                onStep={() => setSnap(snap === HALF ? FULL : HALF)} onArrow={(s) => setSnap(s > 0 ? FULL : HALF)} />
              <div className="flex items-center gap-2 border-b border-border px-4 pb-2">
                <Drawer.Title className="flex-1 text-base font-semibold">{title}</Drawer.Title>
                <Drawer.Close className={closeX} aria-label="Close">✕</Drawer.Close>
              </div>
              <Drawer.Content ref={listRef} className="min-h-0 flex-1 touch-auto overflow-y-auto overscroll-contain py-2">
                {list}
              </Drawer.Content>
              {footer(<Drawer.Close className={`${btn} ml-auto`}>Done</Drawer.Close>)}
            </Drawer.Popup>
          </Drawer.Viewport>
        </Drawer.Portal>
      </Drawer.Root>
    );
  }
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !save.isPending) onClose(); }}>
      <Dialog.Portal>
        {/* Above the Utah header, which sits at z-index 3000 and otherwise covers the title bar. */}
        <Dialog.Backdrop className="fixed inset-0 z-3100 bg-black/40" />
        <Dialog.Popup className="fixed inset-x-0 bottom-0 z-3101 flex max-h-modal flex-col rounded-t-2xl border border-border bg-background shadow-2xl md:inset-x-auto md:bottom-auto md:left-1/2 md:top-1/2 md:w-lg md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-xl">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Dialog.Title className="flex-1 text-base font-semibold">{title}</Dialog.Title>
            <Dialog.Close className={closeX} aria-label="Close">✕</Dialog.Close>
          </div>
          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto py-2">
            {list}
          </div>
          {footer(<Dialog.Close className={`${btn} ml-auto`}>Done</Dialog.Close>)}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
